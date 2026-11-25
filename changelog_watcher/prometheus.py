"""Discover the version running in a cluster through Prometheus.

Relies on kube-state-metrics' ``kube_pod_container_info`` series, which carries
the ``image`` label of every container. Three detection modes:

* ``workload`` – namespace + Deployment/StatefulSet/DaemonSet name (+ container)
* ``helm``     – namespace + Helm release name: every workload of the release
* ``image``    – any pod running an image whose repository matches a pattern
* ``query``    – custom PromQL; every returned series must carry an ``image`` label
  (or a ``version`` label). ``{namespace}`` and ``{name}`` are substituted.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass

import httpx

from .config import expand_env
from .versioning import ghcr_repository, image_repository, image_tag, parse_version


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


def _image_regex(pattern: str) -> str:
    """A plain repository ("ghcr.io/org/app") becomes an anchored regex."""
    pattern = pattern.strip()
    if not any(c in pattern for c in "*+?()[]|^$\\"):
        pattern = rf"(.*/)?{re.escape(pattern)}(:.*|@.*)?"
    return pattern


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
    if mode == "helm":
        if not namespace or not name:
            raise PrometheusError("defina namespace e nome do release Helm")
        ns = f'namespace="{_q(namespace)}"'
        filt = f', container="{_q(project.container_name)}"' if project.container_name else ""
        if project.image_pattern:  # pick one image when the release runs several (app + redis + ...)
            filt += f', image=~"{_q(_image_regex(project.image_pattern))}"'
        by_label = (f"kube_pod_container_info{{{ns}{filt}{extra}}} * on(namespace, pod) group_left() "
                    f'kube_pod_labels{{{ns}, label_app_kubernetes_io_instance="{_q(name)}"{extra}}}')
        by_prefix = f'kube_pod_container_info{{{ns}, pod=~"{_q(re.escape(name))}-.+"{filt}{extra}}}'
        # Pods labelled app.kubernetes.io/instance=<release> when kube-state-metrics
        # exports that label (--metric-labels-allowlist); otherwise "<release>-*" pods.
        # "unless on() X" drops the fallback entirely whenever X returned anything.
        return f"({by_label}) or ({by_prefix} unless on() {by_label})"
    if mode == "image":
        if not project.image_pattern:
            raise PrometheusError("defina o padrão de imagem")
        matchers = [f'image=~"{_q(_image_regex(project.image_pattern))}"']
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


async def instant_query(environment, promql: str, timeout: float = 15.0) -> list[dict]:
    if not environment.prometheus_url:
        raise PrometheusError(f"ambiente '{environment.name}' sem URL do Prometheus")
    url = expand_env(environment.prometheus_url).rstrip("/") + "/api/v1/query"
    headers, auth = {}, None
    if environment.prometheus_auth == "bearer" and environment.prometheus_token:
        headers["Authorization"] = f"Bearer {expand_env(environment.prometheus_token)}"
    elif environment.prometheus_auth == "basic" and environment.prometheus_username:
        auth = (expand_env(environment.prometheus_username) or "", expand_env(environment.prometheus_password) or "")
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=environment.prometheus_verify_tls, auth=auth) as client:
            resp = await client.get(url, params={"query": promql}, headers=headers)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:  # InvalidURL is not an HTTPError
        raise PrometheusError(f"falha ao consultar Prometheus: {exc}") from exc
    if resp.status_code >= 400:
        raise PrometheusError(f"Prometheus respondeu {resp.status_code}: {resp.text[:200]}")
    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):  # e.g. an HTML login page answered with 200
        raise PrometheusError(f"resposta inválida do Prometheus: {resp.text[:200]}")
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
    extra = _selector(environment.prometheus_extra_selector).removeprefix(", ")
    result = await instant_query(environment, "count(kube_pod_container_info{" + extra + "})")
    containers = int(float(result[0]["value"][1])) if result else 0
    return {"ok": True, "containers": containers}


# ----------------------------------------------------------------- discovery
_HELM_SECRET = re.compile(r"sh\.helm\.release\.v1\.(.+)\.v(\d+)$")
_HELM_SECRET_MATCHER = 'secret=~"' + _q(r"sh\.helm\.release\.v1\..+") + '"'


def _owner_workload(kind: str, name: str) -> tuple[str, str]:
    """ReplicaSet "app-5d9c7b" -> deployment "app"; Job "backup-28411200" -> cronjob "backup"."""
    if kind == "ReplicaSet":
        return "deployment", name.rsplit("-", 1)[0]
    if kind == "Job" and re.search(r"-\d{8,}$", name):
        return "cronjob", name.rsplit("-", 1)[0]
    return kind.lower(), name


def _release_for(workload: str, releases: list[str]) -> str | None:
    """Helm charts prefix their objects with the release name (argocd -> argocd-server)."""
    hits = [r for r in releases if workload == r or workload.startswith(r + "-")]
    return max(hits, key=len) if hits else None


def _suggest(group: dict) -> dict:
    """Project fields to start monitoring a discovered group (the UI prefills its form)."""
    repo = image_repository(group["image"]) or ""
    if group["release"]:
        out = {"name": group["release"], "detection_mode": "helm", "workload_name": group["release"]}
    else:
        w = group["workloads"][0]
        if w["kind"] in _POD_SUFFIX:
            out = {"name": w["name"], "detection_mode": "workload", "workload_kind": w["kind"], "workload_name": w["name"]}
        else:
            out = {"name": w["name"], "detection_mode": "image", "image_pattern": repo}
    # Only ghcr.io maps reliably to a GitHub repo; otherwise the user points to the
    # repository or release page (no Docker Hub guess).
    gh = ghcr_repository(repo)
    return {**out, "workload_namespace": group["namespace"], "source_type": "github_releases", "repository": gh}


async def discover(environment, namespace: str | None = None) -> list[dict]:
    """Workloads running in a namespace (or the whole cluster), grouped by Helm release.

    Releases come from Helm's storage Secrets (``kube_secret_info``, exported by
    default); a pod belongs to a release by its ``app.kubernetes.io/instance``
    label when kube-state-metrics exports it, else by the release-name prefix.
    """
    extra = _selector(environment.prometheus_extra_selector).removeprefix(", ")
    base = ", ".join(p for p in (f'namespace="{_q(namespace)}"' if namespace else "", extra) if p)
    helm = ", ".join(p for p in (base, _HELM_SECRET_MATCHER) if p)
    containers, owners, labels, secrets = await asyncio.gather(
        instant_query(environment, f"kube_pod_container_info{{{base}}}"),
        instant_query(environment, f"kube_pod_owner{{{base}}}"),
        instant_query(environment, f"kube_pod_labels{{{base}}}"),
        instant_query(environment, f"kube_secret_info{{{helm}}}"),
    )
    owner = {}
    for s in owners:
        m = s["metric"]
        if m.get("owner_kind") and m.get("owner_name", "<none>") != "<none>":
            owner[(m.get("namespace"), m.get("pod"))] = _owner_workload(m["owner_kind"], m["owner_name"])
    pod_labels = {(s["metric"].get("namespace"), s["metric"].get("pod")): s["metric"] for s in labels}
    releases: dict[tuple, int] = {}  # (namespace, release) -> latest revision
    for s in secrets:
        m = s["metric"]
        if r := _HELM_SECRET.match(m.get("secret", "")):
            key = (m.get("namespace"), r.group(1))
            releases[key] = max(releases.get(key, 0), int(r.group(2)))

    groups: dict[tuple, dict] = {}
    for s in containers:
        m = s["metric"]
        ns, pod = m.get("namespace"), m.get("pod")
        kind, name = owner.get((ns, pod), ("pod", pod))
        lab = pod_labels.get((ns, pod), {})
        release = lab.get("label_app_kubernetes_io_instance") or _release_for(
            name, [r for n, r in releases if n == ns])
        g = groups.setdefault((ns, release or f"{kind}/{name}"), {
            "namespace": ns, "release": release, "helm": (ns, release) in releases,
            "revision": releases.get((ns, release)), "chart": None, "workloads": {}, "series": [],
        })
        g["chart"] = g["chart"] or lab.get("label_helm_sh_chart")
        w = g["workloads"].setdefault(f"{kind}/{name}", {"kind": kind, "name": name, "containers": {}})
        w["containers"][m.get("container")] = m.get("image")
        g["series"].append(s)

    out = []
    for g in groups.values():
        det = pick_version(g.pop("series"))
        g.update(image=det.image, version=det.version, images=det.images)
        g["workloads"] = [{**w, "containers": [{"container": c, "image": i} for c, i in w["containers"].items()]}
                          for w in sorted(g["workloads"].values(), key=lambda w: w["name"])]
        g["suggestion"] = _suggest(g)
        out.append(g)
    out.sort(key=lambda g: (g["namespace"] or "", g["release"] is None, g["release"] or g["workloads"][0]["name"]))
    return out
