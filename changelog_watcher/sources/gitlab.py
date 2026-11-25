from __future__ import annotations

from urllib.parse import quote

import httpx

from .base import ReleaseData, SourceError, paged, parse_datetime


def _base(project, settings) -> str:
    return (project.source_url or settings.gitlab_url).rstrip("/")


def _path(project) -> str:
    path = project.repository.strip()
    if path.startswith("http"):
        path = path.split("://", 1)[1].split("/", 1)[1]
    path = path.removesuffix(".git").strip("/")
    if "/" not in path:
        raise SourceError(f"projeto GitLab inválido: '{project.repository}' (use grupo/projeto)")
    return path


def _headers(settings) -> dict:
    return {"PRIVATE-TOKEN": settings.gitlab_token} if settings.gitlab_token else {}


async def fetch_releases(project, client: httpx.AsyncClient, settings) -> list[ReleaseData]:
    base, path = _base(project, settings), _path(project)
    url = f"{base}/api/v4/projects/{quote(path, safe='')}/releases"
    return [
        ReleaseData(
            tag=item["tag_name"],
            title=item.get("name") or item["tag_name"],
            notes=item.get("description") or "",
            published_at=parse_datetime(item.get("released_at") or item.get("created_at")),
            url=((item.get("_links") or {}).get("self")) or f"{base}/{path}/-/releases/{item['tag_name']}",
            prerelease=bool(item.get("upcoming_release")),
        )
        async for item in paged(client, url, settings, _headers(settings))
    ]


async def fetch_tags(project, client: httpx.AsyncClient, settings) -> list[ReleaseData]:
    base, path = _base(project, settings), _path(project)
    out: list[ReleaseData] = []
    async for item in paged(client, f"{base}/api/v4/projects/{quote(path, safe='')}/repository/tags", settings,
                            _headers(settings)):
        commit = item.get("commit") or {}
        release = item.get("release") or {}
        out.append(
            ReleaseData(
                tag=item["name"],
                notes=release.get("description") or item.get("message") or "",
                published_at=parse_datetime(commit.get("created_at") or commit.get("committed_date")),
                url=f"{base}/{path}/-/tags/{item['name']}",
                commit_sha=commit.get("id"),
            )
        )
    return out
