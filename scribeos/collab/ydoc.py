"""Convert between ProseMirror JSON and the Yjs XML layout used by y-prosemirror.

y-prosemirror stores a document in ``doc.getXmlFragment('default')``:
* block / inline nodes  -> ``XmlElement(nodeName=node.type, attributes=node.attrs)``
* runs of text          -> one ``XmlText`` whose formatting attributes are the marks
                            (``{markName: markAttrs}``)
"""

from __future__ import annotations

from typing import Any

from pycrdt import Doc, XmlElement, XmlFragment, XmlText

from ..storage import pmdoc

FIELD = "default"


def _marks_to_attrs(marks: list[dict] | None) -> dict[str, Any]:
    return {m["type"]: dict(m.get("attrs") or {}) for m in marks or []}


def _fill(parent_children, nodes: list[dict]) -> None:
    text: XmlText | None = None
    pos = 0
    for n in nodes:
        t = n.get("type")
        if t == "text":
            if text is None:
                text = parent_children.append(XmlText())
                pos = 0
            s = n.get("text", "")
            text.insert(pos, s, _marks_to_attrs(n.get("marks")) or None)
            pos += len(s)
            continue
        text = None
        el = parent_children.append(XmlElement(t))
        for k, v in (n.get("attrs") or {}).items():
            if v is not None:
                el.attributes[k] = v
        _fill(el.children, n.get("content") or [])


def doc_to_ydoc(doc: dict, ydoc: Doc | None = None) -> Doc:
    ydoc = ydoc or Doc()
    frag = ydoc.get(FIELD, type=XmlFragment)
    _fill(frag.children, pmdoc.sanitize_doc(doc).get("content") or [])
    return ydoc


def _num(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _read(children) -> list[dict]:
    out: list[dict] = []
    for c in children:
        if isinstance(c, XmlText):
            for chunk, attrs in c.diff():
                if not isinstance(chunk, str) or not chunk:
                    continue
                node: dict = {"type": "text", "text": chunk}
                if attrs:
                    node["marks"] = [{"type": k, "attrs": {ak: _num(av) for ak, av in v.items()}} if isinstance(v, dict) and v else {"type": k} for k, v in attrs.items()]
                out.append(node)
        elif isinstance(c, XmlElement):
            node = {"type": c.tag}
            attrs = {k: _num(v) for k, v in dict(c.attributes).items()}
            if attrs:
                node["attrs"] = attrs
            kids = _read(c.children)
            if kids:
                node["content"] = kids
            out.append(node)
    return out


def ydoc_to_doc(ydoc: Doc) -> dict:
    frag = ydoc.get(FIELD, type=XmlFragment)
    return pmdoc.sanitize_doc({"type": "doc", "content": _read(frag.children)})
