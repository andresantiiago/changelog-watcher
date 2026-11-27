"""Consistency checks for the deployment artifacts (Grafana dashboard / Helm chart)."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "deploy/helm/changelog-watcher/dashboards/changelog-watcher.json"


def test_dashboard_metrics_exist(client, seeded):
    from changelog_watcher.refresh import run_refresh  # noqa: F401  (registers refresh metrics)

    exposed = set(re.findall(r"^# TYPE (\w+) ", client.get("/metrics").text, re.M))
    exposed |= {f"{m}_sum" for m in exposed} | {f"{m}_count" for m in exposed}
    dash = json.loads(DASH.read_text())
    pt = json.loads(DASH.with_name("changelog-watcher.pt-BR.json").read_text())

    def metrics(panels, acc):
        for p in panels:
            metrics(p.get("panels", []), acc)
            for t in p.get("targets", []):
                acc.update(re.findall(r"changelog_watcher_\w+", t["expr"]))
        return acc

    used = metrics(dash["panels"], set())
    assert used == metrics(pt["panels"], set()), "the English and pt-BR dashboards must query the same metrics"
    assert [p["id"] for p in pt["panels"]] == [p["id"] for p in dash["panels"]]
    assert used, "no metrics found in dashboard"
    missing = used - exposed
    assert not missing, f"dashboard uses metrics not exposed: {missing}"
    ids = [p["id"] for p in dash["panels"]]
    assert len(ids) == len(set(ids))


def test_example_config_loads(client):
    from changelog_watcher.db import session_scope
    from changelog_watcher.seed import load_seed

    with session_scope() as s:
        stats = load_seed(s, str(ROOT / "config.example.yaml"))
    assert stats == {"environments": 2, "projects": 6, "webhooks": 2}
    projects = {p["name"]: p for p in client.get("/api/projects").json()}
    assert projects["postgres"]["version_scheme"] == "major_patch"
    assert projects["my-api"]["deployments"][0]["manual_version"] == "2.3.1"
