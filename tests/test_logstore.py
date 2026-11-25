import logging

from changelog_watcher.db import session_scope
from changelog_watcher.models import Environment


def test_log_inside_open_transaction_does_not_touch_it(client):
    """Logging while a session is mid-transaction must not commit/roll back its work."""
    try:
        with session_scope() as s:
            s.add(Environment(name="nao-commitado"))
            s.flush()
            logging.getLogger("changelog_watcher.test").warning("durante a transação")
            raise RuntimeError("desfaz a transação")
    except RuntimeError:
        pass
    assert "nao-commitado" not in [e["name"] for e in client.get("/api/environments").json()]
    logs = client.get("/api/logs?q=DURANTE&level=warning").json()
    assert logs[0]["message"] == "durante a transação"
    assert logs[0]["created_at"].endswith("+00:00")  # UTC offset: the browser shows local time correctly
