"""OIDC login (Authorization Code + PKCE) against a mocked provider (discovery, token, JWKS)."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from changelog_watcher.auth import SESSION_COOKIE, safe_next
from changelog_watcher.config import get_settings

ISSUER = "https://dex.example.com/dex"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWKS = {"keys": [{**RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1", "alg": "RS256", "use": "sig"}]}
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/auth",
    "token_endpoint": f"{ISSUER}/token",
    "jwks_uri": f"{ISSUER}/keys",
}
ENV = {
    "OIDC_ISSUER_URL": ISSUER,
    "OIDC_CLIENT_ID": "changelog-watcher",
    "OIDC_CLIENT_SECRET": "client-s3cret",
    "SESSION_SECRET": "x" * 40,
    "PUBLIC_URL": "https://cw.example.com",
    "OIDC_EDITOR_GROUPS": "platform, sre",
    "OIDC_VIEWER_GROUPS": "devs",
}


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    """Routes cache get_settings() with the OIDC env: drop it before monkeypatch restores the env."""
    yield
    get_settings.cache_clear()


def _app(monkeypatch, **env):
    from changelog_watcher.main import create_app

    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    try:
        return create_app()
    finally:
        get_settings.cache_clear()


@pytest.fixture()
def oidc(client, monkeypatch):  # client: fresh in-memory DB
    c = TestClient(_app(monkeypatch, **ENV), base_url="https://testserver", follow_redirects=False)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(return_value=httpx.Response(200, json=DISCOVERY))
        mock.get(f"{ISSUER}/keys").mock(return_value=httpx.Response(200, json=JWKS))
        c.mock = mock
        yield c


def id_token(nonce, key=KEY, alg="RS256", kid="k1", **claims):
    now = int(time.time())
    body = {"iss": ISSUER, "aud": "changelog-watcher", "sub": "u1", "name": "Ana", "iat": now, "exp": now + 300}
    body |= {"nonce": nonce, "groups": ["platform"]} | claims
    return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm=alg, headers={"kid": kid})


def login(c, next="/", state=None, **token):
    """Go through /auth/login and /auth/callback; ``token`` customizes the ID token."""
    r = c.get("/auth/login", params={"next": next})
    assert r.status_code == 303
    auth = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(auth.query).items()}
    assert f"{auth.scheme}://{auth.netloc}{auth.path}" == f"{ISSUER}/auth"
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"] == "https://cw.example.com/auth/callback"
    assert q["client_id"] == "changelog-watcher" and "groups" in q["scope"].split()
    nonce = token.pop("nonce", q["nonce"])
    tok = token.pop("raw", None) or id_token(nonce, **token)
    c.mock.post(f"{ISSUER}/token").mock(return_value=httpx.Response(200, json={"id_token": tok, "access_token": "a"}))
    resp = c.get("/auth/callback", params={"code": "the-code", "state": state or q["state"]})
    return resp, q


def test_full_flow_editor(oidc):
    resp, q = login(oidc, next="/#/apps")
    assert resp.status_code == 303 and resp.headers["location"] == "/#/apps"
    cookie = next(h for h in resp.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE))
    assert (
        "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower() and "Max-Age=43200" in cookie
    )
    # PKCE: the verifier sent to the token endpoint hashes to the challenge of the authorization request
    token_req = next(call.request for call in oidc.mock.calls if call.request.method == "POST")
    sent = parse_qs(token_req.content.decode())
    digest = hashlib.sha256(sent["code_verifier"][0].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == q["code_challenge"]
    assert sent["code"] == ["the-code"] and sent["grant_type"] == ["authorization_code"]
    assert token_req.headers["authorization"].startswith("Basic ")

    assert oidc.get("/api/me").json() == {"name": "Ana", "can_edit": True, "auth": "oidc"}
    assert oidc.post("/api/environments", json={"name": "prod"}).status_code == 201
    # secrets never come back through the API
    for path in ("/api/meta", "/api/settings", "/api/me"):
        assert "client-s3cret" not in oidc.get(path).text and "x" * 40 not in oidc.get(path).text


def test_viewer_is_read_only(oidc):
    resp, _ = login(oidc, groups=["devs"])
    assert resp.status_code == 303
    assert oidc.get("/api/me").json()["can_edit"] is False
    assert oidc.get("/api/environments").status_code == 200
    assert oidc.post("/api/environments", json={"name": "x"}).status_code == 403
    assert oidc.delete("/api/environments/1").status_code == 403
    assert oidc.post("/api/refresh").status_code == 403


def test_group_without_permission(oidc):
    resp, _ = login(oidc, groups=["marketing"])
    assert resp.status_code == 403 and SESSION_COOKIE not in resp.headers.get("set-cookie", "")
    assert oidc.get("/api/me").status_code == 401
    resp, _ = login(oidc, groups=None)  # no groups claim at all
    assert resp.status_code == 403
    resp, _ = login(oidc, groups="platform")  # not a list: refused, not a 500
    assert resp.status_code == 403


def test_groups_with_non_string_items(oidc):
    resp, _ = login(oidc, groups=[1, {"a": 1}, ["platform"], "platform"])
    assert resp.status_code == 303 and oidc.get("/api/me").json()["can_edit"] is True


def test_login_logs_sub_not_personal_data(oidc, caplog):
    caplog.set_level("INFO", logger="changelog_watcher.auth")
    login(oidc, email="ana@example.com")
    login(oidc, groups=["marketing"], email="ana@example.com")
    assert "u1" in caplog.text and "Ana" not in caplog.text and "ana@example.com" not in caplog.text


@pytest.mark.parametrize(
    "token",
    [
        {"nonce": "other-nonce"},
        {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},  # expired
        {"aud": "another-client"},
        {"aud": ["changelog-watcher", "another-client"]},  # several audiences without azp
        {"azp": "another-client"},  # azp, when present, must be this client
        {"iss": "https://evil.example"},
        {"key": OTHER_KEY},  # invalid signature (same kid, other key)
        {"kid": "unknown"},
        {"key": "y" * 32, "alg": "HS256"},  # symmetric algorithm refused
        {"iat": None},
    ],
)
def test_invalid_id_token(oidc, token):
    resp, _ = login(oidc, **token)
    assert resp.status_code == 400 and SESSION_COOKIE not in resp.headers.get("set-cookie", "")
    assert oidc.get("/api/me").status_code == 401


def test_jwks_encryption_keys_ignored(oidc):
    oidc.mock.get(f"{ISSUER}/keys").mock(
        return_value=httpx.Response(200, json={"keys": [JWKS["keys"][0] | {"use": "enc"}]})
    )
    resp, _ = login(oidc)
    assert resp.status_code == 400


def test_alg_none_refused(oidc):
    header = base64.urlsafe_b64encode(b'{"alg":"none","kid":"k1"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(b'{"iss":"%s","aud":"changelog-watcher","sub":"u"}' % ISSUER.encode())
    resp, _ = login(oidc, raw=f"{header}.{payload.rstrip(b'=').decode()}.")
    assert resp.status_code == 400


def test_wrong_state_and_missing_cookie(oidc):
    resp, _ = login(oidc, state="forged")
    assert resp.status_code == 400
    fresh = TestClient(oidc.app, base_url="https://testserver", follow_redirects=False)
    assert fresh.get("/auth/callback", params={"code": "c", "state": "s"}).status_code == 400  # no login cookie


def test_provider_error_and_token_endpoint_failure(oidc):
    r = oidc.get("/auth/login")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    assert oidc.get("/auth/callback", params={"state": state, "error": "access_denied"}).status_code == 400
    oidc.mock.post(f"{ISSUER}/token").mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    assert oidc.get("/auth/callback", params={"state": state, "code": "c"}).status_code == 400


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/",
        "//evil.example",
        "/\\evil.example",
        "\\\\evil.example",
        "/\t/evil.example",
        "javascript:alert(1)",
        "evil",
        "",
    ],
)
def test_open_redirect(target):
    assert safe_next(target) == "/"


def test_open_redirect_flow(oidc):
    resp, _ = login(oidc, next="https://evil.example/x")
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    assert safe_next("/apps?x=1#/b") == "/apps?x=1#/b"


def test_unauthenticated_api_401_ui_redirect(oidc):
    r = oidc.get("/api/environments")
    assert r.status_code == 401 and "www-authenticate" not in r.headers  # no Basic dialog in the browser
    assert oidc.post("/api/refresh").status_code == 401
    r = oidc.get("/", headers={"Accept": "text/html"})
    assert r.status_code == 303 and r.headers["location"] == "/auth/login?next=%2F"
    assert oidc.get("/static/app.js").status_code == 401
    assert oidc.get("/healthz").status_code == 200 and oidc.get("/readyz").status_code == 200
    assert oidc.get("/metrics").status_code == 200  # unchanged: METRICS_TOKEN only


def test_forged_session_cookie(oidc):
    now = int(time.time())
    claims = {"name": "x", "edit": True, "aud": "session", "iat": now, "exp": now + 60}
    oidc.cookies.set(SESSION_COOKIE, jwt.encode(claims, "z" * 40, algorithm="HS256"))  # other secret
    assert oidc.get("/api/me").status_code == 401
    # the login cookie (signed with the right secret) is not a session
    oidc.cookies.set(SESSION_COOKIE, jwt.encode(claims | {"aud": "login"}, "x" * 40, algorithm="HS256"))
    assert oidc.get("/api/me").status_code == 401
    oidc.cookies.set(SESSION_COOKIE, jwt.encode(claims | {"exp": now - 10}, "x" * 40, algorithm="HS256"))  # expired
    assert oidc.get("/api/me").status_code == 401


def test_logout(oidc):
    login(oidc)
    r = oidc.post("/auth/logout")
    assert r.status_code == 303 and r.headers["location"] == "/auth/logged-out"
    assert oidc.get("/api/me").status_code == 401
    assert oidc.get("/auth/logged-out").status_code == 200


def test_logout_end_session_endpoint(oidc):
    oidc.mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(200, json=DISCOVERY | {"end_session_endpoint": f"{ISSUER}/logout"})
    )
    login(oidc)
    loc = urlsplit(oidc.post("/auth/logout").headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == f"{ISSUER}/logout"
    q = parse_qs(loc.query)
    assert q["post_logout_redirect_uri"] == ["https://cw.example.com/auth/logged-out"]


def test_discovery_issuer_mismatch(oidc):
    oidc.mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(200, json=DISCOVERY | {"issuer": "https://evil.example"})
    )
    assert oidc.get("/auth/login").status_code == 502


def test_discovery_not_an_object(oidc):
    oidc.mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(return_value=httpx.Response(200, json=[1]))
    assert oidc.get("/auth/login").status_code == 502
    r = oidc.post("/auth/logout")  # local logout still works
    assert r.status_code == 303 and r.headers["location"] == "/auth/logged-out"


def test_basic_auth_alongside_oidc(client, monkeypatch):
    c = TestClient(_app(monkeypatch, **ENV, AUTH_USERNAME="admin", AUTH_PASSWORD="pw"), base_url="https://testserver")
    assert c.post("/api/environments", json={"name": "b"}, auth=("admin", "pw")).status_code == 201
    assert c.get("/api/me", auth=("admin", "pw")).json() == {"name": "admin", "can_edit": True, "auth": "basic"}
    assert c.get("/api/me", auth=("admin", "wrong")).status_code == 401


@pytest.mark.parametrize(
    ("drop", "extra", "match"),
    [
        ("OIDC_CLIENT_SECRET", {}, "OIDC_CLIENT_SECRET"),
        ("SESSION_SECRET", {}, "SESSION_SECRET"),
        ("PUBLIC_URL", {}, "PUBLIC_URL"),
        ("OIDC_ISSUER_URL", {}, "OIDC_ISSUER_URL"),
        (None, {"OIDC_EDITOR_GROUPS": "", "OIDC_VIEWER_GROUPS": ""}, "GROUPS"),
        (None, {"SESSION_SECRET": "short"}, "32 characters"),
        (None, {"OIDC_ISSUER_URL": "http://dex.example.com"}, "OIDC_ISSUER_URL must use https"),
        (None, {"PUBLIC_URL": "http://cw.example.com"}, "PUBLIC_URL must use https"),
        (None, {"SESSION_MAX_AGE_HOURS": "0"}, "SESSION_MAX_AGE_HOURS"),
        (None, {"SESSION_MAX_AGE_HOURS": "inf"}, "SESSION_MAX_AGE_HOURS"),
        (None, {"SESSION_MAX_AGE_HOURS": "nan"}, "SESSION_MAX_AGE_HOURS"),
        (None, {"OIDC_SCOPES": "email groups"}, "openid"),
    ],
)
def test_incomplete_oidc_config_fails(monkeypatch, drop, extra, match):
    env = {k: v for k, v in ENV.items() if k != drop} | extra
    with pytest.raises(RuntimeError, match=match):
        _app(monkeypatch, **env)


def test_local_http_urls_allowed(monkeypatch):
    _app(monkeypatch, **ENV | {"PUBLIC_URL": "http://localhost:8080", "OIDC_ISSUER_URL": "http://127.0.0.1:5556/dex"})


def test_auth_disabled_has_no_user(client):
    assert client.get("/api/me").json() == {"name": None, "can_edit": True, "auth": None}
