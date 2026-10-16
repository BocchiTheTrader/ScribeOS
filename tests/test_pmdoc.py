from scribeos.storage import pmdoc

H = "a" * 64
IMG = f"/api/assets/{H}.png"


def test_sanitize_drops_unknown_nodes_and_attrs():
    doc = {
        "type": "doc",
        "content": [
            {"type": "script", "content": [{"type": "text", "text": "alert(1)"}]},
            {"type": "paragraph", "attrs": {"onclick": "x", "textAlign": "center"}, "content": [{"type": "text", "text": "hi", "marks": [{"type": "bold"}, {"type": "evil"}]}]},
            {"type": "image", "attrs": {"src": "https://evil.example/x.png"}},
            {"type": "image", "attrs": {"src": "javascript:alert(1)"}},
            {"type": "image", "attrs": {"src": IMG, "onerror": "x", "width": 50, "align": "left", "caption": "c"}},
        ],
    }
    out = pmdoc.sanitize_doc(doc)
    assert out["content"][0] == {"type": "paragraph", "attrs": {"textAlign": "center"}, "content": [{"type": "text", "text": "hi", "marks": [{"type": "bold"}]}]}
    assert out["content"][1] == {"type": "image", "attrs": {"src": IMG, "width": 50, "align": "left", "caption": "c"}}
    assert len(out["content"]) == 2


def test_links_only_safe_protocols():
    def link(href):
        d = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x", "marks": [{"type": "link", "attrs": {"href": href}}]}]}]}
        return pmdoc.sanitize_doc(d)["content"][0]["content"][0].get("marks")

    assert link("javascript:alert(1)") is None
    assert link(" JaVaScRiPt:alert(1)") is None
    assert link("data:text/html,<script>") is None
    assert link("https://example.com")[0]["attrs"]["href"] == "https://example.com"
    assert link("mailto:a@b.c")


def test_colors_and_fonts_validated():
    d = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "a", "marks": [{"type": "textStyle", "attrs": {"color": "red;background:url(x)"}}]},
        {"type": "text", "text": "b", "marks": [{"type": "textStyle", "attrs": {"color": "#ff0000", "fontFamily": "x</style>"}}]},
        {"type": "text", "text": "c", "marks": [{"type": "highlight", "attrs": {"color": "expression(1)"}}]},
    ]}]}
    p = pmdoc.sanitize_doc(d)["content"][0]["content"]
    assert "marks" not in p[0]
    assert p[1]["marks"] == [{"type": "textStyle", "attrs": {"color": "#ff0000"}}]
    assert p[2]["marks"] == [{"type": "highlight"}]


def test_numeric_coercion_from_yjs():
    d = {"type": "doc", "content": [{"type": "heading", "attrs": {"level": 3.0}, "content": [{"type": "text", "text": "x"}]}, {"type": "orderedList", "attrs": {"start": 4.0}, "content": [{"type": "listItem", "content": [{"type": "paragraph"}]}]}]}
    out = pmdoc.sanitize_doc(d)
    assert out["content"][0]["attrs"]["level"] == 3 and isinstance(out["content"][0]["attrs"]["level"], int)
    assert out["content"][1]["attrs"]["start"] == 4


def test_invalid_input_and_depth_bomb():
    assert pmdoc.sanitize_doc(None) == pmdoc.empty_doc()
    assert pmdoc.sanitize_doc({"type": "paragraph"}) == pmdoc.empty_doc()
    deep: dict = {"type": "paragraph", "content": [{"type": "text", "text": "x"}]}
    for _ in range(500):
        deep = {"type": "blockquote", "content": [deep]}
    out = pmdoc.sanitize_doc({"type": "doc", "content": [deep]})
    assert out["type"] == "doc"


def test_word_count_and_text():
    d = pmdoc.text_to_doc("Hello there world.\n\nSecond   paragraph here")
    assert pmdoc.doc_word_count(d) == 6
    assert pmdoc.doc_text(d) == "Hello there world.\n\nSecond   paragraph here"


def test_html_to_doc_v1_content():
    html = '<h2>Title</h2><hr><p><b>Bold</b> and <i>it</i> <script>alert(1)</script></p><div>plain div</div><ul><li>one</li><li>two</li></ul><blockquote>q</blockquote><img src="javascript:x"><a href="javascript:1">bad</a><span style="color:#ff0000">red</span>'
    d = pmdoc.html_to_doc(html)
    types = [n["type"] for n in d["content"]]
    assert types[:3] == ["heading", "horizontalRule", "paragraph"]
    assert "bulletList" in types and "blockquote" in types
    text = pmdoc.doc_text(d)
    assert "alert" not in text and "bad" in text
    for n in d["content"]:
        for c in n.get("content", []):
            for m in c.get("marks", []):
                assert m["type"] != "link"


def test_doc_to_html_escapes():
    d = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "<script>alert('x')</script>"}]}]}
    out = pmdoc.doc_to_html(d)
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_collect_assets_and_map_text():
    d = pmdoc.sanitize_doc({"type": "doc", "content": [{"type": "image", "attrs": {"src": IMG}}, {"type": "paragraph", "content": [{"type": "text", "text": "cat cat"}]}]})
    assert pmdoc.collect_assets(d) == [f"{H}.png"]
    new, changed = pmdoc.map_text_nodes(d, lambda t: t.replace("cat", "dog"))
    assert changed and pmdoc.doc_text(new).endswith("dog dog")
