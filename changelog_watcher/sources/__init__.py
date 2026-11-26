"""Release sources. Each returns a list of :class:`ReleaseData`."""

from __future__ import annotations

import httpx

from ..analysis import pv
from ..i18n import _
from . import changelog, dockerhub, github, gitlab, helm
from .base import ReleaseData, SourceError

SOURCE_TYPES = {
    "github_releases": "GitHub Releases",
    "github_tags": "GitHub Tags",
    "gitlab_releases": "GitLab Releases",
    "gitlab_tags": "GitLab Tags",
    "helm_index": "Helm repository (index.yaml)",
    "dockerhub": "Docker Hub (tags)",
    "changelog": "CHANGELOG.md file",
    "webpage": "Web page listing the versions",
}

# Every fetcher takes (project, client, settings, known_dates, hints).
_FETCHERS = {
    "github_releases": lambda p, c, s, k, h: github.fetch_releases(p, c, s),
    "github_tags": lambda p, c, s, k, h: github.fetch_tags(p, c, s, k),
    "gitlab_releases": lambda p, c, s, k, h: gitlab.fetch_releases(p, c, s),
    "gitlab_tags": lambda p, c, s, k, h: gitlab.fetch_tags(p, c, s),
    "helm_index": lambda p, c, s, k, h: helm.fetch_index(p, c, s),
    "dockerhub": lambda p, c, s, k, h: dockerhub.fetch_tags(p, c, s, h),
    "changelog": lambda p, c, s, k, h: changelog.fetch_changelog(p, c, s),
    "webpage": lambda p, c, s, k, h: changelog.fetch_page(p, c, s),
}
assert set(_FETCHERS) == set(SOURCE_TYPES)


async def fetch_releases(project, client: httpx.AsyncClient, settings, known_dates: dict | None = None,
                         hints: list[str] | None = None) -> list[ReleaseData]:
    """``hints`` are the versions currently deployed (some sources use them to
    look up older release lines explicitly)."""
    st = project.source_type
    if st not in _FETCHERS:
        raise SourceError(_("unknown source type: {type}", type=st))
    releases = await _FETCHERS[st](project, client, settings, known_dates, hints)
    if st == "changelog":  # already the changelog itself: nothing to enrich
        return releases

    # Enrich missing notes from a CHANGELOG file when one is configured.
    if project.changelog_url and any(not r.notes for r in releases):
        try:
            parsed = await changelog.fetch_changelog(project, client, settings, use_pattern=False)
        except (SourceError, httpx.HTTPError):
            parsed = []
        by_version = {}
        for p in parsed:
            v = pv(project, p.tag)
            if v:
                by_version[v] = p
        for r in releases:
            if r.notes:
                continue
            v = pv(project, r.tag, use_pattern=True)
            match = by_version.get(v) if v else None
            if match:
                r.notes = match.notes
                r.published_at = r.published_at or match.published_at
    return releases


__all__ = ["SOURCE_TYPES", "ReleaseData", "SourceError", "fetch_releases"]
