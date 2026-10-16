"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import ValidationError

from ..storage.project_store import ProjectError
from .api import build_router
from .context import AppContext
from .security import SecurityMiddleware, get_session
from .ws import build_ws_router

log = logging.getLogger("scribeos")

WEB_DIST = Path(__file__).resolve().parent.parent / "web_dist"


def create_app(ctx: AppContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx.loop = asyncio.get_running_loop()
        yield
        await ctx.rooms.flush_all()

    app = FastAPI(title="ScribeOS", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    ctx.app = app
    app.add_middleware(SecurityMiddleware, ctx=ctx)

    @app.exception_handler(ProjectError)
    async def project_error(request: Request, exc: ProjectError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, exc: ValidationError):
        return JSONResponse({"detail": exc.errors()[0].get("msg", "Invalid data")}, status_code=422)

    app.include_router(build_router(ctx))
    app.include_router(build_ws_router(ctx))

    def page(name: str):
        f = WEB_DIST / name
        if not f.exists():
            return PlainTextResponse("Frontend not built. Run `npm run build` in the web/ folder.", status_code=500)
        return FileResponse(f, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/")
    def index(request: Request):
        if get_session(ctx, request) is None:
            return page("join.html")
        return page("index.html")

    @app.get("/join")
    def join():
        return page("join.html")

    @app.get("/{path:path}")
    def static(path: str):
        if path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        root = WEB_DIST.resolve()
        f = (root / path).resolve()
        if root not in f.parents or not f.is_file() or f.suffix == ".html":
            raise HTTPException(404)
        cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{path}" else "no-cache"
        return FileResponse(f, headers={"Cache-Control": cache})

    return app
