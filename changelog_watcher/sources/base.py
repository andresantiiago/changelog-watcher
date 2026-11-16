from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable

import httpx

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings
    from ..models import Project


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


Fetcher = Callable[["Project", httpx.AsyncClient, "Settings"], Awaitable[list[ReleaseData]]]


def parse_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Helm index uses nanoseconds: 2024-01-02T10:11:12.123456789Z
    if "." in text:
        head, _, tail = text.partition(".")
        frac = ""
        rest = ""
        for i, ch in enumerate(tail):
            if not ch.isdigit():
                frac, rest = tail[:i], tail[i:]
                break
        else:
            frac = tail
        text = f"{head}.{frac[:6]}{rest}" if frac else f"{head}{rest}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
