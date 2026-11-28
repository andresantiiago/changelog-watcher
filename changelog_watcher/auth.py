"""Authentication: Basic Auth and/or OIDC login (Authorization Code + PKCE) with a signed session cookie.

Without a valid credential, API calls get 401 and UI navigation is redirected to the OIDC login.
OIDC users are editors or read-only (safe methods only) according to their ``groups`` claim.
"""

from __future__ import annotations

import base64
import hashlib
import html
import logging
import math
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .config import Settings
from .i18n import _

log = logging.getLogger(__name__)

# __Host-: only set by this exact origin over HTTPS (no Domain, Path=/)
SESSION_COOKIE = "__Host-cw-session"
LOGIN_COOKIE = "__Host-cw-login"
LOGIN_TTL = 600  # seconds to come back from the provider
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
# ID token: asymmetric algorithms only, never "none" nor HS* (where the public key would become the HMAC secret).
ID_TOKEN_ALGS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]
OPEN_PATHS = {"/healthz", "/readyz", "/metrics"}  # /metrics: METRICS_TOKEN, checked by its route
OIDC_PATHS = {"/auth/login", "/auth/callback", "/auth/logout", "/auth/logged-out"}
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")  # browsers treat these as secure: Secure cookies work over http


def oidc_enabled(s: Settings) -> bool:
    return bool(s.oidc_issuer_url or s.oidc_client_id or s.oidc_client_secret)


def check_config(s: Settings) -> None:
    """Fail closed: refuse to start without any authentication or with an incomplete OIDC setup."""
    if not oidc_enabled(s):
        if not (s.auth_username and s.auth_password) and not s.auth_disabled:
            # An open API lets anyone reconfigure the app and reach internal hosts.
            raise RuntimeError(
                "set AUTH_USERNAME and AUTH_PASSWORD and/or OIDC_ISSUER_URL, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET"
                " (or AUTH_DISABLED=true to run without authentication)"
            )
        return
    required = {
        "OIDC_ISSUER_URL": s.oidc_issuer_url,
        "OIDC_CLIENT_ID": s.oidc_client_id,
        "OIDC_CLIENT_SECRET": s.oidc_client_secret,
        "SESSION_SECRET": s.session_secret,
        "PUBLIC_URL": s.public_url,  # redirect URI, never derived from the Host header
        "OIDC_EDITOR_GROUPS or OIDC_VIEWER_GROUPS": s.oidc_editor_groups or s.oidc_viewer_groups,
    }
    if missing := [k for k, v in required.items() if not v]:
        raise RuntimeError(f"incomplete OIDC configuration, missing: {', '.join(missing)}")
    if len(s.session_secret) < 32:
        raise RuntimeError("SESSION_SECRET must have at least 32 characters")
    # PUBLIC_URL over http: the browser drops the Secure __Host- cookies and the login silently loops
    for key, url in (("OIDC_ISSUER_URL", s.oidc_issuer_url), ("PUBLIC_URL", s.public_url)):
        parts = urlsplit(url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS):
            raise RuntimeError(f"{key} must use https")
    if not (math.isfinite(s.session_max_age_hours) and s.session_max_age_hours > 0):
        raise RuntimeError("SESSION_MAX_AGE_HOURS must be a positive number")
    if "openid" not in s.oidc_scopes.split():
        raise RuntimeError("OIDC_SCOPES must include openid")


def safe_next(target: str | None) -> str:
    """Post-login destination: only a relative path of this app (no open redirect)."""
    t = target or "/"
    parts = urlsplit(t)
    # browsers read "\" as "/" and drop tabs/newlines, so "/\evil" and "/\t/evil" mean "//evil"
    if not t.startswith("/") or t.startswith("//") or parts.scheme or parts.netloc or "\\" in t:
        return "/"
    return "/" if any(ord(c) < 0x20 or ord(c) == 0x7F for c in t) else t


def _sign(s: Settings, aud: str, payload: dict, ttl: float) -> str:
    now = int(time.time())
    return jwt.encode({**payload, "aud": aud, "iat": now, "exp": now + int(ttl)}, s.session_secret, algorithm="HS256")


def _unsign(s: Settings, aud: str, token: str | None) -> dict | None:
    if not token:
        return None
    try:  # aud separates the login cookie from the session cookie
        return jwt.decode(token, s.session_secret, algorithms=["HS256"], audience=aud, options={"require": ["exp"]})
    except jwt.PyJWTError:
        return None


def _set_cookie(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=True, samesite="lax", path="/")


def _del_cookie(resp: Response, name: str) -> None:
    resp.delete_cookie(name, httponly=True, secure=True, samesite="lax", path="/")


def _page(message: str, status: int = 200) -> HTMLResponse:
    body = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f"<title>Changelog Watcher</title><p>{html.escape(message)}</p>"
        f'<p><a href="/auth/login">{html.escape(_("Sign in"))}</a></p>'
    )
    return HTMLResponse(body, status)


def _basic_user(request: Request, expected: tuple[bytes, bytes]) -> dict | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        user, _sep, pwd = base64.b64decode(header[6:]).partition(b":")
    except Exception:
        return None
    # bytes: compare_digest raises TypeError on non-ASCII str
    # "&", not "and": always compare both, so the time taken does not tell whether the user matched
    if secrets.compare_digest(user, expected[0]) & secrets.compare_digest(pwd, expected[1]):
        return {"name": user.decode(errors="replace"), "edit": True, "auth": "basic"}
    return None


def install(app: FastAPI, s: Settings) -> None:
    """Add the authentication middleware (and the OIDC routes). Nothing when auth is disabled."""
    oidc = oidc_enabled(s)
    basic = (s.auth_username.encode(), s.auth_password.encode()) if s.auth_username and s.auth_password else None
    if not (oidc or basic):
        return
    open_paths = OPEN_PATHS | (OIDC_PATHS if oidc else set())
    if oidc:
        _oidc_routes(app, s)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        path = request.url.path
        if path in open_paths:
            return await call_next(request)
        user = _basic_user(request, basic) if basic else None
        if user is None and oidc and (session := _unsign(s, "session", request.cookies.get(SESSION_COOKIE))):
            user = {"name": session.get("name"), "edit": bool(session.get("edit")), "auth": "oidc"}
        if user is None:
            accept = request.headers.get("accept", "")
            if oidc and request.method == "GET" and not path.startswith("/api/") and "text/html" in accept:
                target = path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse("/auth/login?" + urlencode({"next": target}), 303)
            # with OIDC no Basic challenge: the browser would show its password dialog on SPA calls
            headers = {"WWW-Authenticate": 'Basic realm="changelog-watcher"'} if basic and not oidc else None
            return Response(status_code=401, headers=headers)
        if not user["edit"] and request.method not in SAFE_METHODS:
            return JSONResponse({"detail": _("read-only access")}, status_code=403)
        request.state.user = user
        return await call_next(request)


def _oidc_routes(app: FastAPI, s: Settings) -> None:
    redirect_uri = f"{s.public_url}/auth/callback"
    cache: dict = {}  # discovery document and JWKS, per app

    async def get_json(url: str) -> dict:
        async with httpx.AsyncClient(timeout=s.http_timeout) as c:
            r = await c.get(url, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError(f"{url}: not a JSON object")
        return data

    async def provider() -> dict:
        if "doc" not in cache:
            doc = await get_json(s.oidc_issuer_url.rstrip("/") + "/.well-known/openid-configuration")
            if doc.get("issuer") != s.oidc_issuer_url:
                raise ValueError(f"discovery issuer {doc.get('issuer')!r} != OIDC_ISSUER_URL")
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not isinstance(doc.get(key), str):
                    raise ValueError(f"discovery document without {key}")
            cache["doc"] = doc
        return cache["doc"]

    async def signing_key(doc: dict, token: str) -> jwt.PyJWK:
        kid = jwt.get_unverified_header(token).get("kid")
        for attempt in range(2):  # unknown kid: the provider rotated its keys, fetch them again once
            if attempt or "jwks" not in cache:
                jwks = (await get_json(doc["jwks_uri"])).get("keys")
                usable = [k for k in jwks or [] if isinstance(k, dict) and k.get("use", "sig") == "sig"]
                cache["jwks"] = jwt.PyJWKSet(usable)  # no keys: PyJWKSetError (a PyJWTError)
            keys = [k for k in cache["jwks"].keys if kid is None or k.key_id == kid]
            if len(keys) == 1:
                return keys[0]
        raise jwt.InvalidKeyError(f"no JWKS key for kid {kid!r}")

    async def verify_id_token(doc: dict, token: str, nonce: str) -> dict:
        claims = jwt.decode(
            token,
            await signing_key(doc, token),  # PyJWK: verification uses the key's own algorithm
            algorithms=ID_TOKEN_ALGS,
            audience=s.oidc_client_id,
            issuer=s.oidc_issuer_url,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=60,
        )
        if not secrets.compare_digest(str(claims.get("nonce", "")).encode(), nonce.encode()):
            raise jwt.InvalidTokenError("nonce mismatch")
        several = isinstance(claims["aud"], list) and len(claims["aud"]) > 1
        if ("azp" in claims or several) and claims.get("azp") != s.oidc_client_id:
            raise jwt.InvalidTokenError("azp mismatch")
        return claims

    @app.get("/auth/login", include_in_schema=False)
    async def login(next: str = "/"):
        try:
            doc = await provider()
        except (httpx.HTTPError, ValueError) as exc:
            log.error("OIDC discovery failed: %s", exc)
            return _page(_("The identity provider is unavailable, try again later."), 502)
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {
            "response_type": "code",
            "client_id": s.oidc_client_id,
            "redirect_uri": redirect_uri,
            "scope": s.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        endpoint = doc["authorization_endpoint"]
        resp = RedirectResponse(f"{endpoint}{'&' if '?' in endpoint else '?'}{urlencode(params)}", 303)
        pending = {"state": state, "nonce": nonce, "verifier": verifier, "next": safe_next(next)}
        _set_cookie(resp, LOGIN_COOKIE, _sign(s, "login", pending, LOGIN_TTL), LOGIN_TTL)
        return resp

    @app.get("/auth/callback", include_in_schema=False)
    async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        pending = _unsign(s, "login", request.cookies.get(LOGIN_COOKIE))
        if not pending or not secrets.compare_digest(state.encode(), str(pending.get("state", "")).encode()):
            return _page(_("Login expired or invalid, sign in again."), 400)
        if error or not code:
            log.warning("OIDC login refused by the provider: %r", error[:200])
            return _page(_("Login failed."), 400)
        try:
            doc = await provider()
            async with httpx.AsyncClient(timeout=s.http_timeout) as c:
                r = await c.post(
                    doc["token_endpoint"],
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "code_verifier": pending["verifier"],
                    },
                    # client_secret_basic: RFC 6749 2.3.1 form-encodes id and secret first
                    auth=(quote(s.oidc_client_id, safe=""), quote(s.oidc_client_secret, safe="")),
                    headers={"Accept": "application/json"},
                )
            r.raise_for_status()
            claims = await verify_id_token(doc, r.json()["id_token"], pending["nonce"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError) as exc:
            log.warning("OIDC login failed: %s", exc)  # never the token response body
            return _page(_("Login failed."), 400)
        raw = claims.get("groups")
        groups = {g for g in raw if isinstance(g, str)} if isinstance(raw, list) else set()
        name = str(claims.get("name") or claims.get("preferred_username") or claims.get("email") or claims["sub"])
        if groups & set(s.oidc_editor_groups):
            edit = True
        elif groups & set(s.oidc_viewer_groups):
            edit = False
        else:
            # sub, never name/e-mail: read-only users can read the logs (/api/logs)
            log.warning("OIDC login denied: sub %r not in an allowed group (groups: %s)", claims["sub"], sorted(groups))
            resp: Response = _page(_("Your account is not in a group allowed to use Changelog Watcher."), 403)
            _del_cookie(resp, LOGIN_COOKIE)
            return resp
        log.info("OIDC login: sub %r (%s)", claims["sub"], "editor" if edit else "read-only")
        ttl = int(s.session_max_age_hours * 3600)
        resp = RedirectResponse(safe_next(pending.get("next")), 303)
        _set_cookie(
            resp, SESSION_COOKIE, _sign(s, "session", {"sub": claims["sub"], "name": name, "edit": edit}, ttl), ttl
        )
        _del_cookie(resp, LOGIN_COOKIE)
        return resp

    @app.post("/auth/logout", include_in_schema=False)
    async def logout():
        target = "/auth/logged-out"
        try:
            end = (await provider()).get("end_session_endpoint")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("OIDC discovery failed on logout: %s", exc)
            end = None
        if isinstance(end, str) and end:  # RP-initiated logout (Dex has none: local logout only)
            params = {"client_id": s.oidc_client_id, "post_logout_redirect_uri": f"{s.public_url}{target}"}
            target = f"{end}{'&' if '?' in end else '?'}{urlencode(params)}"
        resp = RedirectResponse(target, 303)
        _del_cookie(resp, SESSION_COOKIE)
        return resp

    @app.get("/auth/logged-out", include_in_schema=False)
    def logged_out():
        return _page(_("You have signed out."))
