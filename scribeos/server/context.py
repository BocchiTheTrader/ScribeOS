"""Process-wide application state shared by the HTTP API, websockets and the desktop bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..collab.rooms import Client, RoomManager
from ..collab.team import TeamSession
from ..config import Config, Paths
from ..storage import backups
from ..storage.project_store import Project, ProjectError
from ..storage.stats import Stats
from .security import SessionStore

log = logging.getLogger("scribeos")


class EventHub:
    """Fan-out of JSON events (tree changed, notes changed, chat...) to every open app."""

    def __init__(self) -> None:
        self.clients: list[Client] = []

    def publish(self, event: dict, exclude: Optional[str] = None, min_role: Optional[str] = None) -> None:
        from .security import ROLES

        data = json.dumps(event)
        for c in list(self.clients):
            if exclude and c.session.sid == exclude:
                continue
            if min_role and ROLES[c.session.role] < ROLES[min_role]:
                continue
            c.send(data)

    async def kick(self, sid: str) -> None:
        for c in list(self.clients):
            if c.session.sid == sid:
                c.send({"type": "kicked"})
                await c.close(4001)


class AppContext:
    def __init__(self, paths: Optional[Paths] = None, dev: bool = False):
        self.paths = paths or Paths()
        self.config = Config(self.paths)
        self.stats = Stats(self.paths.stats_file)
        self.local_token = secrets.token_urlsafe(32)
        self.sessions = SessionStore(secrets.token_urlsafe(32))
        self.local_port = 0
        self.dev = dev
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.project: Optional[Project] = None
        self.project_lock = threading.RLock()
        self.team: Optional[TeamSession] = None
        self.hub = EventHub()
        self.rooms = RoomManager(self._load_chapter, self._persist_room)
        self.voice: Any = None
        self.app: Any = None
        self.window: Any = None
        self._bg_stop = threading.Event()
        self._last_backup = time.time()

    # ------------------------------------------------------------------ helpers
    def publish(self, event: dict, exclude: Optional[str] = None) -> None:
        """Thread-safe event publish (callable from sync route handlers)."""
        if self.loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.hub.publish(event, exclude)
        else:
            self.loop.call_soon_threadsafe(self.hub.publish, event, exclude)

    def require_project(self) -> Project:
        if self.project is None:
            raise ProjectError("No project is open")
        return self.project

    def font_dirs(self) -> list[Path]:
        return [self.paths.fonts_dir]

    # ------------------------------------------------------------------ projects
    def open_project(self, path: Path, force: bool = False) -> Project:
        with self.project_lock:
            p = Project.open(Path(path), force=force)
            self._swap_project(p)
            return p

    def create_project(self, parent: Path, name: str) -> Project:
        with self.project_lock:
            p = Project.create(Path(parent), name, author=self.config.settings.authorName)
            p.acquire_lock()
            self._swap_project(p)
            return p

    def _swap_project(self, p: Project) -> None:
        old = self.project
        if old is not None and old.path != p.path:
            self._backup_if_dirty(old)
            old.close()
        self._flush_rooms_blocking()
        self.project = p
        self.config.add_recent(str(p.path))
        self.publish({"type": "project-changed"})

    def _flush_rooms_blocking(self) -> None:
        """Write pending collaborative edits to the *current* project before it is swapped out.

        Must be called from a worker thread (sync route or desktop bridge), never the event loop.
        """
        if self.loop is None or not self.loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.rooms.close_all("project-changed"), self.loop).result(10)
        except Exception:
            log.exception("could not flush collaboration rooms")

    def flush_rooms(self) -> None:
        """Persist live collaborative edits without closing the rooms (worker threads only)."""
        if self.loop is None or not self.loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.rooms.flush_all(), self.loop).result(10)
        except Exception:
            log.exception("could not flush collaboration rooms")

    def close_project(self) -> None:
        with self.project_lock:
            if self.project is not None:
                self._flush_rooms_blocking()
                self._backup_if_dirty(self.project)
                self.project.close()
                self.project = None
        self.publish({"type": "project-changed"})

    def _backup_if_dirty(self, p: Project) -> None:
        if p.dirty:
            try:
                s = self.config.settings.storage
                backups.make_backup(Path(s.backupDir), p, s.backupKeep)
            except Exception:
                log.exception("backup failed")

    # ------------------------------------------------------------------ collaboration
    def _load_chapter(self, chapter_id: str) -> dict:
        return self.require_project().get_chapter(chapter_id)["doc"]

    async def _persist_room(self, chapter_id: str, doc: dict) -> None:
        project = self.project
        if project is None or project.meta.find_chapter(chapter_id) is None:
            return
        interval = self.config.settings.storage.snapshotIntervalMin * 60
        result = await asyncio.to_thread(project.save_chapter, chapter_id, doc, interval)
        if result["delta"]:
            await asyncio.to_thread(self.stats.record, result["delta"])
        self.hub.publish({"type": "chapter-saved", "id": chapter_id, "wordCount": result["wordCount"]})

    async def stop_team(self) -> None:
        team = self.team
        if team is None:
            return
        await self.rooms.close_all("team-stopped")
        for s in self.sessions.drop_guests():
            await self.hub.kick(s.sid)
        self.team = None
        await team.stop()
        self.hub.publish({"type": "team-changed"})

    # ------------------------------------------------------------------ background work
    def start_background(self) -> None:
        threading.Thread(target=self._background, name="scribeos-bg", daemon=True).start()

    def _background(self) -> None:
        while not self._bg_stop.wait(30):
            p = self.project
            if p is None:
                continue
            p.heartbeat()
            s = self.config.settings.storage
            if p.dirty and time.time() - self._last_backup > s.backupIntervalMin * 60 and time.time() - p.last_change > 5:
                try:
                    backups.make_backup(Path(s.backupDir), p, s.backupKeep)
                    self._last_backup = time.time()
                except Exception:
                    log.exception("periodic backup failed")

    def shutdown(self) -> None:
        self._bg_stop.set()
        if self.loop is not None and self.loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._async_shutdown(), self.loop)
            try:
                fut.result(10)
            except Exception:
                log.exception("shutdown error")
        if self.voice is not None:
            try:
                self.voice.stop()
            except Exception:
                pass
        self.close_project()

    async def _async_shutdown(self) -> None:
        await self.rooms.flush_all()
        await self.stop_team()
