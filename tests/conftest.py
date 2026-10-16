import socket
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from starlette.testclient import TestClient  # noqa: E402

from scribeos.config import Paths  # noqa: E402
from scribeos.server.context import AppContext  # noqa: E402
from scribeos.server.main import create_app  # noqa: E402

LOCAL_PORT = 47123


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(tmp_path / "home")


@pytest.fixture
def ctx(paths: Paths) -> AppContext:
    c = AppContext(paths)
    c.local_port = LOCAL_PORT
    c.config.update({"storage": {"projectsDir": str(paths.documents / "ScribeOS")}})
    return c


@pytest.fixture
def app(ctx):
    return create_app(ctx)


def owner_client(app, ctx) -> TestClient:
    c = TestClient(app, base_url=f"http://127.0.0.1:{LOCAL_PORT}", client=("127.0.0.1", 50000))
    c.__enter__()
    r = c.get(f"/auth?t={ctx.local_token}", follow_redirects=False)
    assert r.status_code == 303, r.text
    me = c.get("/api/me").json()
    c.headers["X-CSRF-Token"] = me["csrf"]
    return c


@pytest.fixture
def owner(app, ctx):
    c = owner_client(app, ctx)
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def project(owner):
    r = owner.post("/api/projects", json={"name": "Test Novel"})
    assert r.status_code == 200, r.text
    return r.json()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def guest_client(app, team_port: int, ip: str = "192.168.50.20") -> TestClient:
    c = TestClient(app, base_url=f"http://192.168.50.1:{team_port}", client=(ip, 40000))
    c.__enter__()
    return c


def join(c: TestClient, room: str, password: str, name: str = "Guest") -> dict:
    r = c.post("/api/team/join", json={"room": room, "name": name, "password": password})
    assert r.status_code == 200, r.text
    me = c.get("/api/me").json()
    c.headers["X-CSRF-Token"] = me["csrf"]
    return me
