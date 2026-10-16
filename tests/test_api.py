import io
import time

from PIL import Image
from starlette.testclient import TestClient

from conftest import LOCAL_PORT, free_port, guest_client, join, owner_client
from scribeos.storage import pmdoc


def gif_bytes():
    b = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(b, "GIF")
    return b.getvalue()


# ------------------------------------------------------------------ authentication & hardening

def test_unauthenticated_access_is_refused(app, ctx):
    c = TestClient(app, base_url=f"http://127.0.0.1:{LOCAL_PORT}", client=("127.0.0.1", 1))
    assert c.get("/api/me").json() == {"signedIn": False, "teamActive": False}
    for path in ("/api/project", "/api/settings", "/api/recent", "/api/stats", "/api/notes", "/api/fonts"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/projects", json={"name": "x"}).status_code == 401
    assert c.get("/auth?t=wrong", follow_redirects=False).status_code == 403
    # Unknown visitors get the join page, not the app.
    r = c.get("/")
    assert r.status_code in (200, 500)


def test_launch_token_only_from_loopback(app, ctx):
    c = TestClient(app, base_url=f"http://127.0.0.1:{LOCAL_PORT}", client=("192.168.1.9", 1))
    assert c.get(f"/auth?t={ctx.local_token}", follow_redirects=False).status_code == 403


def test_owner_cookie_rejected_from_network(app, ctx, owner):
    cookie = owner.cookies.get("scribeos_sid")
    remote = TestClient(app, base_url="http://192.168.1.10:8765", client=("192.168.1.99", 1), cookies={"scribeos_sid": cookie})
    assert remote.get("/api/project").status_code == 401
    assert remote.get("/api/me").json()["signedIn"] is False


def test_host_header_check_blocks_dns_rebinding(app, ctx, owner):
    r = owner.get("/api/me", headers={"host": f"evil.example:{LOCAL_PORT}"})
    assert r.status_code == 403


def test_csrf_required_for_mutations(owner, project):
    token = owner.headers.pop("X-CSRF-Token")
    assert owner.patch("/api/project", json={"name": "x"}).status_code == 403
    assert owner.patch("/api/project", json={"name": "x"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert owner.patch("/api/project", json={"name": "x"}, headers={"X-CSRF-Token": token}).status_code == 200


def test_security_headers(owner, project):
    r = owner.get("/api/project")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_static_path_traversal(owner):
    for p in ("/../config.py", "/..%2f..%2fconfig.py", "/%2e%2e/server/api.py", "/index.html", "/api/../../etc"):
        assert owner.get(p).status_code in (403, 404), p


# ------------------------------------------------------------------ project workflow

def test_project_workflow(owner, project, ctx, tmp_path):
    cid = project["tree"][0]["id"]
    doc = pmdoc.text_to_doc("Hello brave new world")
    r = owner.put(f"/api/chapters/{cid}", json={"doc": doc})
    assert r.json()["wordCount"] == 4 and r.json()["delta"] == 4
    assert ctx.stats.today()["added"] == 4
    evil = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x", "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}]}]}]}
    owner.put(f"/api/chapters/{cid}", json={"doc": evil})
    assert "marks" not in owner.get(f"/api/chapters/{cid}").json()["doc"]["content"][0]["content"][0]
    new = owner.post("/api/project/chapters", json={"title": "Two", "template": "<h2>T</h2><p>body</p>"}).json()
    tree = owner.get("/api/project").json()["tree"]
    assert [n["id"] for n in tree] == [cid, new["id"]]
    assert owner.put("/api/project/tree", json={"tree": [tree[1], tree[0]]}).status_code == 200
    assert owner.put("/api/project/tree", json={"tree": [tree[1]]}).status_code == 400
    assert owner.get("/api/chapters/..%2f..%2fproject").status_code == 404
    snap = owner.post(f"/api/chapters/{cid}/snapshots", json={"label": "keep"}).json()
    assert owner.post(f"/api/chapters/{cid}/snapshots/{snap['id']}/restore").status_code == 200
    assert owner.post("/api/search", json={"q": "x"}).json()
    assert owner.post("/api/search", json={"q": "(", "regex": True}).status_code == 400
    assert owner.delete(f"/api/project/nodes/{new['id']}").status_code == 200
    assert owner.post(f"/api/project/trash/{new['id']}/restore").status_code == 200
    # save-as copies the project somewhere else and switches to it
    r = owner.post("/api/projects/save-as", json={"parent": str(tmp_path / "elsewhere"), "name": "Copy"})
    assert r.status_code == 200 and "elsewhere" in r.json()["path"]
    recent = owner.get("/api/recent").json()
    assert len(recent) == 2 and recent[0]["name"] == "Copy"


def test_project_locked_elsewhere(owner, project, ctx):
    path = project["path"]
    owner.post("/api/projects/close")
    import json
    import pathlib

    (pathlib.Path(path) / "project.lock").write_text(json.dumps({"pid": 1, "host": "other-pc", "heartbeat": time.time(), "token": "zzz"}))
    r = owner.post("/api/projects/open", json={"path": path})
    assert r.status_code == 423 and "other-pc" in r.json()["detail"]
    assert owner.post("/api/projects/open", json={"path": path, "force": True}).status_code == 200


def test_uploads(owner, project, ctx):
    data = gif_bytes()
    r = owner.post("/api/assets", content=data, headers={"Content-Type": "application/octet-stream", "X-Filename": "a.gif"})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["name"].endswith(".gif")
    got = owner.get(info["src"])
    assert got.content == data and got.headers["content-type"] == "image/gif"
    assert "sandbox" in got.headers["content-security-policy"]
    assert owner.get(info["src"] + "/thumb").status_code == 200
    # A file larger than 2 MB is fine now (the old browser limit).
    big = b"GIF89a" + b"\x00" * (3 * 1024 * 1024)
    assert owner.post("/api/assets", content=big).status_code == 200
    # ...but the configured limit is enforced while streaming.
    ctx.config.update({"storage": {"maxUploadMB": 1}})
    assert owner.post("/api/assets", content=big).status_code == 413
    assert owner.post("/api/assets", content=b"<html><script>alert(1)</script>").status_code == 415
    assert owner.get("/api/assets/..%2F..%2Fproject.json").status_code == 404
    assert owner.get("/api/assets/" + "0" * 64 + ".png").status_code == 404
    assert len(owner.get("/api/assets").json()) == 2


def test_settings_validation(owner):
    assert owner.patch("/api/settings", json={"editor": {"fontSize": 999}}).status_code == 422
    assert owner.patch("/api/settings", json={"editor": {"bodyFont": "x;}</style>"}}).status_code == 422
    assert owner.patch("/api/settings", json={"editor": {"theme": "manga", "fontSize": 20}}).json()["editor"]["theme"] == "manga"


def test_fonts(owner, ctx):
    assert owner.post("/api/fonts", content=b"not a font", headers={"X-Filename": "x.ttf"}).status_code == 415
    assert owner.post("/api/fonts", content=b"\x00\x01\x00\x00", headers={"X-Filename": "../../evil.ttf"}).status_code in (400, 415)
    assert not (ctx.paths.data / "evil.ttf").exists()


def test_export_endpoint(owner, project):
    r = owner.post("/api/export", json={"format": "pdf"})
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert "attachment" in r.headers["content-disposition"]
    assert owner.post("/api/export", json={"format": "exe"}).status_code == 422


def test_import_endpoints(owner, project, ctx):
    r = owner.post("/api/import", content=b"# A\n\ntext\n\n# B\n\nmore", headers={"X-Filename": "story.md"})
    assert r.status_code == 200 and len(r.json()["chapters"]) == 2
    v1 = b'{"projects":[{"name":"Old","chapters":[{"id":"c","title":"T","content":"<p>hi</p>"}],"notes":[]}],"settings":{}}'
    r = owner.post("/api/import/v1", content=v1)
    assert r.status_code == 200 and len(r.json()["created"]) == 1
    assert owner.get("/api/project").json()["name"] == "Old"
    assert owner.post("/api/import/v1", content=b"[1,2]").status_code == 400


# ------------------------------------------------------------------ teamwork

def start_team(owner, password="correct horse battery"):
    port = free_port()
    r = owner.post("/api/team/start", json={"password": password, "port": port, "defaultRole": "editor"})
    assert r.status_code == 200, r.text
    return r.json(), port


def test_team_join_roles_and_permissions(app, ctx, owner, project):
    info, port = start_team(owner)
    assert info["password"] == "correct horse battery" and info["qr"].startswith("<")
    g = guest_client(app, port)
    assert g.get("/api/team/status").json() == {"active": True, "locked": False}
    assert g.post("/api/team/join", json={"room": info["roomCode"], "name": "Eve", "password": "wrong"}).status_code == 403
    assert g.post("/api/team/join", json={"room": "WRONG-ROOM-00", "name": "Eve", "password": "correct horse battery"}).status_code == 403
    me = join(g, info["roomCode"].lower(), "correct horse battery", "Aylin <b>")
    assert me["role"] == "editor" and me["session"]["name"] == "Aylin b"
    assert "password" not in me["team"]
    # Guests can read and edit the manuscript, but never touch host-only features.
    assert g.get("/api/project").status_code == 200
    for method, path in (("get", "/api/recent"), ("get", "/api/stats"), ("post", "/api/projects/close"), ("post", "/api/team/stop"), ("patch", "/api/settings"), ("get", "/api/voice/status"), ("post", "/api/backups"), ("get", "/api/fonts/system")):
        r = getattr(g, method)(path, **({"json": {}} if method != "get" else {}))
        assert r.status_code == 403, (path, r.status_code)
    assert g.post("/api/search", json={"q": "a", "regex": True}).status_code == 403
    # Chapter saves go through the live session while Teamwork is on.
    cid = project["tree"][0]["id"]
    assert g.put(f"/api/chapters/{cid}", json={"doc": pmdoc.empty_doc()}).status_code == 409
    # Role change to viewer removes write access immediately.
    sess = ctx.sessions.guests()[0]
    assert owner.patch(f"/api/team/members/{sess.pub_id}", json={"role": "viewer"}).status_code == 200
    assert g.post("/api/project/chapters", json={"title": "x"}).status_code == 403
    assert g.put("/api/notes", json=[]).status_code == 403
    # Kick → session gone
    assert owner.post(f"/api/team/members/{sess.pub_id}/kick").status_code == 200
    assert g.get("/api/project").status_code == 401
    # Stop → all guest sessions invalid, and rejoining is impossible
    g2 = guest_client(app, port, ip="192.168.50.21")
    join(g2, info["roomCode"], "correct horse battery")
    owner.post("/api/team/stop")
    assert g2.get("/api/project").status_code == 401
    assert g2.post("/api/team/join", json={"room": info["roomCode"], "name": "x", "password": "correct horse battery"}).status_code == 409


def test_team_lock_password_rotation_and_rate_limit(app, ctx, owner, project):
    info, port = start_team(owner)
    g = guest_client(app, port)
    join(g, info["roomCode"], "correct horse battery")
    new = owner.patch("/api/team", json={"regeneratePassword": True}).json()
    assert new["password"] != "correct horse battery"
    assert g.get("/api/project").status_code == 401  # rotated password signs everyone out
    owner.patch("/api/team", json={"locked": True})
    assert g.post("/api/team/join", json={"room": new["roomCode"], "name": "x", "password": new["password"]}).status_code == 423
    owner.patch("/api/team", json={"locked": False})
    attacker = guest_client(app, port, ip="10.0.0.66")
    codes = [attacker.post("/api/team/join", json={"room": new["roomCode"], "name": "x", "password": f"guess{i}"}).status_code for i in range(7)]
    assert codes[:5] == [403] * 5 and codes[-1] == 429
    owner.post("/api/team/stop")


def test_live_collaboration_sync_and_persist(app, ctx, owner, project):
    info, port = start_team(owner)
    cid = project["tree"][0]["id"]
    ctx.project.save_chapter(cid, pmdoc.text_to_doc("Seed text"))
    g = guest_client(app, port)
    join(g, info["roomCode"], "correct horse battery")
    origin_owner = {"origin": f"http://127.0.0.1:{LOCAL_PORT}"}
    origin_guest = {"origin": f"http://192.168.50.1:{port}"}

    # Cross-origin websocket is refused.
    from starlette.websockets import WebSocketDisconnect

    try:
        with owner.websocket_connect(f"ws://127.0.0.1:{LOCAL_PORT}/ws/collab/{cid}", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_text()
        raise AssertionError("cross-origin websocket accepted")
    except WebSocketDisconnect:
        pass

    from pycrdt import Doc, XmlFragment, XmlText

    import json

    with owner.websocket_connect(f"ws://127.0.0.1:{LOCAL_PORT}/ws/collab/{cid}", headers=origin_owner) as a:
        init_a = json.loads(a.receive_text())
        state_a = a.receive_bytes()
        assert init_a["type"] == "init" and not init_a["readonly"] and state_a[0] == 0
        with g.websocket_connect(f"ws://192.168.50.1:{port}/ws/collab/{cid}", headers=origin_guest) as b:
            init_b = json.loads(b.receive_text())
            state_b = b.receive_bytes()
            assert init_b["room"] == init_a["room"]
            assert json.loads(a.receive_text())["type"] == "peer-joined"
            # Guest builds its replica from the server state and appends text.
            d = Doc()
            d.apply_update(state_b[1:])
            frag = d.get("default", type=XmlFragment)
            para = list(frag.children)[0]
            text = next(c for c in para.children if isinstance(c, XmlText))
            before = d.get_state()
            text.insert(len(str(text)), " and more")
            b.send_bytes(bytes([0]) + d.get_update(before))
            relayed = a.receive_bytes()
            assert relayed[0] == 0
            # The host's replica converges to the same text.
            da = Doc()
            da.apply_update(state_a[1:])
            da.apply_update(relayed[1:])
            from scribeos.collab import ydoc as _yconv

            assert pmdoc.doc_text(_yconv.ydoc_to_doc(da)) == "Seed text and more"
            # Awareness frames are relayed too.
            b.send_bytes(bytes([1, 0]))
            assert a.receive_bytes()[0] == 1
            # Malformed updates close the sender, not the room.
            b.send_bytes(bytes([0]) + b"garbage-not-a-yjs-update")
    # Taking a snapshot flushes the live room to disk first (as do export and ending the session).
    assert owner.post(f"/api/chapters/{cid}/snapshots", json={"label": "after live edit"}).status_code == 200
    # Merged document was written to disk by the server.
    deadline = time.time() + 5
    while time.time() < deadline and "and more" not in pmdoc.doc_text(ctx.project.get_chapter(cid)["doc"]):
        time.sleep(0.2)
    assert pmdoc.doc_text(ctx.project.get_chapter(cid)["doc"]) == "Seed text and more"

    # Viewers cannot change the document.
    sess = ctx.sessions.guests()[0]
    owner.patch(f"/api/team/members/{sess.pub_id}", json={"role": "viewer"})
    with g.websocket_connect(f"ws://192.168.50.1:{port}/ws/collab/{cid}", headers=origin_guest) as v:
        assert json.loads(v.receive_text())["readonly"] is True
        st = v.receive_bytes()
        d = Doc()
        d.apply_update(st[1:])
        text = next(c for c in list(d.get("default", type=XmlFragment).children)[0].children if isinstance(c, XmlText))
        before = d.get_state()
        text.insert(0, "HACK ")
        v.send_bytes(bytes([0]) + d.get_update(before))
        assert json.loads(v.receive_text())["type"] == "readonly"
    owner.post("/api/team/stop")
    assert "HACK" not in pmdoc.doc_text(ctx.project.get_chapter(cid)["doc"])


def test_events_websocket_and_chat(app, ctx, owner, project):
    info, port = start_team(owner)
    g = guest_client(app, port)
    join(g, info["roomCode"], "correct horse battery", "Deniz")
    import json

    with owner.websocket_connect(f"ws://127.0.0.1:{LOCAL_PORT}/ws/events", headers={"origin": f"http://127.0.0.1:{LOCAL_PORT}"}) as ev:
        assert g.post("/api/chat", json={"text": "hello <script>"}).status_code == 200
        msgs = []
        for _ in range(5):
            m = json.loads(ev.receive_text())
            msgs.append(m)
            if m["type"] == "chat":
                break
        chat = next(m for m in msgs if m["type"] == "chat")
        assert chat["message"]["from"] == "Deniz" and chat["message"]["text"] == "hello <script>"
    owner.post("/api/team/stop")


def test_second_owner_login_still_works(app, ctx, owner):
    c2 = owner_client(app, ctx)
    assert c2.get("/api/me").json()["role"] == "owner"


def test_voice_websocket_with_stub_engine(app, ctx, owner, project):
    """The dictation channel relays engine events and turns transcripts into commands (owner only)."""
    import json
    import threading

    class StubVoice:
        running = False

        def start(self, model, language, prompt, emit):
            assert model == ctx.config.settings.voice.model
            self.running = True
            threading.Thread(target=lambda: (emit({"type": "status", "state": "listening"}), emit({"type": "final", "text": "Hello there new paragraph general"})), daemon=True).start()

        def stop(self):
            self.running = False

    ctx.voice = StubVoice()
    with owner.websocket_connect(f"ws://127.0.0.1:{LOCAL_PORT}/ws/voice", headers={"origin": f"http://127.0.0.1:{LOCAL_PORT}"}) as ws:
        ws.send_text(json.dumps({"cmd": "start"}))
        assert json.loads(ws.receive_text()) == {"type": "status", "state": "listening"}
        final = json.loads(ws.receive_text())
        assert final["segments"] == [{"text": "Hello there"}, {"cmd": "paragraph"}, {"text": "general"}]
        ws.send_text(json.dumps({"cmd": "stop"}))
    assert ctx.voice.running is False

    # Guests can never reach the host's microphone.
    info, port = start_team(owner)
    g = guest_client(app, port)
    join(g, info["roomCode"], "correct horse battery")
    from starlette.websockets import WebSocketDisconnect

    try:
        with g.websocket_connect(f"ws://192.168.50.1:{port}/ws/voice", headers={"origin": f"http://192.168.50.1:{port}"}) as ws:
            ws.receive_text()
        raise AssertionError("guest reached /ws/voice")
    except WebSocketDisconnect as e:
        assert e.code == 4001
    owner.post("/api/team/stop")
