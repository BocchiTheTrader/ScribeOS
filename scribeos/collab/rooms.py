"""Real-time collaboration rooms (one Yjs document per chapter).

Wire protocol on ``/ws/collab/{chapterId}``:

* binary frame ``0x00 + update``     Yjs document update (both directions)
* binary frame ``0x01 + awareness``  y-protocols awareness update (both directions)
* text frames (JSON)                 control: ``init``, ``hello``, ``left``, ``peer-joined``, ``reset``, ``readonly``

The server owns the authoritative Yjs document: it seeds it from the stored chapter,
applies every editor's update, and writes the merged result back to disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
from typing import Any, Awaitable, Callable, Optional

from pycrdt import Doc

from . import ydoc as yconv

log = logging.getLogger("scribeos.collab")

MSG_UPDATE = 0
MSG_AWARENESS = 1
MAX_UPDATE = 16 * 1024 * 1024
MAX_AWARENESS = 64 * 1024
QUEUE_LIMIT = 2000
PERSIST_DELAY = 1.2


class Client:
    """A websocket with its own outgoing queue so one slow peer can't stall the others."""

    def __init__(self, ws: Any, session: Any):
        self.ws = ws
        self.session = session
        self.client_id: Optional[int] = None
        self.queue: asyncio.Queue = asyncio.Queue(QUEUE_LIMIT)
        self.loop = asyncio.get_running_loop()
        self.task: Optional[asyncio.Task] = None
        self.closed = False

    def start(self) -> None:
        self.task = asyncio.create_task(self._sender())

    async def _sender(self) -> None:
        try:
            while True:
                item = await self.queue.get()
                if item is None:
                    break
                if isinstance(item, bytes):
                    await self.ws.send_bytes(item)
                else:
                    await self.ws.send_text(item)
        except Exception:
            pass
        finally:
            self.closed = True

    def send(self, item: bytes | str | dict) -> None:
        """Queue a message. Safe to call from any thread or event loop."""
        if self.closed:
            return
        if isinstance(item, dict):
            item = json.dumps(item)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._enqueue(item)
        else:
            try:
                self.loop.call_soon_threadsafe(self._enqueue, item)
            except RuntimeError:  # loop closed
                self.closed = True

    def _enqueue(self, item: bytes | str) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.closed = True
            asyncio.ensure_future(self.close(1013))

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        try:
            await self.ws.close(code)
        except Exception:
            pass

    async def stop(self) -> None:
        if self.task:
            try:
                self.queue.put_nowait(None)
            except asyncio.QueueFull:
                self.task.cancel()
            try:
                await asyncio.wait_for(self.task, 2)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self.task.cancel()


PersistFn = Callable[[str, dict], Awaitable[None]]


class Room:
    def __init__(self, chapter_id: str, doc_json: dict, persist: PersistFn):
        self.chapter_id = chapter_id
        self.id = secrets.token_hex(8)
        self.ydoc: Doc = yconv.doc_to_ydoc(doc_json)
        self.clients: list[Client] = []
        self.persist_fn = persist
        self.dirty = False
        self._persist_handle: Optional[asyncio.TimerHandle] = None
        self._persist_task: Optional[asyncio.Task] = None

    def state(self) -> bytes:
        return self.ydoc.get_update()

    def others(self, me: Client) -> list[Client]:
        return [c for c in self.clients if c is not me]

    def apply(self, update: bytes) -> None:
        self.ydoc.apply_update(update)
        self.dirty = True
        self._schedule_persist()

    def _schedule_persist(self) -> None:
        loop = asyncio.get_running_loop()
        if self._persist_handle:
            self._persist_handle.cancel()
        self._persist_handle = loop.call_later(PERSIST_DELAY, lambda: asyncio.ensure_future(self.persist()))

    async def persist(self) -> None:
        if not self.dirty:
            return
        self.dirty = False
        doc = yconv.ydoc_to_doc(self.ydoc)
        try:
            # Shielded: a client disconnecting mid-save must not abort the write.
            await asyncio.shield(self.persist_fn(self.chapter_id, doc))
        except asyncio.CancelledError:
            self.dirty = True
            raise
        except Exception:
            log.exception("failed to persist room %s", self.chapter_id)
            self.dirty = True


class RoomManager:
    def __init__(self, load: Callable[[str], dict], persist: PersistFn):
        self.rooms: dict[str, Room] = {}
        self.load = load
        self.persist = persist
        self.lock = threading.Lock()

    async def get(self, chapter_id: str) -> Room:
        room = self.rooms.get(chapter_id)
        if room is not None:
            return room
        doc = await asyncio.to_thread(self.load, chapter_id)
        with self.lock:
            # Another connection may have created the room meanwhile; keep the first one.
            return self.rooms.setdefault(chapter_id, Room(chapter_id, doc, self.persist))

    async def handle(self, ws: Any, session: Any, chapter_id: str, can_edit: Callable[[], bool]) -> None:
        room = await self.get(chapter_id)
        client = Client(ws, session)
        client.start()
        room.clients.append(client)
        client.send({"type": "init", "room": room.id, "role": session.role, "readonly": not can_edit()})
        client.send(bytes([MSG_UPDATE]) + room.state())
        for other in room.others(client):
            other.send({"type": "peer-joined"})
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is not None:
                    if not data:
                        continue
                    kind, payload = data[0], data[1:]
                    if kind == MSG_UPDATE:
                        if len(payload) > MAX_UPDATE:
                            await client.close(1009)
                            break
                        if not can_edit():
                            client.send({"type": "readonly"})
                            continue
                        try:
                            room.apply(payload)
                        except Exception:
                            await client.close(1003)
                            break
                        for other in room.others(client):
                            other.send(data)
                    elif kind == MSG_AWARENESS:
                        if len(payload) > MAX_AWARENESS:
                            continue
                        for other in room.others(client):
                            other.send(data)
                    continue
                text = msg.get("text")
                if text and len(text) < 4096:
                    try:
                        obj = json.loads(text)
                    except ValueError:
                        continue
                    if obj.get("type") == "hello" and isinstance(obj.get("clientId"), int):
                        client.client_id = obj["clientId"]
        finally:
            if client in room.clients:
                room.clients.remove(client)
            await client.stop()
            if client.client_id is not None:
                for other in room.clients:
                    other.send({"type": "left", "clientId": client.client_id})
            if not room.clients:
                await room.persist()

    async def reset(self, chapter_id: str) -> None:
        """Drop a room after its chapter was changed outside the room (restore, replace...)."""
        with self.lock:
            room = self.rooms.pop(chapter_id, None)
        if room:
            if room._persist_handle:
                room._persist_handle.cancel()
            for c in list(room.clients):
                c.send({"type": "reset"})

    async def flush_all(self) -> None:
        for room in list(self.rooms.values()):
            if room._persist_handle:
                room._persist_handle.cancel()
            await room.persist()

    async def close_all(self, reason: str = "team-stopped") -> None:
        await self.flush_all()
        with self.lock:
            rooms = list(self.rooms.values())
            self.rooms.clear()
        for room in rooms:
            for c in list(room.clients):
                c.send({"type": "reset", "reason": reason})
                await c.close(1001)

    async def kick(self, sid: str) -> None:
        for room in list(self.rooms.values()):
            for c in list(room.clients):
                if c.session.sid == sid:
                    await c.close(4001)

    def presence(self) -> dict[str, list[str]]:
        return {cid: [c.session.sid for c in r.clients] for cid, r in self.rooms.items()}
