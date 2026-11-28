"""Regressions for the security review (secret exfiltration, SSRF, auth, CSRF)."""

import asyncio

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from changelog_watcher import safehttp
from changelog_watcher.config import Settings, expand_env, get_settings
from changelog_watcher.models import Environment, Project
from changelog_watcher.sources import fetch_releases
from changelog_watcher.sources.github import fetch_file

SETTINGS = Settings(max_release_pages=1, github_token="ghp_real")


def test_expand_env_whole_value_only(monkeypatch):
    monkeypatch.setenv("PROM_TOKEN", "s3cr3t")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_real")
    assert expand_env("${PROM_TOKEN}") == "s3cr3t"
    assert expand_env(" ${MISSING:-padrao} ") == "padrao"
    assert expand_env("https://atacante/?t=${PROM_TOKEN}") == "https://atacante/?t=${PROM_TOKEN}"  # not composed
    assert expand_env("${GITHUB_TOKEN}") == ""  # the app's own credentials never resolve
    assert expand_env(None) is None


def _project(**kw):
    return Project(
        **{
            "name": "p",
            "source_type": "github_releases",
            "repository": "org/repo",
            "tag_pattern": None,
            "changelog_url": None,
            "source_url": None,
            "chart_name": None,
            "use_app_version": False,
            **kw,
        }
    )


async def _fetch(project):
    async with httpx.AsyncClient() as c:
        return await fetch_releases(project, c, SETTINGS)


@respx.mock
def test_github_token_only_to_configured_api():
    official = respx.get("https://api.github.com/repos/org/repo/releases").mock(
        return_value=httpx.Response(200, json=[])
    )
    evil = respx.get("https://atacante.example/repos/org/repo/releases").mock(return_value=httpx.Response(200, json=[]))
    asyncio.run(_fetch(_project()))
    asyncio.run(_fetch(_project(source_url="https://atacante.example")))
    assert official.calls[0].request.headers["authorization"] == "Bearer ghp_real"
    assert "authorization" not in evil.calls[0].request.headers


@respx.mock
def test_fetch_file_token_by_hostname_not_substring():
    raw = respx.get("https://raw.githubusercontent.com/o/r/main/CHANGELOG.md").mock(
        return_value=httpx.Response(200, text="x")
    )
    evil = respx.get("https://atacante.example/api.github.com/x").mock(return_value=httpx.Response(200, text="x"))

    async def go(url):
        async with httpx.AsyncClient() as c:
            return await fetch_file(url, c, SETTINGS)

    asyncio.run(go("https://raw.githubusercontent.com/o/r/main/CHANGELOG.md"))
    asyncio.run(go("https://atacante.example/api.github.com/x"))
    assert raw.calls[0].request.headers["authorization"] == "Bearer ghp_real"
    assert "authorization" not in evil.calls[0].request.headers


def test_masked_secret_cannot_follow_a_new_url(client):
    env = client.post(
        "/api/environments",
        json={
            "name": "e",
            "prometheus_url": "http://prom:9090",
            "prometheus_auth": "bearer",
            "prometheus_token": "abc",
        },
    ).json()
    moved = client.put(f"/api/environments/{env['id']}", json={**env, "prometheus_url": "https://atacante.example"})
    assert moved.status_code == 422
    assert client.put(f"/api/environments/{env['id']}", json={**env, "description": "ok"}).status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/api",
        "http://[::ffff:169.254.169.254]/",
        "http://localhost/",
    ],
)
def test_guard_blocks_loopback_and_link_local(url):
    async def go():
        async with safehttp.client() as c:
            await c.get(url)

    with pytest.raises(safehttp.BlockedDestination):
        asyncio.run(go())


@respx.mock
def test_guard_checks_redirects():
    respx.get("https://publico.example/x").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    )

    async def go():
        async with safehttp.client(follow_redirects=True) as c:
            await c.get("https://publico.example/x")

    with pytest.raises(safehttp.BlockedDestination):
        asyncio.run(go())


@respx.mock
def test_prometheus_error_does_not_echo_body():
    from changelog_watcher import prometheus as prom

    respx.get("http://interno.example/api/v1/query").mock(return_value=httpx.Response(200, text="SEGREDO-INTERNO"))
    env = Environment(
        name="e", prometheus_url="http://interno.example", prometheus_auth="none", prometheus_verify_tls=True
    )
    with pytest.raises(prom.PrometheusError) as exc:
        asyncio.run(prom.instant_query(env, "up"))
    assert "SEGREDO-INTERNO" not in str(exc.value)


def _app(monkeypatch, **env):
    from changelog_watcher.main import create_app

    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    try:
        return create_app()
    finally:
        get_settings.cache_clear()


def test_auth_is_fail_closed(monkeypatch):
    with pytest.raises(RuntimeError, match="AUTH_USERNAME"):
        _app(monkeypatch)
    _app(monkeypatch, AUTH_DISABLED="true")  # explicit opt-out still works


def test_metrics_token(monkeypatch):
    c = TestClient(_app(monkeypatch, AUTH_DISABLED="true", METRICS_TOKEN="tok"))
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer errado"}).status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer tok"}).status_code == 200


def test_cross_site_writes_are_blocked(client):
    assert client.post("/api/refresh", headers={"Origin": "https://atacante.example"}).status_code == 403
    assert client.get("/api/refresh/status", headers={"Origin": "https://atacante.example"}).status_code == 200
    ok = client.post("/api/environments", json={"name": "same"}, headers={"Origin": "http://testserver"})
    assert ok.status_code == 201


def test_release_urls_must_be_http(client):
    from changelog_watcher.db import session_scope
    from changelog_watcher.models import Release
    from changelog_watcher.refresh import refresh_project_releases

    pid = client.post(
        "/api/projects?refresh=false",
        json={"name": "h", "source_type": "helm_index", "repository": "https://charts.example", "chart_name": "app"},
    ).json()["id"]
    index = "entries:\n  app:\n    - version: 1.0.0\n      urls: ['javascript:alert(1)']\n"
    with respx.mock:
        respx.get("https://charts.example/index.yaml").mock(return_value=httpx.Response(200, text=index))

        async def go():
            async with httpx.AsyncClient() as c:
                return await refresh_project_releases(pid, c)

        asyncio.run(go())
    with session_scope() as s:
        assert [r.url for r in s.query(Release).filter_by(project_id=pid)] == [None]
