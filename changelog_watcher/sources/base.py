from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from ..models import aware


class SourceError(RuntimeError):
    pass


@dataclass
class ReleaseData:
    tag: str
    published_at: datetime | None = None
    title: str | None = None
    notes: str | None = None
    url: str | None = None
    prerelease: bool = False
    commit_sha: str | None = None


def parse_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return aware(value)
    text = str(value).strip()
    try:  # 3.11+ handles "Z" and Helm's nanoseconds (2024-01-02T10:11:12.123456789Z)
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return aware(dt)


async def get_json(client: httpx.AsyncClient, url: str, **kwargs):
    resp = await client.get(url, **kwargs)
    if resp.status_code == 404:
        raise SourceError(f"não encontrado: {url}")
    if resp.status_code in (401, 403):
        detail = resp.headers.get("x-ratelimit-remaining")
        if detail == "0":
            raise SourceError("limite de requisições da API atingido (configure um token)")
        raise SourceError(f"acesso negado ({resp.status_code}) em {url}")
    resp.raise_for_status()
    return resp


async def paged(client: httpx.AsyncClient, url: str, settings, headers: dict | None = None):
    """Items of a page-numbered API (per_page=100), up to MAX_RELEASE_PAGES or the first short page."""
    for page in range(1, settings.max_release_pages + 1):
        items = (await get_json(client, url, params={"per_page": 100, "page": page}, headers=headers)).json()
        for item in items:
            yield item
        if len(items) < 100:
            return
