"""Outdated tolerance: bugfix/minor behind within every enabled limit is not counted as outdated."""

from types import SimpleNamespace as NS

from changelog_watcher.analysis import _within_tolerance
from changelog_watcher.versioning import parse_version


def _status(client, project, env="producao"):
    return next(s for s in client.get("/api/status").json() if s["project"] == project and s["environment"] == env)


def test_tolerance_global_and_per_project(client, seeded):
    from changelog_watcher.db import session_scope
    from changelog_watcher.models import Deployment

    with session_scope() as s:  # app-b prod 3.0.0: one bugfix (3.0.1) behind for 10 days
        dep = s.query(Deployment).filter_by(project_id=seeded["app_b"]["id"], environment_id=seeded["prod"]["id"]).one()
        dep.manual_version = "3.0.0"
    sched = {"report_cron": "0 9 * * mon", "refresh_interval_minutes": 0}
    assert _status(client, "app-b")["status"] == "tolerated"  # defaults: 60 days, 1 minor, 3 bugfixes
    off = {"tolerance_days": None, "tolerance_minors": None, "tolerance_bugfixes": None}
    client.put("/api/settings", json={**sched, **off})
    assert _status(client, "app-b")["status"] == "patch"  # no criterion: tolerance off

    r = client.put("/api/settings", json={**sched, "tolerance_days": 30})
    assert r.json()["tolerance_days"] == 30 and r.json()["tolerance_minors"] is None
    b = _status(client, "app-b")
    assert (b["status"], b["level"]) == ("tolerated", 1)
    assert _status(client, "app-a")["status"] == "major"  # a pending major is never tolerated
    prod = next(e for e in client.get("/api/dashboard").json()["environments"] if e["environment"] == "producao")
    assert prod["counts"]["tolerated"] == 1 and prod["outdated"] == 1  # only app-a
    assert client.get("/api/meta").json()["tolerance"]["tolerance_days"] == 30

    client.put("/api/settings", json={**sched, "tolerance_bugfixes": 0})  # every criterion must hold
    assert _status(client, "app-b")["status"] == "patch"
    client.put("/api/settings", json={**sched, "tolerance_bugfixes": None})  # null disables, days stays 30
    assert _status(client, "app-b")["status"] == "tolerated"

    pid = seeded["app_b"]["id"]  # the project's own limit wins over the global one
    body = {
        **{k: client.get(f"/api/projects/{pid}").json()[k] for k in ("name", "repository", "source_type")},
        "tolerance_days": 5,
    }
    assert client.put(f"/api/projects/{pid}?refresh=false", json=body).status_code == 200
    assert _status(client, "app-b")["status"] == "patch"
    assert client.put("/api/settings", json={**sched, "tolerance_days": -1}).status_code == 422


def test_tolerance_env_defaults(monkeypatch):
    from changelog_watcher.config import Settings

    s = Settings.from_env()
    assert (s.tolerance_days, s.tolerance_minors, s.tolerance_bugfixes) == (60, 1, 3)
    monkeypatch.setenv("TOLERANCE_DAYS", "")  # Helm renders null as "": default
    monkeypatch.setenv("TOLERANCE_MINORS", "off")
    monkeypatch.setenv("TOLERANCE_BUGFIXES", "0")
    s = Settings.from_env()
    assert (s.tolerance_days, s.tolerance_minors, s.tolerance_bugfixes) == (60, None, 0)
    monkeypatch.setenv("TOLERANCE_DAYS", "false")  # Helm renders an unquoted `off` as "false"
    assert Settings.from_env().tolerance_days is None


def test_tolerance_counts_minor_lines():
    cur = parse_version("1.1.0")
    newer = [parse_version(v) for v in ("1.2.0", "1.2.1", "1.3.0")]  # two minor lines, three versions
    st = NS(status="minor", outdated_days=10, patch_behind=0)
    project = NS(tolerance_days=None, tolerance_minors=2, tolerance_bugfixes=None)
    assert _within_tolerance(project, cur, newer, st, {})
    project.tolerance_minors = 1
    assert not _within_tolerance(project, cur, newer, st, {})
