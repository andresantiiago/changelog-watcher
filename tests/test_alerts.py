import asyncio
import json

import httpx
import respx
from sqlalchemy import select

from changelog_watcher.alerts import send_alerts
from changelog_watcher.db import session_scope
from changelog_watcher.models import Release


def _send():
    with session_scope() as s:
        return asyncio.run(send_alerts(s))


@respx.mock
def test_alert_rules_fire_once_per_version_and_deployment(client, seeded):
    respx.route(host="testserver").pass_through()
    route = respx.post("https://hooks.test/alerts").mock(return_value=httpx.Response(200))
    with session_scope() as s:
        rel = s.scalar(select(Release).where(Release.tag == "v1.3.1"))
        rel.notes = "Fixes CVE-2026-12345"
    client.post("/api/webhooks", json={"name": "a", "url": "https://hooks.test/alerts", "alert_major": True,
                                       "alert_security": True, "alert_overdue_days": 150,
                                       "environment_ids": [seeded["prod"]["id"]]})
    client.post("/api/webhooks", json={"name": "report-only", "url": "https://hooks.test/report"})

    assert [r["ok"] for r in _send()] == [True]
    alerts = json.loads(route.calls.last.request.content)["alerts"]
    # app-a in prod (1.1.3): major 2.0.0, security fix in 1.3.1, outdated since 1.1.5 (200 days ago)
    assert {(a["project"], a["environment"], a["rule"], a["version"]) for a in alerts} == {
        ("app-a", "producao", "major", "2.0.0"),
        ("app-a", "producao", "security", "1.3.1"),
        ("app-a", "producao", "overdue", "2.0.0"),
    }
    assert _send() == [] and route.call_count == 1  # already sent: no repeat

    with session_scope() as s:  # a newer major is a new alert
        s.add(Release(project_id=seeded["app"]["id"], tag="v3.0.0", url="https://x/v3.0.0"))
    _send()
    assert {(a["rule"], a["version"]) for a in json.loads(route.calls.last.request.content)["alerts"]} == {
        ("major", "3.0.0"), ("overdue", "3.0.0")}


@respx.mock
def test_failed_alert_is_retried_and_chat_format(client, seeded):
    respx.route(host="testserver").pass_through()
    route = respx.post("https://hooks.slack.test/x").mock(side_effect=[httpx.Response(500), httpx.Response(200)])
    client.post("/api/webhooks", json={"name": "s", "kind": "slack", "url": "https://hooks.slack.test/x",
                                       "alert_major": True})
    assert _send()[0]["ok"] is False
    assert _send()[0]["ok"] is True
    text = json.loads(route.calls.last.request.content)["text"]
    assert "*🚨 Changelog Watcher: 2 alert(s)*" in text and "<https://x/v2.0.0|2.0.0>" in text
    hook = client.get("/api/webhooks").json()[0]
    assert hook["last_status"].endswith("(alerts)") and hook["alert_major"] is True
