from __future__ import annotations

import asyncio
import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from sqlalchemy import text

from . import __version__
from .api import router
from .config import get_settings
from .db import get_engine, init_engine, session_scope
from . import logstore
from .metrics import WatcherCollector
from .refresh import run_refresh
from .scheduler import start_scheduler, stop_scheduler
from .seed import load_seed

log = logging.getLogger("changelog_watcher")
STATIC = Path(__file__).parent / "static"
_collector: WatcherCollector | None = None


def _register_collector() -> None:
    global _collector
    if _collector is None:
        _collector = WatcherCollector()
        REGISTRY.register(_collector)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_engine()
    logstore.install()
    if settings.config_file:
        with session_scope() as s:
            stats = load_seed(s, settings.config_file, overwrite=settings.seed_overwrite)
            log.info("seed loaded from %s: %s", settings.config_file, stats)
    _register_collector()
    start_scheduler()
    task = None
    if settings.refresh_on_startup:
        task = asyncio.create_task(run_refresh())
    yield
    stop_scheduler()
    if task and not task.done():
        task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title="Changelog Watcher", version=__version__, lifespan=lifespan)
    settings = get_settings()

    if settings.auth_username and settings.auth_password:
        expected = (settings.auth_username, settings.auth_password)
        open_paths = ("/healthz", "/readyz", "/metrics")

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path in open_paths:
                return await call_next(request)
            header = request.headers.get("authorization", "")
            if header.lower().startswith("basic "):
                try:
                    user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
                except Exception:
                    user = pwd = ""
                if secrets.compare_digest(user, expected[0]) and secrets.compare_digest(pwd, expected[1]):
                    return await call_next(request)
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="changelog-watcher"'})

    app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover
            return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    class _Revalidate(StaticFiles):  # SPA has no build hash: make browsers revalidate (ETag -> 304) so UI updates apply on reload
        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            resp.headers["Cache-Control"] = "no-cache"
            return resp

    app.mount("/static", _Revalidate(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
