"""Release sources. Each returns a list of :class:`ReleaseData`."""

from __future__ import annotations

import httpx

from ..versioning import parse_version
from . import changelog, dockerhub, github, gitlab, helm
from .base import ReleaseData, SourceError

SOURCE_TYPES = {
    "github_releases": "GitHub Releases",
    "github_tags": "GitHub Tags",
    "gitlab_releases": "GitLab Releases",
    "gitlab_tags": "GitLab Tags",
    "helm_index": "Repositório Helm (index.yaml)",
    "dockerhub": "Docker Hub (tags)",
    "changelog": "Arquivo CHANGELOG.md",
    "webpage": "Página web com as versões",
}


async def fetch_releases(project, client: httpx.AsyncClient, settings, known_dates: dict | None = None,
                         hints: list[str] | None = None) -> list[ReleaseData]:
    """``hints`` are the versions currently deployed (some sources use them to
    look up older release lines explicitly)."""
    st = project.source_type
    if st == "github_releases":
        releases = await github.fetch_releases(project, client, settings)
    elif st == "github_tags":
        releases = await github.fetch_tags(project, client, settings, known_dates)
    elif st == "gitlab_releases":
        releases = await gitlab.fetch_releases(project, client, settings)
    elif st == "gitlab_tags":
        releases = await gitlab.fetch_tags(project, client, settings)
    elif st == "helm_index":
        releases = await helm.fetch_index(project, client, settings)
    elif st == "dockerhub":
        releases = await dockerhub.fetch_tags(project, client, settings, hints)
    elif st == "changelog":
        return await changelog.fetch_changelog(project, client, settings)
    elif st == "webpage":
        releases = await changelog.fetch_page(project, client, settings)
    else:
        raise SourceError(f"tipo de fonte desconhecido: {st}")

    # Enrich missing notes from a CHANGELOG file when one is configured.
    if project.changelog_url and any(not r.notes for r in releases):
        try:
            parsed = await changelog.fetch_changelog(project, client, settings, use_pattern=False)
        except (SourceError, httpx.HTTPError):
            parsed = []
        by_version = {}
        for p in parsed:
            v = parse_version(p.tag, two_part=project.version_scheme == "major_patch")
            if v:
                by_version[v] = p
        for r in releases:
            if r.notes:
                continue
            v = parse_version(r.tag, project.tag_pattern, two_part=project.version_scheme == "major_patch")
            match = by_version.get(v) if v else None
            if match:
                r.notes = match.notes
                r.published_at = r.published_at or match.published_at
    return releases


__all__ = ["SOURCE_TYPES", "ReleaseData", "SourceError", "fetch_releases"]
