"""End of life (EOL) data from endoflife.date.

Each project may name an endoflife.date product (``Project.eol_product``) or, when empty and
``EOL_AUTO_MATCH`` is on, is matched by name / repository against the list of products. The
release cycles of the product are stored in ``Project.eol_data`` on each refresh; the cycle of
the running version (and how close its EOL is) is computed on demand in ``analysis``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime

import httpx

from .config import get_settings
from .i18n import _
from .db import session_scope
from .models import Project
from .sources.base import SourceError, get_json
from .versioning import Version

log = logging.getLogger(__name__)

EOL_DISABLED = "none"
STATUS_OK, STATUS_SOON, STATUS_EOL = "ok", "soon", "eol"
EOL_LEVEL = {STATUS_OK: 0, STATUS_SOON: 1, STATUS_EOL: 2}

# Common names that differ from the endoflife.date product slug.
ALIASES = {
    "postgres": "postgresql",
    "node": "nodejs",
    "k8s": "kubernetes",
    "golang": "go",
    "mongo": "mongodb",
    "argocd": "argo-cd",
}
_SLUG = re.compile(r"[a-z0-9][a-z0-9._+-]*")
_PRODUCTS_TTL = 24 * 3600
_products_cache: tuple[float, list[str]] | None = None


# ------------------------------------------------------------------ fetching
async def fetch_products(client: httpx.AsyncClient, settings) -> list[str]:
    """All endoflife.date product slugs (cached for a day)."""
    global _products_cache
    if _products_cache and time.monotonic() - _products_cache[0] < _PRODUCTS_TTL:
        return _products_cache[1]
    data = (await get_json(client, f"{settings.eol_api_url}/all.json")).json()
    if not isinstance(data, list):
        raise SourceError(_("unexpected response from endoflife.date (product list)"))
    products = sorted({p.strip().lower() for p in data if isinstance(p, str) and _SLUG.fullmatch(p.strip().lower())})
    _products_cache = (time.monotonic(), products)
    return products


def _date_or_bool(value):
    if isinstance(value, bool) or value is None:
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def normalize_cycle(item: dict) -> dict | None:
    cycle = item.get("cycle")
    if cycle is None or str(cycle).strip() == "":
        return None
    return {
        "cycle": str(cycle).strip(),
        # ISO date, True (already EOL, date unknown), False (no EOL announced) or None.
        "eol": _date_or_bool(item.get("eol")),
        "latest": str(item["latest"]) if item.get("latest") else None,
        # vendor page announcing the cycle's support/EOL; only http(s), since it becomes a link in the UI
        "link": link if isinstance(link := item.get("link"), str) and link.startswith(("https://", "http://")) else None,
    }


async def fetch_cycles(client: httpx.AsyncClient, product: str, settings) -> list[dict]:
    if not _SLUG.fullmatch(product):
        raise SourceError(_("invalid product: {product}", product=product))
    data = (await get_json(client, f"{settings.eol_api_url}/{product}.json")).json()
    if not isinstance(data, list):
        raise SourceError(_("unexpected response from endoflife.date for {product}", product=product))
    return [c for c in (normalize_cycle(i) for i in data if isinstance(i, dict)) if c]


# ------------------------------------------------------------------ matching
def _norm(name: str | None) -> str | None:
    if not name:
        return None
    slug = re.sub(r"[\s_]+", "-", name.strip().lower())
    return ALIASES.get(slug, slug)


def candidates(project: Project) -> list[str]:
    """Product slugs that may describe the project, most specific first."""
    out = [_norm(project.name)]
    repo = (project.repository or "").strip().rstrip("/")
    if project.source_type == "helm_index":
        out.append(_norm(project.chart_name))
    elif project.source_type in ("github_releases", "github_tags", "gitlab_releases", "gitlab_tags", "dockerhub"):
        last = repo.split("/")[-1].split(":")[0].removesuffix(".git")
        out.append(_norm(last))
    return [c for i, c in enumerate(out) if c and c not in out[:i]]


def can_auto_match(project: Project) -> bool:
    # A Helm chart version is not the version of the product it installs.
    return not (project.source_type == "helm_index" and not project.use_app_version)


def auto_match(project: Project, products: list[str] | None) -> str | None:
    """The endoflife.date product matching the project's name / repository, if any."""
    if not products or not can_auto_match(project):
        return None
    known = set(products)
    return next((c for c in candidates(project) if c in known), None)


def resolve_product(project: Project, products: list[str] | None, auto: bool) -> str | None:
    manual = (project.eol_product or "").strip().lower()
    if manual == EOL_DISABLED:
        return None
    if manual:
        return manual
    return auto_match(project, products) if auto else None


def _cycle_parts(cycle: str) -> tuple[int, ...] | None:
    parts = cycle.split(".")
    if not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def find_cycle(version: Version | None, cycles: list[dict]) -> dict | None:
    """The release cycle of ``version``: the longest cycle (``16``, ``1.29``) that prefixes it."""
    if version is None:
        return None
    parts = (version.major, version.patch) if version.two_part else (
        version.major, version.minor, version.patch, version.build)
    best, best_len = None, 0
    for c in cycles:
        cp = _cycle_parts(c["cycle"])
        if cp and len(cp) > best_len and parts[: len(cp)] == cp:
            best, best_len = c, len(cp)
    return best


def eol_status(cycle: dict, today: date, warning_days: int) -> tuple[str | None, str | None, int | None]:
    """(status, eol date, days left) of a cycle."""
    eol = cycle.get("eol")
    if eol is True:
        return STATUS_EOL, None, None
    if eol is False:
        return STATUS_OK, None, None
    if not eol:
        return None, None, None
    left = (date.fromisoformat(eol) - today).days
    if left <= 0:
        return STATUS_EOL, eol, left
    return (STATUS_SOON if left <= warning_days else STATUS_OK), eol, left


def deployment_eol(project: Project, version: Version | None, now: datetime) -> dict:
    """EOL fields of one deployment (all None when unknown)."""
    out = {"eol_product": None, "eol_cycle": None, "eol_date": None, "eol_days_left": None, "eol_status": None,
           "eol_cycle_latest": None, "eol_link": None}
    data = project.eol_data or {}
    if not get_settings().eol_enabled or not data.get("product"):
        return out
    out["eol_product"] = data["product"]
    out["eol_link"] = f"https://endoflife.date/{data['product']}"
    cycle = find_cycle(version, data.get("cycles") or [])
    if cycle is None:
        return out
    today = now.date()
    status, eol_date, left = eol_status(cycle, today, get_settings().eol_warning_days)
    out.update(eol_cycle=cycle["cycle"], eol_date=eol_date, eol_days_left=left, eol_status=status,
               eol_cycle_latest=cycle.get("latest"), eol_link=cycle.get("link") or out["eol_link"])
    return out


# ------------------------------------------------------------------ refresh
async def refresh_eol(project_ids: list[int], client: httpx.AsyncClient) -> list[str]:
    """Fetch the EOL cycles of the given projects. Returns error messages."""
    settings = get_settings()
    with session_scope() as s:
        projects = [s.get(Project, pid) for pid in project_ids]
        projects = [p for p in projects if p is not None]
        for p in projects:
            s.expunge(p)
    products = None
    errors: list[str] = []
    if settings.eol_auto_match and any(not p.eol_product and can_auto_match(p) for p in projects):
        try:
            products = await fetch_products(client, settings)
        except (SourceError, httpx.HTTPError, ValueError) as exc:
            errors.append(_("EOL: endoflife.date product list: {error}", error=exc))
            log.warning("EOL: failed to fetch the product list: %s", exc)

    sem = asyncio.Semaphore(6)

    async def one(p: Project):
        product = resolve_product(p, products, settings.eol_auto_match)
        if product is None:
            # automatic match pending on a failed product list: keep what we had
            keep = products is None and not p.eol_product and settings.eol_auto_match and can_auto_match(p)
            return p.id, ("keep" if keep else None), None
        async with sem:
            try:
                return p.id, {"product": product, "cycles": await fetch_cycles(client, product, settings)}, None
            except (SourceError, httpx.HTTPError, ValueError) as exc:
                same = (p.eol_data or {}).get("product") == product
                return p.id, ("keep" if same else None), f"{p.name}: EOL ({product}): {exc}"

    results = await asyncio.gather(*(one(p) for p in projects))
    with session_scope() as s:
        for pid, data, err in results:
            p = s.get(Project, pid)
            if p is None or (data == "keep" and err is None):
                continue
            p.eol_error = err
            if data != "keep":
                p.eol_data = data
            if err:
                errors.append(err)
                log.warning("%s", err)
    return errors
