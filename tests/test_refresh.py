import asyncio

import httpx
import respx

from changelog_watcher.db import session_scope
from changelog_watcher.models import Project
from changelog_watcher.refresh import refresh_project_releases, run_refresh


def test_unexpected_source_failure_is_recorded(client):
    pid = client.post("/api/projects?refresh=false", json={"name": "app", "repository": "org/app"}).json()["id"]
    with respx.mock:
        # 200 with an HTML body: resp.json() raises a ValueError, not a SourceError.
        respx.get("https://api.github.com/repos/org/app/releases").mock(return_value=httpx.Response(200, text="<html>"))
        r = asyncio.run(run_refresh([pid], detect=False))
    assert r["projects_failed"] == 1
    with session_scope() as s:
        p = s.get(Project, pid)
        assert p.last_error and p.last_checked_at


def test_deleted_project_is_skipped(client):
    async def go():
        async with httpx.AsyncClient() as c:
            return await refresh_project_releases(999, c)

    assert asyncio.run(go()) == 0
