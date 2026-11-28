"""Discover the version running in the cluster the app itself runs in, straight from the Kubernetes API.

Read-only, with the pod's ServiceAccount (token + CA, ``KUBERNETES_SERVICE_HOST/PORT``).
``Deployment.version_source`` picks what is read; namespace and name come from the project's
``workload_namespace``/``workload_name`` (or the deployment's overrides):

* ``kubernetes`` – image tag of a container of a Deployment/StatefulSet/DaemonSet
  (``workload_kind``, ``container_name``)
* ``argocd``     – ``targetRevision`` of an Argo CD ``Application`` (the synced revision as fallback)
* ``flux``       – chart version of a Flux ``HelmRelease`` (the deployed release first)
"""

from __future__ import annotations

import os
import re
import ssl
from pathlib import Path

import httpx

from . import prometheus as prom
from .i18n import _
from .versioning import parse_version

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
KUBE_SOURCES = ("kubernetes", "argocd", "flux")

# DNS-1123 label (namespace) and subdomain (resource name): the only user input that reaches the URL path.
_LABEL = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")
_SUBDOMAIN = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*")

# An exact version ("1.2.3", "v2.0.0-rc.1"), not a range ("1.2.*", "~1.2.0"), branch or commit.
_EXACT = re.compile(r"v?\d+\.\d+(\.\d+)*([-+].+)?")

_WORKLOADS = {
    "deployment": ("Deployment", "apps/v1", "deployments"),
    "statefulset": ("StatefulSet", "apps/v1", "statefulsets"),
    "daemonset": ("DaemonSet", "apps/v1", "daemonsets"),
}
_CRDS = {
    "argocd": ("Argo CD Application", "argoproj.io/v1alpha1", "applications"),
    "flux": ("Flux HelmRelease", "helm.toolkit.fluxcd.io/v2", "helmreleases"),
}


class KubeError(prom.PrometheusError):
    """A PrometheusError subclass so the existing handlers (refresh, API) cover it."""


def _dig(obj, *path):
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


async def _get(path: str, what: str) -> dict:
    """GET a Kubernetes object. Never echoes the response body: only short, translated messages."""
    host, port = os.environ.get("KUBERNETES_SERVICE_HOST"), os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    try:
        token = (SA_DIR / "token").read_text().strip()
        ctx = ssl.create_default_context(cafile=str(SA_DIR / "ca.crt"))
    except OSError, ssl.SSLError:
        token, ctx = "", None
    if not host or not token or ctx is None:
        raise KubeError(_("Kubernetes API unavailable: the app must run in a cluster with a ServiceAccount token"))
    if ":" in host:  # IPv6
        host = f"[{host}]"
    try:
        # trust_env=False: the cluster API must never go through an HTTP(S)_PROXY
        async with httpx.AsyncClient(
            verify=ctx, timeout=15.0, trust_env=False, headers={"Authorization": f"Bearer {token}"}
        ) as client:
            resp = await client.get(f"https://{host}:{port}{path}")
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise KubeError(_("failed to query the Kubernetes API: {error}", error=exc)) from exc
    if resp.status_code == 404:
        raise KubeError(_("{what} not found in the Kubernetes API", what=what))
    if resp.status_code in (401, 403):
        raise KubeError(_("no permission to read {what}: check the ServiceAccount's RBAC", what=what))
    if resp.status_code >= 400:
        raise KubeError(_("Kubernetes API answered HTTP {status}", status=resp.status_code))
    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise KubeError(_("invalid response: not the Kubernetes API"))
    return body


def _workload_image(obj: dict, project, name: str, what: str) -> str:
    containers = {
        c.get("name"): c.get("image")
        for c in _dig(obj, "spec", "template", "spec", "containers") or []
        if isinstance(c, dict)
    }
    if project.container_name:
        image = containers.get(project.container_name)
        if not image:
            raise KubeError(
                _("container '{container}' not found in {what}", container=project.container_name, what=what)
            )
        return image
    if len(containers) == 1:
        return next(iter(containers.values())) or ""
    if containers.get(name):  # a container named like the workload
        return containers[name] or ""
    raise KubeError(_("{what} has several containers: set the container name", what=what))


def _argocd_revisions(obj: dict) -> list:
    sources = _dig(obj, "spec", "sources")
    if not isinstance(sources, list):
        sources = [_dig(obj, "spec", "source")]
    revisions = _dig(obj, "status", "sync", "revisions")
    # Helm chart sources first: a values `ref` source pinned at "v1" must not win
    sources = sorted(sources, key=lambda s: not _dig(s, "chart"))
    return (
        [_dig(s, "targetRevision") for s in sources]
        + [_dig(obj, "status", "sync", "revision")]
        + (revisions if isinstance(revisions, list) else [])
    )


def _flux_revisions(obj: dict) -> list:
    history = _dig(obj, "status", "history")
    # newest first; skip releases that failed (the previous one is what runs)
    deployed = (
        [h.get("chartVersion") for h in history if isinstance(h, dict) and h.get("status", "deployed") == "deployed"]
        if isinstance(history, list)
        else []
    )
    return [*deployed, _dig(obj, "spec", "chart", "spec", "version")]


async def detect(project, deployment, environment) -> prom.Detection:
    """The running version of a deployment: Kubernetes/Argo CD/Flux when its ``version_source`` says so,
    Prometheus otherwise."""
    source = deployment.version_source if deployment else "prometheus"
    if source not in KUBE_SOURCES:
        return await prom.detect(project, deployment, environment)
    namespace = (deployment.namespace_override or project.workload_namespace or "").strip()
    name = (deployment.workload_name_override or project.workload_name or "").strip()
    if not namespace or not name:
        raise KubeError(_("set the namespace and workload name"))
    if not _LABEL.fullmatch(namespace) or len(name) > 253 or not _SUBDOMAIN.fullmatch(name):
        raise KubeError(_("invalid namespace or name: use a DNS-1123 name (lowercase letters, digits, '-' and '.')"))

    if source == "kubernetes":
        if project.workload_kind not in _WORKLOADS:
            raise KubeError(
                _("workload type '{kind}' is not supported with the Kubernetes API", kind=project.workload_kind)
            )
        kind, api, plural = _WORKLOADS[project.workload_kind]
    else:
        kind, api, plural = _CRDS[source]
    path = f"/apis/{api}/namespaces/{namespace}/{plural}/{name}"
    what = f"{kind} '{namespace}/{name}'"
    obj = await _get(path, what)

    two_part = project.version_scheme == "major_patch"
    pattern = project.image_version_pattern
    if source == "kubernetes":
        det = prom.pick_version([{"metric": {"image": _workload_image(obj, project, name, what)}}], pattern, two_part)
        if det.version is None:
            raise KubeError(_("could not extract a version from image '{image}'", image=det.image))
    else:
        revisions = _argocd_revisions(obj) if source == "argocd" else _flux_revisions(obj)
        for rev in revisions:  # first one that holds a version (branches, ranges and commits don't)
            if (
                isinstance(rev, str)
                and (pattern or _EXACT.fullmatch(rev))
                and (parsed := parse_version(rev, pattern, two_part))
            ):
                det = prom.Detection(None, str(parsed), "", {})
                break
        else:
            raise KubeError(_("could not extract a version from {what}", what=what))
    det.query = f"GET {path}"
    return det
