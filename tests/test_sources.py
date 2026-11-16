import httpx
import respx

from changelog_watcher.config import Settings
from changelog_watcher.models import Project
from changelog_watcher.sources import fetch_releases
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
    base = dict(name="p", source_type="github_releases", repository="org/repo", tag_pattern=None,
                changelog_url=None, source_url=None, chart_name=None, use_app_version=False)
    base.update(kw)
    return Project(**base)


async def _run(project):
    async with httpx.AsyncClient() as c:
        return await fetch_releases(project, c, Settings(max_release_pages=1))


@respx.mock
def test_github_releases():
    import asyncio

    respx.get("https://api.github.com/repos/org/repo/releases").mock(return_value=httpx.Response(200, json=[
        {"tag_name": "v1.0.1", "name": "1.0.1", "body": "fix", "html_url": "u", "published_at": "2024-01-02T00:00:00Z",
         "prerelease": False, "draft": False},
        {"tag_name": "v1.1.0-rc1", "body": "", "prerelease": True, "draft": False, "published_at": None},
        {"tag_name": "v9", "draft": True},
    ]))
    rels = asyncio.run(_run(_project()))
    assert [r.tag for r in rels] == ["v1.0.1", "v1.1.0-rc1"]
    assert rels[1].prerelease


@respx.mock
def test_helm_index_app_version():
    import asyncio

    respx.get("https://charts.example.com/index.yaml").mock(return_value=httpx.Response(200, text="""
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
"""))
    p = _project(source_type="helm_index", repository="https://charts.example.com", chart_name="app",
                 use_app_version=True)
    rels = asyncio.run(_run(p))
    by_tag = {r.tag: r for r in rels}
    assert set(by_tag) == {"v1.5.0", "v1.4.2"}
    assert by_tag["v1.5.0"].published_at.date().isoformat() == "2024-01-01"


@respx.mock
def test_tags_enriched_from_changelog():
    import asyncio

    respx.get("https://api.github.com/repos/org/repo/tags").mock(return_value=httpx.Response(200, json=[
        {"name": "v1.2.0", "commit": {"sha": "aaa"}},
    ]))
    respx.get("https://api.github.com/repos/org/repo/commits/aaa").mock(return_value=httpx.Response(200, json={
        "commit": {"committer": {"date": "2024-05-02T00:00:00Z"}}}))
    respx.get("https://raw.githubusercontent.com/org/repo/main/CHANGELOG.md").mock(
        return_value=httpx.Response(200, text=CHANGELOG))
    p = _project(source_type="github_tags", changelog_url="https://github.com/org/repo/blob/main/CHANGELOG.md")
    rels = asyncio.run(_run(p))
    assert rels[0].published_at.date().isoformat() == "2024-05-02"
    assert "feature X" in rels[0].notes
