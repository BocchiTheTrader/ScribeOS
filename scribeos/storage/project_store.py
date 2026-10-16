"""Load and save ``.scribe`` project folders.

Layout::

    MyNovel.scribe/
      project.json        metadata + chapter tree
      chapters/<id>.json  one ProseMirror document per chapter
      notes.json          scratchpad sticky notes
      codex.json          characters, places, items...
      assets/             content-addressed media
      snapshots/<id>/     chapter versions
      trash/              deleted chapters (restorable)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any, Optional

from pydantic import TypeAdapter, ValidationError

from . import pmdoc
from .assets import AssetStore
from .fileio import atomic_write_json, read_json
from .models import (
    ID_RE,
    ChapterNode,
    CodexEntry,
    Note,
    PartNode,
    ProjectMeta,
    TreeNode,
    new_id,
    now_iso,
)

LOCK_STALE_SECONDS = 120
MAX_SNAPSHOTS = 100
_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

_tree_adapter = TypeAdapter(list[TreeNode])
_notes_adapter = TypeAdapter(list[Note])
_codex_adapter = TypeAdapter(list[CodexEntry])


class ProjectError(Exception):
    pass


class ProjectLocked(ProjectError):
    pass


def safe_folder_name(name: str) -> str:
    n = _INVALID_FS.sub("_", name).strip().rstrip(". ")
    if not n or n.upper() in _RESERVED:
        n = "Untitled"
    return n[:80]


def is_project_dir(path: Path) -> bool:
    return (path / "project.json").is_file()


def _check_id(cid: str) -> str:
    if not isinstance(cid, str) or not ID_RE.match(cid):
        raise ProjectError("invalid id")
    return cid


class Project:
    def __init__(self, path: Path, meta: ProjectMeta):
        self.path = path
        self.meta = meta
        self.assets = AssetStore(path / "assets")
        self._lock = threading.RLock()
        self.dirty = False
        self.last_change = 0.0
        self._last_snapshot: dict[str, float] = {}
        self._lock_token = new_id()

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, parent: Path, name: str, author: str = "", first_chapter: str = "Chapter 1") -> "Project":
        parent = Path(parent)
        parent.mkdir(parents=True, exist_ok=True)
        base = safe_folder_name(name)
        path = parent / f"{base}.scribe"
        i = 2
        while path.exists():
            path = parent / f"{base} ({i}).scribe"
            i += 1
        path.mkdir()
        for sub in ("chapters", "assets", "snapshots", "trash"):
            (path / sub).mkdir()
        cid = new_id("c")
        meta = ProjectMeta(id=new_id("p"), name=name.strip()[:200] or "Untitled", author=author, tree=[ChapterNode(id=cid, title=first_chapter)], activeChapterId=cid)
        p = cls(path, meta)
        p._write_meta()
        p._write_chapter(cid, pmdoc.empty_doc())
        atomic_write_json(path / "notes.json", [])
        atomic_write_json(path / "codex.json", [])
        return p

    @classmethod
    def open(cls, path: Path, force: bool = False) -> "Project":
        path = Path(path)
        if path.suffix.lower() == ".json" and path.name == "project.json":
            path = path.parent
        if not is_project_dir(path):
            raise ProjectError("Not a ScribeOS project folder (project.json missing)")
        raw = read_json(path / "project.json")
        try:
            meta = ProjectMeta.model_validate(raw)
        except ValidationError as e:
            raise ProjectError(f"project.json is invalid: {e.errors()[0].get('msg')}") from e
        if meta.schemaVersion > 2:
            raise ProjectError("This project was made by a newer ScribeOS version")
        p = cls(path, meta)
        p.acquire_lock(force)
        for sub in ("chapters", "assets", "snapshots", "trash"):
            (path / sub).mkdir(exist_ok=True)
        # Make sure every chapter in the tree has a file.
        for c in meta.chapters():
            if not (path / "chapters" / f"{c.id}.json").exists():
                p._write_chapter(c.id, pmdoc.empty_doc())
        return p

    def acquire_lock(self, force: bool = False) -> None:
        lock = self.path / "project.lock"
        info = read_json(lock, None) if lock.exists() else None
        if info and not force:
            age = time.time() - float(info.get("heartbeat", 0))
            if age < LOCK_STALE_SECONDS and info.get("token") != self._lock_token:
                raise ProjectLocked(f"Project appears to be open on {info.get('host', 'another computer')} (pid {info.get('pid')})")
        self.heartbeat()

    def heartbeat(self) -> None:
        try:
            atomic_write_json(self.path / "project.lock", {"pid": os.getpid(), "host": socket.gethostname(), "heartbeat": time.time(), "token": self._lock_token})
        except OSError:
            pass

    def close(self) -> None:
        lock = self.path / "project.lock"
        info = read_json(lock, None) if lock.exists() else None
        if info and info.get("token") == self._lock_token:
            try:
                lock.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ meta
    def _write_meta(self) -> None:
        self.meta.updatedAt = now_iso()
        atomic_write_json(self.path / "project.json", self.meta.model_dump())
        self._touch()

    def _touch(self) -> None:
        self.dirty = True
        self.last_change = time.time()

    def summary(self) -> dict:
        return {"path": str(self.path), **self.meta.model_dump()}

    def update_meta(self, patch: dict) -> ProjectMeta:
        allowed = {"name", "author", "theme", "mode", "targets", "activeChapterId"}
        with self._lock:
            data = self.meta.model_dump()
            for k, v in patch.items():
                if k in allowed:
                    data[k] = v
            if data.get("activeChapterId") and not self.meta.find_chapter(data["activeChapterId"]):
                data["activeChapterId"] = self.meta.activeChapterId
            self.meta = ProjectMeta.model_validate(data)
            self._write_meta()
            return self.meta

    # ------------------------------------------------------------------ tree
    def set_tree(self, tree: Any) -> ProjectMeta:
        """Replace the tree order/structure. The set of chapter ids must stay the same."""
        with self._lock:
            new_tree = _tree_adapter.validate_python(tree)
            current = {c.id: c for c in self.meta.chapters()}
            seen: set[str] = set()
            fixed: list = []
            for node in new_tree:
                if isinstance(node, PartNode):
                    kids = []
                    for ch in node.children:
                        if ch.id not in current or ch.id in seen:
                            raise ProjectError("tree contains unknown or duplicate chapter")
                        seen.add(ch.id)
                        kids.append(current[ch.id].model_copy(update={"title": ch.title, "status": ch.status, "synopsis": ch.synopsis, "label": ch.label, "target": ch.target}))
                    fixed.append(node.model_copy(update={"children": kids}))
                else:
                    if node.id not in current or node.id in seen:
                        raise ProjectError("tree contains unknown or duplicate chapter")
                    seen.add(node.id)
                    fixed.append(current[node.id].model_copy(update={"title": node.title, "status": node.status, "synopsis": node.synopsis, "label": node.label, "target": node.target}))
            if seen != set(current):
                raise ProjectError("tree must contain every chapter exactly once")
            self.meta.tree = fixed
            self._write_meta()
            return self.meta

    def update_node(self, nid: str, patch: dict) -> None:
        with self._lock:
            for n in self.meta.tree:
                targets = [n] + (list(n.children) if isinstance(n, PartNode) else [])
                for t in targets:
                    if t.id == nid:
                        data = t.model_dump()
                        for k in ("title", "status", "synopsis", "label", "target", "collapsed"):
                            if k in patch and k in data:
                                data[k] = patch[k]
                        new = type(t).model_validate(data)
                        for k in data:
                            setattr(t, k, getattr(new, k))
                        self._write_meta()
                        return
            raise ProjectError("node not found")

    def add_chapter(self, title: str = "New Chapter", part_id: Optional[str] = None, doc: Optional[dict] = None, after: Optional[str] = None) -> ChapterNode:
        with self._lock:
            cid = new_id("c")
            clean = pmdoc.sanitize_doc(doc) if doc else pmdoc.empty_doc()
            node = ChapterNode(id=cid, title=title[:300] or "Untitled", wordCount=pmdoc.doc_word_count(clean))
            self._write_chapter(cid, clean)
            container = self.meta.tree
            if part_id:
                part = next((n for n in self.meta.tree if isinstance(n, PartNode) and n.id == part_id), None)
                if part is None:
                    raise ProjectError("part not found")
                container = part.children
            idx = next((i + 1 for i, n in enumerate(container) if n.id == after), len(container))
            container.insert(idx, node)
            self.meta.activeChapterId = cid
            self._write_meta()
            return node

    def add_part(self, title: str = "New Part") -> PartNode:
        with self._lock:
            part = PartNode(id=new_id("part"), title=title[:300] or "Part")
            self.meta.tree.append(part)
            self._write_meta()
            return part

    def delete_node(self, nid: str) -> None:
        """Chapters go to trash/; parts are dissolved (their chapters move to the top level)."""
        with self._lock:
            for i, n in enumerate(self.meta.tree):
                if n.id == nid:
                    if isinstance(n, PartNode):
                        self.meta.tree[i : i + 1] = list(n.children)
                    else:
                        if len(self.meta.chapters()) <= 1:
                            raise ProjectError("A project needs at least one chapter")
                        self._trash(n)
                        del self.meta.tree[i]
                    break
                if isinstance(n, PartNode):
                    for j, ch in enumerate(n.children):
                        if ch.id == nid:
                            if len(self.meta.chapters()) <= 1:
                                raise ProjectError("A project needs at least one chapter")
                            self._trash(ch)
                            del n.children[j]
                            break
                    else:
                        continue
                    break
            else:
                raise ProjectError("node not found")
            if self.meta.activeChapterId == nid or not self.meta.find_chapter(self.meta.activeChapterId or ""):
                chs = self.meta.chapters()
                self.meta.activeChapterId = chs[0].id if chs else None
            self._write_meta()

    def _trash(self, node: ChapterNode) -> None:
        src = self.path / "chapters" / f"{node.id}.json"
        data = read_json(src, {}) or {}
        data["node"] = node.model_dump()
        data["deletedAt"] = now_iso()
        atomic_write_json(self.path / "trash" / f"{node.id}.json", data)
        if src.exists():
            src.unlink()

    def list_trash(self) -> list[dict]:
        out = []
        for p in sorted((self.path / "trash").glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            d = read_json(p, {}) or {}
            node = d.get("node") or {}
            out.append({"id": p.stem, "title": node.get("title", "Untitled"), "deletedAt": d.get("deletedAt"), "wordCount": node.get("wordCount", 0)})
        return out

    def restore_trash(self, cid: str) -> ChapterNode:
        _check_id(cid)
        with self._lock:
            p = self.path / "trash" / f"{cid}.json"
            d = read_json(p, None)
            if not d:
                raise ProjectError("not in trash")
            node = ChapterNode.model_validate(d.get("node") or {"id": cid})
            self._write_chapter(cid, pmdoc.sanitize_doc(d.get("doc")))
            self.meta.tree.append(node)
            p.unlink()
            self._write_meta()
            return node

    # ------------------------------------------------------------------ chapters
    def _chapter_file(self, cid: str) -> Path:
        return self.path / "chapters" / f"{_check_id(cid)}.json"

    def _write_chapter(self, cid: str, doc: dict) -> None:
        atomic_write_json(self._chapter_file(cid), {"id": cid, "updatedAt": now_iso(), "doc": doc})
        self._touch()

    def get_chapter(self, cid: str) -> dict:
        node = self.meta.find_chapter(cid)
        if node is None:
            raise ProjectError("chapter not found")
        data = read_json(self._chapter_file(cid), None) or {"doc": pmdoc.empty_doc(), "updatedAt": None}
        return {"id": cid, "title": node.title, "doc": pmdoc.sanitize_doc(data.get("doc")), "updatedAt": data.get("updatedAt"), "wordCount": node.wordCount}

    def save_chapter(self, cid: str, doc: Any, snapshot_interval: float = 1800) -> dict:
        """Persist a chapter. Returns word statistics so callers can track daily progress."""
        with self._lock:
            node = self.meta.find_chapter(cid)
            if node is None:
                raise ProjectError("chapter not found")
            clean = pmdoc.sanitize_doc(doc)
            before = node.wordCount
            old = read_json(self._chapter_file(cid), None)
            if old and old.get("doc") == clean:
                return {"wordCount": before, "delta": 0, "updatedAt": old.get("updatedAt")}
            # Periodic automatic snapshot of the *previous* version.
            last = self._last_snapshot.get(cid) or self._latest_snapshot_time(cid)
            if old and time.time() - last > snapshot_interval and pmdoc.doc_word_count(old.get("doc") or {}) > 0:
                self.create_snapshot(cid, label="Auto", auto=True, doc=old.get("doc"))
            self._write_chapter(cid, clean)
            node.wordCount = pmdoc.doc_word_count(clean)
            self._write_meta()
            return {"wordCount": node.wordCount, "delta": node.wordCount - before, "updatedAt": now_iso()}

    def all_chapter_docs(self) -> list[tuple[ChapterNode, dict]]:
        return [(c, self.get_chapter(c.id)["doc"]) for c in self.meta.chapters()]

    # ------------------------------------------------------------------ snapshots
    def _snap_dir(self, cid: str) -> Path:
        return self.path / "snapshots" / _check_id(cid)

    def _latest_snapshot_time(self, cid: str) -> float:
        d = self._snap_dir(cid)
        if not d.exists():
            return 0.0
        times = [p.stat().st_mtime for p in d.glob("*.json")]
        t = max(times) if times else 0.0
        self._last_snapshot[cid] = t
        return t

    def create_snapshot(self, cid: str, label: str = "", auto: bool = False, doc: Optional[dict] = None) -> dict:
        with self._lock:
            node = self.meta.find_chapter(cid)
            if node is None:
                raise ProjectError("chapter not found")
            doc = pmdoc.sanitize_doc(doc) if doc is not None else self.get_chapter(cid)["doc"]
            sid = f"{int(time.time() * 1000):013d}"
            d = self._snap_dir(cid)
            d.mkdir(parents=True, exist_ok=True)
            while (d / f"{sid}.json").exists():
                sid = str(int(sid) + 1).zfill(13)
            entry = {"id": sid, "createdAt": now_iso(), "label": label[:120], "auto": auto, "title": node.title, "wordCount": pmdoc.doc_word_count(doc), "doc": doc}
            atomic_write_json(d / f"{sid}.json", entry)
            self._last_snapshot[cid] = time.time()
            self._prune_snapshots(d)
            return {k: v for k, v in entry.items() if k != "doc"}

    def _prune_snapshots(self, d: Path) -> None:
        files = sorted(d.glob("*.json"))
        excess = len(files) - MAX_SNAPSHOTS
        if excess <= 0:
            return
        autos = [f for f in files if (read_json(f, {}) or {}).get("auto")]
        for f in (autos + [f for f in files if f not in autos])[:excess]:
            f.unlink()

    def list_snapshots(self, cid: str) -> list[dict]:
        d = self._snap_dir(cid)
        if not d.exists():
            return []
        out = []
        for p in sorted(d.glob("*.json"), reverse=True):
            e = read_json(p, {}) or {}
            out.append({k: e.get(k) for k in ("id", "createdAt", "label", "auto", "title", "wordCount")})
        return out

    def get_snapshot(self, cid: str, sid: str) -> dict:
        if not re.fullmatch(r"\d{13}", sid or ""):
            raise ProjectError("invalid snapshot id")
        e = read_json(self._snap_dir(cid) / f"{sid}.json", None)
        if not e:
            raise ProjectError("snapshot not found")
        e["doc"] = pmdoc.sanitize_doc(e.get("doc"))
        return e

    def restore_snapshot(self, cid: str, sid: str) -> dict:
        snap = self.get_snapshot(cid, sid)
        self.create_snapshot(cid, label="Before restore", auto=True)
        return self.save_chapter(cid, snap["doc"], snapshot_interval=float("inf"))

    def delete_snapshot(self, cid: str, sid: str) -> None:
        if not re.fullmatch(r"\d{13}", sid or ""):
            raise ProjectError("invalid snapshot id")
        p = self._snap_dir(cid) / f"{sid}.json"
        if p.exists():
            p.unlink()

    # ------------------------------------------------------------------ notes / codex
    def get_notes(self) -> list[dict]:
        raw = read_json(self.path / "notes.json", []) or []
        try:
            return [n.model_dump() for n in _notes_adapter.validate_python(raw)]
        except ValidationError:
            return []

    def save_notes(self, notes: Any) -> list[dict]:
        with self._lock:
            valid = _notes_adapter.validate_python(notes)
            if len(valid) > 500:
                raise ProjectError("too many notes")
            data = [n.model_dump() for n in valid]
            atomic_write_json(self.path / "notes.json", data)
            self._touch()
            return data

    def get_codex(self) -> list[dict]:
        raw = read_json(self.path / "codex.json", []) or []
        try:
            return [n.model_dump() for n in _codex_adapter.validate_python(raw)]
        except ValidationError:
            return []

    def save_codex(self, entries: Any) -> list[dict]:
        with self._lock:
            valid = _codex_adapter.validate_python(entries)
            if len(valid) > 5000:
                raise ProjectError("too many codex entries")
            ids = [e.id for e in valid]
            if len(ids) != len(set(ids)):
                raise ProjectError("duplicate codex id")
            data = [e.model_dump() for e in valid]
            atomic_write_json(self.path / "codex.json", data)
            self._touch()
            return data

    # ------------------------------------------------------------------ search
    def search(self, query: str, regex: bool = False, case: bool = False, whole: bool = False, limit: int = 500) -> list[dict]:
        pattern = compile_search(query, regex, case, whole)
        results = []
        for node, doc in self.all_chapter_docs():
            for bi, text in enumerate(pmdoc.iter_blocks_text(doc)):
                for m in pattern.finditer(text):
                    if m.end() == m.start():
                        continue
                    s, e = max(0, m.start() - 40), min(len(text), m.end() + 40)
                    results.append({"chapterId": node.id, "chapterTitle": node.title, "block": bi, "before": text[s : m.start()], "match": m.group(0), "after": text[m.end() : e]})
                    if len(results) >= limit:
                        return results
        return results

    def replace_all(self, query: str, replacement: str, regex: bool = False, case: bool = False, whole: bool = False, chapter_ids: Optional[list[str]] = None) -> dict:
        pattern = compile_search(query, regex, case, whole)
        changed_chapters = []
        total = 0
        with self._lock:
            for node, doc in self.all_chapter_docs():
                if chapter_ids and node.id not in chapter_ids:
                    continue
                count = 0

                def sub(text: str) -> str:
                    nonlocal count
                    new, n = pattern.subn(replacement if regex else replacement.replace("\\", "\\\\"), text)
                    count += n
                    return new

                new_doc, changed = pmdoc.map_text_nodes(doc, sub)
                if changed:
                    self.create_snapshot(node.id, label="Before replace", auto=True)
                    self.save_chapter(node.id, new_doc, snapshot_interval=float("inf"))
                    changed_chapters.append(node.id)
                    total += count
        return {"replaced": total, "chapters": changed_chapters}

    # ------------------------------------------------------------------ copy / move
    def copy_to(self, dest_parent: Path, new_name: Optional[str] = None) -> Path:
        name = safe_folder_name(new_name or self.meta.name)
        dest = Path(dest_parent) / f"{name}.scribe"
        i = 2
        while dest.exists():
            dest = Path(dest_parent) / f"{name} ({i}).scribe"
            i += 1
        shutil.copytree(self.path, dest, ignore=shutil.ignore_patterns("project.lock", ".*.tmp", ".upload-*"))
        if new_name:
            meta = read_json(dest / "project.json")
            meta["name"] = new_name[:200]
            atomic_write_json(dest / "project.json", meta)
        return dest


def compile_search(query: str, regex: bool, case: bool, whole: bool) -> "re.Pattern[str]":
    if not query or len(query) > 1000:
        raise ProjectError("empty or too long search")
    body = query if regex else re.escape(query)
    if whole:
        body = rf"\b(?:{body})\b"
    try:
        return re.compile(body, 0 if case else re.IGNORECASE)
    except re.error as e:
        raise ProjectError(f"invalid regular expression: {e}") from e


def load_json_str(s: str) -> Any:
    return json.loads(s)
