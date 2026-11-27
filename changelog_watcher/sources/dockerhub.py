from __future__ import annotations

import re

import httpx

from ..i18n import _
from ..versioning import parse_version
from .base import ReleaseData, SourceError, get_json, parse_datetime

HUB = "https://hub.docker.com/v2"
# Without an explicit tag_pattern only plain version tags are kept: variants
# ("1.27.3-alpine") and floating tags ("latest", "1") would pollute the list.
DEFAULT_TAG = re.compile(r"^v?\d+\.\d+(\.\d+)?([-.]?(rc|beta|alpha)[.-]?\d*)?$", re.IGNORECASE)
_TWO_PARTS = re.compile(r"^v?(\d+)\.(\d+)$")


def _repo(project) -> str:
    repo = project.repository.strip()
    for prefix in ("docker.io/", "index.docker.io/", "registry-1.docker.io/"):
        repo = repo.removeprefix(prefix)
    repo = repo.split(":", 1)[0]
    if "/" not in repo:
        repo = f"library/{repo}"
    if repo.count("/") != 1:
        raise SourceError(_("invalid Docker Hub image: '{repo}'", repo=project.repository))
    return repo


async def _page(client, url, params, project, repo, out: dict[str, ReleaseData]):
    resp = await get_json(client, url, params=params)
    body = resp.json()
    for item in body.get("results", []):
        name = item["name"]
        if project.tag_pattern:
            if not re.search(project.tag_pattern, name):
                continue
        elif not DEFAULT_TAG.match(name):
            continue
        out[name] = ReleaseData(
            tag=name,
            published_at=parse_datetime(item.get("tag_last_pushed") or item.get("last_updated")),
            url=f"https://hub.docker.com/r/{repo.removeprefix('library/')}/tags?name={name}",
        )
    return body.get("next")


async def fetch_tags(project, client: httpx.AsyncClient, settings, hints: list[str] | None = None) -> list[ReleaseData]:
    """Docker Hub tags ordered by push date.

    The listing is capped (MAX_RELEASE_PAGES), so for every version currently
    deployed (``hints``) we also query its ``major.minor`` line explicitly –
    otherwise old release lines would be missing their bugfix versions.
    """
    repo = _repo(project)
    out: dict[str, ReleaseData] = {}
    url: str | None = f"{HUB}/repositories/{repo}/tags"
    params: dict | None = {"page_size": 100, "ordering": "last_updated"}
    for _page_n in range(settings.max_release_pages):
        if not url:
            break
        url = await _page(client, url, params, project, repo, out)
        params = None

    lines = set()
    for hint in hints or []:
        v = parse_version(hint)
        if v:
            # major_patch (postgres 16.2): the release line is the major ("16." substring filter).
            lines.add(f"{v.major}." if project.version_scheme == "major_patch" else f"{v.major}.{v.minor}")
    for line in sorted(lines):
        url, params = f"{HUB}/repositories/{repo}/tags", {"page_size": 100, "ordering": "last_updated", "name": line}
        for _page_n in range(settings.max_release_pages):
            try:
                url = await _page(client, url, params, project, repo, out)
            except (SourceError, httpx.HTTPError):
                break
            params = None
            if not url:
                break

    # "1.27" is usually a floating alias of the newest 1.27.x: drop X.Y tags
    # when an X.Y.Z exists (projects like postgres only publish X.Y).
    full_lines = set()
    for name in out:
        v = parse_version(name)
        if v and not _TWO_PARTS.match(name):
            full_lines.add((v.major, v.minor))
    return [
        r for name, r in out.items()
        if not ((m := _TWO_PARTS.match(name)) and (int(m.group(1)), int(m.group(2))) in full_lines)
    ]
