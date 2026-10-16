import io
import time
import zipfile
from datetime import date, datetime, timedelta

import pytest
from PIL import Image

from scribeos.storage import backups, migrations, pmdoc
from scribeos.storage.assets import AssetError, AssetStore, sniff
from scribeos.storage.project_store import Project, ProjectError, ProjectLocked, safe_folder_name
from scribeos.storage.stats import Stats


def png_bytes(color="red", fmt="PNG"):
    b = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(b, fmt)
    return b.getvalue()


def test_create_open_and_save(tmp_path):
    p = Project.create(tmp_path, "My: Novel?")
    assert p.path.name == "My_ Novel_.scribe"
    cid = p.meta.tree[0].id
    res = p.save_chapter(cid, pmdoc.text_to_doc("one two three"))
    assert res == {"wordCount": 3, "delta": 3, "updatedAt": res["updatedAt"]}
    assert p.save_chapter(cid, pmdoc.text_to_doc("one two three"))["delta"] == 0
    p.close()
    q = Project.open(p.path)
    assert q.get_chapter(cid)["wordCount"] == 3
    assert pmdoc.doc_text(q.get_chapter(cid)["doc"]) == "one two three"


def test_folder_name_safety():
    assert safe_folder_name("../../etc") == ".._.._etc"
    assert safe_folder_name("CON") == "Untitled"
    assert safe_folder_name("   ") == "Untitled"
    assert "/" not in safe_folder_name("a/b\\c")


def test_lock(tmp_path):
    p = Project.create(tmp_path, "Locked")
    p.acquire_lock()
    with pytest.raises(ProjectLocked):
        Project.open(p.path)
    Project.open(p.path, force=True)


def test_ids_cannot_traverse(tmp_path):
    p = Project.create(tmp_path, "T")
    for bad in ("../x", "..\\x", "a/b", "", "x" * 100):
        with pytest.raises(ProjectError):
            p.get_chapter(bad)
        with pytest.raises(ProjectError):
            p._chapter_file(bad)
    with pytest.raises(ProjectError):
        p.get_snapshot(p.meta.tree[0].id, "../../project")


def test_tree_operations(tmp_path):
    p = Project.create(tmp_path, "T")
    a = p.meta.tree[0].id
    b = p.add_chapter("B").id
    part = p.add_part("Part One")
    tree = [{"type": "part", "id": part.id, "title": "Part One", "children": [{"type": "chapter", "id": b, "title": "B renamed"}]}, {"type": "chapter", "id": a, "title": "A"}]
    p.set_tree(tree)
    assert [c.id for c in p.meta.chapters()] == [b, a]
    assert p.meta.find_chapter(b).title == "B renamed"
    with pytest.raises(ProjectError):
        p.set_tree([{"type": "chapter", "id": a}])  # missing b
    with pytest.raises(ProjectError):
        p.set_tree([{"type": "chapter", "id": a}, {"type": "chapter", "id": a}, {"type": "chapter", "id": b}])
    with pytest.raises(Exception):
        p.set_tree([{"type": "chapter", "id": "../evil"}])
    p.update_node(b, {"status": "final", "synopsis": "s", "title": "X"})
    assert p.meta.find_chapter(b).status == "final"
    with pytest.raises(Exception):
        p.update_node(b, {"status": "nope"})


def test_delete_trash_restore(tmp_path):
    p = Project.create(tmp_path, "T")
    a = p.meta.tree[0].id
    with pytest.raises(ProjectError):
        p.delete_node(a)  # last chapter
    b = p.add_chapter("B", doc=pmdoc.text_to_doc("keep me")).id
    p.delete_node(b)
    assert p.meta.find_chapter(b) is None
    assert [t["id"] for t in p.list_trash()] == [b]
    p.restore_trash(b)
    assert pmdoc.doc_text(p.get_chapter(b)["doc"]) == "keep me"


def test_snapshots(tmp_path):
    p = Project.create(tmp_path, "T")
    cid = p.meta.tree[0].id
    p.save_chapter(cid, pmdoc.text_to_doc("version one"))
    snap = p.create_snapshot(cid, "v1")
    p.save_chapter(cid, pmdoc.text_to_doc("version two"))
    p.restore_snapshot(cid, snap["id"])
    assert pmdoc.doc_text(p.get_chapter(cid)["doc"]) == "version one"
    labels = [s["label"] for s in p.list_snapshots(cid)]
    assert "Before restore" in labels and "v1" in labels


def test_auto_snapshot_interval(tmp_path):
    p = Project.create(tmp_path, "T")
    cid = p.meta.tree[0].id
    p.save_chapter(cid, pmdoc.text_to_doc("first"), snapshot_interval=0)
    p.save_chapter(cid, pmdoc.text_to_doc("second"), snapshot_interval=0)
    assert any(s["auto"] for s in p.list_snapshots(cid))


def test_notes_and_codex_validation(tmp_path):
    p = Project.create(tmp_path, "T")
    p.save_notes([{"id": "n1", "text": "hello", "color": "cyan", "x": 10, "y": 20, "w": 200, "h": 150}])
    assert p.get_notes()[0]["color"] == "cyan"
    with pytest.raises(Exception):
        p.save_notes([{"id": "n1", "color": "javascript"}])
    p.save_codex([{"id": "k1", "name": "Kaelith", "aliases": ["Kae"], "kind": "character"}])
    with pytest.raises(Exception):
        p.save_codex([{"id": "k1", "name": "A"}, {"id": "k1", "name": "B"}])
    with pytest.raises(Exception):
        p.save_codex([{"id": "k2", "name": "X", "image": "https://evil/x.png"}])


def test_search_replace(tmp_path):
    p = Project.create(tmp_path, "T")
    cid = p.meta.tree[0].id
    p.save_chapter(cid, pmdoc.text_to_doc("The cat sat. Concatenate CAT."))
    assert len(p.search("cat")) == 3
    assert len(p.search("cat", whole=True)) == 2
    assert len(p.search("cat", case=True)) == 2
    r = p.replace_all("cat", "dog", whole=True)
    assert r["replaced"] == 2
    assert pmdoc.doc_text(p.get_chapter(cid)["doc"]) == "The dog sat. Concatenate dog."
    # replacement string is literal unless regex mode
    p.replace_all("dog", r"\1", whole=True)
    assert "\\1" in pmdoc.doc_text(p.get_chapter(cid)["doc"])
    with pytest.raises(ProjectError):
        p.search("(unclosed", regex=True)


def test_copy_to(tmp_path):
    p = Project.create(tmp_path / "a", "Orig")
    p.acquire_lock()
    dest = p.copy_to(tmp_path / "b", "Copy")
    assert (dest / "project.json").exists() and not (dest / "project.lock").exists()
    assert Project.open(dest).meta.name == "Copy"


def test_assets(tmp_path):
    store = AssetStore(tmp_path / "assets")
    info = store.save_bytes(png_bytes())
    assert info["name"].endswith(".png") and info["kind"] == "image"
    assert store.save_bytes(png_bytes())["name"] == info["name"]  # dedup
    gif = store.save_bytes(png_bytes(fmt="GIF"))
    assert gif["name"].endswith(".gif")
    assert store.thumbnail(gif["name"]).exists()
    with pytest.raises(AssetError):
        store.save_bytes(b"MZ\x90\x00 this is an exe")
    with pytest.raises(AssetError):
        store.save_bytes(b"<html><script>alert(1)</script></html>")
    with pytest.raises(AssetError):
        store.path("../../secret.png")
    assert sniff(b"GIF89a....") == "gif"
    assert sniff(b"\x1a\x45\xdf\xa3....") == "webm"


def test_backups_rotation_and_safe_extract(tmp_path):
    p = Project.create(tmp_path / "projects", "Backed")
    out = backups.make_backup(tmp_path / "bk", p, keep=3)
    assert out.exists()
    folder = out.parent
    now = datetime(2026, 10, 16, 12, 0, 0)
    for i in range(10):
        ts = now - timedelta(hours=i)
        (folder / ts.strftime("backup-%Y%m%d-%H%M%S.zip")).write_bytes(b"x")
    backups.rotate(folder, keep=3, now=now)
    left = sorted(folder.glob("backup-*.zip"))
    assert len(left) <= 5
    restored = backups.safe_extract(out, tmp_path / "restore")
    assert (restored / "project.json").exists()

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("proj.scribe/project.json", "{}")
        z.writestr("proj.scribe/../../escape.txt", "pwned")
    backups.safe_extract(evil, tmp_path / "restore2")
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "restore2" / ".." / "escape.txt").resolve().exists()

    two = tmp_path / "two.zip"
    with zipfile.ZipFile(two, "w") as z:
        z.writestr("a/project.json", "{}")
        z.writestr("b/project.json", "{}")
    with pytest.raises(ValueError):
        backups.safe_extract(two, tmp_path / "restore3")


def test_stats(tmp_path):
    s = Stats(tmp_path / "stats.json")
    d = date(2026, 10, 16)
    s.record(500, d)
    s.record(-20, d)
    s.record(1200, d - timedelta(days=1))
    s.record(1500, d - timedelta(days=2))
    assert s.today(d) == {"added": 500, "deleted": 20}
    assert s.streak(1000, d) == 2  # today below goal → count from yesterday
    assert s.streak(400, d) == 3
    assert Stats(tmp_path / "stats.json").today(d)["added"] == 500


def test_v1_migration(tmp_path):
    import base64

    data_uri = "data:image/png;base64," + base64.b64encode(png_bytes("blue")).decode()
    v1 = {
        "activeProjectId": "p1",
        "projects": [
            {"id": "p1", "name": "Cyberpunk", "activeChapterId": "c2", "chapters": [
                {"id": "c1", "title": "One", "content": "<p>Hello <b>world</b></p>", "wordCount": 2},
                {"id": "c2", "title": "Two", "content": f'<p>Pic:</p><img src="{data_uri}"><p onclick="x">ok</p>', "wordCount": 2},
            ], "notes": [{"id": "n1", "text": "<b>Plot</b> hole", "color": "cyan", "x": 100, "y": 50}]},
            {"id": "p2", "name": "Empty", "chapters": [], "notes": []},
        ],
        "settings": {"fontFamily": "Merriweather, serif", "fontSize": "20", "lineHeight": "1.8", "contentWidth": "900", "dailyGoal": 750, "typewriter": True, "zenMode": False},
    }
    paths, patch = migrations.import_v1(v1, tmp_path)
    assert len(paths) == 2
    p = Project.open(paths[0])
    titles = [c.title for c in p.meta.chapters()]
    assert titles == ["One", "Two"]
    two = p.meta.chapters()[1]
    assert p.meta.activeChapterId == two.id
    doc = p.get_chapter(two.id)["doc"]
    assert pmdoc.collect_assets(doc)
    assert p.get_notes()[0]["text"] == "Plot hole"
    assert patch["editor"] == {"bodyFont": "Merriweather", "fontSize": 20, "lineHeight": 1.8, "contentWidth": 900, "dailyGoal": 750, "typewriter": True}
    with pytest.raises(migrations.MigrationError):
        migrations.import_v1({"nope": 1}, tmp_path)
    time.sleep(0)
