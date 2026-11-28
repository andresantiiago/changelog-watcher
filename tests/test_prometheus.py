import asyncio

import httpx
import respx

from changelog_watcher import prometheus as prom
from changelog_watcher.models import Deployment, Environment, Project


def _project(**kw):
    base = dict(
        name="argocd",
        detection_mode="workload",
        workload_kind="deployment",
        workload_namespace="argocd",
        workload_name="argocd-server",
        container_name="server",
        image_pattern=None,
        promql_query=None,
        image_version_pattern=None,
    )
    base.update(kw)
    return Project(**base)


ENV = Environment(
    name="prod",
    prometheus_url="http://prom:9090",
    prometheus_auth="none",
    prometheus_verify_tls=True,
    prometheus_extra_selector='cluster="prod"',
)


def test_build_query_workload():
    q = prom.build_query(_project(), None, ENV)
    assert q.startswith('kube_pod_container_info{namespace="argocd", pod=~"argocd\\\\-server-')
    assert 'container="server"' in q and 'cluster="prod"' in q


def test_build_query_overrides_and_statefulset():
    dep = Deployment(namespace_override="argo-dev", workload_name_override=None)
    q = prom.build_query(_project(workload_kind="statefulset"), dep, Environment(name="d"))
    assert 'namespace="argo-dev"' in q and '-[0-9]+"' in q


def test_build_query_image():
    q = prom.build_query(
        _project(
            detection_mode="image",
            image_pattern="quay.io/argoproj/argocd",
            workload_namespace=None,
            container_name=None,
        ),
        None,
        Environment(name="d"),
    )
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


def test_pick_version_tie_prefers_image_named_by_release():
    series = [
        {"metric": {"image": "registry.intranet/prometheus/prometheus:v3.8.0"}},
        {"metric": {"image": "registry.intranet/thanosio/thanos:v0.40.1"}},
    ]
    assert prom.pick_version(series).version == "0.40.1"  # no hint: oldest
    assert prom.pick_version(series, prefer="kube-prometheus-stack-prometheus").version == "3.8.0"


@respx.mock
def test_detect_with_bearer(monkeypatch):
    monkeypatch.setenv("PROM_TOKEN", "s3cr3t")
    env = Environment(
        name="prod",
        prometheus_url="http://prom:9090",
        prometheus_auth="bearer",
        prometheus_token="${PROM_TOKEN}",
        prometheus_verify_tls=True,
    )
    route = respx.get("http://prom:9090/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {"image": "nginx:1.25.3-alpine"}, "value": [0, "1"]}],
                },
            },
        )
    )
    det = asyncio.run(prom.detect(_project(), None, env))
    assert det.version == "1.25.3"
    assert route.calls[0].request.headers["authorization"] == "Bearer s3cr3t"


@respx.mock
def test_detect_no_series():
    respx.get("http://prom:9090/api/v1/query").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"result": []}})
    )
    env = Environment(
        name="prod", prometheus_url="http://prom:9090", prometheus_auth="none", prometheus_verify_tls=True
    )
    try:
        asyncio.run(prom.detect(_project(), None, env))
    except prom.PrometheusError as exc:
        assert "no series" in str(exc)
    else:
        raise AssertionError("expected error")


def test_build_query_helm_label_with_prefix_fallback():
    q = prom.build_query(
        _project(
            detection_mode="helm", workload_name="argocd", container_name=None, image_pattern="quay.io/argoproj/argocd"
        ),
        None,
        Environment(name="d"),
    )
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
                {
                    "namespace": "argocd",
                    "pod": "argocd-server-5d9c7b-abcde",
                    "container": "server",
                    "image": "quay.io/argoproj/argocd:v2.9.3",
                },
                {
                    "namespace": "argocd",
                    "pod": "argocd-repo-server-7f8-xyz12",
                    "container": "repo",
                    "image": "quay.io/argoproj/argocd:v2.9.3",
                },
                {"namespace": "argocd", "pod": "argocd-redis-66b-qwert", "container": "redis", "image": "redis:7.0.14"},
                {
                    "namespace": "argocd",
                    "pod": "argocd-image-updater-6c-zxcvb",
                    "container": "updater",
                    "image": "ghcr.io/argoproj-labs/argocd-image-updater:v0.12.2",
                },
                {"namespace": "argocd", "pod": "tools-0", "container": "tools", "image": "busybox:1.36"},
            ],
            "kube_pod_owner": [
                {
                    "namespace": "argocd",
                    "pod": "argocd-server-5d9c7b-abcde",
                    "owner_kind": "ReplicaSet",
                    "owner_name": "argocd-server-5d9c7b",
                },
                {
                    "namespace": "argocd",
                    "pod": "argocd-repo-server-7f8-xyz12",
                    "owner_kind": "ReplicaSet",
                    "owner_name": "argocd-repo-server-7f8",
                },
                {
                    "namespace": "argocd",
                    "pod": "argocd-redis-66b-qwert",
                    "owner_kind": "ReplicaSet",
                    "owner_name": "argocd-redis-66b",
                },
                {
                    "namespace": "argocd",
                    "pod": "argocd-image-updater-6c-zxcvb",
                    "owner_kind": "ReplicaSet",
                    "owner_name": "argocd-image-updater-6c",
                },
                {"namespace": "argocd", "pod": "tools-0", "owner_kind": "StatefulSet", "owner_name": "tools"},
            ],
            # Only the image-updater exports its instance label.
            "kube_pod_labels": [
                {
                    "namespace": "argocd",
                    "pod": "argocd-image-updater-6c-zxcvb",
                    "label_app_kubernetes_io_instance": "argocd-image-updater",
                },
            ],
            "kube_secret_info": [
                {"namespace": "argocd", "secret": "sh.helm.release.v1.argocd.v1"},
                {"namespace": "argocd", "secret": "sh.helm.release.v1.argocd.v7"},
                {"namespace": "argocd", "secret": "sh.helm.release.v1.argocd-image-updater.v2"},
            ],
        }[q.split("{", 1)[0]]
        return httpx.Response(
            200, json={"status": "success", "data": {"result": [{"metric": m, "value": [0, "1"]} for m in by_metric]}}
        )

    respx.get("http://prom:9090/api/v1/query").mock(side_effect=answer)
    env = Environment(
        name="prod", prometheus_url="http://prom:9090", prometheus_auth="none", prometheus_verify_tls=True
    )
    groups = {g["release"] or g["workloads"][0]["name"]: g for g in asyncio.run(prom.discover(env, "argocd"))}

    assert set(groups) == {"argocd", "argocd-image-updater", "tools"}
    argo = groups["argocd"]
    assert argo["helm"] and argo["revision"] == 7
    assert [w["name"] for w in argo["workloads"]] == ["argocd-redis", "argocd-repo-server", "argocd-server"]
    assert argo["version"] == "2.9.3"  # majority image wins over redis
    # Two images (argocd + redis): one component each, pinned by image_pattern.
    assert argo["suggestion"] == {
        "name": "argocd",
        "detection_mode": "helm",
        "workload_name": "argocd",
        "image_pattern": "quay.io/argoproj/argocd",
        "workload_namespace": "argocd",
        "source_type": "github_releases",
        "repository": "",
    }
    assert [c["repository"] for c in argo["components"]] == ["quay.io/argoproj/argocd", "redis"]
    assert argo["components"][1]["suggestion"]["name"] == "argocd (redis)"
    assert argo["components"][1]["suggestion"]["image_pattern"] == "redis"
    assert groups["argocd-image-updater"]["parent"] is None  # a Helm release of its own
    assert groups["argocd-image-updater"]["suggestion"]["repository"] == "argoproj-labs/argocd-image-updater"
    tools = groups["tools"]
    assert not tools["helm"] and tools["suggestion"]["detection_mode"] == "workload"
    assert tools["suggestion"]["workload_kind"] == "statefulset"
    assert "image_pattern" not in tools["suggestion"] and len(tools["components"]) == 1


def _answer(by_metric):
    def answer(request):
        series = by_metric.get(request.url.params["query"].split("{", 1)[0], [])
        return httpx.Response(
            200, json={"status": "success", "data": {"result": [{"metric": m, "value": [0, "1"]} for m in series]}}
        )

    return answer


@respx.mock
def test_discover_splits_images_and_links_operator_pods_to_release():
    ns = "kube-monitoring"
    pod = "prometheus-kube-prometheus-stack-prometheus-0"
    op = "kube-prometheus-stack-operator-5d9c7b-abcde"
    respx.get("http://prom:9090/api/v1/query").mock(
        side_effect=_answer(
            {
                "kube_pod_container_info": [
                    {
                        "namespace": ns,
                        "pod": pod,
                        "container": "prometheus",
                        "image": "registry.intranet/prometheus/prometheus:v3.8.0",
                    },
                    {
                        "namespace": ns,
                        "pod": pod,
                        "container": "config-reloader",
                        "image": "registry.intranet/prometheus-operator/prometheus-config-reloader:v0.87.1",
                    },
                    {
                        "namespace": ns,
                        "pod": pod,
                        "container": "thanos-sidecar",
                        "image": "registry.intranet/thanosio/thanos:v0.40.1",
                    },
                    {
                        "namespace": ns,
                        "pod": op,
                        "container": "operator",
                        "image": "registry.intranet/prometheus-operator/prometheus-operator:v0.87.1",
                    },
                ],
                "kube_pod_owner": [
                    {
                        "namespace": ns,
                        "pod": pod,
                        "owner_kind": "StatefulSet",
                        "owner_name": "prometheus-kube-prometheus-stack-prometheus",
                    },
                    {
                        "namespace": ns,
                        "pod": op,
                        "owner_kind": "ReplicaSet",
                        "owner_name": "kube-prometheus-stack-operator-5d9c7b",
                    },
                ],
                # The operator labels the Prometheus pods with the Prometheus CR name, not the Helm release.
                "kube_pod_labels": [
                    {
                        "namespace": ns,
                        "pod": pod,
                        "label_app_kubernetes_io_instance": "kube-prometheus-stack-prometheus",
                    },
                    {
                        "namespace": ns,
                        "pod": op,
                        "label_app_kubernetes_io_instance": "kube-prometheus-stack",
                        "label_helm_sh_chart": "kube-prometheus-stack-79.0.0",
                    },
                ],
                "kube_secret_info": [{"namespace": ns, "secret": "sh.helm.release.v1.kube-prometheus-stack.v3"}],
            }
        )
    )
    env = Environment(
        name="prod", prometheus_url="http://prom:9090", prometheus_auth="none", prometheus_verify_tls=True
    )
    projects = [
        # Already tracked: the thanos sidecar, pinned by image; and an older unpinned project (picks the main image).
        Project(
            id=1,
            name="thanos",
            detection_mode="helm",
            workload_namespace=ns,
            workload_name="kube-prometheus-stack-prometheus",
            image_pattern="registry.intranet/thanosio/thanos",
        ),
        Project(
            id=2,
            name="prometheus",
            detection_mode="helm",
            workload_namespace=ns,
            workload_name="kube-prometheus-stack-prometheus",
        ),
    ]
    out = asyncio.run(prom.discover(env, ns, projects))

    assert [g["release"] for g in out] == ["kube-prometheus-stack", "kube-prometheus-stack-prometheus"]
    stack, prom_group = out
    assert stack["helm"] and stack["parent"] is None and stack["chart"] == "kube-prometheus-stack-79.0.0"
    assert not prom_group["helm"] and prom_group["parent"] == "kube-prometheus-stack"
    comps = {c["repository"].rsplit("/", 1)[-1]: c for c in prom_group["components"]}
    assert prom_group["components"][0] is comps["prometheus"]  # named after the group, not the oldest tag
    assert prom_group["version"] == "3.8.0"
    assert comps["prometheus"]["suggestion"]["name"] == "kube-prometheus-stack-prometheus"
    assert comps["thanos"]["suggestion"] == {
        "name": "kube-prometheus-stack-prometheus (thanos)",
        "detection_mode": "helm",
        "workload_name": "kube-prometheus-stack-prometheus",
        "image_pattern": "registry.intranet/thanosio/thanos",
        "workload_namespace": ns,
        "source_type": "github_releases",
        "repository": "",
    }
    assert comps["thanos"]["version"] == "0.40.1"
    assert comps["thanos"]["containers"] == [
        {
            "kind": "statefulset",
            "workload": "prometheus-kube-prometheus-stack-prometheus",
            "container": "thanos-sidecar",
        }
    ]
    assert comps["thanos"]["project"] == {"id": 1, "name": "thanos"}
    assert comps["prometheus"]["project"] == {"id": 2, "name": "prometheus"}
    assert comps["prometheus-config-reloader"]["project"] is None
    assert prom_group["project"] == {"id": 2, "name": "prometheus"}


@respx.mock
def test_discover_workload_with_sidecars_pins_container():
    respx.get("http://prom:9090/api/v1/query").mock(
        side_effect=_answer(
            {
                "kube_pod_container_info": [
                    {
                        "namespace": "apps",
                        "pod": "api-7f8-xyz12",
                        "container": "api",
                        "image": "ghcr.io/acme/api:1.4.0",
                    },
                    {
                        "namespace": "apps",
                        "pod": "api-7f8-xyz12",
                        "container": "proxy",
                        "image": "envoyproxy/envoy:v1.31.0",
                    },
                ],
                "kube_pod_owner": [
                    {"namespace": "apps", "pod": "api-7f8-xyz12", "owner_kind": "ReplicaSet", "owner_name": "api-7f8"}
                ],
            }
        )
    )
    env = Environment(
        name="prod", prometheus_url="http://prom:9090", prometheus_auth="none", prometheus_verify_tls=True
    )
    projects = [
        Project(
            id=5,
            name="api",
            detection_mode="workload",
            workload_kind="deployment",
            workload_namespace="apps",
            workload_name="api",
            container_name="proxy",
        )
    ]
    (g,) = asyncio.run(prom.discover(env, "apps", projects))
    api, envoy = g["components"]
    assert api["repository"] == "ghcr.io/acme/api" and api["suggestion"]["container_name"] == "api"
    assert api["suggestion"]["repository"] == "acme/api" and api["project"] is None
    assert envoy["suggestion"]["container_name"] == "proxy" and envoy["suggestion"]["name"] == "api (envoy)"
    assert envoy["project"] == {"id": 5, "name": "api"}


@respx.mock
def test_instant_query_non_json_is_prometheus_error():
    respx.get("http://prom:9090/api/v1/query").mock(return_value=httpx.Response(200, text="<html>login</html>"))
    env = Environment(
        name="prod", prometheus_url="http://prom:9090", prometheus_auth="none", prometheus_verify_tls=True
    )
    try:
        asyncio.run(prom.instant_query(env, "up"))
    except prom.PrometheusError as exc:
        assert "invalid" in str(exc)
    else:
        raise AssertionError("expected error")
