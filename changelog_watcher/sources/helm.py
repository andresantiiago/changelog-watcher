from __future__ import annotations

import httpx
import yaml

from .base import ReleaseData, SourceError, get_json, parse_datetime


async def fetch_index(project, client: httpx.AsyncClient, settings) -> list[ReleaseData]:
    """Read a Helm repository ``index.yaml``.

    ``repository`` is the repo URL (e.g. https://argoproj.github.io/argo-helm),
    ``chart_name`` the chart. With ``use_app_version`` the chart's appVersion is
    what gets compared (useful when the image tag follows the app, not the chart).
    """
    if not project.chart_name:
        raise SourceError("informe o nome do chart (chart_name) para fontes Helm")
    url = project.repository.rstrip("/")
    if not url.endswith(".yaml"):
        url += "/index.yaml"
    resp = await get_json(client, url)
    index = yaml.safe_load(resp.text) or {}
    entries = (index.get("entries") or {}).get(project.chart_name)
    if entries is None:
        raise SourceError(f"chart '{project.chart_name}' não encontrado em {url}")
    seen: dict[str, ReleaseData] = {}
    for entry in entries:
        raw = entry.get("appVersion") if project.use_app_version else entry.get("version")
        version = str(raw).strip() if raw is not None else ""
        if not version:
            continue
        created = parse_datetime(entry.get("created"))
        title = f"{project.chart_name} {entry.get('version')} (app {entry.get('appVersion')})"
        data = ReleaseData(
            tag=version,
            title=title,
            published_at=created,
            notes=entry.get("description") or "",
            url=(entry.get("urls") or [None])[0],
            prerelease=bool(entry.get("annotations", {}) and entry["annotations"].get("artifacthub.io/prerelease") == "true"),
        )
        # Several chart versions may ship the same appVersion: keep the oldest date.
        prev = seen.get(version)
        if prev is None or (created and (not prev.published_at or created < prev.published_at)):
            seen[version] = data
    return list(seen.values())
