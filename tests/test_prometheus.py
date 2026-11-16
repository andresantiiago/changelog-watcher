import asyncio

import httpx
import respx

from changelog_watcher import prometheus as prom
from changelog_watcher.models import Deployment, Environment, Project


def _project(**kw):
    base = dict(name="argocd", detection_mode="workload", workload_kind="deployment", workload_namespace="argocd",
                workload_name="argocd-server", container_name="server", image_pattern=None, promql_query=None,
                image_version_pattern=None)
    base.update(kw)
    return Project(**base)


ENV = Environment(name="prod", prometheus_url="http://prom:9090", prometheus_auth="none",
                  prometheus_verify_tls=True, prometheus_extra_selector='cluster="prod"')


def test_build_query_workload():
    q = prom.build_query(_project(), None, ENV)
    assert q.startswith('kube_pod_container_info{namespace="argocd", pod=~"argocd\\\\-server-')
    assert 'container="server"' in q and 'cluster="prod"' in q


def test_build_query_overrides_and_statefulset():
    dep = Deployment(namespace_override="argo-dev", workload_name_override=None)
    q = prom.build_query(_project(workload_kind="statefulset"), dep, Environment(name="d"))
    assert 'namespace="argo-dev"' in q and '-[0-9]+"' in q


def test_build_query_image():
    q = prom.build_query(_project(detection_mode="image", image_pattern="quay.io/argoproj/argocd",
                                  workload_namespace=None, container_name=None), None, Environment(name="d"))
    assert 'image=~"(.*/)?quay\\\\.io/argoproj/argocd(:.*|@.*)?"' in q


def test_pick_version_prefers_majority():
    series = [
        {"metric": {"image": "quay.io/argoproj/argocd:v2.9.3"}},
        {"metric": {"image": "quay.io/argoproj/argocd:v2.9.3"}},
        {"metric": {"image": "quay.io/argoproj/argocd:v2.10.0"}},
    ]
    det = prom.pick_version(series)
    assert det.version == "2.9.3"
    assert det.images["quay.io/argoproj/argocd:v2.10.0"] == 1


@respx.mock
def test_detect_with_bearer(monkeypatch):
    monkeypatch.setenv("PROM_TOKEN", "s3cr3t")
    env = Environment(name="prod", prometheus_url="http://prom:9090", prometheus_auth="bearer",
                      prometheus_token="${PROM_TOKEN}", prometheus_verify_tls=True)
    route = respx.get("http://prom:9090/api/v1/query").mock(return_value=httpx.Response(200, json={
        "status": "success", "data": {"resultType": "vector", "result": [
            {"metric": {"image": "nginx:1.25.3-alpine"}, "value": [0, "1"]}]}}))
    det = asyncio.run(prom.detect(_project(), None, env))
    assert det.version == "1.25.3"
    assert route.calls[0].request.headers["authorization"] == "Bearer s3cr3t"


@respx.mock
def test_detect_no_series():
    respx.get("http://prom:9090/api/v1/query").mock(return_value=httpx.Response(200, json={
        "status": "success", "data": {"result": []}}))
    env = Environment(name="prod", prometheus_url="http://prom:9090", prometheus_auth="none", prometheus_verify_tls=True)
    try:
        asyncio.run(prom.detect(_project(), None, env))
    except prom.PrometheusError as exc:
        assert "nenhuma série" in str(exc)
    else:
        raise AssertionError("expected error")


def test_build_query_helm_label_with_prefix_fallback():
    q = prom.build_query(_project(detection_mode="helm", workload_name="argocd", container_name=None,
                                  image_pattern="quay.io/argoproj/argocd"), None, Environment(name="d"))
    label, _, fallback = q.partition(" or ")
    assert 'label_app_kubernetes_io_instance="argocd"' in label and "group_left()" in label
    assert 'pod=~"argocd-.+"' in fallback and "unless on()" in fallback
    assert q.count('image=~"(.*/)?quay') == 3  # the image filter applies to every branch


@respx.mock
def test_discover_groups_by_helm_release():
    def answer(request):
        q = request.url.params["query"]
        by_metric = {
            "kube_pod_container_info": [
                {"namespace": "argocd", "pod": "argocd-server-5d9c7b-abcde", "container": "server", "image": "quay.io/argoproj/argocd:v2.9.3"},
                {"namespace": "argocd", "pod": "argocd-repo-server-7f8-xyz12", "container": "repo", "image": "quay.io/argoproj/argocd:v2.9.3"},
                {"namespace": "argocd", "pod": "argocd-redis-66b-qwert", "container": "redis", "image": "redis:7.0.14"},
                {"namespace": "argocd", "pod": "argocd-image-updater-6c-zxcvb", "container": "updater", "image": "ghcr.io/argoproj-labs/argocd-image-updater:v0.12.2"},
                {"namespace": "argocd", "pod": "tools-0", "container": "tools", "image": "busybox:1.36"},
            ],
            "kube_pod_owner": [
                {"namespace": "argocd", "pod": "argocd-server-5d9c7b-abcde", "owner_kind": "ReplicaSet", "owner_name": "argocd-server-5d9c7b"},
                {"namespace": "argocd", "pod": "argocd-repo-server-7f8-xyz12", "owner_kind": "ReplicaSet", "owner_name": "argocd-repo-server-7f8"},
                {"namespace": "argocd", "pod": "argocd-redis-66b-qwert", "owner_kind": "ReplicaSet", "owner_name": "argocd-redis-66b"},
                {"namespace": "argocd", "pod": "argocd-image-updater-6c-zxcvb", "owner_kind": "ReplicaSet", "owner_name": "argocd-image-updater-6c"},
                {"namespace": "argocd", "pod": "tools-0", "owner_kind": "StatefulSet", "owner_name": "tools"},
            ],
            # Only the image-updater exports its instance label.
            "kube_pod_labels": [
                {"namespace": "argocd", "pod": "argocd-image-updater-6c-zxcvb", "label_app_kubernetes_io_instance": "argocd-image-updater"},
            ],
            "kube_secret_info": [
                {"namespace": "argocd", "secret": "sh.helm.release.v1.argocd.v1"},
                {"namespace": "argocd", "secret": "sh.helm.release.v1.argocd.v7"},
                {"namespace": "argocd", "secret": "sh.helm.release.v1.argocd-image-updater.v2"},
            ],
        }[q.split("{", 1)[0]]
        return httpx.Response(200, json={"status": "success", "data": {"result": [
            {"metric": m, "value": [0, "1"]} for m in by_metric]}})

    respx.get("http://prom:9090/api/v1/query").mock(side_effect=answer)
    env = Environment(name="prod", prometheus_url="http://prom:9090", prometheus_auth="none", prometheus_verify_tls=True)
    groups = {g["release"] or g["workloads"][0]["name"]: g for g in asyncio.run(prom.discover(env, "argocd"))}

    assert set(groups) == {"argocd", "argocd-image-updater", "tools"}
    argo = groups["argocd"]
    assert argo["helm"] and argo["revision"] == 7
    assert [w["name"] for w in argo["workloads"]] == ["argocd-redis", "argocd-repo-server", "argocd-server"]
    assert argo["version"] == "2.9.3"  # majority image wins over redis
    assert argo["suggestion"] == {"name": "argocd", "detection_mode": "helm", "workload_name": "argocd",
                                  "workload_namespace": "argocd", "source_type": "github_releases", "repository": ""}
    assert groups["argocd-image-updater"]["suggestion"]["repository"] == "argoproj-labs/argocd-image-updater"
    tools = groups["tools"]
    assert not tools["helm"] and tools["suggestion"]["detection_mode"] == "workload"
    assert tools["suggestion"]["workload_kind"] == "statefulset"
