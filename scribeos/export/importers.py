"""Import DOCX, Markdown, HTML and plain text into chapters."""

from __future__ import annotations

import base64
import binascii
import io
import re

from ..storage import pmdoc
from ..storage.assets import AssetError
from ..storage.project_store import Project

MAX_IMPORT_BYTES = 200 * 1024 * 1024
CHAPTER_LINE = re.compile(r"^\s*(chapter|bölüm|kapitel|chapitre|cap[ií]tulo)\s+[\w\-]+.*$", re.I)


class ImportError_(ValueError):
    pass


def _data_uri_resolver(project: Project):
    def resolve(src: str) -> str | None:
        m = re.match(r"^data:image/(png|jpeg|gif|webp);base64,(.+)$", src or "", re.S)
        if not m:
            return src if pmdoc.ASSET_SRC_RE.match(src or "") else None
        try:
            return project.assets.save_bytes(base64.b64decode(m.group(2), validate=False), allowed_kinds=("image",))["src"]
        except (binascii.Error, AssetError):
            return None

    return resolve


def split_doc_on_h1(doc: dict, fallback_title: str) -> list[tuple[str, dict]]:
    """Split a document into chapters at level-1 headings."""
    chapters: list[tuple[str, list]] = []
    title, buf = fallback_title, []
    for n in doc.get("content") or []:
        if n.get("type") == "heading" and (n.get("attrs") or {}).get("level") == 1:
            if any(b.get("content") or b.get("type") != "paragraph" for b in buf):
                chapters.append((title, buf))
            title = pmdoc.inline_text(n.get("content") or []).strip() or "Untitled"
            buf = []
        else:
            buf.append(n)
    if buf or not chapters:
        chapters.append((title, buf))
    return [(t[:300], pmdoc.sanitize_doc({"type": "doc", "content": c})) for t, c in chapters]


def html_chapters(project: Project, markup: str, title: str) -> list[tuple[str, dict]]:
    doc = pmdoc.html_to_doc(markup, image_resolver=_data_uri_resolver(project))
    return split_doc_on_h1(doc, title)


def import_file(project: Project, filename: str, data: bytes) -> list[tuple[str, dict]]:
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportError_("File too large to import")
    name = filename.lower()
    stem = re.sub(r"\.[^.]+$", "", filename)[:300] or "Imported"
    if name.endswith(".docx"):
        import mammoth

        try:
            result = mammoth.convert_to_html(io.BytesIO(data))
        except Exception as e:
            raise ImportError_(f"Could not read DOCX: {e}") from e
        return html_chapters(project, result.value, stem)
    text = _decode(data)
    if name.endswith((".md", ".markdown")):
        from markdown_it import MarkdownIt

        md = MarkdownIt("commonmark", {"html": False}).enable("strikethrough")
        return html_chapters(project, md.render(text), stem)
    if name.endswith((".html", ".htm")):
        return html_chapters(project, text, stem)
    if name.endswith(".txt"):
        return txt_chapters(text, stem)
    raise ImportError_("Supported formats: .docx, .md, .html, .txt")


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace")


def txt_chapters(text: str, title: str) -> list[tuple[str, dict]]:
    lines = text.replace("\r\n", "\n").split("\n")
    chapters: list[tuple[str, list[str]]] = []
    cur_title, buf = title, []
    for line in lines:
        if CHAPTER_LINE.match(line) and len(line) < 120:
            if "".join(buf).strip():
                chapters.append((cur_title, buf))
            cur_title, buf = line.strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip() or not chapters:
        chapters.append((cur_title, buf))
    return [(t[:300], pmdoc.text_to_doc("\n".join(b))) for t, b in chapters]
