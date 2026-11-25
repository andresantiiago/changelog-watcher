from __future__ import annotations

import asyncio
import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from sqlalchemy import text

from . import __version__
from .api import router
from .config import get_settings
from .db import get_engine, init_engine, is_memory, session_scope
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
    if is_memory(settings.database_url):
        log.warning("banco em memória: os dados serão perdidos ao reiniciar (use um arquivo SQLite em volume persistente para mantê-los)")
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

    has_auth = bool(settings.auth_username and settings.auth_password)
    if not has_auth and not settings.auth_disabled:
        # Fail closed: an open API lets anyone reconfigure the app and reach internal hosts.
        raise RuntimeError("defina AUTH_USERNAME e AUTH_PASSWORD (ou AUTH_DISABLED=true para rodar sem autenticação)")

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        """CSRF: browsers resend Basic Auth credentials on cross-site form posts."""
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "requisição de outra origem bloqueada"}, status_code=403)
        return await call_next(request)

    if has_auth:
        # bytes: compare_digest raises TypeError on non-ASCII str
        expected = (settings.auth_username.encode(), settings.auth_password.encode())
        open_paths = ("/healthz", "/readyz", "/metrics")  # /metrics: METRICS_TOKEN, checked below

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path in open_paths:
                return await call_next(request)
            header = request.headers.get("authorization", "")
            if header.lower().startswith("basic "):
                try:
                    user, _, pwd = base64.b64decode(header[6:]).partition(b":")
                except Exception:
                    user = pwd = b""
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
    def metrics(request: Request):
        if settings.metrics_token:  # app/version inventory: a map of what is outdated
            sent = request.headers.get("authorization", "").removeprefix("Bearer ").encode()
            if not secrets.compare_digest(sent, settings.metrics_token.encode()):
                return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
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
