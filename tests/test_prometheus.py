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
