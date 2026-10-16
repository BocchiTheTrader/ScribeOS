"""Rotating zip backups of a project folder."""

from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from .project_store import Project, safe_folder_name

_NAME_RE = re.compile(r"^backup-(\d{8})-(\d{6})\.zip$")
SKIP = {"project.lock"}


def zip_project(src: Path, dest_zip: Path) -> Path:
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_zip.with_name(dest_zip.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f in SKIP or f.startswith(".") or f.endswith((".tmp", ".part")):
                    continue
                full = Path(root) / f
                z.write(full, Path(src.name) / full.relative_to(src))
    os.replace(tmp, dest_zip)
    return dest_zip


def backup_folder(backup_root: Path, project: Project) -> Path:
    return Path(backup_root) / f"{safe_folder_name(project.meta.name)}-{project.meta.id[-8:]}"


def make_backup(backup_root: Path, project: Project, keep: int = 20) -> Path:
    folder = backup_folder(backup_root, project)
    name = datetime.now().strftime("backup-%Y%m%d-%H%M%S.zip")
    out = zip_project(project.path, folder / name)
    rotate(folder, keep)
    project.dirty = False
    return out


def rotate(folder: Path, keep: int, daily_days: int = 30, now: datetime | None = None) -> list[Path]:
    """Keep the newest ``keep`` backups plus the newest backup of each of the last ``daily_days`` days."""
    now = now or datetime.now()
    entries = []
    for p in folder.glob("backup-*.zip"):
        m = _NAME_RE.match(p.name)
        if m:
            entries.append((datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"), p))
    entries.sort(reverse=True)
    keep_set = {p for _, p in entries[:keep]}
    seen_days = set()
    for ts, p in entries:
        day = ts.date()
        if now - ts <= timedelta(days=daily_days) and day not in seen_days:
            seen_days.add(day)
            keep_set.add(p)
    removed = []
    for _, p in entries:
        if p not in keep_set:
            p.unlink()
            removed.append(p)
    return removed


def list_backups(backup_root: Path, project: Project) -> list[dict]:
    folder = backup_folder(backup_root, project)
    out = []
    for p in sorted(folder.glob("backup-*.zip"), reverse=True):
        out.append({"name": p.name, "size": p.stat().st_size, "path": str(p)})
    return out


def safe_extract(zip_path: Path, dest_parent: Path) -> Path:
    """Extract a project archive, refusing path traversal and absurd sizes (zip bombs)."""
    dest_parent = Path(dest_parent).resolve()
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        total = sum(i.file_size for i in z.infolist())
        if total > 20 * 1024**3 or len(names) > 200_000:
            raise ValueError("Archive is too large")
        tops = {n.replace("\\", "/").split("/", 1)[0] for n in names if n.strip("/")}
        if len(tops) != 1:
            raise ValueError("Archive must contain exactly one project folder")
        top = safe_folder_name(tops.pop().removesuffix(".scribe")) + ".scribe"
        target = dest_parent / top
        i = 2
        while target.exists():
            target = dest_parent / f"{top.removesuffix('.scribe')} ({i}).scribe"
            i += 1
        for info in z.infolist():
            parts = info.filename.replace("\\", "/").split("/")[1:]
            if not parts or not parts[-1] or any(p in ("..", "") or ":" in p for p in parts):
                continue
            out = (target / Path(*parts)).resolve()
            if not str(out).startswith(str(target.resolve()) + os.sep):
                raise ValueError("Unsafe path in archive")
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
    return target
