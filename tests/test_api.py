import httpx
import respx


def _by(statuses, project, env):
    return next(s for s in statuses if s["project"] == project and s["environment"] == env)


def test_status_classification(client, seeded):
    statuses = client.get("/api/status").json()
    a = _by(statuses, "app-a", "producao")
    assert a["current_version"] == "1.1.3"
    assert a["status"] == "major"
    assert (a["latest_patch"], a["latest_minor"], a["latest_major"]) == ("1.1.7", "1.3.1", "2.0.0")
    assert a["versions_behind"] == 5  # rc ignored
    assert 199 < a["outdated_days"] < 201
    assert a["libyears"] > 1.0
    b = _by(statuses, "app-b", "producao")
    assert b["status"] == "up_to_date" and b["libyears"] == 0
    c = _by(statuses, "app-c", "producao")
    assert c["status"] == "up_to_date"  # 0.9.0 is newer than every release known


def test_dashboard(client, seeded):
    d = client.get("/api/dashboard").json()
    prod = next(e for e in d["environments"] if e["environment"] == "producao")
    assert prod["total"] == 3
    assert prod["counts"]["major"] == 1
    assert prod["patch_pending"] == 1
    assert prod["up_to_date_ratio"] == round(2 / 3, 4)
    assert d["drift_count"] == 1  # app-a differs between environments
    assert d["overall"]["total"] == 5


def test_compare(client, seeded):
    r = client.get("/api/compare", params={"source": seeded["dev"]["id"], "target": seeded["prod"]["id"]}).json()
    rows = {row["project"]: row for row in r["rows"]}
    assert rows["app-a"]["relation"] == "source_ahead"
    assert rows["app-a"]["releases_between"] == 3  # 1.1.5, 1.1.7, 1.2.0
    assert rows["app-b"]["relation"] == "same"
    assert rows["app-c"]["relation"] == "only_target"
    r = client.get("/api/compare", params={"source": seeded["dev"]["id"], "target": seeded["prod"]["id"],
                                           "only_differences": True}).json()
    assert "app-b" not in {row["project"] for row in r["rows"]}


def test_changelog_and_releases(client, seeded):
    pid = seeded["app"]["id"]
    notes = client.get(f"/api/projects/{pid}/changelog", params={"from": "1.1.3", "to": "1.2.0"}).json()
    assert [n["version"] for n in notes] == ["1.2.0", "1.1.7", "1.1.5"]
    recent = client.get("/api/releases/recent", params={"days": 7}).json()
    assert [r["tag"] for r in recent] == ["v1.3.1"]
    assert recent[0]["level"] == "minor"


def test_project_crud_and_deployments(client, seeded):
    pid = seeded["app_c"]["id"]
    body = client.get(f"/api/projects/{pid}").json()
    assert len(body["deployments"]) == 1
    upd = {k: body[k] for k in ("name", "repository", "source_type")}
    upd["deployments"] = {str(seeded["dev"]["id"]): {"version_source": "manual", "manual_version": "0.8.0"}}
    r = client.put(f"/api/projects/{pid}?refresh=false", json=upd)
    assert r.status_code == 200, r.text
    deps = r.json()["deployments"]
    assert [d["environment_id"] for d in deps] == [seeded["dev"]["id"]]
    assert client.post("/api/projects?refresh=false", json={"name": "app-a", "repository": "x/y"}).status_code == 409
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_environment_secret_masking(client):
    env = client.post("/api/environments", json={"name": "x", "prometheus_auth": "bearer",
                                                 "prometheus_token": "abc"}).json()
    assert env["prometheus_token"] == "********"
    env2 = client.put(f"/api/environments/{env['id']}", json={**env, "description": "d"}).json()
    from changelog_watcher.db import session_scope
    from changelog_watcher.models import Environment

    with session_scope() as s:
        assert s.get(Environment, env2["id"]).prometheus_token == "abc"
    placeholder = client.post("/api/environments", json={"name": "y", "prometheus_token": "${TOKEN}"}).json()
    assert placeholder["prometheus_token"] == "${TOKEN}"


def test_metrics(client, seeded):
    text = client.get("/metrics").text
    assert 'changelog_watcher_app_update_level{environment="producao",project="app-a"} 3.0' in text
    assert 'changelog_watcher_environment_apps{environment="producao",status="major"} 1.0' in text
    assert 'changelog_watcher_project_version_mismatch{project="app-a"} 1.0' in text
    assert 'latest_patch="1.1.7"' in text
    assert "changelog_watcher_environment_up_to_date_ratio" in text


@respx.mock
def test_report_and_webhooks(client, seeded):
    respx.route(host="testserver").pass_through()
    slack = respx.post("https://hooks.slack.test/x").mock(return_value=httpx.Response(200))
    teams = respx.post("https://teams.test/x").mock(return_value=httpx.Response(202))
    client.post("/api/webhooks", json={"name": "slack", "kind": "slack", "url": "https://hooks.slack.test/x"})
    client.post("/api/webhooks", json={"name": "teams", "kind": "teams", "url": "https://teams.test/x",
                                       "environment_ids": [seeded["dev"]["id"]]})
    r = client.post("/api/reports", json={"send": True}).json()
    assert "1.1.3" in r["markdown"] and "bugfix **1.1.7**" in r["markdown"]
    assert "Diferenças entre ambientes" in r["markdown"]
    assert all(d["ok"] for d in r["delivery"]), r["delivery"]
    slack_body = slack.calls[0].request.content.decode()
    assert "*producao*" in slack_body
    teams_body = teams.calls[0].request.content.decode()
    assert "AdaptiveCard" in teams_body and "producao" not in teams_body
    hooks = client.get("/api/webhooks").json()
    assert hooks[0]["url"] == "********"
    assert client.get("/api/reports").json()[0]["outdated"] >= 1


def test_history_snapshot(client, seeded):
    from changelog_watcher.refresh import store_snapshots

    store_snapshots()
    hist = client.get("/api/history").json()
    prod = next(h for h in hist if h["environment"] == "producao")
    assert prod["major"] == 1 and prod["total"] == 3


def test_refresh_with_github(client, seeded):
    respx_mock = respx.mock(assert_all_called=False)
    with respx_mock:
        respx_mock.route(host="testserver").pass_through()
        respx_mock.get("https://api.github.com/repos/org/app-a/releases").mock(return_value=httpx.Response(200, json=[
            {"tag_name": "v2.2.0", "body": "new", "published_at": "2026-01-01T00:00:00Z", "draft": False}]))
        respx_mock.get(url__regex=r"https://api\.github\.com/repos/org/app-[bc]/releases").mock(
            return_value=httpx.Response(404))
        r = client.post("/api/refresh", json={"wait": True, "detect": False}).json()
    assert r["new_releases"] == 1
    assert r["projects_failed"] == 2
    a = _by(client.get("/api/status").json(), "app-a", "producao")
    assert a["latest_major"] == "2.2.0"
    projects = {p["name"]: p for p in client.get("/api/projects").json()}
    assert "não encontrado" in projects["app-b"]["last_error"]


def test_seed(tmp_path, client):
    from changelog_watcher.db import session_scope
    from changelog_watcher.seed import load_seed

    cfg = tmp_path / "c.yaml"
    cfg.write_text("""
environments:
  - name: prod
    prometheus_url: http://prom
projects:
  - name: argo
    repository: argoproj/argo-cd
    detection_mode: workload
    workload_namespace: argocd
    workload_name: argocd-server
    deployments:
      prod: {}
      missing: {}
webhooks:
  - name: hook
    url: ${HOOK_URL}
    environments: [prod]
""")
    with session_scope() as s:
        assert load_seed(s, str(cfg)) == {"environments": 1, "projects": 1, "webhooks": 1}
    with session_scope() as s:
        assert load_seed(s, str(cfg)) == {"environments": 0, "projects": 0, "webhooks": 0}
    p = client.get("/api/projects").json()[0]
    assert p["deployments"][0]["version_source"] == "prometheus"


def test_adds_missing_columns(tmp_path):
    import sqlite3

    from changelog_watcher import db as dbmod

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name VARCHAR(200), repository VARCHAR(500))")
    con.execute("INSERT INTO projects (name, repository) VALUES ('a', 'o/r')")
    con.commit()
    con.close()
    dbmod.init_engine(f"sqlite:///{path}")
    cols = {r[1]: r for r in sqlite3.connect(path).execute("PRAGMA table_info(projects)")}
    assert "version_scheme" in cols and "detection_mode" in cols
    assert sqlite3.connect(path).execute("SELECT version_scheme, enabled FROM projects").fetchone() == ("semver", 1)
    dbmod.init_engine("sqlite:///:memory:")
