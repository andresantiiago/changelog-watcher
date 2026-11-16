"""Parse CHANGELOG.md style files ("Keep a Changelog" and friends).

Recognised headings (any level, ``#`` to ``####``)::

    ## [1.2.3] - 2024-05-01
    ## v1.2.3 (2024-05-01)
    ## 1.2.3 / 2024-05-01
    # Release 1.2.3
"""

from __future__ import annotations

import html
import re

import httpx

from ..versioning import parse_version
from .base import ReleaseData, SourceError, parse_datetime

_HEADING = re.compile(r"^(#{1,4})\s+(?P<title>.+?)\s*#*\s*$")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_UNRELEASED = re.compile(r"unreleased", re.IGNORECASE)
_TAG_TOKEN = re.compile(r"(?<![\w.])v?\d+\.\d+(?:\.\d+){0,2}(?:-(?:alpha|beta|rc)[.\w]*)?(?![\w.])", re.IGNORECASE)


def parse_changelog(text: str, tag_pattern: str | None = None) -> list[ReleaseData]:
    sections: list[tuple[str, int, list[str]]] = []  # title, level, lines
    current: tuple[str, int, list[str]] | None = None
    version_level: int | None = None
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            title = m.group("title")
            is_version = bool(parse_version(_strip_links(title), tag_pattern)) and not _UNRELEASED.search(title)
            if is_version and (version_level is None or level <= version_level):
                version_level = level
                current = (title, level, [])
                sections.append(current)
                continue
            if version_level is not None and level <= version_level:
                # Non-version heading at the same level (e.g. "Unreleased") ends the section.
                current = None
                continue
        if current is not None:
            current[2].append(line)

    out: list[ReleaseData] = []
    for title, _, lines in sections:
        clean = _strip_links(title)
        version = parse_version(clean, tag_pattern)
        if not version:
            continue
        date_match = _DATE.search(clean)
        out.append(
            ReleaseData(
                tag=_version_token(clean, version),
                title=clean,
                published_at=parse_datetime(date_match.group(1)) if date_match else None,
                notes="\n".join(lines).strip(),
                prerelease=version.is_prerelease,
            )
        )
    return out


def _strip_links(title: str) -> str:
    # "[1.2.3](https://...)" -> "1.2.3"
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", title).replace("[", "").replace("]", "")


def _version_token(title: str, version) -> str:
    m = re.search(r"v?\d+\.\d+[\w.\-+]*", title)
    return m.group(0).rstrip(".") if m else str(version)


async def fetch_changelog(project, client: httpx.AsyncClient, settings) -> list[ReleaseData]:
    from .github import fetch_file

    url = project.changelog_url or project.repository
    if not url.startswith("http"):
        raise SourceError("informe a URL do arquivo de changelog")
    text = await fetch_file(url, client, settings)
    releases = parse_changelog(text, project.tag_pattern)
    if not releases:
        raise SourceError("nenhuma versão encontrada no changelog")
    for r in releases:
        r.url = url
    return releases


async def fetch_page(project, client: httpx.AsyncClient, settings) -> list[ReleaseData]:
    """Any web page listing the versions (release notes site, download page...).

    Every version-like token of the page text is a release; ``tag_pattern``
    narrows it when the page mentions other software versions too.
    """
    from .github import fetch_file

    url = project.repository
    if not url.startswith("http"):
        raise SourceError("informe a URL da página de releases")
    text = html.unescape(re.sub(r"<[^>]+>", " ", await fetch_file(url, client, settings)))
    if project.tag_pattern:
        tags = [m.group(0) for m in re.finditer(project.tag_pattern, text)]
    else:
        tags = _TAG_TOKEN.findall(text)
    # ponytail: no publication dates (age metrics stay empty); add a date regex if pages need it.
    releases = [ReleaseData(tag=t, url=url) for t in dict.fromkeys(tags)]
    if not releases:
        raise SourceError("nenhuma versão encontrada na página")
    return releases
