"""Persist the app's own log records so the UI can show them (Logs page)."""

from __future__ import annotations

import logging
import traceback

from sqlalchemy import delete, select

from .db import session_scope
from .models import LogEntry

KEEP = 5000  # ponytail: row cap pruned every 200 inserts; add a time-based retention if needed


class DBLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self._n = 0
        self._busy = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._busy:  # never log while logging
            return
        self._busy = True
        try:
            detail = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None
            with session_scope() as s:
                s.add(LogEntry(level=record.levelname, logger=record.name, message=record.getMessage()[:4000], detail=detail))
                self._n += 1
                if self._n % 200 == 0:
                    cutoff = s.scalar(select(LogEntry.id).order_by(LogEntry.id.desc()).offset(KEEP))
                    if cutoff:
                        s.execute(delete(LogEntry).where(LogEntry.id <= cutoff))
        except Exception:  # DB not ready/closed: logging must never break the app
            pass
        finally:
            self._busy = False


def install() -> None:
    root = logging.getLogger("changelog_watcher")
    if not any(isinstance(h, DBLogHandler) for h in root.handlers):
        root.addHandler(DBLogHandler())
