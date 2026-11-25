"""Suggest where a project's releases come from, given its name, image and/or Helm chart.

Each lookup is independent and best effort: a failure (network, rate limit, private
image) only means fewer candidates. Candidates come ordered by confidence.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlsplit

import httpx

from .sources.base import get_json
from .sources.github import _headers as github_headers
from .versioning import ghcr_repository, image_repository, image_tag

log = logging.getLogger(__name__)

_MANIFEST_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))
_REPO_URL = re.compile(r"^https?://(github\.com|gitlab\.com)/([\w.-]+(?:/[\w.-]+)+)$")
# ponytail: só registries públicos, para a API não servir de proxy para a rede interna (SSRF);
# uma setting com registries extras se registries privados com acesso anônimo fizerem falta.
_REGISTRIES = {"registry-1.docker.io", "ghcr.io", "quay.io", "gcr.io", "registry.k8s.io", "public.ecr.aws",
               "mcr.microsoft.com", "registry.gitlab.com", "docker.elastic.co"}
_LOOKUP_TIMEOUT = 15.0  # s por consulta (a da imagem faz até 5 requisições em sequência)


def _candidate(source_type: str, repository: str, reason: str, **extra) -> dict:
    return {"source_type": source_type, "repository": repository, "reason": reason, **extra}


def _from_repo_url(url: str, reason: str) -> dict | None:
    """https://github.com/org/app(.git) -> github_releases org/app; same for gitlab.com."""
    m = _REPO_URL.match((url or "").strip().rstrip("/"))
    if not m:
        return None
    host, path = m.group(1), m.group(2).removesuffix(".git")
    if host == "github.com":
        return _candidate("github_releases", "/".join(path.split("/")[:2]), reason)
    return _candidate("gitlab_releases", path, reason)


def chart_name(chart: str | None) -> str | None:
    """helm.sh/chart label "argo-cd-5.46.7" -> "argo-cd" (a versão é o último -X.Y.Z)."""
    return re.sub(r"^(.*)-v?\d+\.\d+\.\d+\S*$", r"\1", chart) if chart else None


def _registry(image: str) -> tuple[str, str]:
    """"nginx" -> ("registry-1.docker.io", "library/nginx"); "quay.io/org/app" -> ("quay.io", "org/app")."""
    repo = image_repository(image) or ""
    first, _, rest = repo.partition("/")
    if not rest or ("." not in first and ":" not in first and first != "localhost"):
        first, rest = "docker.io", repo
    if first in ("docker.io", "index.docker.io"):
        return "registry-1.docker.io", rest if "/" in rest else f"library/{rest}"
    return first, rest


async def _image_source(client: httpx.AsyncClient, image: str) -> dict | None:
    """Read the org.opencontainers.image.source label of the image config (anonymous pull)."""
    host, repo = _registry(image)
    if host not in _REGISTRIES:
        return None
    domain = ".".join(host.split(".")[-2:])  # registry-1.docker.io -> docker.io
    base = f"https://{host}/v2/{repo}"
    headers = {"Accept": _MANIFEST_TYPES}

    async def get(url: str) -> httpx.Response:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 401 and "Authorization" not in headers:
            # Bearer realm="https://auth.docker.io/token",service="...",scope="..."
            params = dict(re.findall(r'(\w+)="([^"]*)"', resp.headers.get("www-authenticate", "")))
            realm = urlsplit(params.pop("realm", ""))
            realm_host = realm.hostname or ""
            if realm.scheme != "https" or not (realm_host == domain or realm_host.endswith(f".{domain}")):
                resp.raise_for_status()  # token só no domínio do próprio registry
            token = (await client.get(realm.geturl(), params=params)).raise_for_status().json()
            headers["Authorization"] = f"Bearer {token.get('token') or token.get('access_token')}"
            resp = await client.get(url, headers=headers)
        return resp.raise_for_status()

    ref = image.partition("@")[2] or image_tag(image) or "latest"
    manifest = (await get(f"{base}/manifests/{ref}")).json()
    if "manifests" in manifest:  # multi-arch index: any platform carries the same labels
        items = [m for m in manifest["manifests"] if m.get("platform", {}).get("os") != "unknown"]  # sem attestations
        pick = next((m for m in items if m.get("platform", {}).get("architecture") == "amd64"), items[0])
        manifest = (await get(f"{base}/manifests/{pick['digest']}")).json()
    config = (await get(f"{base}/blobs/{manifest['config']['digest']}")).json()
    labels = (config.get("config") or {}).get("Labels") or {}
    return _from_repo_url(labels.get("org.opencontainers.image.source", ""), "label org.opencontainers.image.source da imagem")


async def _artifacthub(client: httpx.AsyncClient, chart: str) -> list[dict]:
    """Helm repositories publishing a chart with exactly this name, most trusted first."""
    resp = await get_json(client, "https://artifacthub.io/api/v1/packages/search",
                          params={"ts_query_web": chart, "kind": 0, "limit": 20})
    hits = [p for p in resp.json().get("packages", [])
            if p.get("name") == chart and p["repository"]["url"].startswith("http")]  # helm_index lê index.yaml via HTTP
    hits.sort(key=lambda p: (bool(p["repository"].get("official")), bool(p["repository"].get("verified_publisher")),
                             p.get("stars") or 0), reverse=True)
    return [_candidate("helm_index", p["repository"]["url"], f"Artifact Hub ({p['repository']['name']}, ★ {p.get('stars') or 0})",
                       chart_name=chart, use_app_version=True) for p in hits[:2]]


async def _github_search(client: httpx.AsyncClient, settings, term: str) -> list[dict]:
    resp = await get_json(client, "https://api.github.com/search/repositories", headers=github_headers(settings),
                          params={"q": f"{term} in:name", "sort": "stars", "order": "desc", "per_page": 20})
    # Only repos named exactly like the term (argocd ~ argo-cd): the rest is noise.
    norm = lambda s: re.sub(r"[-_.]", "", s.lower())  # noqa: E731
    hits = [r for r in resp.json().get("items", []) if norm(r["name"]) == norm(term)]
    return [_candidate("github_releases", r["full_name"], f"busca no GitHub (★ {r['stargazers_count']})")
            for r in hits[:3]]


async def suggest_sources(client: httpx.AsyncClient, settings, name: str | None = None,
                          image: str | None = None, chart: str | None = None) -> list[dict]:
    image = (image or "").strip() or None
    if image and not re.fullmatch(r"[\w.:/@+-]+", image):
        image = None  # image_pattern may be a regex: nothing to look up
    repo = image_repository(image) if image else None
    term = chart_name((chart or "").strip()) or (name or "").strip() or (repo.rsplit("/", 1)[-1] if repo else None)

    # asyncio.sleep(0): consulta sem dado de entrada, resultado None.
    lookups = [_image_source(client, image) if image else asyncio.sleep(0),
               _artifacthub(client, term) if term else asyncio.sleep(0),
               _github_search(client, settings, term) if term else asyncio.sleep(0)]
    # Best effort: rede, rate limit, imagem privada ou payload inesperado só tiram candidatos.
    results = await asyncio.gather(*(asyncio.wait_for(c, _LOOKUP_TIMEOUT) for c in lookups), return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.warning("Sugestão de fonte: consulta falhou: %r", r)
    label, charts, repos = [None if isinstance(r, Exception) else r for r in results]
    gh = ghcr_repository(repo or "")
    ghcr = [_candidate("github_releases", gh, "imagem no ghcr.io")] if gh else []
    host, path = _registry(image) if image else ("", "")
    hub = [_candidate("dockerhub", path.removeprefix("library/"), "imagem no Docker Hub")] \
        if host == "registry-1.docker.io" else []

    # Most reliable first: the image's own label, then registry/chart hints, GitHub search last.
    out, seen = [], set()
    for c in [label, *ghcr, *(charts or []), *hub, *(repos or [])]:
        key = c and (c["source_type"], c["repository"].lower())
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out
