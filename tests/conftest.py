import os
from datetime import UTC, datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("REFRESH_ON_STARTUP", "false")
os.environ.setdefault("METRICS_CACHE_SECONDS", "0")
os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("EOL_ENABLED", "false")  # no calls to endoflife.date from refresh tests

import pytest
from fastapi.testclient import TestClient

from changelog_watcher import db as dbmod
from changelog_watcher.db import Base


@pytest.fixture()
def client():
    engine = dbmod.init_engine("sqlite:///:memory:")
    from changelog_watcher.main import create_app

    with TestClient(create_app()) as c:
        yield c
    Base.metadata.drop_all(engine)


NOW = datetime.now(UTC)


def days_ago(n: float) -> datetime:
    return NOW - timedelta(days=n)


@pytest.fixture()
def seeded(client):
    """Two environments, three projects and a set of releases."""
    from changelog_watcher.db import session_scope
    from changelog_watcher.models import Release

    prod = client.post("/api/environments", json={"name": "producao", "prometheus_url": "http://prom-prod:9090"}).json()
    dev = client.post("/api/environments", json={"name": "desenvolvimento", "sort_order": 1,
                                                  "prometheus_url": "http://prom-dev:9090"}).json()
    app = client.post("/api/projects?refresh=false", json={
        "name": "app-a", "repository": "org/app-a",
        "deployments": {
            str(prod["id"]): {"version_source": "manual", "manual_version": "1.1.3"},
            str(dev["id"]): {"version_source": "manual", "manual_version": "1.2.0"},
        },
    }).json()
    app_b = client.post("/api/projects?refresh=false", json={
        "name": "app-b", "repository": "org/app-b",
        "deployments": {str(prod["id"]): {"manual_version": "v3.0.1"}, str(dev["id"]): {"manual_version": "3.0.1"}},
    }).json()
    app_c = client.post("/api/projects?refresh=false", json={
        "name": "app-c", "repository": "org/app-c",
        "deployments": {str(prod["id"]): {"manual_version": "0.9.0"}},
    }).json()
    rels = {
        app["id"]: [("v1.1.3", 400), ("v1.1.5", 200), ("v1.1.7", 100), ("v1.2.0", 90), ("v1.3.1", 3),
                    ("v2.0.0", 20), ("v2.1.0-rc.1", 1)],
        app_b["id"]: [("v3.0.0", 50), ("v3.0.1", 10)],
        app_c["id"]: [("0.8.0", 900)],
    }
    with session_scope() as s:
        for pid, items in rels.items():
            for tag, age in items:
                s.add(Release(project_id=pid, tag=tag, published_at=days_ago(age), url=f"https://x/{tag}",
                              notes=f"notes {tag}", prerelease="rc" in tag))
    return {"prod": prod, "dev": dev, "app": app, "app_b": app_b, "app_c": app_c}
