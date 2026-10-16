"""Teamwork mode: a second, network-facing listener protected by a room code + password."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import ipaddress
import logging
import secrets
import socket
import time
from pathlib import Path
from typing import Any, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

log = logging.getLogger("scribeos.team")

WORDS = (
    "amber", "atlas", "birch", "cedar", "comet", "coral", "dawn", "delta", "ember", "fable", "fern", "flint",
    "frost", "harbor", "ink", "iris", "jade", "lark", "lotus", "lunar", "maple", "meadow", "mist", "moon",
    "nova", "oak", "opal", "orbit", "otter", "pearl", "pine", "quill", "raven", "reed", "river", "sage",
    "scroll", "shadow", "solar", "spark", "stone", "storm", "swift", "tide", "vale", "willow", "wren", "zephyr",
)

_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


def generate_password() -> str:
    """A readable passphrase with ~60 bits of entropy: e.g. ``quill-otter-river-4821-sage``."""
    words = [secrets.choice(WORDS) for _ in range(4)]
    return f"{words[0]}-{words[1]}-{words[2]}-{secrets.randbelow(10000):04d}-{words[3]}"


def generate_room_code() -> str:
    return f"{secrets.choice(WORDS).upper()}-{secrets.choice(WORDS).upper()}-{secrets.randbelow(100):02d}"


def lan_addresses() -> list[str]:
    """Best-effort list of this machine's LAN IPv4 addresses, primary first."""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # no packets are sent for UDP connect
            found.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        import ifaddr

        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str):
                    addr = ipaddress.ip_address(ip.ip)
                    if not (addr.is_loopback or addr.is_link_local) and ip.ip not in found:
                        found.append(ip.ip)
    except Exception:
        pass
    return [a for a in found if a != "0.0.0.0"] or ["127.0.0.1"]  # nosec B104  # noqa: S104 - a comparison, not a bind


def qr_svg(data: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")


def ensure_certificate(tls_dir: Path, ips: list[str]) -> tuple[Path, Path, str]:
    """Create (or reuse) a self-signed certificate valid for the LAN addresses."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_p, key_p = tls_dir / "team-cert.pem", tls_dir / "team-key.pem"
    wanted = sorted(set(ips) | {"127.0.0.1"})
    if cert_p.exists() and key_p.exists():
        cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = sorted(str(i) for i in san.get_values_for_type(x509.IPAddress))
        except x509.ExtensionNotFound:
            have = []
        if have == wanted and cert.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7):
            return cert_p, key_p, cert.fingerprint(hashes.SHA256()).hex(":").upper()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ScribeOS Teamwork")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in wanted]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_p, key_p, cert.fingerprint(hashes.SHA256()).hex(":").upper()


class TeamSession:
    def __init__(self, port: int, default_role: str, guest_upload_mb: int, https: bool, password: Optional[str] = None):
        self.port = port
        self.default_role = default_role
        self.guest_upload_mb = guest_upload_mb
        self.https = https
        self.room_code = generate_room_code()
        self.password = password or generate_password()
        self._hash = _hasher.hash(self.password)
        self.locked = False
        self.started_at = time.time()
        self.epoch = secrets.token_hex(8)
        self.addresses = lan_addresses()
        self.fingerprint: Optional[str] = None
        self.server: Any = None
        self.task: Optional[asyncio.Task] = None
        self.chat: list[dict] = []

    # -------------------------------------------------------------- credentials
    def set_password(self, password: str) -> None:
        self.password = password
        self._hash = _hasher.hash(password)
        self.epoch = secrets.token_hex(8)  # invalidates every guest session

    def verify(self, room: str, password: str) -> bool:
        ok_room = secrets.compare_digest(hashlib.sha256(room.strip().upper().encode()).digest(), hashlib.sha256(self.room_code.encode()).digest())
        try:
            ok_pw = _hasher.verify(self._hash, password)
        except (VerificationError, InvalidHashError):
            ok_pw = False
        return ok_room and ok_pw

    # -------------------------------------------------------------- info
    @property
    def scheme(self) -> str:
        return "https" if self.https else "http"

    def urls(self) -> list[str]:
        return [f"{self.scheme}://{ip}:{self.port}/join?room={self.room_code}" for ip in self.addresses]

    def info(self, include_secret: bool) -> dict:
        d = {
            "active": True,
            "port": self.port,
            "roomCode": self.room_code,
            "urls": self.urls(),
            "locked": self.locked,
            "defaultRole": self.default_role,
            "https": self.https,
            "fingerprint": self.fingerprint,
            "startedAt": self.started_at,
            "guestMaxUploadMB": self.guest_upload_mb,
        }
        if include_secret:
            d["password"] = self.password
            d["qr"] = qr_svg(self.urls()[0])
        return d

    # -------------------------------------------------------------- server
    async def start(self, app: Any, tls_dir: Path) -> None:
        import uvicorn

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        try:
            # Intentional: Teamwork is an explicit, password-protected opt-in for LAN guests.
            sock.bind(("0.0.0.0", self.port))  # nosec B104  # noqa: S104
        except OSError as e:
            sock.close()
            raise RuntimeError(f"Port {self.port} is already in use. Choose another port in Settings → Teamwork.") from e
        sock.listen(128)
        sock.setblocking(False)
        kwargs: dict[str, Any] = {}
        if self.https:
            cert, key, fp = ensure_certificate(tls_dir, self.addresses)
            self.fingerprint = fp
            kwargs.update(ssl_certfile=str(cert), ssl_keyfile=str(key))
        config = uvicorn.Config(app, lifespan="off", log_level="warning", proxy_headers=False, server_header=False, ws_max_size=MAX_WS, **kwargs)
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]))
        for _ in range(100):
            if self.server.started or self.task.done():
                break
            await asyncio.sleep(0.02)
        if self.task.done():
            exc = self.task.exception()
            raise RuntimeError(f"Teamwork server failed to start: {exc}")

    async def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.task:
            try:
                await asyncio.wait_for(self.task, 5)
            except (asyncio.TimeoutError, Exception):
                self.task.cancel()
        self.server = None
        self.task = None


MAX_WS = 17 * 1024 * 1024
