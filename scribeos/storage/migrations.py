"""Import data from ScribeOS 1.x (the browser version's JSON backup / localStorage dump)."""

from __future__ import annotations

import base64
import binascii
import html
import re
from pathlib import Path
from typing import Any

from . import pmdoc
from .assets import AssetError
from .models import Note, new_id
from .project_store import Project

DATA_URI_RE = re.compile(r"^data:(image/(png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)$")
V1_FONTS = {"Lora, serif": "Lora", "Merriweather, serif": "Merriweather", "Inter, sans-serif": "Inter", "JetBrains Mono, monospace": "JetBrains Mono"}


class MigrationError(ValueError):
    pass


def is_v1_backup(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("projects"), list) and isinstance(data.get("settings"), dict) and "schemaVersion" not in data


def _html_to_text(markup: str) -> str:
    text = re.sub(r"<br\s*/?>|</(p|div|li|h\d)>", "\n", markup or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def import_v1(data: Any, dest_parent: Path, author: str = "") -> tuple[list[Path], dict]:
    """Create one ``.scribe`` project per v1 project. Returns (paths, settings_patch)."""
    if not is_v1_backup(data):
        raise MigrationError("This is not a ScribeOS 1.x backup file")
    if len(data["projects"]) > 500:
        raise MigrationError("Backup contains too many projects")
    created: list[Path] = []
    for proj in data["projects"]:
        if not isinstance(proj, dict):
            continue
        name = str(proj.get("name") or "Imported Project")[:200]
        p = Project.create(Path(dest_parent), name, author=author)
        first_id = p.meta.tree[0].id

        def resolve(src: str, project: Project = p) -> str | None:
            m = DATA_URI_RE.match(src or "")
            if not m:
                return None
            try:
                raw = base64.b64decode(re.sub(r"\s", "", m.group(3)), validate=True)
                return project.assets.save_bytes(raw, allowed_kinds=("image",))["src"]
            except (binascii.Error, AssetError):
                return None

        chapters = [c for c in (proj.get("chapters") or []) if isinstance(c, dict)]
        active_old = proj.get("activeChapterId")
        active_new = None
        for ch in chapters:
            doc = pmdoc.html_to_doc(str(ch.get("content") or ""), image_resolver=resolve)
            node = p.add_chapter(title=str(ch.get("title") or "Untitled")[:300], doc=doc)
            if ch.get("id") == active_old:
                active_new = node.id
        if chapters:
            p.delete_node(first_id)
        notes = []
        for n in proj.get("notes") or []:
            if not isinstance(n, dict):
                continue
            color = n.get("color") if n.get("color") in ("yellow", "cyan", "purple", "green") else "yellow"
            try:
                x, y = int(float(n.get("x", 200))), int(float(n.get("y", 200)))
            except (TypeError, ValueError):
                x, y = 200, 200
            notes.append(Note(id=new_id("n"), text=_html_to_text(str(n.get("text", "")))[:20000], color=color, x=max(0, min(x, 5000)), y=max(0, min(y, 5000))).model_dump())
        p.save_notes(notes)
        if active_new:
            p.update_meta({"activeChapterId": active_new})
        p.close()
        created.append(p.path)

    s = data.get("settings") or {}
    patch: dict = {"editor": {}}
    if s.get("fontFamily") in V1_FONTS:
        patch["editor"]["bodyFont"] = V1_FONTS[s["fontFamily"]]
    for key, (lo, hi, cast) in {"fontSize": (12, 36, int), "lineHeight": (1.0, 3.0, float), "contentWidth": (480, 1600, int), "dailyGoal": (0, 100000, int)}.items():
        try:
            v = cast(float(s[key]))
            if lo <= v <= hi:
                patch["editor"][key] = v
        except (KeyError, TypeError, ValueError):
            pass
    if isinstance(s.get("typewriter"), bool):
        patch["editor"]["typewriter"] = s["typewriter"]
    return created, patch
