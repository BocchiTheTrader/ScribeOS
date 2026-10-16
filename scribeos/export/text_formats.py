"""Markdown, HTML and plain-text exporters."""

from __future__ import annotations

import base64
import html
import io
import re
import zipfile

from ..storage import pmdoc
from ..storage.assets import MIME
from ..storage.project_store import Project
from .common import Chapter, ExportOptions, asset_path

# --------------------------------------------------------------------------- Markdown

_MD_ESCAPE = re.compile(r"([\\`*_{}\[\]<>#|~])")


def _md_escape(text: str) -> str:
    text = _MD_ESCAPE.sub(r"\\\1", text)
    # Characters that only matter at the start of a line.
    return re.sub(r"(?m)^(\s*)([+\-]|\d+\.)(\s)", r"\1\\\2\3", text)


def _md_inline(nodes: list) -> str:
    out = []
    for n in nodes or []:
        if n.get("type") == "hardBreak":
            out.append("  \n")
            continue
        text = n.get("text", "")
        marks = {m["type"]: m.get("attrs") or {} for m in n.get("marks") or []}
        if "code" in marks:
            ticks = "``" if "`" in text else "`"
            s = f"{ticks}{text}{ticks}"
        else:
            s = _md_escape(text)
            lead = len(s) - len(s.lstrip())
            trail = len(s) - len(s.rstrip())
            core = s.strip()
            if core:
                if "bold" in marks:
                    core = f"**{core}**"
                if "italic" in marks:
                    core = f"*{core}*"
                if "strike" in marks:
                    core = f"~~{core}~~"
                if "highlight" in marks:
                    core = f"=={core}=="
            s = s[:lead] + core + (s[len(s) - trail :] if trail else "")
        if "link" in marks:
            href = marks["link"].get("href", "").replace(")", "%29").replace(" ", "%20")
            s = f"[{s}]({href})"
        out.append(s)
    return "".join(out)


def doc_to_markdown(doc: dict, image_map: dict[str, str], scene_break: str = "* * *") -> str:
    lines: list[str] = []

    def block(n: dict, prefix: str = "") -> None:
        t = n.get("type")
        a = n.get("attrs") or {}
        if t == "paragraph":
            lines.append(prefix + _md_inline(n.get("content")))
        elif t == "heading":
            lines.append(prefix + "#" * (int(a.get("level", 2)) + 1) + " " + _md_inline(n.get("content")))
        elif t == "blockquote":
            for c in n.get("content") or []:
                block(c, prefix + "> ")
                lines.append(prefix + ">")
            if lines and lines[-1] == prefix + ">":
                lines.pop()
        elif t in ("bulletList", "orderedList"):
            start = a.get("start", 1)
            for i, li in enumerate(n.get("content") or []):
                marker = "- " if t == "bulletList" else f"{start + i}. "
                kids = li.get("content") or []
                for j, c in enumerate(kids):
                    before = len(lines)
                    block(c, prefix + ("  " * (len(marker) // 2) if j else ""))
                    if j == 0 and len(lines) > before:
                        lines[before] = prefix + marker + lines[before][len(prefix) :]
        elif t == "horizontalRule":
            lines.append(prefix + "\\* \\* \\*" if scene_break == "* * *" else prefix + _md_escape(scene_break))
        elif t == "codeBlock":
            code = pmdoc.inline_text(n.get("content") or [])
            fence = "````" if "```" in code else "```"
            lines.append(prefix + fence)
            lines.extend(prefix + ln for ln in code.split("\n"))
            lines.append(prefix + fence)
        elif t == "image":
            target = image_map.get(a.get("src", ""))
            if target:
                alt = (a.get("alt") or a.get("caption") or "").replace("]", "")
                lines.append(f"{prefix}![{alt}]({target})")
                if a.get("caption"):
                    lines.append(prefix + "*" + _md_escape(a["caption"]) + "*")
        elif t in ("video", "audio"):
            target = image_map.get(a.get("src", ""))
            if target:
                lines.append(f"{prefix}[{t}: {target}]({target})")
        lines.append(prefix.rstrip() if prefix.strip() else "")

    for n in doc.get("content") or []:
        block(n)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def export_markdown(project: Project, chapters: list[Chapter], opts: ExportOptions) -> tuple[bytes, str]:
    """Returns (bytes, extension). Produces a .zip with an images/ folder when media is present."""
    media: dict[str, str] = {}
    files: dict[str, bytes] = {}
    if opts.includeImages:
        for ch in chapters:
            for name in pmdoc.collect_assets(ch.doc):
                p = asset_path(project, "/api/assets/" + name)
                if p:
                    media["/api/assets/" + name] = f"images/{name}"
                    files[f"images/{name}"] = p.read_bytes()
    title = opts.title or project.meta.name
    parts = [f"# {_md_escape(title)}\n"]
    if opts.author or project.meta.author:
        parts.append(f"*by {_md_escape(opts.author or project.meta.author)}*\n")
    for i, ch in enumerate(chapters, 1):
        heading = f"Chapter {i}: {ch.title}" if opts.chapterNumbers else ch.title
        parts.append(f"\n## {_md_escape(heading)}\n\n" + doc_to_markdown(ch.doc, media, opts.sceneBreak))
    md = "\n".join(parts).encode("utf-8")
    if not files:
        return md, "md"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manuscript.md", md)
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue(), "zip"


# --------------------------------------------------------------------------- HTML

HTML_CSS = """
body{font-family:Georgia,'Times New Roman',serif;max-width:42em;margin:3em auto;padding:0 1em;line-height:1.65;color:#1f1f1f;background:#fff}
h1.book-title{text-align:center;font-size:2.4em;margin-top:2em}p.author{text-align:center;font-style:italic}
h2.chapter{page-break-before:always;margin-top:3em;text-align:center}
blockquote{border-left:3px solid #c9a227;margin-left:0;padding-left:1em;color:#555;font-style:italic}
figure{margin:1.5em auto;text-align:center}figure img{max-width:100%}figcaption{font-size:.9em;color:#666}
figure.align-left{text-align:left}figure.align-right{text-align:right}
hr{border:0;text-align:center}hr:after{content:'* * *';letter-spacing:.5em;color:#777}
nav.toc ol{line-height:2}video,audio{max-width:100%}
"""


def export_html(project: Project, chapters: list[Chapter], opts: ExportOptions) -> bytes:
    def resolve(src: str) -> str | None:
        if not opts.includeImages:
            return None
        p = asset_path(project, src)
        if not p:
            return None
        ext = p.suffix[1:]
        return f"data:{MIME[ext]};base64,{base64.b64encode(p.read_bytes()).decode()}"

    title = html.escape(opts.title or project.meta.name)
    author = html.escape(opts.author or project.meta.author or "")
    body = []
    if opts.titlePage:
        body.append(f'<h1 class="book-title">{title}</h1>')
        if author:
            body.append(f'<p class="author">{author}</p>')
    if opts.toc and len(chapters) > 1:
        body.append('<nav class="toc"><h2>Contents</h2><ol>' + "".join(f'<li><a href="#ch{i}">{html.escape(c.title)}</a></li>' for i, c in enumerate(chapters, 1)) + "</ol></nav>")
    for i, ch in enumerate(chapters, 1):
        heading = f"Chapter {i}: {ch.title}" if opts.chapterNumbers else ch.title
        body.append(f'<section><h2 class="chapter" id="ch{i}">{html.escape(heading)}</h2>{pmdoc.doc_to_html(ch.doc, resolve, heading_shift=1)}</section>')
    doc = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'\">"
        f"<title>{title}</title><style>{HTML_CSS}</style></head><body>{''.join(body)}</body></html>"
    )
    return doc.encode("utf-8")


# --------------------------------------------------------------------------- TXT

def export_txt(project: Project, chapters: list[Chapter], opts: ExportOptions) -> bytes:
    out = []
    title = opts.title or project.meta.name
    if opts.titlePage:
        out.append(title.upper())
        if opts.author or project.meta.author:
            out.append(f"by {opts.author or project.meta.author}")
        out.append("\n")
    for i, ch in enumerate(chapters, 1):
        heading = f"Chapter {i}: {ch.title}" if opts.chapterNumbers else ch.title
        out.append(heading)
        out.append("=" * min(len(heading), 60))
        out.append("")
        for n in ch.doc.get("content") or []:
            if n.get("type") == "horizontalRule":
                out.append(opts.sceneBreak)
            elif n.get("type") in ("bulletList", "orderedList"):
                for j, li in enumerate(n.get("content") or [], 1):
                    bullet = "-" if n["type"] == "bulletList" else f"{j}."
                    out.append(f"{bullet} " + " ".join(pmdoc.iter_blocks_text(li)))
            else:
                text = "\n".join(pmdoc.iter_blocks_text(n))
                if text or n.get("type") == "paragraph":
                    out.append(text)
            out.append("")
        out.append("\n")
    return "\n".join(out).rstrip().encode("utf-8") + b"\n"
