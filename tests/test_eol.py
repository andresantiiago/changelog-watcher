import asyncio
from datetime import date, timedelta

import httpx
import respx

from changelog_watcher import eol
from changelog_watcher.models import Project
from changelog_watcher.versioning import parse_version

API = "https://endoflife.date/api"
TODAY = date.today()


def _d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


PG_CYCLES = [
    {"cycle": "17", "releaseDate": "2024-09-26", "eol": _d(1500), "latest": "17.2"},
    {"cycle": "13", "releaseDate": "2020-09-24", "eol": _d(30), "latest": "13.18",
     "link": "https://www.postgresql.org/support/versioning/"},
    {"cycle": "12", "releaseDate": "2019-10-03", "eol": _d(-20), "latest": "12.22", "link": "javascript:alert(1)"},
]


def _cycles(items):
    return [eol.normalize_cycle(i) for i in items]


def test_find_cycle_semver_and_two_part():
    k8s = _cycles([{"cycle": "1.29", "eol": _d(10)}, {"cycle": "1.30", "eol": _d(200)}, {"cycle": "1", "eol": False}])
    assert eol.find_cycle(parse_version("v1.29.3"), k8s)["cycle"] == "1.29"  # longest prefix wins
    assert eol.find_cycle(parse_version("1.31.0"), k8s)["cycle"] == "1"
    assert eol.find_cycle(parse_version("2.0.0"), k8s) is None
    pg = _cycles(PG_CYCLES)
    assert eol.find_cycle(parse_version("13.4", two_part=True), pg)["cycle"] == "13"
    assert eol.find_cycle(None, pg) is None
    assert eol.find_cycle(parse_version("1.0.0"), _cycles([{"cycle": "bookworm", "eol": False}])) is None


def test_eol_status():
    assert eol.eol_status({"eol": _d(30)}, TODAY, 90) == ("soon", _d(30), 30)
    assert eol.eol_status({"eol": _d(91)}, TODAY, 90)[0] == "ok"
    assert eol.eol_status({"eol": _d(-5)}, TODAY, 90) == ("eol", _d(-5), -5)
    assert eol.eol_status({"eol": True}, TODAY, 90) == ("eol", None, None)
    assert eol.eol_status({"eol": False}, TODAY, 90) == ("ok", None, None)
    assert eol.eol_status({"eol": None}, TODAY, 90) == (None, None, None)


def test_resolve_product():
    def p(**kw):
        base = {"name": "x", "repository": "org/x", "source_type": "github_releases", "eol_product": None,
                "chart_name": None, "use_app_version": False}
        return Project(**(base | kw))

    products = ["postgresql", "redis", "kubernetes", "nginx"]
    assert eol.resolve_product(p(name="Postgres"), products, True) == "postgresql"  # alias
    assert eol.resolve_product(p(name="cache", repository="redis/redis"), products, True) == "redis"
    assert eol.resolve_product(p(name="web", source_type="dockerhub", repository="library/nginx"), products,
                               True) == "nginx"
    assert eol.resolve_product(p(name="redis"), products, False) is None  # auto match off
    assert eol.resolve_product(p(name="redis", eol_product="none"), products, True) is None
    assert eol.resolve_product(p(name="x", eol_product="kubernetes"), None, False) == "kubernetes"
    # a chart version is not the product version
    assert eol.resolve_product(p(name="redis", source_type="helm_index", chart_name="redis"), products, True) is None
    assert eol.resolve_product(p(name="redis", source_type="helm_index", chart_name="redis", use_app_version=True),
                               products, True) == "redis"


def _project(client, prod_id, name, version, **extra):
    body = {"name": name, "repository": f"org/{name}",
            "deployments": {str(prod_id): {"manual_version": version}}} | extra
    r = client.post("/api/projects?refresh=false", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _refresh(ids):
    async def run():
        async with httpx.AsyncClient() as c:
            return await eol.refresh_eol(ids, c)
    return asyncio.run(run())


@respx.mock(assert_all_called=False)
def test_refresh_and_outputs(respx_mock, client, seeded):
    eol._products_cache = None
    respx_mock.route(host="testserver").pass_through()
    respx_mock.get(f"{API}/all.json").mock(return_value=httpx.Response(200, json=["postgresql", "redis"]))
    respx_mock.get(f"{API}/postgresql.json").mock(return_value=httpx.Response(200, json=PG_CYCLES))
    respx_mock.get(f"{API}/kubernetes.json").mock(return_value=httpx.Response(404))
    prod = seeded["prod"]["id"]
    pg = _project(client, prod, "postgres", "13.4", version_scheme="major_patch")
    old = _project(client, prod, "pg-old", "12.9", version_scheme="major_patch", eol_product="postgresql")
    k8s = _project(client, prod, "cluster", "1.29.1", eol_product="kubernetes")
    off = _project(client, prod, "redis", "7.2.1", eol_product="none")

    errors = _refresh([pg["id"], old["id"], k8s["id"], off["id"], seeded["app"]["id"]])
    assert len(errors) == 1 and "kubernetes" in errors[0]

    projects = {p["name"]: p for p in client.get("/api/projects").json()}
    assert projects["postgres"]["eol_data"]["product"] == "postgresql"
    assert projects["cluster"]["eol_error"] and projects["cluster"]["eol_data"] is None
    assert projects["redis"]["eol_data"] is None
    assert projects["app-a"]["eol_data"] is None  # no product matches its name

    statuses = {s["project"]: s for s in client.get(f"/api/status?environment_id={prod}").json()}
    assert statuses["postgres"]["eol_cycle"] == "13" and statuses["postgres"]["eol_status"] == "soon"
    assert statuses["postgres"]["eol_days_left"] == 30 and statuses["postgres"]["eol_cycle_latest"] == "13.18"
    assert statuses["pg-old"]["eol_status"] == "eol"
    # the cycle's vendor page; a non-http(s) link falls back to the product page on endoflife.date
    assert statuses["postgres"]["eol_link"] == "https://www.postgresql.org/support/versioning/"
    assert statuses["pg-old"]["eol_link"] == "https://endoflife.date/postgresql"
    assert statuses["app-a"]["eol_link"] is None
    assert statuses["app-a"]["eol_status"] is None

    dash = client.get("/api/dashboard").json()
    assert [s["project"] for s in dash["eol"]] == ["pg-old", "postgres"]
    summ = next(e for e in dash["environments"] if e["environment_id"] == prod)
    assert (summ["eol_reached"], summ["eol_soon"]) == (1, 1)

    metrics = client.get("/metrics").text
    assert 'changelog_watcher_app_eol_level{cycle="13",environment="producao",product="postgresql",project="postgres"} 1.0' in metrics
    assert 'changelog_watcher_app_eol_days_left{cycle="12",environment="producao",product="postgresql",project="pg-old"} -20.0' in metrics
    assert 'changelog_watcher_environment_apps_eol{environment="producao",status="eol"} 1.0' in metrics

    from changelog_watcher.db import session_scope
    from changelog_watcher.report import build_report_data, render_markdown
    from changelog_watcher.webhooks import chat_lines, filter_data

    with session_scope() as s:
        data = build_report_data(s)
    md = render_markdown(data)
    assert "## End of life (EOL) (2)" in md and "postgresql 13" in md and "in 30 days" in md
    assert any("pg-old" in line and "20 days ago" in line for line in chat_lines(data))
    assert filter_data(data, [seeded["dev"]["id"]])["eol"] == []

    # a failed fetch keeps the previous cycles
    respx_mock.get(f"{API}/postgresql.json").mock(return_value=httpx.Response(500))
    _refresh([pg["id"]])
    p = client.get(f"/api/projects/{pg['id']}").json()
    assert p["eol_error"] and p["eol_data"]["product"] == "postgresql"


def test_eol_product_validation(client):
    r = client.post("/api/projects?refresh=false", json={"name": "x", "repository": "o/x", "eol_product": "../etc"})
    assert r.status_code == 422
    r = client.post("/api/projects?refresh=false", json={"name": "y", "repository": "o/y", "eol_product": " Redis "})
    assert r.json()["eol_product"] == "redis"


@respx.mock(assert_all_called=False)
def test_products_endpoint(respx_mock, client, monkeypatch):
    from changelog_watcher.config import get_settings

    eol._products_cache = None
    respx_mock.route(host="testserver").pass_through()
    respx_mock.get(f"{API}/all.json").mock(return_value=httpx.Response(200, json=["redis", "nginx"]))
    monkeypatch.setattr(get_settings(), "eol_enabled", True)
    p = client.post("/api/projects?refresh=false", json={"name": "redis", "repository": "o/r"}).json()
    r = client.get(f"/api/eol/products?project_id={p['id']}").json()
    assert r["products"] == ["nginx", "redis"] and r["suggested"] == "redis"
    monkeypatch.setattr(get_settings(), "eol_enabled", False)
    assert client.get("/api/eol/products").json()["enabled"] is False
