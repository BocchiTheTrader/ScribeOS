"""WebSocket endpoints: app events, live collaboration, dictation."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket

from ..collab.rooms import Client
from ..storage.models import ID_RE
from .context import AppContext
from .security import ROLES, get_session, host_allowed, same_origin_ws


def build_ws_router(ctx: AppContext) -> APIRouter:
    r = APIRouter()

    async def authenticate(ws: WebSocket, min_role: str = "viewer"):
        if not same_origin_ws(ws) or not host_allowed(ctx, ws):
            await ws.close(4003)
            return None
        s = get_session(ctx, ws)
        if s is None or ROLES[s.role] < ROLES[min_role]:
            await ws.close(4001)
            return None
        return s

    @r.websocket("/ws/events")
    async def events(ws: WebSocket):
        s = await authenticate(ws)
        if s is None:
            return
        await ws.accept()
        client = Client(ws, s)
        client.start()
        ctx.hub.clients.append(client)
        ctx.hub.publish({"type": "members-changed"})
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                # Re-validate on every ping so a kicked/expired session drops promptly.
                if get_session(ctx, ws) is None:
                    await client.close(4001)
                    break
                if msg.get("text") == "ping":
                    client.send("pong")
        finally:
            if client in ctx.hub.clients:
                ctx.hub.clients.remove(client)
            await client.stop()
            ctx.hub.publish({"type": "members-changed"})

    @r.websocket("/ws/collab/{chapter_id}")
    async def collab(ws: WebSocket, chapter_id: str):
        s = await authenticate(ws)
        if s is None:
            return
        if ctx.team is None or ctx.project is None or not ID_RE.match(chapter_id) or ctx.project.meta.find_chapter(chapter_id) is None:
            await ws.close(4004)
            return
        await ws.accept()

        def can_edit() -> bool:
            live = get_session(ctx, ws)
            return live is not None and ROLES[live.role] >= ROLES["editor"]

        await ctx.rooms.handle(ws, s, chapter_id, can_edit)

    @r.websocket("/ws/voice")
    async def voice(ws: WebSocket):
        s = await authenticate(ws, "owner")
        if s is None:
            return
        await ws.accept()
        from ..voice import commands
        from ..voice.engine import VoiceService

        if ctx.voice is None:
            ctx.voice = VoiceService(ctx.paths.models_dir)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(500)
        settings = ctx.config.settings.voice

        def emit(event: dict) -> None:  # called from the voice thread
            if event.get("type") == "final":
                event = {"type": "final", "text": event["text"], "segments": commands.parse(event["text"], settings.commands)}
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except (RuntimeError, asyncio.QueueFull):
                pass

        async def pump():
            while True:
                ev = await queue.get()
                await ws.send_text(json.dumps(ev))

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                try:
                    cmd = json.loads(msg.get("text") or "{}")
                except ValueError:
                    continue
                if cmd.get("cmd") == "start":
                    prompt = ""
                    if ctx.project is not None:
                        names = [e["name"] for e in ctx.project.get_codex()][:100]
                        prompt = ", ".join(names)
                    try:
                        ctx.voice.start(settings.model, settings.language, prompt, emit)
                    except RuntimeError as e:
                        emit({"type": "status", "state": "error", "message": str(e)})
                elif cmd.get("cmd") == "stop":
                    await asyncio.to_thread(ctx.voice.stop)
        finally:
            await asyncio.to_thread(ctx.voice.stop)
            pump_task.cancel()

    return r
