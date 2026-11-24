"""Persist the app's own log records so the UI can show them (Logs page).

The handler only queues records; a background thread writes them through an engine
of its own. Writing inline would run while the caller's transaction is still open:
on SQLite it waits for the caller's own write lock (the app freezes ~5 s and the line
is lost) and with an in-memory database (StaticPool, one shared connection) it would
commit or roll back the caller's unfinished work. With an in-memory database the logs
therefore live in a separate in-memory database of their own.
"""

from __future__ import annotations

import logging
import queue
import threading
import traceback
from datetime import datetime, timezone

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import is_memory, make_engine
from .models import LogEntry

KEEP = 5000  # ponytail: row cap pruned every ~200 inserts; add a time-based retention if needed

_queue: queue.Queue = queue.Queue(maxsize=10_000)
_engine = None
_lock = threading.Lock()


class DBLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _queue.put_nowait({
                "created_at": datetime.fromtimestamp(record.created, timezone.utc),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage()[:4000],
                "detail": "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None,
            })
        except Exception:  # queue full or unformattable record: logging must never break the app
            pass


def _writer() -> None:
    since_prune = 0
    while True:
        rows = [_queue.get()]
        while True:  # write whatever is already queued in one transaction
            try:
                rows.append(_queue.get_nowait())
            except queue.Empty:
                break
        try:
            with _engine.begin() as conn:
                conn.execute(insert(LogEntry), rows)
                since_prune += len(rows)
                if since_prune >= 200:
                    since_prune = 0
                    cutoff = conn.scalar(select(LogEntry.id).order_by(LogEntry.id.desc()).offset(KEEP).limit(1))
                    if cutoff:
                        conn.execute(delete(LogEntry).where(LogEntry.id <= cutoff))
        except Exception:  # DB unavailable: drop this batch
            pass
        finally:
            for _ in rows:
                _queue.task_done()


def flush() -> None:
    """Wait until every queued record is written (the Logs API reads after this)."""
    if _engine is not None:
        _queue.join()


def session() -> Session | None:
    return Session(_engine) if _engine is not None else None


def install() -> None:
    global _engine
    with _lock:
        if _engine is None:
            url = get_settings().database_url
            _engine = make_engine(url)
            if is_memory(url):
                LogEntry.__table__.create(_engine, checkfirst=True)
            threading.Thread(target=_writer, name="log-writer", daemon=True).start()
    root = logging.getLogger("changelog_watcher")
    if not any(isinstance(h, DBLogHandler) for h in root.handlers):
        root.addHandler(DBLogHandler())
