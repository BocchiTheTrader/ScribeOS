"""Content-addressed media storage (images, GIFs, audio, video)."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import AsyncIterator, Iterable

ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpg|gif|webp|avif|svg|mp4|webm|mp3|ogg|wav|m4a)$")

MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "svg": "image/svg+xml",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
}
KIND = {ext: mime.split("/")[0] for ext, mime in MIME.items()}


class AssetError(ValueError):
    pass


class TooLarge(AssetError):
    pass


def sniff(head: bytes) -> str | None:
    """Detect the real file type from its first bytes. Extensions are never trusted."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand in (b"M4A ", b"M4B "):
            return "m4a"
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith(b"<svg") or (text.startswith(b"<?xml") and b"<svg" in head.lower()):
        return "svg"
    return None


class AssetStore:
    def __init__(self, root: Path):
        self.root = root
        self.thumbs = root / "thumbs"

    def path(self, name: str) -> Path:
        if not ASSET_NAME_RE.match(name):
            raise AssetError("invalid asset name")
        return self.root / name

    def exists(self, name: str) -> bool:
        return ASSET_NAME_RE.match(name) is not None and (self.root / name).is_file()

    async def save_stream(self, chunks: AsyncIterator[bytes], max_bytes: int, allowed_kinds: Iterable[str] = ("image", "video", "audio")) -> dict:
        """Stream an upload to disk while hashing it; never holds the whole file in memory."""
        self.root.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        size = 0
        head = b""
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".upload-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise TooLarge(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
                    if len(head) < 64:
                        head += chunk[: 64 - len(head)]
                    h.update(chunk)
                    f.write(chunk)
            return self._finish(tmp, h.hexdigest(), head, size, allowed_kinds)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def save_bytes(self, data: bytes, allowed_kinds: Iterable[str] = ("image", "video", "audio")) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".upload-", suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            return self._finish(tmp, hashlib.sha256(data).hexdigest(), data[:64], len(data), allowed_kinds)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _finish(self, tmp: str, digest: str, head: bytes, size: int, allowed_kinds: Iterable[str]) -> dict:
        ext = sniff(head)
        if size == 0 or ext is None:
            raise AssetError("Unsupported or unrecognised file type")
        if KIND[ext] not in set(allowed_kinds):
            raise AssetError(f"{KIND[ext]} files are not allowed here")
        name = f"{digest}.{ext}"
        dest = self.root / name
        if dest.exists():
            os.unlink(tmp)
        else:
            os.replace(tmp, dest)
        info = {"name": name, "src": f"/api/assets/{name}", "kind": KIND[ext], "mime": MIME[ext], "size": size}
        if KIND[ext] == "image" and ext != "svg":
            info.update(self._image_info(dest))
        return info

    def _image_info(self, path: Path) -> dict:
        try:
            from PIL import Image

            with Image.open(path) as im:
                return {"width": im.width, "height": im.height, "animated": bool(getattr(im, "is_animated", False))}
        except Exception:
            return {}

    def thumbnail(self, name: str, size: int = 320) -> Path | None:
        """Return a cached PNG/JPEG thumbnail (first frame for GIFs)."""
        src = self.path(name)
        ext = name.rsplit(".", 1)[1]
        if KIND.get(ext) != "image" or ext == "svg" or not src.exists():
            return None
        out = self.thumbs / f"{name.rsplit('.', 1)[0]}_{size}.png"
        if out.exists():
            return out
        from PIL import Image

        self.thumbs.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(src) as im:
                im.seek(0)
                frame = im.convert("RGBA")
                frame.thumbnail((size, size))
                frame.save(out, "PNG")
        except Exception:
            return None
        return out

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        out = []
        for p in sorted(self.root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and ASSET_NAME_RE.match(p.name):
                ext = p.suffix[1:]
                out.append({"name": p.name, "src": f"/api/assets/{p.name}", "kind": KIND[ext], "size": p.stat().st_size})
        return out

    def delete(self, name: str) -> None:
        p = self.path(name)
        if p.exists():
            p.unlink()
        stem = name.rsplit(".", 1)[0]
        if self.thumbs.exists():
            for t in self.thumbs.glob(f"{stem}_*.png"):
                t.unlink()
