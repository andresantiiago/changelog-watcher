import re

import httpx
import pytest
import respx

from changelog_watcher.config import Settings
from changelog_watcher.models import Project
from changelog_watcher.sources import SourceError, fetch_releases
from changelog_watcher.sources.changelog import parse_changelog

CHANGELOG = """# Changelog

## [Unreleased]
- wip

## [1.2.0] - 2024-05-01
### Added
- feature X

## [1.1.7](https://github.com/o/r/compare/v1.1.6...v1.1.7) (2024-04-10)
### Fixed
- bug Y

## v1.1.6 / 2024-03-01
* fix Z
"""


def test_parse_changelog():
    rels = parse_changelog(CHANGELOG)
    assert [r.tag for r in rels] == ["1.2.0", "1.1.7", "v1.1.6"]
    assert "feature X" in rels[0].notes and "### Added" in rels[0].notes
    assert "wip" not in rels[0].notes
    assert rels[1].published_at.date().isoformat() == "2024-04-10"


def _project(**kw):
    base = dict(
        name="p",
        source_type="github_releases",
        repository="org/repo",
        tag_pattern=None,
        changelog_url=None,
        source_url=None,
        chart_name=None,
        use_app_version=False,
    )
    base.update(kw)
    return Project(**base)


async def _run(project):
    async with httpx.AsyncClient() as c:
        return await fetch_releases(project, c, Settings(max_release_pages=1))


@respx.mock
def test_github_releases():
    import asyncio

    respx.get("https://api.github.com/repos/org/repo/releases").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "tag_name": "v1.0.1",
                    "name": "1.0.1",
                    "body": "fix",
                    "html_url": "u",
                    "published_at": "2024-01-02T00:00:00Z",
                    "prerelease": False,
                    "draft": False,
                },
                {"tag_name": "v1.1.0-rc1", "body": "", "prerelease": True, "draft": False, "published_at": None},
                {"tag_name": "v9", "draft": True},
            ],
        )
    )
    rels = asyncio.run(_run(_project()))
    assert [r.tag for r in rels] == ["v1.0.1", "v1.1.0-rc1"]
    assert rels[1].prerelease


@respx.mock
def test_helm_index_app_version():
    import asyncio

    respx.get("https://charts.example.com/index.yaml").mock(
        return_value=httpx.Response(
            200,
            text="""
apiVersion: v1
entries:
  app:
    - version: 2.0.1
      appVersion: v1.5.0
      created: "2024-02-01T10:00:00.123456789Z"
    - version: 2.0.0
      appVersion: v1.5.0
      created: "2024-01-01T10:00:00Z"
    - version: 1.9.0
      appVersion: v1.4.2
      created: "2023-12-01T10:00:00Z"
""",
        )
    )
    p = _project(
        source_type="helm_index", repository="https://charts.example.com", chart_name="app", use_app_version=True
    )
    rels = asyncio.run(_run(p))
    by_tag = {r.tag: r for r in rels}
    assert set(by_tag) == {"v1.5.0", "v1.4.2"}
    assert by_tag["v1.5.0"].published_at.date().isoformat() == "2024-01-01"


@respx.mock
def test_tags_enriched_from_changelog():
    import asyncio

    respx.get("https://api.github.com/repos/org/repo/tags").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "v1.2.0", "commit": {"sha": "aaa"}},
            ],
        )
    )
    respx.get("https://api.github.com/repos/org/repo/commits/aaa").mock(
        return_value=httpx.Response(200, json={"commit": {"committer": {"date": "2024-05-02T00:00:00Z"}}})
    )
    respx.get("https://raw.githubusercontent.com/org/repo/main/CHANGELOG.md").mock(
        return_value=httpx.Response(200, text=CHANGELOG)
    )
    p = _project(source_type="github_tags", changelog_url="https://github.com/org/repo/blob/main/CHANGELOG.md")
    rels = asyncio.run(_run(p))
    assert rels[0].published_at.date().isoformat() == "2024-05-02"
    assert "feature X" in rels[0].notes


@respx.mock
def test_webpage_versions():
    import asyncio

    respx.get("https://app.example.com/releases").mock(
        return_value=httpx.Response(
            200,
            text="""
<html><body><h2>Release v2.4.1</h2><p>Requer Python 3.12.</p><h2>Release v2.4.0</h2>
<a href="/dl/app-v2.4.0.tar.gz">download</a><h2>Release v2.4.1</h2></body></html>""",
        )
    )
    p = _project(source_type="webpage", repository="https://app.example.com/releases", tag_pattern=r"Release (v\S+)")
    rels = asyncio.run(_run(p))
    assert [r.tag for r in rels] == ["Release v2.4.1", "Release v2.4.0"]
    assert rels[0].url == "https://app.example.com/releases"


@respx.mock
def test_helm_null_app_version_and_undated_entry():
    import asyncio

    respx.get("https://charts.example.com/index.yaml").mock(
        return_value=httpx.Response(
            200,
            text="""
entries:
  app:
    - version: 1.1.0
      appVersion: 2.0.0
    - version: 1.0.0
      appVersion: 2.0.0
      created: "2024-01-01T10:00:00Z"
    - version: 0.9.0
      appVersion: null
""",
        )
    )
    p = _project(
        source_type="helm_index", repository="https://charts.example.com", chart_name="app", use_app_version=True
    )
    rels = asyncio.run(_run(p))
    assert [r.tag for r in rels] == ["2.0.0"]
    assert rels[0].published_at.date().isoformat() == "2024-01-01"


@respx.mock
def test_dockerhub_hint_line_major_patch():
    import asyncio

    hub = "https://hub.docker.com/v2/repositories/library/postgres/tags"
    respx.get(hub, params={"name": "12."}).mock(
        return_value=httpx.Response(
            200, json={"results": [{"name": "12.22", "tag_last_pushed": "2024-11-01T00:00:00Z"}]}
        )
    )
    respx.get(hub).mock(
        return_value=httpx.Response(
            200, json={"results": [{"name": "17.2", "tag_last_pushed": "2024-11-20T00:00:00Z"}, {"name": "latest"}]}
        )
    )
    p = _project(source_type="dockerhub", repository="postgres", version_scheme="major_patch")

    async def go():
        async with httpx.AsyncClient() as c:
            return await fetch_releases(p, c, Settings(max_release_pages=1), hints=["12.5"])

    assert sorted(r.tag for r in asyncio.run(go())) == ["12.22", "17.2"]


def test_changelog_tags_match_tag_pattern():
    from changelog_watcher.versioning import parse_version

    pattern = r"^app (?P<version>\S+)"
    rels = parse_changelog("## app 1.2.0 (2024-01-01)\n- x\n## lib 0.3.0\n- y\n## app 1.1.0\n", pattern)
    assert [str(parse_version(r.tag, pattern)) for r in rels] == ["1.2.0", "1.1.0"]
    assert rels[0].notes == "- x"  # the non-matching "lib" heading ends the section


@respx.mock
def test_webpage_with_tag_pattern_enriched_from_changelog():
    import asyncio

    respx.get("https://app.example.com/releases").mock(return_value=httpx.Response(200, text="<h2>Release v1.2.0</h2>"))
    respx.get("https://app.example.com/CHANGELOG.md").mock(return_value=httpx.Response(200, text=CHANGELOG))
    p = _project(
        source_type="webpage",
        repository="https://app.example.com/releases",
        tag_pattern=r"Release (v\S+)",
        changelog_url="https://app.example.com/CHANGELOG.md",
    )
    rels = asyncio.run(_run(p))
    assert "feature X" in rels[0].notes
    assert rels[0].published_at.date().isoformat() == "2024-05-01"


@respx.mock
def test_gitlab_releases_and_tags_token_only_to_configured_gitlab():
    import asyncio

    rel = respx.get("https://gitlab.com/api/v4/projects/grp%2Fapp/releases").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "tag_name": "v2.0.0",
                    "name": "2.0",
                    "description": "notes",
                    "released_at": "2024-03-01T00:00:00Z",
                    "_links": {"self": "https://gitlab.com/grp/app/-/releases/v2.0.0"},
                },
                {"tag_name": "v2.1.0", "upcoming_release": True, "created_at": "2024-04-01"},
            ],
        )
    )
    tags = respx.get("https://git.corp/api/v4/projects/grp%2Fapp/repository/tags").mock(
        return_value=httpx.Response(
            200, json=[{"name": "1.0.0", "message": "m", "commit": {"created_at": "2024-01-01"}}]
        )
    )

    async def run(project):
        async with httpx.AsyncClient() as c:
            return await fetch_releases(project, c, Settings(max_release_pages=1, gitlab_token="tok"))

    rels = asyncio.run(run(_project(source_type="gitlab_releases", repository="https://gitlab.com/grp/app.git")))
    assert [(r.tag, r.prerelease) for r in rels] == [("v2.0.0", False), ("v2.1.0", True)]
    assert rels[0].url.endswith("/releases/v2.0.0") and rels[1].published_at.year == 2024
    assert rel.calls.last.request.headers["PRIVATE-TOKEN"] == "tok"

    rels = asyncio.run(run(_project(source_type="gitlab_tags", repository="grp/app", source_url="https://git.corp")))
    assert [r.tag for r in rels] == ["1.0.0"] and rels[0].url == "https://git.corp/grp/app/-/tags/1.0.0"
    assert "PRIVATE-TOKEN" not in tags.calls.last.request.headers  # token never leaves GITLAB_URL


@pytest.mark.parametrize(
    "response,error",
    [
        (httpx.Response(404), "not found"),
        (httpx.Response(403, headers={"x-ratelimit-remaining": "0"}), "rate limit"),
        (httpx.Response(401), "access denied (401)"),
        (httpx.Response(500), "500"),
        (httpx.TimeoutException("timed out"), "timed out"),
    ],
)
@respx.mock
def test_source_http_failures_raise(response, error):
    import asyncio

    respx.get("https://api.github.com/repos/org/repo/releases").mock(side_effect=[response])
    with pytest.raises((SourceError, httpx.HTTPError), match=re.escape(error)):
        asyncio.run(_run(_project()))


@respx.mock
def test_source_empty_response():
    import asyncio

    respx.get("https://api.github.com/repos/org/repo/releases").mock(return_value=httpx.Response(200, json=[]))
    assert asyncio.run(_run(_project())) == []
