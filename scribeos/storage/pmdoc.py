"""ProseMirror (TipTap) JSON document handling.

Every document that reaches disk passes through :func:`sanitize_doc`, which keeps only
the node types, marks and attributes the editor schema knows about. This is the
server-side half of the XSS defence (the editor schema is the client-side half).
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any, Callable

MAX_DEPTH = 64
MAX_TEXT = 5_000_000

ASSET_SRC_RE = re.compile(r"^/api/assets/[0-9a-f]{64}\.(png|jpg|gif|webp|avif|svg|mp4|webm|mp3|ogg|wav|m4a)$")
COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*(0|1|0?\.\d+)\s*)?\))$")
FONT_RE = re.compile(r"^[A-Za-z0-9 ,'\"\-]{1,120}$")
ALIGNMENTS = {"left", "center", "right", "justify"}
IMG_ALIGN = {"left", "center", "right", "full"}

MARK_ORDER = ("link", "bold", "italic", "underline", "strike", "code", "highlight", "textStyle")
BLOCK_CONTAINERS = {"doc", "blockquote", "listItem"}
INLINE_PARENTS = {"paragraph", "heading"}


def empty_doc() -> dict:
    return {"type": "doc", "content": [{"type": "paragraph"}]}


def safe_href(href: Any) -> str | None:
    if not isinstance(href, str):
        return None
    href = href.strip()
    if len(href) > 2048:
        return None
    if re.match(r"^(https?:|mailto:)", href, re.I) or href.startswith("#"):
        return href
    return None


def _int(v: Any) -> int | None:
    """Coerce JSON/Yjs numbers (which may arrive as floats or numeric strings) to int."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _str(v: Any, limit: int = 500) -> str | None:
    if isinstance(v, str):
        return v[:limit]
    return None


def _align(attrs: dict) -> dict:
    a = attrs.get("textAlign")
    return {"textAlign": a} if a in ALIGNMENTS else {}


def _clean_marks(marks: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(marks, list):
        return out
    seen = set()
    for m in marks[:16]:
        if not isinstance(m, dict):
            continue
        t = m.get("type")
        attrs = m.get("attrs") if isinstance(m.get("attrs"), dict) else {}
        if t in ("bold", "italic", "strike", "underline", "code"):
            mark: dict = {"type": t}
        elif t == "link":
            href = safe_href(attrs.get("href"))
            if not href:
                continue
            mark = {"type": "link", "attrs": {"href": href}}
        elif t == "highlight":
            c = attrs.get("color")
            mark = {"type": "highlight"}
            if isinstance(c, str) and COLOR_RE.match(c):
                mark["attrs"] = {"color": c}
        elif t == "textStyle":
            clean = {}
            c = attrs.get("color")
            if isinstance(c, str) and COLOR_RE.match(c):
                clean["color"] = c
            f = attrs.get("fontFamily")
            if isinstance(f, str) and FONT_RE.match(f):
                clean["fontFamily"] = f
            if not clean:
                continue
            mark = {"type": "textStyle", "attrs": clean}
        else:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(mark)
    # Canonical order: Yjs stores marks as an unordered map, so normalise for stable output.
    out.sort(key=lambda m: MARK_ORDER.index(m["type"]))
    return out


def _clean_inline(nodes: Any, depth: int) -> list[dict]:
    out: list[dict] = []
    if not isinstance(nodes, list):
        return out
    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        if t == "text":
            text = n.get("text")
            if not isinstance(text, str) or not text:
                continue
            node: dict = {"type": "text", "text": text[:MAX_TEXT]}
            marks = _clean_marks(n.get("marks"))
            if marks:
                node["marks"] = marks
            out.append(node)
        elif t == "hardBreak":
            out.append({"type": "hardBreak"})
        elif t in ("paragraph", "heading") or t in BLOCK_CONTAINERS:
            # Block content where inline is expected: flatten to its inline text.
            out.extend(_clean_inline(n.get("content"), depth + 1))
    return out


def _clean_media(n: dict, kind: str) -> dict | None:
    attrs = n.get("attrs") if isinstance(n.get("attrs"), dict) else {}
    src = attrs.get("src")
    if not isinstance(src, str) or not ASSET_SRC_RE.match(src):
        return None
    clean: dict = {"src": src}
    if kind == "image":
        for key in ("alt", "title", "caption"):
            v = _str(attrs.get(key))
            if v:
                clean[key] = v
        w = attrs.get("width")
        if isinstance(w, (int, float)) and not isinstance(w, bool) and 5 <= w <= 100:
            clean["width"] = int(w)
        if attrs.get("align") in IMG_ALIGN:
            clean["align"] = attrs["align"]
    return {"type": kind, "attrs": clean}


def _clean_blocks(nodes: Any, depth: int, parent: str) -> list[dict]:
    if depth > MAX_DEPTH:
        return []
    out: list[dict] = []
    if not isinstance(nodes, list):
        return out
    pending_inline: list[dict] = []

    def flush() -> None:
        if pending_inline:
            out.append({"type": "paragraph", "content": list(pending_inline)})
            pending_inline.clear()

    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        attrs = n.get("attrs") if isinstance(n.get("attrs"), dict) else {}
        if t in ("text", "hardBreak"):
            pending_inline.extend(_clean_inline([n], depth))
            continue
        flush()
        if t == "paragraph":
            node: dict = {"type": "paragraph"}
            a = _align(attrs)
            if a:
                node["attrs"] = a
            content = _clean_inline(n.get("content"), depth + 1)
            if content:
                node["content"] = content
            out.append(node)
        elif t == "heading":
            level = _int(attrs.get("level"))
            level = level if level in (1, 2, 3) else 2
            node = {"type": "heading", "attrs": {"level": level, **_align(attrs)}}
            content = _clean_inline(n.get("content"), depth + 1)
            if content:
                node["content"] = content
            out.append(node)
        elif t == "blockquote":
            content = _clean_blocks(n.get("content"), depth + 1, "blockquote") or [{"type": "paragraph"}]
            out.append({"type": "blockquote", "content": content})
        elif t in ("bulletList", "orderedList"):
            items = []
            for li in n.get("content") or []:
                if isinstance(li, dict) and li.get("type") == "listItem":
                    c = _clean_blocks(li.get("content"), depth + 2, "listItem")
                    if not c or c[0]["type"] != "paragraph":
                        c = [{"type": "paragraph"}] + c
                    items.append({"type": "listItem", "content": c})
            if not items:
                continue
            node = {"type": t, "content": items}
            if t == "orderedList":
                start = _int(attrs.get("start"))
                if start is not None and 0 <= start < 1_000_000 and start != 1:
                    node["attrs"] = {"start": start}
            out.append(node)
        elif t == "horizontalRule":
            out.append({"type": "horizontalRule"})
        elif t == "codeBlock":
            text = "".join(c.get("text", "") for c in (n.get("content") or []) if isinstance(c, dict) and isinstance(c.get("text"), str))
            node = {"type": "codeBlock"}
            if text:
                node["content"] = [{"type": "text", "text": text[:MAX_TEXT]}]
            out.append(node)
        elif t in ("image", "video", "audio"):
            m = _clean_media(n, t)
            if m:
                out.append(m)
        elif t in ("doc", "listItem", "bulletList"):
            out.extend(_clean_blocks(n.get("content"), depth + 1, parent))
        # Unknown node types are dropped.
    flush()
    return out


def sanitize_doc(doc: Any) -> dict:
    """Return a schema-conformant copy of ``doc`` with anything unknown removed."""
    if not isinstance(doc, dict) or doc.get("type") != "doc":
        return empty_doc()
    content = _clean_blocks(doc.get("content"), 0, "doc")
    return {"type": "doc", "content": content or [{"type": "paragraph"}]}


# --------------------------------------------------------------------------- text

def iter_blocks_text(doc: dict) -> list[str]:
    """Plain text of each textblock, in document order."""
    out: list[str] = []

    def walk(n: dict) -> None:
        t = n.get("type")
        if t in ("paragraph", "heading", "codeBlock"):
            out.append(inline_text(n.get("content") or []))
            return
        for c in n.get("content") or []:
            if isinstance(c, dict):
                walk(c)

    walk(doc)
    return out


def inline_text(nodes: list) -> str:
    parts = []
    for c in nodes:
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
        elif c.get("type") == "hardBreak":
            parts.append("\n")
    return "".join(parts)


def doc_text(doc: dict) -> str:
    return "\n\n".join(iter_blocks_text(doc))


WORD_RE = re.compile(r"\S+")


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def doc_word_count(doc: dict) -> int:
    return sum(count_words(t) for t in iter_blocks_text(doc))


def collect_assets(doc: dict) -> list[str]:
    """Asset file names (``<hash>.<ext>``) referenced by a document."""
    found: list[str] = []

    def walk(n: dict) -> None:
        if n.get("type") in ("image", "video", "audio"):
            src = (n.get("attrs") or {}).get("src", "")
            if isinstance(src, str) and ASSET_SRC_RE.match(src):
                found.append(src.rsplit("/", 1)[1])
        for c in n.get("content") or []:
            if isinstance(c, dict):
                walk(c)

    walk(doc)
    return found


def map_text_nodes(doc: dict, fn: Callable[[str], str]) -> tuple[dict, bool]:
    """Apply ``fn`` to every text node. Returns (new_doc, changed)."""
    changed = False

    def walk(n: dict) -> dict:
        nonlocal changed
        n = dict(n)
        if n.get("type") == "text":
            new = fn(n.get("text", ""))
            if new != n.get("text"):
                changed = True
                n["text"] = new
            return n
        if n.get("type") == "codeBlock":
            return n
        if "content" in n:
            n["content"] = [walk(c) for c in n["content"]]
            n["content"] = [c for c in n["content"] if c.get("type") != "text" or c.get("text")]
        return n

    return walk(doc), changed


# --------------------------------------------------------------------------- PM -> HTML

def _mark_wrap(text: str, marks: list[dict]) -> str:
    for m in marks or []:
        t = m.get("type")
        a = m.get("attrs") or {}
        if t == "bold":
            text = f"<strong>{text}</strong>"
        elif t == "italic":
            text = f"<em>{text}</em>"
        elif t == "strike":
            text = f"<s>{text}</s>"
        elif t == "underline":
            text = f"<u>{text}</u>"
        elif t == "code":
            text = f"<code>{text}</code>"
        elif t == "link" and safe_href(a.get("href")):
            text = f'<a href="{html.escape(a["href"], quote=True)}">{text}</a>'
        elif t == "highlight":
            c = a.get("color")
            style = f' style="background-color:{c}"' if isinstance(c, str) and COLOR_RE.match(c) else ""
            text = f"<mark{style}>{text}</mark>"
        elif t == "textStyle":
            styles = []
            if isinstance(a.get("color"), str) and COLOR_RE.match(a["color"]):
                styles.append(f"color:{a['color']}")
            if isinstance(a.get("fontFamily"), str) and FONT_RE.match(a["fontFamily"]):
                styles.append(f"font-family:{html.escape(a['fontFamily'], quote=True)}")
            if styles:
                text = f'<span style="{";".join(styles)}">{text}</span>'
    return text


def _inline_html(nodes: list) -> str:
    out = []
    for c in nodes or []:
        if c.get("type") == "text":
            out.append(_mark_wrap(html.escape(c.get("text", ""), quote=False), c.get("marks") or []))
        elif c.get("type") == "hardBreak":
            out.append("<br/>")
    return "".join(out)


def doc_to_html(doc: dict, resolve_src: Callable[[str], str | None] | None = None, heading_shift: int = 0) -> str:
    """Render a sanitized document to XHTML-compatible markup.

    ``resolve_src`` maps an asset src (``/api/assets/...``) to the URL to emit, or None to skip.
    """
    resolve = resolve_src or (lambda s: s)

    def block(n: dict) -> str:
        t = n.get("type")
        a = n.get("attrs") or {}
        align = a.get("textAlign")
        style = f' style="text-align:{align}"' if align in ALIGNMENTS else ""
        if t == "paragraph":
            return f"<p{style}>{_inline_html(n.get('content'))}</p>"
        if t == "heading":
            lvl = min(6, max(1, int(a.get("level", 2)) + heading_shift))
            return f"<h{lvl}{style}>{_inline_html(n.get('content'))}</h{lvl}>"
        if t == "blockquote":
            return "<blockquote>" + "".join(block(c) for c in n.get("content") or []) + "</blockquote>"
        if t == "bulletList":
            return "<ul>" + "".join("<li>" + "".join(block(c) for c in li.get("content") or []) + "</li>" for li in n.get("content") or []) + "</ul>"
        if t == "orderedList":
            start = a.get("start")
            st = f' start="{int(start)}"' if isinstance(start, int) else ""
            return f"<ol{st}>" + "".join("<li>" + "".join(block(c) for c in li.get("content") or []) + "</li>" for li in n.get("content") or []) + "</ol>"
        if t == "horizontalRule":
            return '<hr/>'
        if t == "codeBlock":
            return "<pre><code>" + html.escape(inline_text(n.get("content") or []), quote=False) + "</code></pre>"
        if t in ("image", "video", "audio"):
            src = resolve(a.get("src", ""))
            if not src:
                return ""
            esc = html.escape(src, quote=True)
            if t == "image":
                width = f' style="width:{int(a["width"])}%"' if a.get("width") else ""
                alt = html.escape(a.get("alt", "") or "", quote=True)
                img = f'<img src="{esc}" alt="{alt}"{width}/>'
                cap = a.get("caption")
                if cap:
                    return f'<figure class="align-{a.get("align", "center")}">{img}<figcaption>{html.escape(cap, quote=False)}</figcaption></figure>'
                return f'<figure class="align-{a.get("align", "center")}">{img}</figure>'
            if t == "video":
                return f'<video controls="controls" src="{esc}"></video>'
            return f'<audio controls="controls" src="{esc}"></audio>'
        return ""

    return "\n".join(block(c) for c in doc.get("content") or [])


# --------------------------------------------------------------------------- HTML -> PM

class _Builder(HTMLParser):
    """Lenient HTML to ProseMirror JSON converter used by importers and the v1 migration."""

    BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol", "li", "pre", "hr", "figure", "section", "article", "body", "html", "table", "tr", "td", "th", "tbody", "thead", "header", "footer", "main"}

    def __init__(self, image_resolver: Callable[[str], str | None] | None):
        super().__init__(convert_charrefs=True)
        self.image_resolver = image_resolver
        self.root: dict = {"type": "doc", "content": []}
        self.stack: list[dict] = [self.root]
        self.marks: list[tuple[str, dict]] = []
        self.skip = 0
        self.in_pre = 0

    # helpers
    def _top(self) -> dict:
        return self.stack[-1]

    def _ensure_textblock(self) -> dict:
        top = self._top()
        if top["type"] in ("paragraph", "heading", "codeBlock"):
            return top
        p = {"type": "paragraph", "content": []}
        top.setdefault("content", []).append(p)
        self.stack.append(p)
        p["_implicit"] = True
        return p

    def _close_implicit(self) -> None:
        while self._top().get("_implicit"):
            self.stack.pop()

    def _push_block(self, node: dict) -> None:
        self._close_textblocks()
        self._top().setdefault("content", []).append(node)
        self.stack.append(node)

    def _close_textblocks(self) -> None:
        while self._top()["type"] in ("paragraph", "heading", "codeBlock"):
            self.stack.pop()

    def _pop_until(self, types: tuple[str, ...]) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["type"] in types:
                del self.stack[i:]
                return

    def handle_starttag(self, tag: str, attrs_list: list) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        if tag in ("script", "style", "head", "title", "template", "noscript", "iframe", "object", "svg"):
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("p", "div", "section", "article", "header", "footer", "main", "td", "th"):
            self._close_implicit()
            node = {"type": "paragraph", "content": []}
            align = re.search(r"text-align\s*:\s*(left|center|right|justify)", attrs.get("style", ""))
            if align:
                node["attrs"] = {"textAlign": align.group(1)}
            if self._top()["type"] in ("paragraph", "heading"):
                self.stack.pop()
            self._push_block(node)
        elif re.fullmatch(r"h[1-6]", tag):
            self._close_implicit()
            self._close_textblocks()
            self._push_block({"type": "heading", "attrs": {"level": min(3, int(tag[1]))}, "content": []})
        elif tag == "blockquote":
            self._close_implicit()
            self._push_block({"type": "blockquote", "content": []})
        elif tag in ("ul", "ol"):
            self._close_implicit()
            node = {"type": "bulletList" if tag == "ul" else "orderedList", "content": []}
            if tag == "ol" and attrs.get("start", "").isdigit():
                node["attrs"] = {"start": int(attrs["start"])}
            self._push_block(node)
        elif tag == "li":
            self._close_implicit()
            self._close_textblocks()
            if self._top()["type"] == "listItem":
                self.stack.pop()
            if self._top()["type"] not in ("bulletList", "orderedList"):
                self._push_block({"type": "bulletList", "content": []})
            self._push_block({"type": "listItem", "content": []})
        elif tag == "pre":
            self._close_implicit()
            self._push_block({"type": "codeBlock", "content": []})
            self.in_pre += 1
        elif tag == "hr":
            self._close_implicit()
            self._close_textblocks()
            self._top().setdefault("content", []).append({"type": "horizontalRule"})
        elif tag == "br":
            self._ensure_textblock().setdefault("content", []).append({"type": "hardBreak"})
        elif tag == "img":
            src = attrs.get("src", "")
            resolved = self.image_resolver(src) if self.image_resolver else (src if ASSET_SRC_RE.match(src) else None)
            if resolved:
                self._close_implicit()
                self._close_textblocks()
                node = {"type": "image", "attrs": {"src": resolved}}
                if attrs.get("alt"):
                    node["attrs"]["alt"] = attrs["alt"][:500]
                self._top().setdefault("content", []).append(node)
        elif tag in ("b", "strong"):
            self.marks.append((tag, {"type": "bold"}))
        elif tag in ("i", "em"):
            self.marks.append((tag, {"type": "italic"}))
        elif tag in ("s", "strike", "del"):
            self.marks.append((tag, {"type": "strike"}))
        elif tag == "u":
            self.marks.append((tag, {"type": "underline"}))
        elif tag == "code" and not self.in_pre:
            self.marks.append((tag, {"type": "code"}))
        elif tag == "mark":
            self.marks.append((tag, {"type": "highlight"}))
        elif tag == "a":
            href = safe_href(attrs.get("href"))
            self.marks.append((tag, {"type": "link", "attrs": {"href": href}} if href else {"type": "_none"}))
        elif tag in ("span", "font"):
            color = attrs.get("color") or ""
            m = re.search(r"(?<![-\w])color\s*:\s*([^;]+)", attrs.get("style", ""))
            if m:
                color = m.group(1).strip()
            if color and COLOR_RE.match(color):
                self.marks.append((tag, {"type": "textStyle", "attrs": {"color": color}}))
            else:
                self.marks.append((tag, {"type": "_none"}))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head", "title", "template", "noscript", "iframe", "object", "svg"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("p", "div", "section", "article", "header", "footer", "main", "td", "th"):
            self._close_implicit()
            if self._top()["type"] == "paragraph":
                self.stack.pop()
        elif re.fullmatch(r"h[1-6]", tag):
            self._pop_until(("heading",))
        elif tag == "blockquote":
            self._pop_until(("blockquote",))
        elif tag in ("ul", "ol"):
            self._pop_until(("bulletList", "orderedList"))
        elif tag == "li":
            self._pop_until(("listItem",))
        elif tag == "pre":
            self.in_pre = max(0, self.in_pre - 1)
            self._pop_until(("codeBlock",))
        else:
            for i in range(len(self.marks) - 1, -1, -1):
                if self.marks[i][0] == tag:
                    del self.marks[i]
                    break

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if not self.in_pre:
            data = re.sub(r"\s+", " ", data)
            top = self._top()
            if data == " " and top["type"] not in ("paragraph", "heading"):
                return
        if not data:
            return
        tb = self._ensure_textblock()
        node: dict = {"type": "text", "text": data}
        marks = [m for _, m in self.marks if m.get("type") != "_none"]
        if marks and tb["type"] != "codeBlock":
            node["marks"] = marks
        tb.setdefault("content", []).append(node)


def _trim(node: dict) -> dict:
    """Strip helper keys and leading/trailing spaces in textblocks."""
    node.pop("_implicit", None)
    content = node.get("content")
    if content is not None:
        node["content"] = [_trim(c) for c in content]
        if node["type"] in ("paragraph", "heading") and node["content"]:
            first, last = node["content"][0], node["content"][-1]
            if first.get("type") == "text":
                first["text"] = first["text"].lstrip()
            if last.get("type") == "text":
                last["text"] = last["text"].rstrip()
            node["content"] = [c for c in node["content"] if c.get("type") != "text" or c.get("text")]
    return node


def html_to_doc(markup: str, image_resolver: Callable[[str], str | None] | None = None) -> dict:
    b = _Builder(image_resolver)
    b.feed(markup or "")
    b.close()
    root = _trim(b.root)
    # Drop empty paragraphs produced by wrapper elements, keep intentional ones sparse.
    cleaned = []
    for n in root.get("content", []):
        if n.get("type") == "paragraph" and not n.get("content"):
            if cleaned and cleaned[-1].get("type") == "paragraph" and not cleaned[-1].get("content"):
                continue
        cleaned.append(n)
    root["content"] = cleaned
    return sanitize_doc(root)


def text_to_doc(text: str) -> dict:
    paras = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    content = []
    for p in paras:
        p = p.strip("\n")
        if not p.strip():
            continue
        lines = p.split("\n")
        inline: list[dict] = []
        for i, line in enumerate(lines):
            if i:
                inline.append({"type": "hardBreak"})
            if line:
                inline.append({"type": "text", "text": line})
        content.append({"type": "paragraph", "content": inline})
    return sanitize_doc({"type": "doc", "content": content})
