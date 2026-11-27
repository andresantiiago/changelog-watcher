"""Keep the app's latest log records in memory so the UI can show them (Logs page)."""

from __future__ import annotations

import logging
import traceback
from collections import deque
from datetime import UTC, datetime

# ponytail: só em memória, perdido ao reiniciar (o histórico fica nos logs do pod);
# persistir se a UI precisar de linhas mais antigas.
RECORDS: deque[dict] = deque(maxlen=5000)


class MemoryLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            RECORDS.append({
                "created_at": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage()[:4000],
                "detail": "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None,
            })
        except Exception:
            self.handleError(record)


def install() -> None:
    root = logging.getLogger("changelog_watcher")
    if not any(isinstance(h, MemoryLogHandler) for h in root.handlers):
        root.addHandler(MemoryLogHandler(logging.INFO))
