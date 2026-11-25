import httpx
import respx

REG = "https://registry-1.docker.io/v2/argoproj/argocd"


def _mock_registry(mock):
    auth = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:argoproj/argocd:pull"'
    authed = {"Authorization": "Bearer tok"}
    mock.get(f"{REG}/manifests/v2.9.3", headers=authed).mock(return_value=httpx.Response(200, json={"manifests": [
        {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
        {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}},
    ]}))
    mock.get(f"{REG}/manifests/v2.9.3").mock(return_value=httpx.Response(401, headers={"www-authenticate": auth}))
    mock.get("https://auth.docker.io/token", params={"service": "registry.docker.io"}).mock(
        return_value=httpx.Response(200, json={"token": "tok"}))
    mock.get(f"{REG}/manifests/sha256:amd").mock(return_value=httpx.Response(200, json={"config": {"digest": "sha256:cfg"}}))
    mock.get(f"{REG}/blobs/sha256:cfg").mock(return_value=httpx.Response(200, json={"config": {"Labels": {
        "org.opencontainers.image.source": "https://github.com/argoproj/argo-cd.git"}}}))


def test_suggest_source(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        _mock_registry(mock)
        mock.get("https://artifacthub.io/api/v1/packages/search").mock(return_value=httpx.Response(200, json={"packages": [
            {"name": "argo-cd", "stars": 21, "repository": {"name": "bitnami", "url": "https://charts.bitnami.com/bitnami", "verified_publisher": True}},
            {"name": "argo-cd", "stars": 900, "repository": {"name": "oci", "url": "oci://ghcr.io/argoproj/argo-helm/argo-cd"}},
            {"name": "argo-cd-crds", "stars": 5, "repository": {"name": "x", "url": "https://x.test"}},
            {"name": "argo-cd", "stars": 846, "repository": {"name": "argo", "url": "https://argoproj.github.io/argo-helm", "verified_publisher": True}},
        ]}))
        mock.get("https://api.github.com/search/repositories").mock(return_value=httpx.Response(200, json={"items": [
            {"name": "DescomplicandoArgoCD", "full_name": "badtuxx/DescomplicandoArgoCD", "stargazers_count": 284},
            {"name": "argo-cd", "full_name": "argoproj/argo-cd", "stargazers_count": 18000},
        ]}))
        r = client.get("/api/suggest-source", params={"name": "argocd", "image": "argoproj/argocd:v2.9.3", "chart": "argo-cd-5.46.7"})

    got = [(c["source_type"], c["repository"]) for c in r.json()["candidates"]]
    assert got == [
        ("github_releases", "argoproj/argo-cd"),  # image label; the GitHub search hit is deduplicated
        ("helm_index", "https://argoproj.github.io/argo-helm"),  # exact chart name, most stars; oci:// skipped
        ("helm_index", "https://charts.bitnami.com/bitnami"),
        ("dockerhub", "argoproj/argocd"),
    ]
    assert r.json()["candidates"][1]["chart_name"] == "argo-cd"


def test_suggest_source_failures_are_skipped(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        mock.get(host="ghcr.io").mock(return_value=httpx.Response(500))
        mock.get(host="artifacthub.io").mock(side_effect=httpx.ConnectError("offline"))
        mock.get(host="api.github.com").mock(return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"}))
        r = client.get("/api/suggest-source", params={"image": "ghcr.io/stefanprodan/podinfo:6.7.1"})
    assert [(c["source_type"], c["repository"]) for c in r.json()["candidates"]] == [("github_releases", "stefanprodan/podinfo")]


def test_suggest_source_by_digest_skips_attestations(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        mock.get(host="api.github.com").mock(return_value=httpx.Response(200, json={"items": []}))
        mock.get(host="artifacthub.io").mock(return_value=httpx.Response(200, json={"packages": []}))
        q = "https://quay.io/v2/org/app"
        mock.get(f"{q}/manifests/sha256:idx").mock(return_value=httpx.Response(200, json={"manifests": [
            {"digest": "sha256:att", "platform": {"os": "unknown", "architecture": "unknown"}},
            {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
        ]}))
        mock.get(f"{q}/manifests/sha256:arm").mock(return_value=httpx.Response(200, json={"config": {"digest": "sha256:cfg"}}))
        mock.get(f"{q}/blobs/sha256:cfg").mock(return_value=httpx.Response(200, json={"config": {"Labels": {
            "org.opencontainers.image.source": "https://github.com/org/app"}}}))
        r = client.get("/api/suggest-source", params={"image": "quay.io/org/app:1.0@sha256:idx"})
    assert r.json()["candidates"][0]["repository"] == "org/app"


def test_suggest_source_only_public_registries(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        mock.get(host="api.github.com").mock(return_value=httpx.Response(200, json={"items": []}))
        mock.get(host="artifacthub.io").mock(return_value=httpx.Response(200, json={"packages": []}))
        internal = mock.get(host="10.0.0.5").mock(return_value=httpx.Response(200, json={}))
        metadata = mock.get(host="169.254.169.254").mock(return_value=httpx.Response(200, json={"token": "x"}))
        mock.get(host="ghcr.io").mock(return_value=httpx.Response(
            401, headers={"www-authenticate": 'Bearer realm="https://169.254.169.254/token"'}))
        client.get("/api/suggest-source", params={"image": "10.0.0.5:5000/org/app:1"})
        client.get("/api/suggest-source", params={"image": "ghcr.io/org/app:1"})
    assert not internal.called and not metadata.called  # registry fora da lista e realm de outro domínio


def test_chart_name():
    from changelog_watcher.suggest import chart_name
    assert chart_name("argo-cd-5.46.7") == "argo-cd"
    assert chart_name("app-1.2.3-rc.1") == "app"
    assert chart_name("k8s-1.2.0-exporter-0.3.1") == "k8s-1.2.0-exporter"  # só o último -X.Y.Z é versão


def _hub_search(mock, query, results):
    mock.get("https://hub.docker.com/v2/search/repositories/", params={"query": query}).mock(return_value=httpx.Response(200, json={"results": results}))


def _hub_image(mock, repo, tag, labels):
    base = f"https://registry-1.docker.io/v2/{repo}"
    mock.get(f"{base}/manifests/{tag}").mock(return_value=httpx.Response(200, json={"config": {"digest": "sha256:c"}}))
    mock.get(f"{base}/blobs/sha256:c").mock(return_value=httpx.Response(200, json={"config": {"Labels": labels}}))


def test_suggest_source_internal_mirror_of_official_image(client):
    """registry.intranet/nginx run by a workload named "frontend": the image name wins."""
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        mock.get(host="artifacthub.io").mock(return_value=httpx.Response(200, json={"packages": []}))
        _hub_search(mock, "nginx", [{"repo_name": "nginx", "is_official": True, "pull_count": 10**10},
                           {"repo_name": "fulano/nginx", "pull_count": 50}])
        _hub_image(mock, "library/nginx", "1.30.0", {})
        mock.get("https://api.github.com/search/repositories", params={"q": "nginx in:name"}).mock(
            return_value=httpx.Response(200, json={"items": [{"name": "nginx", "full_name": "nginx/nginx", "stargazers_count": 33000}]}))
        mock.get("https://api.github.com/search/repositories", params={"q": "frontend in:name"}).mock(
            return_value=httpx.Response(200, json={"items": [{"name": "frontend", "full_name": "x/frontend", "stargazers_count": 3}]}))
        r = client.get("/api/suggest-source", params={"name": "frontend", "image": "registry.intranet/nginx:1.30.0"})
    assert [(c["source_type"], c["repository"]) for c in r.json()["candidates"]] == [
        ("dockerhub", "nginx"),  # mesma tag existe na imagem oficial
        ("github_releases", "nginx/nginx"),  # busca pelo nome da imagem; x/frontend (★ 3) é ruído
    ]


def test_suggest_source_internal_mirror_label(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        mock.get(host="artifacthub.io").mock(return_value=httpx.Response(200, json={"packages": []}))
        mock.get(host="api.github.com").mock(return_value=httpx.Response(200, json={"items": []}))
        mock.get(host="ghcr.io").mock(return_value=httpx.Response(404))  # ghcr.io/plataforma/podinfo
        mock.get("https://registry-1.docker.io/v2/plataforma/podinfo/manifests/6.17.0").mock(return_value=httpx.Response(401))
        _hub_search(mock, "podinfo", [{"repo_name": "stefanprodan/podinfo", "pull_count": 8 * 10**8}])
        _hub_image(mock, "stefanprodan/podinfo", "6.17.0", {"org.opencontainers.image.source": "https://github.com/stefanprodan/podinfo"})
        r = client.get("/api/suggest-source", params={"image": "registry.intranet/plataforma/podinfo:6.17.0"})
        assert [(c["source_type"], c["repository"]) for c in r.json()["candidates"]] == [
            ("github_releases", "stefanprodan/podinfo"), ("dockerhub", "stefanprodan/podinfo")]
        _hub_image(mock, "fulano/pagamentos-api", "2.3.1", {})  # existe, mas é pessoal (poucos pulls)
        mock.get(host="registry-1.docker.io").mock(return_value=httpx.Response(401))  # time/pagamentos-api
        _hub_search(mock, "pagamentos-api", [{"repo_name": "fulano/pagamentos-api", "pull_count": 300}])  # imagem pessoal: ignorada
        r = client.get("/api/suggest-source", params={"image": "registry.intranet/time/pagamentos-api:2.3.1"})
        assert r.json()["candidates"] == []


def test_public_refs_keeps_original_registry_in_path():
    import asyncio
    from changelog_watcher.suggest import _public_refs
    assert asyncio.run(_public_refs(None, "harbor.corp/proxy/quay.io/prometheus/prometheus:v3.1.0")) == [
        "quay.io/prometheus/prometheus:v3.1.0"]
    assert asyncio.run(_public_refs(None, "ghcr.io/org/app:1")) == []  # já é pública
