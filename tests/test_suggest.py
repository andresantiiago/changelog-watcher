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
