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

from .i18n import _
from . import safehttp
from .config import expand_env
from .suggest import chart_name as _chart_name
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
            raise PrometheusError(_("set the namespace and workload name"))
        suffix = _POD_SUFFIX.get(project.workload_kind or "deployment", _POD_SUFFIX["deployment"])
        matchers = [f'namespace="{_q(namespace)}"', f'pod=~"{_q(re.escape(name))}{_q(suffix)}"']
        if project.container_name:
            matchers.append(f'container="{_q(project.container_name)}"')
        return "kube_pod_container_info{" + ", ".join(matchers) + extra + "}"
    if mode == "helm":
        if not namespace or not name:
            raise PrometheusError(_("set the namespace and Helm release name"))
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
            raise PrometheusError(_("set the image pattern"))
        matchers = [f'image=~"{_q(_image_regex(project.image_pattern))}"']
        if namespace:
            matchers.append(f'namespace="{_q(namespace)}"')
        if project.container_name:
            matchers.append(f'container="{_q(project.container_name)}"')
        return "kube_pod_container_info{" + ", ".join(matchers) + extra + "}"
    if mode == "query":
        if not project.promql_query:
            raise PrometheusError(_("set the PromQL query"))
        return project.promql_query.replace("{namespace}", namespace or "").replace("{name}", name or "")
    raise PrometheusError(_("Prometheus detection disabled for this application"))


async def instant_query(environment, promql: str, timeout: float = 15.0) -> list[dict]:
    if not environment.prometheus_url:
        raise PrometheusError(_("environment '{env}' has no Prometheus URL", env=environment.name))
    url = expand_env(environment.prometheus_url).rstrip("/") + "/api/v1/query"
    headers, auth = {}, None
    if environment.prometheus_auth == "bearer" and environment.prometheus_token:
        headers["Authorization"] = f"Bearer {expand_env(environment.prometheus_token)}"
    elif environment.prometheus_auth == "basic" and environment.prometheus_username:
        auth = (expand_env(environment.prometheus_username) or "", expand_env(environment.prometheus_password) or "")
    try:
        async with safehttp.client(timeout=timeout, verify=environment.prometheus_verify_tls, auth=auth) as client:
            resp = await client.get(url, params={"query": promql}, headers=headers)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:  # InvalidURL is not an HTTPError
        raise PrometheusError(_("failed to query Prometheus: {error}", error=exc)) from exc
    # Never echo the body: the URL is user-configured, so it would turn this into an SSRF read.
    if resp.status_code >= 400:
        raise PrometheusError(_("Prometheus answered HTTP {status}", status=resp.status_code))
    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):  # e.g. an HTML login page answered with 200
        raise PrometheusError(_("invalid response: not the Prometheus API (check the URL)"))
    if body.get("status") != "success":
        raise PrometheusError(_("query error: {error}", error=body.get("error")))
    return body.get("data", {}).get("result", [])


def _basename(repo: str | None) -> str:
    return (repo or "").rsplit("/", 1)[-1]


def _names_image(name: str, repo: str | None) -> bool:
    """"kube-prometheus-stack-prometheus" names the image ".../prometheus/prometheus"."""
    b = _basename(repo)
    return bool(b) and (name == b or name.endswith("-" + b) or name.startswith(b + "-"))


def pick_version(series: list[dict], version_pattern: str | None = None, two_part: bool = False,
                 prefer: str | None = None) -> Detection:
    """The image most pods run; ``prefer`` (the release/workload name) breaks ties between
    different images ("kube-prometheus-stack-prometheus" -> prometheus, not its thanos sidecar)."""
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
        named = bool(prefer) and _names_image(prefer, image_repository(image))
        return (-count, not named, parsed or parse_version("0.0.0"))

    image, _ = sorted(images.items(), key=sort_key)[0]
    tag = image_tag(image)
    parsed = parse_version(tag, version_pattern, two_part)
    return Detection(image, str(parsed) if parsed else None, "", dict(images))


async def detect(project, deployment, environment) -> Detection:
    query = build_query(project, deployment, environment)
    series = await instant_query(environment, query)
    name = (deployment.workload_name_override if deployment else None) or project.workload_name
    det = pick_version(series, project.image_version_pattern, project.version_scheme == "major_patch", prefer=name)
    det.query = query
    if not series:
        raise PrometheusError(_("no series found for: {query}", query=query))
    if det.version is None:
        raise PrometheusError(_("could not extract a version from image '{image}'", image=det.image))
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


_LINK_LABELS = ("label_app_kubernetes_io_part_of", "label_release")  # older charts label pods release=<name>


def _base_name(group: dict) -> str:
    return group["release"] or group["workloads"][0]["name"]


def _suggest(group: dict, comp: dict, multi: bool) -> dict:
    """Project fields to start monitoring one image of a discovered group (the UI prefills its form).

    When the group runs several images (app + sidecars, subcharts) each one becomes its own
    project, pinned by ``image_pattern`` (helm/image) or ``container_name`` (workload)."""
    repo = comp["repository"] or ""
    if group["release"]:
        out = {"name": group["release"], "detection_mode": "helm", "workload_name": group["release"]}
        if multi:
            out["image_pattern"] = repo
    else:
        w = group["workloads"][0]
        if w["kind"] in _POD_SUFFIX:
            out = {"name": w["name"], "detection_mode": "workload", "workload_kind": w["kind"], "workload_name": w["name"]}
            if multi:
                out["container_name"] = comp["containers"][0]["container"]
        else:
            out = {"name": w["name"], "detection_mode": "image", "image_pattern": repo}
    # Only ghcr.io maps reliably to a GitHub repo; otherwise the user points to the
    # repository or release page (no Docker Hub guess).
    gh = ghcr_repository(repo)
    return {**out, "workload_namespace": group["namespace"], "source_type": "github_releases", "repository": gh}


def _image_matches(pattern: str, image: str | None) -> bool:
    try:
        return re.fullmatch(_image_regex(pattern), image or "") is not None
    except re.error:
        return False


def _project_rows(project, rows: list[dict]) -> list[dict]:
    """The discovered containers the project's detection query would select (approximation)."""
    mode, ns = project.detection_mode, project.workload_namespace
    if mode == "helm":
        sel = [r for r in rows if r["namespace"] == ns and r["release"] == project.workload_name]
    elif mode == "workload":
        kind = project.workload_kind or "deployment"
        sel = [r for r in rows if r["namespace"] == ns and r["kind"] == kind and r["workload"] == project.workload_name]
    elif mode == "image" and project.image_pattern:
        sel = [r for r in rows if not ns or r["namespace"] == ns]
    else:
        return []
    if project.container_name:
        sel = [r for r in sel if r["container"] == project.container_name]
    if project.image_pattern and mode in ("helm", "image"):
        sel = [r for r in sel if _image_matches(project.image_pattern, r["image"])]
    return sel


def _link_projects(groups: dict, rows: list[dict], projects) -> None:
    """Mark each image component with the project already tracking it, if any."""
    for p in sorted(projects, key=lambda p: p.id or 0):
        sel = _project_rows(p, rows)
        if not sel:
            continue
        if p.detection_mode == "image":
            repos = {r["repo"] for r in sel}
        else:  # the image the detection would pick (majority, oldest on ties)
            det = pick_version([{"metric": {"image": r["image"]}} for r in sel], p.image_version_pattern,
                               prefer=p.workload_name)
            repos = {image_repository(det.image)}
        for r in sel:
            if r["repo"] in repos:
                comp = groups[r["group"]]["components"][r["repo"]]
                comp["project"] = comp["project"] or {"id": p.id, "name": p.name}


def _main_chart(name: str, charts: Counter) -> str | None:
    """The release's own chart: the one named like the release, else the most common."""
    named = [c for c in charts if (cn := _chart_name(c)) and (name == cn or name.startswith(cn + "-"))]
    if named:
        return max(named, key=len)
    return charts.most_common(1)[0][0] if charts else None


async def discover(environment, namespace: str | None = None, projects=()) -> list[dict]:
    """Workloads running in a namespace (or the whole cluster), grouped by Helm release.

    Releases come from Helm's storage Secrets (``kube_secret_info``, exported by
    default); a pod belongs to a release by its ``app.kubernetes.io/instance``
    label when kube-state-metrics exports it, else by the release-name prefix.
    Each distinct image of a group is a ``component`` that can be monitored on its
    own; ``projects`` (already registered) mark the components they track. A group
    whose release isn't a Helm release but belongs to one (pods created by an
    operator, e.g. ``kube-prometheus-stack-prometheus``) gets it as ``parent``.
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
    rows: list[dict] = []
    for s in containers:
        m = s["metric"]
        ns, pod, image = m.get("namespace"), m.get("pod"), m.get("image")
        kind, name = owner.get((ns, pod), ("pod", pod))
        lab = pod_labels.get((ns, pod), {})
        release = lab.get("label_app_kubernetes_io_instance") or _release_for(
            name, [r for n, r in releases if n == ns])
        key = (ns, release or f"{kind}/{name}")
        g = groups.setdefault(key, {
            "namespace": ns, "release": release, "helm": (ns, release) in releases,
            "revision": releases.get((ns, release)), "chart": None, "parent": None,
            "workloads": {}, "components": {}, "_charts": Counter(), "_links": set(),
        })
        chart = lab.get("label_helm_sh_chart")
        w = g["workloads"].setdefault(f"{kind}/{name}", {"kind": kind, "name": name, "chart": chart, "containers": {}})
        w["chart"] = w["chart"] or chart
        w["containers"][m.get("container")] = image
        if chart:
            g["_charts"][chart] += 1
        g["_links"].update(v for lbl in _LINK_LABELS if (v := lab.get(lbl)) and v != release)
        repo = image_repository(image)
        comp = g["components"].setdefault(repo, {"repository": repo, "containers": [], "series": [], "charts": Counter(),
                                                 "project": None})
        ref = {"kind": kind, "workload": name, "container": m.get("container")}
        if ref not in comp["containers"]:
            comp["containers"].append(ref)
        comp["series"].append(s)
        if chart:
            comp["charts"][chart] += 1
        rows.append({**ref, "namespace": ns, "release": release, "image": image, "repo": repo, "group": key})

    _link_projects(groups, rows, projects)
    known = {(g["namespace"], g["release"]) for g in groups.values() if g["release"]} | set(releases)
    out = []
    for g in groups.values():
        name = _base_name({**g, "workloads": list(g["workloads"].values())})
        g["chart"] = _main_chart(name, g.pop("_charts"))
        links = sorted(v for v in g.pop("_links") if (g["namespace"], v) in known)
        if links:
            g["parent"] = links[0]
        elif not g["helm"]:  # e.g. pods an operator labels with its own instance name
            g["parent"] = _release_for(name, [r for n, r in releases if n == g["namespace"] and r != g["release"]])
        g["workloads"] = [{**w, "containers": [{"container": c, "image": i} for c, i in w["containers"].items()]}
                          for w in sorted(g["workloads"].values(), key=lambda w: w["name"])]
        comps = []
        for c in g.pop("components").values():
            det = pick_version(c.pop("series"))
            charts = c.pop("charts")
            comps.append({**c, "image": det.image, "version": det.version, "images": det.images, "pods": sum(det.images.values()),
                          "chart": next((ch for ch in charts if _names_image(_chart_name(ch) or "", c["repository"])), None)})
        # Main image: most pods; on ties the one the group is named after, then the oldest version.
        comps.sort(key=lambda c: (-c["pods"], not _names_image(name, c["repository"]),
                                  parse_version(image_tag(c["image"])) or parse_version("0.0.0"), c["repository"] or ""))
        multi, used = len(comps) > 1, set()
        for i, c in enumerate(comps):
            c["suggestion"] = sg = _suggest(g, c, multi)
            if i:
                sg["name"] = f"{name} ({_basename(c['repository'])})"
                if sg["name"] in used:
                    sg["name"] = f"{name} ({c['repository']})"
            used.add(sg["name"])
            c.pop("pods")
        comps[0]["chart"] = comps[0]["chart"] or g["chart"]
        main = comps[0]
        g.update(image=main["image"], version=main["version"], images=main["images"], components=comps,
                 suggestion=main["suggestion"], project=main["project"])
        out.append(g)

    def order(g):
        family = g["parent"] or g["release"]
        return (g["namespace"] or "", family is None, family or g["workloads"][0]["name"], g["parent"] is not None,
                _base_name(g))
    out.sort(key=order)
    return out
