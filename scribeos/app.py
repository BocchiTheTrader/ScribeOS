"""Desktop entry point: start the private API server, then open the native window."""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from . import APP_NAME, __version__
from .config import Paths
from .server.context import AppContext
from .server.main import create_app

log = logging.getLogger("scribeos")


def _free_socket(port: int = 0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    sock.setblocking(False)
    return sock


class ServerThread(threading.Thread):
    def __init__(self, ctx: AppContext, port: int = 0):
        super().__init__(name="scribeos-server", daemon=True)
        self.ctx = ctx
        self.sock = _free_socket(port)
        ctx.local_port = self.sock.getsockname()[1]
        app = create_app(ctx)
        from .collab.team import MAX_WS

        config = uvicorn.Config(app, log_level="warning", proxy_headers=False, server_header=False, ws_max_size=MAX_WS, lifespan="on")
        self.server = uvicorn.Server(config)

    def run(self) -> None:
        asyncio.run(self.server.serve(sockets=[self.sock]))

    def wait_started(self, timeout: float = 15) -> None:
        end = time.time() + timeout
        while not self.server.started:
            if time.time() > end or not self.is_alive():
                raise RuntimeError("ScribeOS server failed to start")
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.join(10)


def _reopen_last(ctx: AppContext) -> None:
    for p in ctx.config.settings.recentProjects[:1]:
        try:
            ctx.open_project(Path(p))
        except Exception as e:
            log.info("could not reopen %s: %s", p, e)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scribeos", description=f"{APP_NAME} {__version__}")
    ap.add_argument("--browser", action="store_true", help="open in the default web browser instead of a desktop window")
    ap.add_argument("--port", type=int, default=0, help="port for the private (localhost) server")
    ap.add_argument("--data-dir", type=Path, help="keep settings and data in this folder (portable mode)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--self-test", metavar="REPORT.json", type=Path, help="run a diagnostic check in a temporary folder, write a JSON report and exit")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "httpcore", "huggingface_hub", "faster_whisper", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.DEBUG if args.debug else logging.WARNING)
    if args.self_test:
        return self_test(args.self_test)

    ctx = AppContext(Paths(args.data_dir) if args.data_dir else None, dev=args.debug)
    server = ServerThread(ctx, args.port)
    server.start()
    server.wait_started()
    _reopen_last(ctx)
    ctx.start_background()
    url = f"http://127.0.0.1:{ctx.local_port}/auth?t={ctx.local_token}"

    try:
        if args.browser:
            print(f"{APP_NAME} is running at http://127.0.0.1:{ctx.local_port}  (Ctrl+C to quit)")
            webbrowser.open(url)
            while server.is_alive():
                time.sleep(0.5)
        else:
            _run_window(ctx, url, args.debug)
    except KeyboardInterrupt:
        pass
    finally:
        ctx.shutdown()
        server.stop()
    return 0


def _run_window(ctx: AppContext, url: str, debug: bool) -> None:
    import webview

    from .bridge import Bridge

    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_DOWNLOADS"] = True
    geo = ctx.config.settings.window or {}
    window = webview.create_window(
        APP_NAME,
        url,
        js_api=Bridge(ctx),
        width=int(geo.get("width", 1280)),
        height=int(geo.get("height", 820)),
        x=geo.get("x"),
        y=geo.get("y"),
        min_size=(720, 480),
        background_color="#000000",
        text_select=True,
        maximized=bool(geo.get("maximized", False)),
    )
    ctx.window = window

    def on_closing():
        try:
            if window.width and window.height:
                ctx.config.settings.window = {"width": window.width, "height": window.height, "x": window.x, "y": window.y}
                ctx.config.save()
        except Exception:
            pass

    window.events.closing += on_closing
    icon = Path(__file__).resolve().parent / "assets" / "icon.png"
    webview.start(debug=debug, private_mode=False, storage_path=str(ctx.paths.webview_storage), icon=str(icon) if icon.exists() else None)


def self_test(report: Path) -> int:
    """Exercise the main subsystems in a throwaway data folder (also works in the frozen .exe)."""
    import asyncio as _asyncio
    import json
    import tempfile
    import traceback

    results: dict = {"version": __version__}

    def check(name, fn):
        try:
            results[name] = fn() or "ok"
        except Exception as e:  # report, don't crash
            results[name] = f"FAIL: {e!r}"
            results.setdefault("_tracebacks", {})[name] = traceback.format_exc()

    with tempfile.TemporaryDirectory(prefix="scribeos-selftest-") as tmp:
        base = Path(tmp)
        ctx = AppContext(Paths(base))
        server = ServerThread(ctx)
        server.start()
        server.wait_started()
        check("project", lambda: ctx.create_project(base / "Projects", "Self Test").summary()["name"])

        def save():
            from .storage import pmdoc

            cid = ctx.project.meta.tree[0].id
            return f"{ctx.project.save_chapter(cid, pmdoc.text_to_doc('Self test words here'))['wordCount']} words"

        check("save", save)

        def exports():
            from .export.common import ExportOptions
            from .export.service import export

            return {f: len(export(ctx.project, ExportOptions(format=f), ctx.font_dirs())[0]) for f in ("pdf", "docx", "epub", "md", "html", "txt")}

        check("export", exports)

        def team():
            from .collab.team import TeamSession

            async def run():
                t = TeamSession(_free_port(), "editor", 25, False)
                await t.start(ctx.app, ctx.paths.tls_dir)
                await t.stop()

            _asyncio.run_coroutine_threadsafe(run(), ctx.loop).result(20)

        check("teamwork_listener", team)

        def voice():
            import os

            from .voice.engine import VoiceService, availability

            info = availability()
            wav = os.environ.get("SCRIBEOS_SELFTEST_WAV")
            if info["available"] and wav:
                import wave

                import numpy as np

                with wave.open(wav) as w:
                    audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768
                info["transcript"] = VoiceService(ctx.paths.models_dir).transcribe(audio, "tiny", "en")
            return info

        check("voice", voice)

        def window():
            import webview

            from .bridge import Bridge

            out: dict = {}

            def probe(w):
                for _ in range(150):
                    time.sleep(0.2)
                    try:
                        if w.evaluate_js("!!document.querySelector('.ProseMirror')"):
                            out["editor"] = True
                            break
                    except Exception:
                        pass
                out["bridge"] = w.evaluate_js("typeof (window.pywebview && window.pywebview.api && window.pywebview.api.export_to_file)")
                w.destroy()

            w = webview.create_window("ScribeOS self-test", f"http://127.0.0.1:{ctx.local_port}/auth?t={ctx.local_token}", js_api=Bridge(ctx), hidden=True)
            ctx.window = w
            webview.start(probe, w, private_mode=True)
            if not out.get("editor"):
                raise RuntimeError(f"editor did not load: {out}")
            return out

        check("window", window)
        ctx.shutdown()
        server.stop()
    results["passed"] = not any(isinstance(v, str) and v.startswith("FAIL") for v in results.values())
    Path(report).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if results["passed"] else 1


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


if __name__ == "__main__":
    sys.exit(main())
