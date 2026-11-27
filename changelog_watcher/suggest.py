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

from .i18n import _
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
_MIN_STARS = 50  # busca no GitHub: abaixo disso é quase sempre repositório pessoal de mesmo nome
_MIN_PULLS = 100_000  # imagem do Docker Hub candidata a original de um mirror interno


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


class _NotFound(Exception):
    """The image (or that tag) does not exist in the registry, or needs credentials."""


async def _image_labels(client: httpx.AsyncClient, image: str) -> dict:
    """Labels of the image config (anonymous pull); only public registries (see _REGISTRIES)."""
    host, repo = _registry(image)
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
            if realm.scheme == "https" and (realm_host == domain or realm_host.endswith(f".{domain}")):  # token só no domínio do registry
                token = await client.get(realm.geturl(), params=params)
                if token.is_success:
                    headers["Authorization"] = f"Bearer {token.json().get('token') or token.json().get('access_token')}"
                    resp = await client.get(url, headers=headers)
        if resp.status_code in (401, 403, 404):  # Docker Hub responde 401 para repositório inexistente
            raise _NotFound(image)
        return resp.raise_for_status()

    ref = image.partition("@")[2] or image_tag(image) or "latest"
    manifest = (await get(f"{base}/manifests/{ref}")).json()
    if "manifests" in manifest:  # multi-arch index: any platform carries the same labels
        items = [m for m in manifest["manifests"] if m.get("platform", {}).get("os") != "unknown"]  # sem attestations
        pick = next((m for m in items if m.get("platform", {}).get("architecture") == "amd64"), items[0])
        manifest = (await get(f"{base}/manifests/{pick['digest']}")).json()
    config = (await get(f"{base}/blobs/{manifest['config']['digest']}")).json()
    return (config.get("config") or {}).get("Labels") or {}


def _source_label(labels: dict, reason: str) -> list[dict]:
    c = _from_repo_url(labels.get("org.opencontainers.image.source", ""), reason)
    return [c] if c else []


async def _image_source(client: httpx.AsyncClient, image: str) -> list[dict]:
    """The image's own org.opencontainers.image.source label."""
    if _registry(image)[0] not in _REGISTRIES:
        return []
    return _source_label(await _image_labels(client, image), _("image label org.opencontainers.image.source"))


async def _public_refs(client: httpx.AsyncClient, image: str) -> list[str]:
    """Public images an internal-registry image may mirror, most likely first.

    registry.intranet/mirror/quay.io/org/app:1 -> quay.io/org/app:1 (path keeps the original
    registry); registry.intranet/plataforma/podinfo:6.17.0 -> ghcr.io/plataforma/podinfo,
    docker.io/plataforma/podinfo and the Docker Hub images named "podinfo" (official or popular).
    """
    host, repo = _registry(image)
    if host in _REGISTRIES:
        return []
    digest = image.partition("@")[2]
    ref = f"@{digest}" if digest else f":{image_tag(image) or 'latest'}"  # mirror keeps tag and digest
    parts = repo.split("/")
    for i, p in enumerate(parts[:-1]):
        if p in _REGISTRIES or p == "docker.io":
            return ["/".join(parts[i:]) + ref]
    refs = [f"{h}/{'/'.join(parts[-2:])}{ref}" for h in ("ghcr.io", "docker.io")] if len(parts) > 1 else []
    resp = await get_json(client, "https://hub.docker.com/v2/search/repositories/",
                          params={"query": parts[-1], "page_size": 25})
    hits = [r for r in resp.json().get("results", [])
            if r["repo_name"].rsplit("/", 1)[-1] == parts[-1]
            and (r.get("is_official") or (r.get("pull_count") or 0) >= _MIN_PULLS)]  # sem imagens pessoais
    hits.sort(key=lambda r: (bool(r.get("is_official")), r.get("pull_count") or 0), reverse=True)
    refs += [r["repo_name"] + ref for r in hits[:2]]
    return list(dict.fromkeys(refs))


async def _mirror_source(client: httpx.AsyncClient, image: str) -> list[dict]:
    """First public image with the same name and tag (or digest): its label and, on Docker Hub, the image."""
    for ref in await _public_refs(client, image):
        try:
            labels = await _image_labels(client, ref)
        except _NotFound:
            continue
        out = _source_label(labels, _("label of the equivalent public image {ref}", ref=ref))
        host, path = _registry(ref)
        if host == "registry-1.docker.io":
            out.append(_candidate("dockerhub", path.removeprefix("library/"), _("equivalent public image {ref}", ref=ref)))
        return out
    return []


async def _artifacthub(client: httpx.AsyncClient, chart: str) -> list[dict]:
    """Helm repositories publishing a chart with exactly this name, most trusted first."""
    resp = await get_json(client, "https://artifacthub.io/api/v1/packages/search",
                          params={"ts_query_web": chart, "kind": 0, "limit": 20})
    hits = [p for p in resp.json().get("packages", [])
            if p.get("name") == chart and p["repository"]["url"].startswith("http")  # helm_index lê index.yaml via HTTP
            and (p["repository"].get("official") or (p.get("stars") or 0) >= 5)]  # "verificado" só diz que o dono é o dono
    hits.sort(key=lambda p: (bool(p["repository"].get("official")), bool(p["repository"].get("verified_publisher")),
                             p.get("stars") or 0), reverse=True)
    return [_candidate("helm_index", p["repository"]["url"], f"Artifact Hub ({p['repository']['name']}, ★ {p.get('stars') or 0})",
                       chart_name=chart, use_app_version=True) for p in hits[:2]]


async def _github_search(client: httpx.AsyncClient, settings, term: str) -> list[dict]:
    resp = await get_json(client, "https://api.github.com/search/repositories", headers=github_headers(settings, "https://api.github.com"),
                          params={"q": f"{term} in:name", "sort": "stars", "order": "desc", "per_page": 20})
    # Only repos named exactly like the term (argocd ~ argo-cd): the rest is noise.
    norm = lambda s: re.sub(r"[-_.]", "", s.lower())  # noqa: E731
    hits = [r for r in resp.json().get("items", [])
            if norm(r["name"]) == norm(term) and r["stargazers_count"] >= _MIN_STARS]
    return [_candidate("github_releases", r["full_name"], _("GitHub search (★ {stars})", stars=r["stargazers_count"]))
            for r in hits[:3]]


async def suggest_sources(client: httpx.AsyncClient, settings, name: str | None = None,
                          image: str | None = None, chart: str | None = None) -> list[dict]:
    image = (image or "").strip() or None
    if image and not re.fullmatch(r"[\w.:/@+-]+", image):
        image = None  # image_pattern may be a regex: nothing to look up
    repo = image_repository(image) if image else None
    # Chart, then the image name (says what runs, even from an internal registry), then the app name
    # (often the workload name: "frontend" running nginx). Same name modulo -_. searched once.
    terms = {}
    for t in (chart_name((chart or "").strip()), repo and repo.rsplit("/", 1)[-1], (name or "").strip()):
        if t:
            terms.setdefault(re.sub(r"[-_.]", "", t.lower()), t)
    terms = list(terms.values())

    lookups = [_image_source(client, image), _mirror_source(client, image)] if image else []
    lookups += [_artifacthub(client, t) for t in terms] + [_github_search(client, settings, t) for t in terms]
    # Best effort: rede, rate limit, imagem privada ou payload inesperado só tiram candidatos.
    results = await asyncio.gather(*(asyncio.wait_for(c, _LOOKUP_TIMEOUT) for c in lookups), return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.warning("source suggestion: lookup failed: %r", r)
    results = [r if isinstance(r, list) else [] for r in results]
    k, n = (2 if image else 0), len(terms)
    images, charts, repos = results[:k], results[k:k + n], results[k + n:]
    gh = ghcr_repository(repo or "")
    ghcr = [_candidate("github_releases", gh, _("image on ghcr.io"))] if gh else []
    host, path = _registry(image) if image else ("", "")
    hub = [_candidate("dockerhub", path.removeprefix("library/"), _("image on Docker Hub"))] \
        if host == "registry-1.docker.io" else []

    # Most reliable first: the image's own label (or its public equivalent's), then registry/chart
    # hints, GitHub search last.
    out, seen = [], set()
    for c in [*sum(images, []), *ghcr, *sum(charts, []), *hub, *sum(repos, [])]:
        key = (c["source_type"], c["repository"].lower())
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out
