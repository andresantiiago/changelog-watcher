from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def is_memory(url: str) -> bool:
    return url.startswith("sqlite") and (":memory:" in url or url in ("sqlite://", "sqlite:///"))


def make_engine(url: str):
    """A new SQLite engine for ``url``. For an in-memory URL that is a new, empty database."""
    if not url.startswith("sqlite"):
        raise ValueError(
            f"DATABASE_URL inválida ({url.split(':', 1)[0]}): apenas SQLite é suportado "
            "(sqlite:////data/changelog-watcher.db ou sqlite:///:memory:)"
        )
    kwargs: dict = {"pool_pre_ping": True, "connect_args": {"check_same_thread": False}}
    if is_memory(url):
        from sqlalchemy.pool import StaticPool

        kwargs["poolclass"] = StaticPool
    else:
        os.makedirs(os.path.dirname(os.path.abspath(url.split("///", 1)[-1])), exist_ok=True)
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


def init_engine(url: str | None = None):
    global _engine, _SessionLocal
    _engine = make_engine(url or get_settings().database_url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(_engine)
    _add_missing_columns(_engine)
    return _engine


def _add_missing_columns(engine) -> None:
    """Minimal forward-only migration: add columns introduced by newer versions.

    New columns must be nullable or have a scalar default.
    """
    from sqlalchemy import inspect, literal, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
                default = col.default.arg if col.default is not None and col.default.is_scalar else None
                if default is not None:  # literal rendered by the dialect (e.g. booleans as 1/0)
                    sql = literal(default, col.type).compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True})
                    ddl += f" DEFAULT {sql}"
                conn.execute(text(ddl))
                log.info("added column %s.%s", table.name, col.name)


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        init_engine()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session
