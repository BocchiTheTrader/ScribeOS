"""Vector PDF export through the Typst typesetting engine.

All user text is emitted as Typst *string literals* (``#"..."``), never as raw markup,
so nothing a writer types can be interpreted as Typst code.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from ..storage.project_store import Project
from .common import Chapter, ExportOptions, asset_path

HEX = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?([0-9a-fA-F]{2})?$")
RGB = re.compile(r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})")

PRESETS = {
    "manuscript": {
        "page": 'paper: "us-letter", margin: 1in',
        "fonts": ["Courier Prime", "Courier New", "DejaVu Sans Mono"],
        "size": 12,
        "leading": "1.3em",
        "indent": "0.5in",
        "justify": False,
        "chapter_top": "2.5in",
        "header": True,
    },
    "paperback": {
        "page": "width: 6in, height: 9in, margin: (inside: 0.8in, outside: 0.6in, top: 0.75in, bottom: 0.85in)",
        "fonts": ["EB Garamond", "Literata", "Libertinus Serif", "Georgia"],
        "size": 11,
        "leading": "0.7em",
        "indent": "1.2em",
        "justify": True,
        "chapter_top": "1.4in",
        "header": False,
    },
    "a4": {
        "page": 'paper: "a4", margin: 2.5cm',
        "fonts": ["Libertinus Serif", "Georgia"],
        "size": 11,
        "leading": "0.75em",
        "indent": "1.5em",
        "justify": True,
        "chapter_top": "3cm",
        "header": False,
    },
    "a5": {
        "page": 'paper: "a5", margin: (x: 1.6cm, y: 1.8cm)',
        "fonts": ["Libertinus Serif", "Georgia"],
        "size": 10,
        "leading": "0.7em",
        "indent": "1.2em",
        "justify": True,
        "chapter_top": "2cm",
        "header": False,
    },
    "manga": {
        "page": 'paper: "a4", margin: 2cm',
        "fonts": ["Noto Sans JP", "Inter", "Arial", "DejaVu Sans"],
        "size": 11,
        "leading": "0.8em",
        "indent": "0em",
        "justify": False,
        "chapter_top": "1cm",
        "header": False,
    },
}

TYPST_IMAGE_OK = {"png", "jpg", "gif", "svg"}


def tstr(s: str) -> str:
    """Typst string literal."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n").replace("\t", "\\t")
    return f'"{s}"'


def tcolor(c: str | None) -> str | None:
    if not isinstance(c, str):
        return None
    if HEX.match(c):
        return f"rgb({tstr(c)})"
    m = RGB.match(c)
    if m:
        r, g, b = (min(255, int(x)) for x in m.groups())
        return f"rgb({r}, {g}, {b})"
    return None


class _Gen:
    def __init__(self, project: Project, opts: ExportOptions, imgdir: Path):
        self.project = project
        self.opts = opts
        self.imgdir = imgdir
        self.images: dict[str, str] = {}

    def image(self, src: str) -> str | None:
        if src in self.images:
            return self.images[src]
        p = asset_path(self.project, src)
        if not p:
            return None
        ext = p.suffix[1:]
        if ext not in TYPST_IMAGE_OK and ext not in ("webp", "avif"):
            return None
        out_name = p.name
        if ext in TYPST_IMAGE_OK:
            shutil.copyfile(p, self.imgdir / out_name)
        else:
            try:
                from PIL import Image

                out_name = p.stem + ".png"
                with Image.open(p) as im:
                    im.save(self.imgdir / out_name, "PNG")
            except Exception:
                return None
        rel = f"img/{out_name}"
        self.images[src] = rel
        return rel

    def inline(self, nodes: list) -> str:
        out = []
        for n in nodes or []:
            if n.get("type") == "hardBreak":
                out.append("#linebreak()")
                continue
            text = n.get("text", "")
            if not text:
                continue
            s = f"#{tstr(text)}"
            for m in n.get("marks") or []:
                t, a = m.get("type"), m.get("attrs") or {}
                if t == "bold":
                    s = f"#strong[{s}]"
                elif t == "italic":
                    s = f"#emph[{s}]"
                elif t == "underline":
                    s = f"#underline[{s}]"
                elif t == "strike":
                    s = f"#strike[{s}]"
                elif t == "code":
                    s = f"#raw({tstr(text)})"
                elif t == "highlight":
                    col = tcolor(a.get("color")) or 'rgb("#fef08a")'
                    s = f"#highlight(fill: {col})[{s}]"
                elif t == "textStyle":
                    col = tcolor(a.get("color"))
                    if col:
                        s = f"#text(fill: {col})[{s}]"
                    if isinstance(a.get("fontFamily"), str):
                        family = a["fontFamily"].split(",")[0].strip(" '\"")
                        s = f"#text(font: {tstr(family)})[{s}]"
                elif t == "link":
                    href = a.get("href", "")
                    if href.startswith(("http://", "https://", "mailto:")):
                        s = f"#link({tstr(href)})[{s}]"
            out.append(s)
        return "".join(out)

    def blocks(self, nodes: list, sep: str = "\n\n") -> str:
        return sep.join(b for b in (self.block(n) for n in nodes or []) if b is not None)

    def block(self, n: dict) -> str | None:
        t = n.get("type")
        a = n.get("attrs") or {}
        if t == "paragraph":
            body = self.inline(n.get("content"))
            if not body:
                return "#v(0.6em)"
            if a.get("textAlign") in ("center", "right"):
                return f"#align({a['textAlign']})[{body}]"
            return body
        if t == "heading":
            lvl = min(6, int(a.get("level", 2)) + 1)
            return f"#heading(level: {lvl})[{self.inline(n.get('content'))}]"
        if t == "blockquote":
            return f"#quote(block: true)[{self.blocks(n.get('content'), ' #parbreak() ')}]"
        if t in ("bulletList", "orderedList"):
            items = ", ".join(f"[{self.blocks(li.get('content'), ' #parbreak() ')}]" for li in n.get("content") or [])
            if t == "bulletList":
                return f"#list(tight: true, {items})"
            return f"#enum(tight: true, start: {int(a.get('start', 1))}, {items})"
        if t == "horizontalRule":
            return f"#align(center)[#v(0.4em)#{tstr(self.opts.sceneBreak)}#v(0.4em)]"
        if t == "codeBlock":
            code = "".join(c.get("text", "") for c in n.get("content") or [])
            return f"#raw({tstr(code)}, block: true)"
        if t == "image":
            if not self.opts.includeImages:
                return None
            rel = self.image(a.get("src", ""))
            if not rel:
                return None
            width = f"{int(a['width'])}%" if a.get("width") else "auto"
            img = f"image({tstr(rel)}, width: {width})"
            cap = a.get("caption")
            fig = f"#figure({img}, caption: [#{tstr(cap)}])" if cap else f"#figure({img})"
            align = {"left": "left", "right": "right"}.get(a.get("align", ""), "center")
            return f"#align({align})[{fig}]"
        if t in ("video", "audio"):
            return f"#align(center)[#emph[#{tstr(f'[{t} — see digital edition]')}]]"
        return None


def build_typst(project: Project, chapters: list[Chapter], opts: ExportOptions, imgdir: Path) -> str:
    preset = PRESETS[opts.preset]
    fonts = ([opts.font] if opts.font else []) + preset["fonts"]
    size = opts.fontSize or preset["size"]
    title = opts.title or project.meta.name
    author = opts.author if opts.author is not None else project.meta.author
    g = _Gen(project, opts, imgdir)
    font_list = "(" + ", ".join(tstr(f) for f in fonts) + ",)"
    lines = [
        f"#set document(title: {tstr(title)}" + (f", author: {tstr(author)}" if author else "") + ")",
        f"#set page({preset['page']})",
        f"#set text(font: {font_list}, size: {size}pt, lang: \"en\", hyphenate: {'true' if preset['justify'] else 'false'})",
        f"#set par(justify: {'true' if preset['justify'] else 'false'}, leading: {preset['leading']}, first-line-indent: {preset['indent']}, spacing: {preset['leading'] if preset['indent'] != '0em' else '1.1em'})",
        "#set figure(numbering: none)",
        "#set quote(block: true)",
        "#show heading.where(level: 1): it => { pagebreak(weak: true); v(" + preset["chapter_top"] + "); align(center, text(size: 1.7em, weight: \"bold\", it.body)); v(2em) }",
        "#show heading.where(level: 2): set text(size: 1.25em)",
        "#show link: underline",
    ]
    if opts.titlePage:
        lines.append(f"#page(numbering: none, header: none)[#align(center + horizon)[#text(size: 2.4em, weight: \"bold\")[#{tstr(title)}]" + (f"\\ #v(1.5em) #text(size: 1.3em, style: \"italic\")[#{tstr(author)}]" if author else "") + "]]")
    if opts.toc and len(chapters) > 1:
        lines.append("#page(numbering: none, header: none)[#outline(title: [#\"Contents\"], depth: 1)]")
    header = "none"
    if preset["header"]:
        label = " / ".join(x for x in (author, title) if x)
        header = f"context align(right)[#{tstr(label + ' / ')}#counter(page).display()]"
    numbering = '"1"' if opts.pageNumbers else "none"
    lines.append(f"#set page(numbering: {numbering}, header: {header})")
    lines.append("#counter(page).update(1)")
    for i, ch in enumerate(chapters, 1):
        heading = f"Chapter {i}: {ch.title}" if opts.chapterNumbers else ch.title
        lines.append(f"#heading(level: 1, outlined: true)[#{tstr(heading)}]")
        lines.append(g.blocks(ch.doc.get("content")))
    return "\n\n".join(lines) + "\n"


def font_dirs(extra: list[Path]) -> list[str]:
    return [str(p) for p in extra if p.exists()]


def export_pdf(project: Project, chapters: list[Chapter], opts: ExportOptions, fonts: list[Path] | None = None, fmt: str = "pdf", ppi: float | None = None):
    import typst

    with tempfile.TemporaryDirectory(prefix="scribeos-typst-") as tmp:
        root = Path(tmp)
        (root / "img").mkdir()
        src = build_typst(project, chapters, opts, root / "img")
        main = root / "main.typ"
        main.write_text(src, encoding="utf-8")
        kwargs = {"root": str(root), "font_paths": font_dirs(fonts or [])}
        if fmt != "pdf":
            kwargs.update(format=fmt, ppi=ppi or 60)
        return typst.compile(str(main), **kwargs)
