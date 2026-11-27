import asyncio

import httpx

from changelog_watcher.models import Webhook
from changelog_watcher.webhooks import build_payloads, filter_data, send_webhook


def _data():
    return {
        "period_start": "2026-01-01T00:00:00",
        "period_end": "2026-01-08T00:00:00",
        "environments": [{"id": 1, "name": "prod"}, {"id": 2, "name": "dev"}, {"id": 3, "name": "qa"}],
        "summaries": [],
        "outdated": [],
        "new_releases": [],
        "drift_pairs": [],
        "drift": [
            {
                "project": "app",
                "environments": {
                    "1": {"version": "1.0.0", "status": "minor"},
                    "2": {"version": None, "status": "unknown"},
                    "3": {"version": "1.1.0", "status": "up_to_date"},
                },
            }
        ],
    }


def test_filter_data_drift_ignores_unknown_versions():
    assert filter_data(_data(), [1, 2])["drift"] == []  # 1.0.0 vs "no version" is not drift
    assert len(filter_data(_data(), [1, 3])["drift"]) == 1


def test_send_webhook_invalid_url_is_reported_not_raised():
    hook = Webhook(name="h", kind="generic", url="http://[::1/x", enabled=True, only_outdated=False, environment_ids=[])

    async def run():
        async with httpx.AsyncClient() as client:
            return await send_webhook(hook, _data(), "md", client)

    r = asyncio.run(run())
    assert r["ok"] is False and r["detail"]


def test_discord_payloads_split_under_limit():
    data = _data() | {
        "new_releases": [
            {"project": f"projeto-{i:03d}", "tag": f"v1.{i}.0", "url": "https://x/" + "y" * 60, "level": "minor"}
            for i in range(25)
        ]
    }
    payloads = build_payloads("discord", data, "")
    assert len(payloads) > 1 and all(0 < len(p["content"]) <= 2000 for p in payloads)


def test_google_chat_and_generic_payloads():
    data = _data() | {"new_releases": [{"project": "app", "tag": "v2.0.0", "url": "https://x/v2", "level": "major"}]}
    [chat] = build_payloads("google_chat", data, "md")
    assert "*📦" in chat["text"] and "<https://x/v2|v2.0.0>" in chat["text"]  # Slack-style markup
    [generic] = build_payloads("generic", data, "md")
    assert generic == {"event": "changelog_watcher.report", "markdown": "md", "report": data}
