"""Sessions, roles, CSRF, rate limiting and HTTP security headers."""

from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException, Request, WebSocket
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

COOKIE = "scribeos_sid"
ROLES = {"viewer": 0, "commenter": 1, "editor": 2, "owner": 3}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
GUEST_TTL = 12 * 3600
COLORS = ["#f59e0b", "#22c55e", "#3b82f6", "#ec4899", "#a855f7", "#14b8a6", "#ef4444", "#eab308", "#06b6d4", "#f97316"]

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'; worker-src 'self' blob:"
)
ASSET_CSP = "default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; sandbox"


@dataclass
class Session:
    sid: str
    role: str
    name: str
    color: str
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    created: float = field(default_factory=time.time)
    expires: Optional[float] = None
    team_epoch: Optional[str] = None
    ip: str = ""
    # Public identifier shown to other members; unrelated to the secret session id.
    pub_id: str = field(default_factory=lambda: secrets.token_hex(6))

    def public(self) -> dict:
        return {"id": self.pub_id, "name": self.name, "role": self.role, "color": self.color}


class SessionStore:
    def __init__(self, secret: str):
        self._signer = URLSafeSerializer(secret, salt="scribeos-session")
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, role: str, name: str, ip: str, epoch: Optional[str] = None, ttl: Optional[float] = None) -> tuple[Session, str]:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            used = {s.color for s in self._sessions.values()}
            color = next((c for c in COLORS if c not in used), secrets.choice(COLORS))
            s = Session(sid=sid, role=role, name=name, color=color, expires=(time.time() + ttl) if ttl else None, team_epoch=epoch, ip=ip)
            self._sessions[sid] = s
        return s, self._signer.dumps(sid)

    def resolve(self, cookie: Optional[str]) -> Optional[Session]:
        if not cookie or len(cookie) > 512:
            return None
        try:
            sid = self._signer.loads(cookie)
        except BadSignature:
            return None
        s = self._sessions.get(sid)
        if s and s.expires and s.expires < time.time():
            self.remove(sid)
            return None
        return s

    def remove(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def guests(self) -> list[Session]:
        return [s for s in self._sessions.values() if s.role != "owner"]

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def drop_guests(self) -> list[Session]:
        with self._lock:
            gone = [s for s in self._sessions.values() if s.role != "owner"]
            for s in gone:
                del self._sessions[s.sid]
        return gone


class RateLimiter:
    """Sliding-window limiter keyed by client address."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self.hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self.hits) > 10000:
                for k in [k for k, v in self.hits.items() if not v]:
                    del self.hits[k]
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self.hits.pop(key, None)


def client_ip(conn: Request | WebSocket) -> str:
    return conn.client.host if conn.client else ""


def is_loopback(conn: Request | WebSocket) -> bool:
    return client_ip(conn) in LOOPBACK


def session_valid(ctx, s: Optional[Session], conn: Request | WebSocket) -> bool:
    if s is None:
        return False
    if s.role == "owner":
        # The owner cookie is only honoured from this machine.
        return is_loopback(conn)
    team = ctx.team
    return team is not None and s.team_epoch == team.epoch


def get_session(ctx, conn: Request | WebSocket) -> Optional[Session]:
    s = ctx.sessions.resolve(conn.cookies.get(COOKIE))
    return s if session_valid(ctx, s, conn) else None


def require(ctx, request: Request, min_role: str = "viewer") -> Session:
    s = get_session(ctx, request)
    if s is None:
        raise HTTPException(401, "Not signed in")
    if ROLES[s.role] < ROLES[min_role]:
        raise HTTPException(403, "Your role does not allow this")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        token = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(token, s.csrf):
            raise HTTPException(403, "CSRF check failed")
    return s


def same_origin_ws(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    host = ws.headers.get("host")
    if not origin or not host:
        return False
    return origin.split("://", 1)[-1].rstrip("/") == host


def host_allowed(ctx, conn: Request | WebSocket) -> bool:
    """On the private (localhost) listener only loopback Host headers are accepted (anti DNS-rebinding)."""
    server = conn.scope.get("server") or ("", 0)
    if server and server[1] == ctx.local_port:
        host = (conn.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        return host in LOOPBACK
    return True


class SecurityMiddleware(BaseHTTPMiddleware):
    """Host-header check on the private listener plus strict response headers."""

    def __init__(self, app, ctx):
        super().__init__(app)
        self.ctx = ctx

    async def dispatch(self, request: Request, call_next):
        if not host_allowed(self.ctx, request):
            return PlainTextResponse("Forbidden host", status_code=403)
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
        if request.url.path.startswith("/api/assets/") or request.url.path.startswith("/api/fonts/file/"):
            h["Content-Security-Policy"] = ASSET_CSP
        else:
            h.setdefault("Content-Security-Policy", CSP)
        if request.url.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        return response
