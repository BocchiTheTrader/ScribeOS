"""REST API."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, ValidationError

from ..collab.team import TeamSession
from ..export.common import ExportOptions
from ..storage import backups, migrations, pmdoc
from ..storage.assets import ASSET_NAME_RE, AssetError, TooLarge
from ..storage.models import ID_RE
from ..storage.project_store import ProjectError, ProjectLocked
from .context import AppContext
from .security import COOKIE, GUEST_TTL, ROLES, RateLimiter, client_ip, get_session, is_loopback, require

FONT_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-]{1,80}\.(ttf|otf|woff2?)$")
MAX_FONT = 30 * 1024 * 1024
MAX_IMPORT = 200 * 1024 * 1024


# --------------------------------------------------------------------------- request bodies
class NewProject(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    parent: Optional[str] = Field(None, max_length=1000)


class OpenProject(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)
    force: bool = False


class SaveAs(BaseModel):
    parent: str = Field(..., min_length=1, max_length=1000)
    name: Optional[str] = Field(None, max_length=200)


class ChapterSave(BaseModel):
    doc: dict
    baseUpdatedAt: Optional[str] = None


class NewChapter(BaseModel):
    title: str = Field("New Chapter", max_length=300)
    partId: Optional[str] = Field(None, pattern=ID_RE.pattern)
    after: Optional[str] = Field(None, pattern=ID_RE.pattern)
    template: Optional[str] = Field(None, max_length=200_000)


class Search(BaseModel):
    q: str = Field(..., min_length=1, max_length=1000)
    regex: bool = False
    case: bool = False
    whole: bool = False
    replacement: str = Field("", max_length=1000)
    chapterIds: Optional[list[str]] = None


class TeamStart(BaseModel):
    password: Optional[str] = Field(None, min_length=8, max_length=200)
    port: Optional[int] = Field(None, ge=1024, le=65535)
    defaultRole: Optional[str] = Field(None, pattern="^(editor|commenter|viewer)$")
    https: Optional[bool] = None


class TeamPatch(BaseModel):
    locked: Optional[bool] = None
    defaultRole: Optional[str] = Field(None, pattern="^(editor|commenter|viewer)$")
    password: Optional[str] = Field(None, min_length=8, max_length=200)
    regeneratePassword: bool = False


class Join(BaseModel):
    room: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=40)
    password: str = Field(..., min_length=1, max_length=200)


class RoleChange(BaseModel):
    role: str = Field(..., pattern="^(editor|commenter|viewer)$")


class Chat(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


async def _limited_stream(request: Request, max_bytes: int) -> AsyncIterator[bytes]:
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_bytes:
        raise TooLarge(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise TooLarge(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
        yield chunk


async def _read_body(request: Request, max_bytes: int) -> bytes:
    parts = []
    async for chunk in _limited_stream(request, max_bytes):
        parts.append(chunk)
    return b"".join(parts)


def _filename(request: Request) -> str:
    raw = request.headers.get("x-filename") or request.query_params.get("filename") or "upload"
    try:
        from urllib.parse import unquote

        raw = unquote(raw)
    except Exception:
        pass
    return os.path.basename(raw.replace("\\", "/"))[:200]


def build_router(ctx: AppContext) -> APIRouter:
    r = APIRouter()
    join_limiter = RateLimiter(5, 60)
    join_global = RateLimiter(60, 60)

    def auth(request: Request, role: str = "viewer"):
        return require(ctx, request, role)

    def project():
        try:
            return ctx.require_project()
        except ProjectError as e:
            raise HTTPException(409, str(e)) from e

    def team_info_for(session) -> dict:
        if ctx.team is None:
            return {"active": False}
        return ctx.team.info(include_secret=session.role == "owner")

    # ------------------------------------------------------------------ auth
    @r.get("/auth")
    def owner_login(request: Request, t: str = ""):
        if not is_loopback(request) or not t or not secrets.compare_digest(t, ctx.local_token):
            raise HTTPException(403, "Invalid launch token")
        existing = get_session(ctx, request)
        resp = RedirectResponse("/", status_code=303)
        if existing is None or existing.role != "owner":
            _, cookie = ctx.sessions.create("owner", ctx.config.settings.authorName or "Host", client_ip(request))
            resp.set_cookie(COOKIE, cookie, httponly=True, samesite="strict", path="/")
        return resp

    @r.get("/api/me")
    def me(request: Request):
        s = get_session(ctx, request)
        if s is None:
            return {"signedIn": False, "teamActive": ctx.team is not None}
        return {
            "signedIn": True,
            "session": s.public(),
            "csrf": s.csrf,
            "role": s.role,
            "projectOpen": ctx.project is not None,
            "team": team_info_for(s),
            "desktop": ctx.window is not None and s.role == "owner",
            "dev": ctx.dev,
        }

    @r.post("/api/logout")
    async def logout(request: Request):
        s = auth(request)
        if s.role != "owner":
            ctx.sessions.remove(s.sid)
            ctx.hub.publish({"type": "members-changed"})
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ------------------------------------------------------------------ settings
    @r.get("/api/settings")
    def get_settings(request: Request):
        s = auth(request)
        data = ctx.config.settings.model_dump()
        if s.role != "owner":
            return {"editor": data["editor"], "team": {"guestMaxUploadMB": data["team"]["guestMaxUploadMB"]}}
        return data

    @r.patch("/api/settings")
    async def patch_settings(request: Request):
        auth(request, "owner")
        patch = await request.json()
        if not isinstance(patch, dict):
            raise HTTPException(400, "Expected an object")
        patch.pop("recentProjects", None)
        try:
            settings = ctx.config.update(patch)
        except ValidationError as e:
            raise HTTPException(422, e.errors()[0].get("msg", "Invalid settings")) from e
        ctx.hub.publish({"type": "settings-changed"})
        return settings.model_dump()

    @r.get("/api/recent")
    def recent(request: Request):
        auth(request, "owner")
        out = []
        for p in ctx.config.settings.recentProjects:
            meta = None
            try:
                with open(Path(p) / "project.json", encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                pass
            out.append({"path": p, "exists": meta is not None, "name": (meta or {}).get("name") or Path(p).stem, "updatedAt": (meta or {}).get("updatedAt")})
        return out

    @r.post("/api/recent/remove")
    async def recent_remove(request: Request):
        auth(request, "owner")
        body = await request.json()
        ctx.config.remove_recent(str(body.get("path", "")))
        return {"ok": True}

    # ------------------------------------------------------------------ project lifecycle (owner)
    @r.post("/api/projects")
    def create_project(request: Request, body: NewProject):
        auth(request, "owner")
        parent = Path(body.parent or ctx.config.settings.storage.projectsDir)
        try:
            p = ctx.create_project(parent, body.name)
        except OSError as e:
            raise HTTPException(400, f"Could not create project folder: {e}") from e
        return p.summary()

    @r.post("/api/projects/open")
    def open_project(request: Request, body: OpenProject):
        auth(request, "owner")
        try:
            p = ctx.open_project(Path(body.path), force=body.force)
        except ProjectLocked as e:
            raise HTTPException(423, str(e)) from e
        except (ProjectError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return p.summary()

    @r.post("/api/projects/close")
    def close_project(request: Request):
        auth(request, "owner")
        ctx.close_project()
        return {"ok": True}

    @r.post("/api/projects/save-as")
    def save_as(request: Request, body: SaveAs):
        auth(request, "owner")
        p = project()
        try:
            dest = p.copy_to(Path(body.parent), body.name)
            newp = ctx.open_project(dest)
        except (OSError, ProjectError) as e:
            raise HTTPException(400, str(e)) from e
        return newp.summary()

    @r.post("/api/projects/import-archive")
    async def import_archive(request: Request):
        auth(request, "owner")
        with tempfile.TemporaryDirectory() as tmp:
            zp = Path(tmp) / "in.zip"
            with open(zp, "wb") as f:
                async for chunk in _limited_stream(request, 4 * 1024**3):
                    f.write(chunk)
            try:
                dest = await asyncio.to_thread(backups.safe_extract, zp, Path(ctx.config.settings.storage.projectsDir))
            except Exception as e:
                raise HTTPException(400, f"Not a valid project archive: {e}") from e
        try:
            p = await run_in_threadpool(ctx.open_project, dest)
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e
        return p.summary()

    @r.post("/api/import/v1")
    async def import_v1(request: Request):
        auth(request, "owner")
        raw = await _read_body(request, 100 * 1024 * 1024)
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as e:
            raise HTTPException(400, "File is not valid JSON") from e
        try:
            paths, patch = await asyncio.to_thread(migrations.import_v1, data, Path(ctx.config.settings.storage.projectsDir), ctx.config.settings.authorName)
        except migrations.MigrationError as e:
            raise HTTPException(400, str(e)) from e
        if patch.get("editor"):
            try:
                ctx.config.update(patch)
            except ValidationError:
                pass
        for pth in reversed(paths[1:]):
            ctx.config.add_recent(str(pth))
        if paths:
            await run_in_threadpool(ctx.open_project, paths[0])
        return {"created": [str(p) for p in paths]}

    @r.get("/api/backups")
    def list_backups(request: Request):
        auth(request, "owner")
        return backups.list_backups(Path(ctx.config.settings.storage.backupDir), project())

    @r.post("/api/backups")
    def backup_now(request: Request):
        auth(request, "owner")
        s = ctx.config.settings.storage
        out = backups.make_backup(Path(s.backupDir), project(), s.backupKeep)
        return {"name": out.name}

    @r.post("/api/backups/restore")
    async def restore_backup(request: Request):
        auth(request, "owner")
        body = await request.json()
        name = str(body.get("name", ""))
        if not re.fullmatch(r"backup-\d{8}-\d{6}\.zip", name):
            raise HTTPException(400, "Invalid backup name")
        src = backups.backup_folder(Path(ctx.config.settings.storage.backupDir), project()) / name
        if not src.exists():
            raise HTTPException(404, "Backup not found")
        dest = await asyncio.to_thread(backups.safe_extract, src, Path(ctx.config.settings.storage.projectsDir))
        p = await run_in_threadpool(ctx.open_project, dest)
        p.update_meta({"name": f"{p.meta.name} (restored)"})
        return p.summary()

    # ------------------------------------------------------------------ current project
    @r.get("/api/project")
    def get_project(request: Request):
        auth(request)
        return project().summary() | {"teamActive": ctx.team is not None}

    @r.patch("/api/project")
    async def patch_project(request: Request):
        auth(request, "editor")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected an object")
        try:
            meta = project().update_meta(body)
        except ValidationError as e:
            raise HTTPException(422, e.errors()[0].get("msg")) from e
        ctx.hub.publish({"type": "tree-changed"})
        return meta.model_dump()

    @r.put("/api/project/tree")
    async def put_tree(request: Request):
        s = auth(request, "editor")
        body = await request.json()
        try:
            meta = project().set_tree(body.get("tree") if isinstance(body, dict) else None)
        except (ValidationError, ProjectError) as e:
            raise HTTPException(400, str(e)) from e
        ctx.hub.publish({"type": "tree-changed"}, exclude=s.sid)
        return meta.model_dump()

    @r.patch("/api/project/nodes/{nid}")
    async def patch_node(nid: str, request: Request):
        s = auth(request, "editor")
        body = await request.json()
        try:
            project().update_node(nid, body if isinstance(body, dict) else {})
        except (ValidationError, ProjectError) as e:
            raise HTTPException(400, str(e)) from e
        ctx.hub.publish({"type": "tree-changed"}, exclude=s.sid)
        return {"ok": True}

    @r.post("/api/project/chapters")
    def new_chapter(request: Request, body: NewChapter):
        auth(request, "editor")
        doc = pmdoc.html_to_doc(body.template) if body.template else None
        try:
            node = project().add_chapter(body.title, body.partId, doc, body.after)
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e
        ctx.publish({"type": "tree-changed"})
        return node.model_dump()

    @r.post("/api/project/parts")
    async def new_part(request: Request):
        auth(request, "editor")
        body = await request.json()
        part = project().add_part(str(body.get("title") or "New Part")[:300])
        ctx.hub.publish({"type": "tree-changed"})
        return part.model_dump()

    @r.delete("/api/project/nodes/{nid}")
    async def delete_node(nid: str, request: Request):
        auth(request, "editor")
        if nid in ctx.rooms.rooms:
            await ctx.rooms.reset(nid)
        try:
            project().delete_node(nid)
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e
        ctx.hub.publish({"type": "tree-changed"})
        return {"ok": True}

    @r.get("/api/project/trash")
    def trash(request: Request):
        auth(request)
        return project().list_trash()

    @r.post("/api/project/trash/{cid}/restore")
    def restore_trash(cid: str, request: Request):
        auth(request, "editor")
        try:
            node = project().restore_trash(cid)
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e
        ctx.publish({"type": "tree-changed"})
        return node.model_dump()

    # ------------------------------------------------------------------ chapters
    @r.get("/api/chapters/{cid}")
    def get_chapter(cid: str, request: Request):
        auth(request)
        try:
            return project().get_chapter(cid)
        except ProjectError as e:
            raise HTTPException(404, str(e)) from e

    @r.put("/api/chapters/{cid}")
    def put_chapter(cid: str, request: Request, body: ChapterSave):
        s = auth(request, "editor")
        if ctx.team is not None:
            raise HTTPException(409, "Teamwork is active: changes are saved through the live session")
        p = project()
        try:
            result = p.save_chapter(cid, body.doc, ctx.config.settings.storage.snapshotIntervalMin * 60)
        except ProjectError as e:
            raise HTTPException(404, str(e)) from e
        ctx.stats.record(result["delta"])
        ctx.publish({"type": "chapter-saved", "id": cid, "wordCount": result["wordCount"]}, exclude=s.sid)
        return result

    async def _external_change(cid: str, fn):
        """Run a server-side rewrite of a chapter safely while it may be open in a live room."""
        room = ctx.rooms.rooms.get(cid)
        if room is not None:
            await room.persist()
        result = await asyncio.to_thread(fn)
        await ctx.rooms.reset(cid)
        ctx.hub.publish({"type": "chapter-replaced", "id": cid})
        return result

    @r.get("/api/chapters/{cid}/snapshots")
    def snapshots(cid: str, request: Request):
        auth(request)
        try:
            return project().list_snapshots(cid)
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e

    @r.post("/api/chapters/{cid}/snapshots")
    async def make_snapshot(cid: str, request: Request):
        auth(request, "editor")
        body = await request.json()
        room = ctx.rooms.rooms.get(cid)
        if room is not None:
            await room.persist()
        try:
            return project().create_snapshot(cid, label=str(body.get("label", ""))[:120])
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e

    @r.get("/api/chapters/{cid}/snapshots/{sid}")
    def get_snapshot(cid: str, sid: str, request: Request):
        auth(request)
        try:
            snap = project().get_snapshot(cid, sid)
        except ProjectError as e:
            raise HTTPException(404, str(e)) from e
        snap["text"] = pmdoc.doc_text(snap["doc"])
        snap["currentText"] = pmdoc.doc_text(project().get_chapter(cid)["doc"])
        return snap

    @r.post("/api/chapters/{cid}/snapshots/{sid}/restore")
    async def restore_snapshot(cid: str, sid: str, request: Request):
        auth(request, "editor")
        try:
            return await _external_change(cid, lambda: project().restore_snapshot(cid, sid))
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e

    @r.delete("/api/chapters/{cid}/snapshots/{sid}")
    def delete_snapshot(cid: str, sid: str, request: Request):
        auth(request, "editor")
        try:
            project().delete_snapshot(cid, sid)
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    # ------------------------------------------------------------------ notes & codex
    @r.get("/api/notes")
    def get_notes(request: Request):
        auth(request)
        return project().get_notes()

    @r.put("/api/notes")
    async def put_notes(request: Request):
        s = auth(request, "commenter")
        body = await request.json()
        try:
            notes = project().save_notes(body)
        except (ValidationError, ProjectError) as e:
            raise HTTPException(400, "Invalid notes") from e
        ctx.hub.publish({"type": "notes-changed"}, exclude=s.sid)
        return notes

    @r.get("/api/codex")
    def get_codex(request: Request):
        auth(request)
        return project().get_codex()

    @r.put("/api/codex")
    async def put_codex(request: Request):
        s = auth(request, "editor")
        body = await request.json()
        try:
            entries = project().save_codex(body)
        except (ValidationError, ProjectError) as e:
            raise HTTPException(400, f"Invalid codex: {e}") from e
        ctx.hub.publish({"type": "codex-changed"}, exclude=s.sid)
        return entries

    # ------------------------------------------------------------------ search / replace
    @r.post("/api/search")
    def search(request: Request, body: Search):
        s = auth(request)
        if body.regex and s.role != "owner":
            raise HTTPException(403, "Regular expressions are only available to the host")
        try:
            return project().search(body.q, body.regex, body.case, body.whole)
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e

    @r.post("/api/replace")
    async def replace(request: Request, body: Search):
        s = auth(request, "editor")
        if body.regex and s.role != "owner":
            raise HTTPException(403, "Regular expressions are only available to the host")
        p = project()
        for cid in list(ctx.rooms.rooms):
            await ctx.rooms.rooms[cid].persist()
        try:
            result = await asyncio.to_thread(p.replace_all, body.q, body.replacement, body.regex, body.case, body.whole, body.chapterIds)
        except ProjectError as e:
            raise HTTPException(400, str(e)) from e
        for cid in result["chapters"]:
            await ctx.rooms.reset(cid)
            ctx.hub.publish({"type": "chapter-replaced", "id": cid})
        ctx.hub.publish({"type": "tree-changed"})
        return result

    # ------------------------------------------------------------------ assets
    @r.post("/api/assets")
    async def upload_asset(request: Request):
        s = auth(request, "editor")
        p = project()
        limit_mb = ctx.config.settings.storage.maxUploadMB
        if s.role != "owner" and ctx.team is not None:
            limit_mb = min(limit_mb, ctx.team.guest_upload_mb)
        try:
            return await p.assets.save_stream(_limited_stream(request, limit_mb * 1024 * 1024), limit_mb * 1024 * 1024)
        except TooLarge as e:
            raise HTTPException(413, str(e)) from e
        except AssetError as e:
            raise HTTPException(415, str(e)) from e

    @r.get("/api/assets")
    def list_assets(request: Request):
        auth(request)
        p = project()
        used: dict[str, list[str]] = {}
        for node, doc in p.all_chapter_docs():
            for name in pmdoc.collect_assets(doc):
                used.setdefault(name, []).append(node.title)
        for e in p.get_codex():
            if e.get("image"):
                used.setdefault(e["image"].rsplit("/", 1)[1], []).append(f"Codex: {e['name']}")
        return [a | {"usedIn": sorted(set(used.get(a["name"], [])))} for a in p.assets.list()]

    @r.get("/api/assets/{name}")
    def get_asset(name: str, request: Request):
        auth(request)
        if not ASSET_NAME_RE.match(name):
            raise HTTPException(404)
        path = project().assets.path(name)
        if not path.exists():
            raise HTTPException(404)
        from ..storage.assets import MIME

        return FileResponse(path, media_type=MIME[name.rsplit(".", 1)[1]], headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @r.get("/api/assets/{name}/thumb")
    def get_thumb(name: str, request: Request):
        auth(request)
        if not ASSET_NAME_RE.match(name):
            raise HTTPException(404)
        t = project().assets.thumbnail(name)
        if t is None:
            raise HTTPException(404)
        return FileResponse(t, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @r.delete("/api/assets/{name}")
    def delete_asset(name: str, request: Request):
        auth(request, "owner")
        try:
            project().assets.delete(name)
        except AssetError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    # ------------------------------------------------------------------ import into project
    @r.post("/api/import")
    async def import_into(request: Request):
        auth(request, "owner")
        from ..export.importers import ImportError_, import_file

        name = _filename(request)
        data = await _read_body(request, MAX_IMPORT)
        p = project()
        try:
            chapters = await asyncio.to_thread(import_file, p, name, data)
        except ImportError_ as e:
            raise HTTPException(400, str(e)) from e
        created = []
        for title, doc in chapters[:2000]:
            created.append(p.add_chapter(title, doc=doc).model_dump())
        ctx.hub.publish({"type": "tree-changed"})
        return {"chapters": created}

    # ------------------------------------------------------------------ export
    @r.post("/api/export")
    async def do_export(request: Request, opts: ExportOptions):
        auth(request, "editor")
        from ..export.service import export

        for room in list(ctx.rooms.rooms.values()):
            await room.persist()
        try:
            data, filename, mime = await run_in_threadpool(export, project(), opts, ctx.font_dirs())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # typst errors etc.
            raise HTTPException(500, f"Export failed: {e}") from e
        from urllib.parse import quote

        return Response(data, media_type=mime, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})

    @r.post("/api/export/preview")
    async def export_preview(request: Request, opts: ExportOptions):
        auth(request, "editor")
        from ..export.service import pdf_preview

        try:
            pages = await run_in_threadpool(pdf_preview, project(), opts, ctx.font_dirs())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(500, f"Preview failed: {e}") from e
        return {"pages": ["data:image/png;base64," + base64.b64encode(pg).decode() for pg in pages]}

    # ------------------------------------------------------------------ stats
    @r.get("/api/stats")
    def stats(request: Request):
        auth(request, "owner")
        out = ctx.stats.summary(ctx.config.settings.editor.dailyGoal)
        p = ctx.project
        if p is not None:
            total = sum(c.wordCount for c in p.meta.chapters())
            out["project"] = {"words": total, "target": p.meta.targets.projectWords, "deadline": p.meta.targets.deadline}
            if p.meta.targets.projectWords and p.meta.targets.deadline:
                from datetime import date

                try:
                    days_left = (date.fromisoformat(p.meta.targets.deadline) - date.today()).days
                except ValueError:
                    days_left = 0
                remaining = max(0, p.meta.targets.projectWords - total)
                out["project"]["perDay"] = remaining if days_left <= 0 else -(-remaining // days_left)
                out["project"]["daysLeft"] = days_left
        return out

    # ------------------------------------------------------------------ fonts
    @r.get("/api/fonts")
    def list_fonts(request: Request):
        auth(request)
        out = []
        for f in sorted(ctx.paths.fonts_dir.iterdir()):
            if f.is_file() and FONT_NAME_RE.match(f.name):
                meta = _font_meta(f)
                out.append({"file": f.name, "family": meta or f.stem, "url": f"/api/fonts/file/{f.name}"})
        return out

    @r.post("/api/fonts")
    async def upload_font(request: Request):
        auth(request, "owner")
        name = _filename(request)
        name = re.sub(r"[^A-Za-z0-9 _.\-]", "_", name)
        if not FONT_NAME_RE.match(name):
            raise HTTPException(400, "Font must be a .ttf, .otf, .woff or .woff2 file")
        data = await _read_body(request, MAX_FONT)
        if not data.startswith((b"\x00\x01\x00\x00", b"OTTO", b"true", b"wOFF", b"wOF2")):
            raise HTTPException(415, "Not a valid font file")
        dest = ctx.paths.fonts_dir / name
        dest.write_bytes(data)
        family = _font_meta(dest)
        if family is None:
            dest.unlink()
            raise HTTPException(415, "Could not read the font")
        return {"file": name, "family": family, "url": f"/api/fonts/file/{name}"}

    @r.delete("/api/fonts/{name}")
    def delete_font(name: str, request: Request):
        auth(request, "owner")
        if not FONT_NAME_RE.match(name):
            raise HTTPException(400)
        (ctx.paths.fonts_dir / name).unlink(missing_ok=True)
        return {"ok": True}

    @r.get("/api/fonts/file/{name}")
    def font_file(name: str, request: Request):
        auth(request)
        if not FONT_NAME_RE.match(name):
            raise HTTPException(404)
        path = ctx.paths.fonts_dir / name
        if not path.is_file():
            raise HTTPException(404)
        mt = {"ttf": "font/ttf", "otf": "font/otf", "woff": "font/woff", "woff2": "font/woff2"}[name.rsplit(".", 1)[1]]
        return FileResponse(path, media_type=mt, headers={"Cache-Control": "private, max-age=86400"})

    @r.get("/api/fonts/system")
    async def system_fonts(request: Request):
        auth(request, "owner")
        return await asyncio.to_thread(_system_fonts)

    # ------------------------------------------------------------------ teamwork
    @r.get("/api/team/status")
    def team_status():
        # Public: only says whether a session is running.
        return {"active": ctx.team is not None, "locked": bool(ctx.team and ctx.team.locked)}

    @r.get("/api/team")
    def team(request: Request):
        s = auth(request)
        return team_info_for(s) | {"members": _members()}

    def _members() -> list[dict]:
        seen: dict[str, dict] = {}
        presence = ctx.rooms.presence()
        for c in ctx.hub.clients:
            sess = c.session
            if sess.sid in seen:
                continue
            chapter = next((cid for cid, sids in presence.items() if sess.sid in sids), None)
            seen[sess.sid] = sess.public() | {"chapterId": chapter}
        return list(seen.values())

    @r.post("/api/team/start")
    async def team_start(request: Request, body: TeamStart):
        auth(request, "owner")
        project()
        if ctx.team is not None:
            return ctx.team.info(True)
        st = ctx.config.settings.team
        patch = {k: v for k, v in {"port": body.port, "defaultRole": body.defaultRole, "https": body.https}.items() if v is not None}
        if patch:
            ctx.config.update({"team": patch})
            st = ctx.config.settings.team
        team = TeamSession(st.port, st.defaultRole, st.guestMaxUploadMB, st.https, body.password)
        try:
            await team.start(ctx.app, ctx.paths.tls_dir)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        # Snapshot every chapter so a session can always be rolled back.
        p = project()
        await asyncio.to_thread(lambda: [p.create_snapshot(c.id, "Teamwork session started", auto=True) for c in p.meta.chapters()])
        ctx.team = team
        ctx.hub.publish({"type": "team-changed"})
        return team.info(True)

    @r.post("/api/team/stop")
    async def team_stop(request: Request):
        auth(request, "owner")
        await ctx.stop_team()
        return {"ok": True}

    @r.patch("/api/team")
    async def team_patch(request: Request, body: TeamPatch):
        auth(request, "owner")
        team = ctx.team
        if team is None:
            raise HTTPException(409, "Teamwork is not running")
        if body.locked is not None:
            team.locked = body.locked
        if body.defaultRole:
            team.default_role = body.defaultRole
            ctx.config.update({"team": {"defaultRole": body.defaultRole}})
        if body.password or body.regeneratePassword:
            from ..collab.team import generate_password

            team.set_password(body.password or generate_password())
            for sess in ctx.sessions.drop_guests():
                await ctx.hub.kick(sess.sid)
                await ctx.rooms.kick(sess.sid)
        ctx.hub.publish({"type": "team-changed"})
        return team.info(True)

    @r.post("/api/team/join")
    def team_join(request: Request, body: Join):
        team = ctx.team
        ip = client_ip(request)
        if team is None:
            raise HTTPException(409, "No teamwork session is running")
        if not join_limiter.check(ip) or not join_global.check("all"):
            raise HTTPException(429, "Too many attempts. Wait a minute and try again.")
        if team.locked:
            raise HTTPException(423, "The host has locked this session")
        if not team.verify(body.room, body.password):
            time.sleep(0.4)
            raise HTTPException(403, "Wrong room code or password")
        join_limiter.reset(ip)
        if len(ctx.sessions.guests()) >= 50:
            raise HTTPException(429, "This session is full")
        name = re.sub(r"[\x00-\x1f<>]", "", body.name).strip()[:40] or "Guest"
        sess, cookie = ctx.sessions.create(team.default_role, name, ip, epoch=team.epoch, ttl=GUEST_TTL)
        resp = JSONResponse({"ok": True, "role": sess.role})
        resp.set_cookie(COOKIE, cookie, httponly=True, samesite="strict", secure=team.https, max_age=GUEST_TTL, path="/")
        ctx.publish({"type": "members-changed"})
        ctx.publish({"type": "toast", "message": f"{name} joined"})
        return resp

    def _find_guest(member_id: str):
        for sess in ctx.sessions.guests():
            if secrets.compare_digest(sess.pub_id, member_id):
                return sess
        raise HTTPException(404, "Member not found")

    @r.post("/api/team/members/{mid}/kick")
    async def team_kick(mid: str, request: Request):
        auth(request, "owner")
        sess = _find_guest(mid)
        ctx.sessions.remove(sess.sid)
        await ctx.hub.kick(sess.sid)
        await ctx.rooms.kick(sess.sid)
        ctx.hub.publish({"type": "members-changed"})
        return {"ok": True}

    @r.patch("/api/team/members/{mid}")
    async def team_role(mid: str, request: Request, body: RoleChange):
        auth(request, "owner")
        sess = _find_guest(mid)
        sess.role = body.role
        for c in ctx.hub.clients:
            if c.session.sid == sess.sid:
                c.send({"type": "role-changed", "role": body.role})
        ctx.hub.publish({"type": "members-changed"})
        return sess.public()

    @r.get("/api/chat")
    def chat_history(request: Request):
        auth(request)
        return ctx.team.chat if ctx.team else []

    @r.post("/api/chat")
    async def chat(request: Request, body: Chat):
        s = auth(request)
        if ctx.team is None:
            raise HTTPException(409, "Chat is available during Teamwork sessions")
        msg = {"from": s.name, "color": s.color, "text": body.text.strip()[:2000], "at": time.time()}
        ctx.team.chat = (ctx.team.chat + [msg])[-200:]
        ctx.hub.publish({"type": "chat", "message": msg})
        return msg

    # ------------------------------------------------------------------ voice
    @r.get("/api/voice/status")
    def voice_status(request: Request):
        auth(request, "owner")
        from ..voice.engine import availability

        return availability() | {"running": bool(ctx.voice and ctx.voice.running)}

    return r


def _font_meta(path: Path) -> Optional[str]:
    try:
        from fontTools.ttLib import TTFont

        with TTFont(path, lazy=True, fontNumber=0) as f:
            name = f["name"]
            fam = name.getDebugName(16) or name.getDebugName(1)
            if fam and re.fullmatch(r"[A-Za-z0-9 \-']{1,80}", fam):
                return fam
            return re.sub(r"[^A-Za-z0-9 \-']", "", fam or "")[:80] or None
    except Exception:
        return None


_system_cache: Optional[list[str]] = None


def _system_fonts() -> list[str]:
    global _system_cache
    if _system_cache is not None:
        return _system_cache
    dirs = []
    if os.name == "nt":
        dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    else:
        dirs = [Path("/usr/share/fonts"), Path("/Library/Fonts"), Path.home() / ".fonts", Path.home() / "Library" / "Fonts"]
    fams: set[str] = set()
    for d in dirs:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.suffix.lower() in (".ttf", ".otf") and f.is_file():
                fam = _font_meta(f)
                if fam:
                    fams.add(fam)
    _system_cache = sorted(fams)
    return _system_cache


_ = (ROLES, Any)
