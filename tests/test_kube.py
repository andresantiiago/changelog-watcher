import asyncio
import shutil

import certifi
import httpx
import pytest
import respx

from changelog_watcher import kube
from changelog_watcher.models import Deployment, Environment, Project

API = "https://10.0.0.1:443"


@pytest.fixture(autouse=True)
def in_cluster(tmp_path, monkeypatch):
    (tmp_path / "token").write_text("sa-token\n")
    shutil.copy(certifi.where(), tmp_path / "ca.crt")
    monkeypatch.setattr(kube, "SA_DIR", tmp_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")


def _project(**kw):
    base = dict(
        name="app",
        workload_kind="deployment",
        workload_namespace="prod",
        workload_name="app",
        container_name=None,
        image_version_pattern=None,
        version_scheme="semver",
    )
    return Project(**base | kw)


def _detect(source, project=None, **dep):
    dep = Deployment(version_source=source, **dep)
    return asyncio.run(kube.detect(project or _project(), dep, Environment(name="prod")))


def _workload(*containers):
    return {"spec": {"template": {"spec": {"containers": [{"name": n, "image": i} for n, i in containers]}}}}


@respx.mock
def test_kubernetes_image_tag_and_bearer():
    route = respx.get(f"{API}/apis/apps/v1/namespaces/prod/deployments/app").respond(
        json=_workload(("app", "ghcr.io/org/app:v1.4.2"))
    )
    det = _detect("kubernetes")
    assert (det.version, det.image) == ("1.4.2", "ghcr.io/org/app:v1.4.2")
    assert route.calls[0].request.headers["Authorization"] == "Bearer sa-token"


@respx.mock
def test_kubernetes_statefulset_override_and_container():
    respx.get(f"{API}/apis/apps/v1/namespaces/dev/statefulsets/db").respond(
        json=_workload(("db", "postgres:16.2"), ("exporter", "prom/exporter:0.9.0"))
    )
    det = _detect(
        "kubernetes",
        _project(workload_kind="statefulset", container_name="exporter"),
        namespace_override="dev",
        workload_name_override="db",
    )
    assert det.version == "0.9.0"


@respx.mock
def test_kubernetes_several_containers_need_a_name():
    respx.get(f"{API}/apis/apps/v1/namespaces/prod/deployments/app").respond(
        json=_workload(("proxy", "envoy:1.30.0"), ("sidecar", "x/y:2.0.0"))
    )
    with pytest.raises(kube.KubeError, match="several containers"):
        _detect("kubernetes")
    with pytest.raises(kube.KubeError, match="container 'nope' not found"):
        _detect("kubernetes", _project(container_name="nope"))


@respx.mock
def test_kubernetes_image_without_tag():
    respx.get(f"{API}/apis/apps/v1/namespaces/prod/deployments/app").respond(
        json=_workload(("app", "org/app@sha256:abc"))
    )
    with pytest.raises(kube.KubeError, match="could not extract a version"):
        _detect("kubernetes")


@respx.mock
def test_argocd_target_revision_helm_chart():
    route = respx.get(f"{API}/apis/argoproj.io/v1alpha1/namespaces/prod/applications/app").respond(
        json={"spec": {"sources": [{"targetRevision": "main"}, {"targetRevision": "5.51.4"}]}}
    )
    det = _detect("argocd")
    assert (det.version, det.image) == ("5.51.4", None)
    assert route.called


@respx.mock
def test_argocd_chart_source_wins_over_values_ref():
    respx.get(f"{API}/apis/argoproj.io/v1alpha1/namespaces/prod/applications/app").respond(
        json={
            "spec": {
                "sources": [{"ref": "values", "targetRevision": "v9.9.9"}, {"chart": "app", "targetRevision": "5.51.4"}]
            }
        }
    )
    assert _detect("argocd").version == "5.51.4"


def test_preview_of_manual_deployment_still_shows_the_promql(client, seeded):
    pid, eid = seeded["app"]["id"], seeded["prod"]["id"]
    client.put(
        f"/api/projects/{pid}",
        json={
            "name": "app-a",
            "repository": "org/app-a",
            "detection_mode": "workload",
            "workload_namespace": "prod",
            "workload_name": "app-a",
        },
    )
    r = client.post(f"/api/projects/{pid}/detect?environment_id={eid}").json()
    assert not r["ok"] and r["query"].startswith("kube_pod_container_info{")


@respx.mock
def test_argocd_range_falls_back_to_synced_revision():
    respx.get(f"{API}/apis/argoproj.io/v1alpha1/namespaces/prod/applications/app").respond(
        json={"spec": {"source": {"targetRevision": "5.*"}}, "status": {"sync": {"revision": "5.52.1"}}}
    )
    assert _detect("argocd").version == "5.52.1"


@respx.mock
def test_argocd_git_branch_has_no_version():
    respx.get(f"{API}/apis/argoproj.io/v1alpha1/namespaces/prod/applications/app").respond(
        json={"spec": {"source": {"targetRevision": "HEAD"}}, "status": {"sync": {"revision": "a94a8fe5ccb19ba6"}}}
    )
    with pytest.raises(kube.KubeError, match="could not extract a version"):
        _detect("argocd")


@respx.mock
def test_flux_prefers_deployed_release_over_spec_range():
    route = respx.get(f"{API}/apis/helm.toolkit.fluxcd.io/v2/namespaces/prod/helmreleases/app").respond(
        json={
            "spec": {"chart": {"spec": {"version": ">=1.0.0"}}},
            "status": {
                "history": [
                    {"chartVersion": "1.3.0", "status": "failed"},
                    {"chartVersion": "1.2.0", "status": "deployed"},
                ]
            },
        }
    )
    assert _detect("flux").version == "1.2.0"
    assert route.called


@respx.mock
def test_flux_spec_version_when_no_status():
    respx.get(f"{API}/apis/helm.toolkit.fluxcd.io/v2/namespaces/prod/helmreleases/app").respond(
        json={"spec": {"chart": {"spec": {"version": "2.1.0"}}}}
    )
    assert _detect("flux").version == "2.1.0"


@respx.mock
@pytest.mark.parametrize(
    "source,path",
    [
        ("kubernetes", "apps/v1/namespaces/prod/deployments/app"),
        ("argocd", "argoproj.io/v1alpha1/namespaces/prod/applications/app"),
        ("flux", "helm.toolkit.fluxcd.io/v2/namespaces/prod/helmreleases/app"),
    ],
)
def test_not_found_forbidden_and_server_error(source, path):
    route = respx.get(f"{API}/apis/{path}")
    route.respond(404, json={"message": "secret body"})
    with pytest.raises(kube.KubeError, match="not found") as e:
        _detect(source)
    assert "prod/app" in str(e.value) and "secret body" not in str(e.value)
    route.respond(403, json={"message": "secret body"})
    with pytest.raises(kube.KubeError, match="no permission") as e:
        _detect(source)
    assert "secret body" not in str(e.value)
    route.respond(500, text="boom")
    with pytest.raises(kube.KubeError, match="HTTP 500"):
        _detect(source)
    route.respond(200, text="<html>")
    with pytest.raises(kube.KubeError, match="not the Kubernetes API"):
        _detect(source)


@respx.mock
def test_connection_error():
    respx.get(f"{API}/apis/apps/v1/namespaces/prod/deployments/app").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(kube.KubeError, match="failed to query the Kubernetes API"):
        _detect("kubernetes")


@respx.mock
@pytest.mark.parametrize(
    "ns,name",
    [
        ("prod", "../secrets/x"),
        ("pr/od", "app"),
        ("prod", "app?watch=1"),
        ("Prod", "app"),
        ("prod", "a" * 254),
        ("prod", "-app"),
        ("prod", "a..b"),
        ("a" * 64, "app"),
    ],
)
def test_invalid_names_never_reach_the_api(ns, name):
    route = respx.route()
    with pytest.raises(kube.KubeError, match="DNS-1123"):
        _detect("argocd", _project(workload_namespace=ns, workload_name=name))
    assert not route.called


def test_missing_namespace_or_name():
    with pytest.raises(kube.KubeError, match="set the namespace"):
        _detect("flux", _project(workload_name=None))


def test_unsupported_workload_kind():
    with pytest.raises(kube.KubeError, match="not supported"):
        _detect("kubernetes", _project(workload_kind="cronjob"))


def test_not_in_cluster(monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
    with pytest.raises(kube.KubeError, match="unavailable"):
        _detect("kubernetes")


@respx.mock
def test_refresh_and_preview_with_kubernetes_source(client, seeded):
    from changelog_watcher.db import session_scope
    from changelog_watcher.models import Deployment as Dep
    from changelog_watcher.refresh import detect_versions

    pid, eid = seeded["app"]["id"], seeded["prod"]["id"]
    client.put(
        f"/api/projects/{pid}",
        json={
            "name": "app-a",
            "repository": "org/app-a",
            "detection_mode": "workload",
            "workload_namespace": "prod",
            "workload_name": "app-a",
            "deployments": {str(eid): {"version_source": "kubernetes"}},
        },
    )
    respx.get(f"{API}/apis/apps/v1/namespaces/prod/deployments/app-a").respond(
        json=_workload(("app-a", "org/app-a:v1.3.1"))
    )
    ok, failed, _errors = asyncio.run(detect_versions())
    assert (ok, failed) == (1, 0)
    with session_scope() as s:
        d = s.query(Dep).filter_by(version_source="kubernetes").one()
        assert (d.detected_version, d.current_version) == ("1.3.1", "1.3.1")
    r = client.post(f"/api/projects/{pid}/detect?environment_id={eid}").json()
    assert r["ok"] and r["version"] == "1.3.1" and r["query"].startswith("GET /apis/apps/v1")
