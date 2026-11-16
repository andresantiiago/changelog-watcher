"""Discover the version running in a cluster through Prometheus.

Relies on kube-state-metrics' ``kube_pod_container_info`` series, which carries
the ``image`` label of every container. Three detection modes:

* ``workload`` – namespace + Deployment/StatefulSet/DaemonSet name (+ container)
* ``image``    – any pod running an image whose repository matches a pattern
* ``query``    – custom PromQL; every returned series must carry an ``image`` label
  (or a ``version`` label). ``{namespace}`` and ``{name}`` are substituted.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import httpx

from .config import expand_env
from .versioning import image_tag, parse_version


class PrometheusError(RuntimeError):
    pass


@dataclass
class Detection:
    image: str | None
    version: str | None
    query: str
    images: dict[str, int]


_POD_SUFFIX = {
    # <name>-<replicaset hash>-<pod hash>
    "deployment": r"-[a-z0-9]{1,10}-[a-z0-9]{5}",
    "statefulset": r"-[0-9]+",
    "daemonset": r"-[a-z0-9]{5}",
    "cronjob": r"-[0-9]+-[a-z0-9]{5}",
}


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _selector(extra: str | None) -> str:
    extra = (extra or "").strip().strip(",{}")
    return f", {extra}" if extra else ""


def build_query(project, deployment, environment) -> str:
    extra = _selector(environment.prometheus_extra_selector)
    namespace = (deployment.namespace_override if deployment else None) or project.workload_namespace
    name = (deployment.workload_name_override if deployment else None) or project.workload_name
    mode = project.detection_mode
    if mode == "workload":
        if not namespace or not name:
            raise PrometheusError("defina namespace e nome do workload")
        suffix = _POD_SUFFIX.get(project.workload_kind or "deployment", _POD_SUFFIX["deployment"])
        matchers = [f'namespace="{_q(namespace)}"', f'pod=~"{_q(re.escape(name))}{_q(suffix)}"']
        if project.container_name:
            matchers.append(f'container="{_q(project.container_name)}"')
        return "kube_pod_container_info{" + ", ".join(matchers) + extra + "}"
    if mode == "image":
        if not project.image_pattern:
            raise PrometheusError("defina o padrão de imagem")
        pattern = project.image_pattern.strip()
        # A plain repository ("ghcr.io/org/app") is turned into an anchored regex.
        if not any(c in pattern for c in "*+?()[]|^$\\"):
            pattern = rf"(.*/)?{re.escape(pattern)}(:.*|@.*)?"
        matchers = [f'image=~"{_q(pattern)}"']
        if namespace:
            matchers.append(f'namespace="{_q(namespace)}"')
        if project.container_name:
            matchers.append(f'container="{_q(project.container_name)}"')
        return "kube_pod_container_info{" + ", ".join(matchers) + extra + "}"
    if mode == "query":
        if not project.promql_query:
            raise PrometheusError("defina a consulta PromQL")
        return project.promql_query.replace("{namespace}", namespace or "").replace("{name}", name or "")
    raise PrometheusError("detecção via Prometheus desabilitada para esta aplicação")


def _client_kwargs(environment) -> dict:
    headers = {}
    auth = None
    if environment.prometheus_auth == "bearer" and environment.prometheus_token:
        headers["Authorization"] = f"Bearer {expand_env(environment.prometheus_token)}"
    elif environment.prometheus_auth == "basic" and environment.prometheus_username:
        auth = (expand_env(environment.prometheus_username) or "", expand_env(environment.prometheus_password) or "")
    return {"headers": headers, "auth": auth, "verify": environment.prometheus_verify_tls}


async def instant_query(environment, promql: str, timeout: float = 15.0) -> list[dict]:
    if not environment.prometheus_url:
        raise PrometheusError(f"ambiente '{environment.name}' sem URL do Prometheus")
    url = expand_env(environment.prometheus_url).rstrip("/") + "/api/v1/query"
    kw = _client_kwargs(environment)
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=kw["verify"], auth=kw["auth"]) as client:
            resp = await client.get(url, params={"query": promql}, headers=kw["headers"])
    except httpx.HTTPError as exc:
        raise PrometheusError(f"falha ao consultar Prometheus: {exc}") from exc
    if resp.status_code >= 400:
        raise PrometheusError(f"Prometheus respondeu {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if body.get("status") != "success":
        raise PrometheusError(f"erro na consulta: {body.get('error')}")
    return body.get("data", {}).get("result", [])


def pick_version(series: list[dict], version_pattern: str | None = None, two_part: bool = False) -> Detection:
    images: Counter[str] = Counter()
    explicit_versions: Counter[str] = Counter()
    for s in series:
        metric = s.get("metric", {})
        if metric.get("version"):
            explicit_versions[metric["version"]] += 1
        image = metric.get("image") or metric.get("image_spec")
        if image:
            images[image] += 1
    if explicit_versions and not images:
        version, _ = explicit_versions.most_common(1)[0]
        parsed = parse_version(version, version_pattern, two_part)
        return Detection(None, str(parsed) if parsed else version, "", {})
    if not images:
        return Detection(None, None, "", {})

    # During a rollout two images can coexist: prefer the most common one and,
    # on ties, the oldest version (conservative).
    def sort_key(item):
        image, count = item
        parsed = parse_version(image_tag(image), version_pattern)
        return (-count, parsed or parse_version("0.0.0"))

    image, _ = sorted(images.items(), key=sort_key)[0]
    tag = image_tag(image)
    parsed = parse_version(tag, version_pattern, two_part)
    return Detection(image, str(parsed) if parsed else None, "", dict(images))


async def detect(project, deployment, environment) -> Detection:
    query = build_query(project, deployment, environment)
    series = await instant_query(environment, query)
    det = pick_version(series, project.image_version_pattern, project.version_scheme == "major_patch")
    det.query = query
    if not series:
        raise PrometheusError(f"nenhuma série encontrada para: {query}")
    if det.version is None:
        raise PrometheusError(f"não foi possível extrair versão da imagem '{det.image}'")
    return det


async def test_connection(environment) -> dict:
    result = await instant_query(environment, "count(kube_pod_container_info" + (
        "{" + environment.prometheus_extra_selector.strip().strip("{},") + "}"
        if environment.prometheus_extra_selector else ""
    ) + ")")
    containers = int(float(result[0]["value"][1])) if result else 0
    return {"ok": True, "containers": containers}
