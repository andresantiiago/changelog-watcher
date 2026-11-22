from __future__ import annotations

import asyncio

import httpx

from ..versioning import parse_version
from .base import ReleaseData, SourceError, get_json, parse_datetime

MAX_TAG_DATE_LOOKUPS = 25  # commits fetched to date unknown tags per run


def _headers(settings, accept: str = "application/vnd.github+json") -> dict:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _api(project, settings) -> str:
    return (project.source_url or settings.github_api_url).rstrip("/")


def _repo(project) -> str:
    repo = project.repository.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if repo.startswith(prefix):
            repo = repo[len(prefix):]
    repo = repo.removesuffix(".git").strip("/")
    if repo.count("/") != 1:
        raise SourceError(f"repositório GitHub inválido: '{project.repository}' (use owner/repo)")
    return repo


async def fetch_releases(project, client: httpx.AsyncClient, settings) -> list[ReleaseData]:
    api, repo = _api(project, settings), _repo(project)
    out: list[ReleaseData] = []
    for page in range(1, settings.max_release_pages + 1):
        resp = await get_json(
            client,
            f"{api}/repos/{repo}/releases",
            params={"per_page": 100, "page": page},
            headers=_headers(settings),
        )
        items = resp.json()
        for item in items:
            if item.get("draft"):
                continue
            out.append(
                ReleaseData(
                    tag=item["tag_name"],
                    published_at=parse_datetime(item.get("published_at") or item.get("created_at")),
                    title=item.get("name") or item["tag_name"],
                    notes=item.get("body") or "",
                    url=item.get("html_url"),
                    prerelease=bool(item.get("prerelease")),
                )
            )
        if len(items) < 100:
            break
    return out


async def fetch_tags(project, client: httpx.AsyncClient, settings, known_dates: dict | None = None) -> list[ReleaseData]:
    """Tags have no date in the list endpoint; we look up commit dates for the
    newest tags we don't know yet (bounded by MAX_TAG_DATE_LOOKUPS)."""
    api, repo = _api(project, settings), _repo(project)
    known_dates = known_dates or {}
    tags: list[ReleaseData] = []
    for page in range(1, settings.max_release_pages + 1):
        resp = await get_json(
            client,
            f"{api}/repos/{repo}/tags",
            params={"per_page": 100, "page": page},
            headers=_headers(settings),
        )
        items = resp.json()
        for item in items:
            sha = (item.get("commit") or {}).get("sha")
            tags.append(
                ReleaseData(
                    tag=item["name"],
                    url=f"https://github.com/{repo}/releases/tag/{item['name']}",
                    commit_sha=sha,
                    published_at=known_dates.get(item["name"]),
                )
            )
        if len(items) < 100:
            break

    missing = [t for t in tags if t.published_at is None and t.commit_sha]
    missing.sort(key=lambda t: parse_version(t.tag, project.tag_pattern) or parse_version("0.0.0"), reverse=True)
    missing = missing[:MAX_TAG_DATE_LOOKUPS]
    sem = asyncio.Semaphore(5)

    async def lookup(t: ReleaseData):
        async with sem:
            try:
                resp = await get_json(client, f"{api}/repos/{repo}/commits/{t.commit_sha}", headers=_headers(settings))
                commit = resp.json().get("commit", {})
                t.published_at = parse_datetime(
                    (commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get("date")
                )
            except (SourceError, httpx.HTTPError):
                pass

    await asyncio.gather(*(lookup(t) for t in missing))
    return tags


async def fetch_file(url: str, client: httpx.AsyncClient, settings) -> str:
    """Fetch a raw file. github.com blob URLs are rewritten to raw URLs."""
    if "github.com/" in url and "/blob/" in url:
        url = url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/", 1)
    headers = {}
    if settings.github_token and ("githubusercontent.com" in url or "api.github.com" in url):
        headers["Authorization"] = f"Bearer {settings.github_token}"
    resp = await get_json(client, url, headers=headers)
    return resp.text
