"""Runtime settings, read from environment variables."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    database_url: str = "sqlite:///./data/changelog-watcher.db"
    config_file: str | None = None
    seed_overwrite: bool = False
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    gitlab_token: str | None = None
    gitlab_url: str = "https://gitlab.com"
    http_timeout: float = 20.0
    max_release_pages: int = 3
    refresh_interval_minutes: int = 360
    refresh_on_startup: bool = True
    report_cron: str = "0 9 * * mon"
    report_days: int = 7
    report_send_enabled: bool = True
    timezone: str = "UTC"
    public_url: str = ""
    auth_username: str | None = None
    auth_password: str | None = None
    auth_disabled: bool = False
    metrics_token: str | None = None
    scheduler_enabled: bool = True
    metrics_cache_seconds: int = 30
    log_level: str = "INFO"
    language: str = "en"  # en | pt-BR (overridden by the Settings page)
    eol_enabled: bool = True
    eol_api_url: str = "https://endoflife.date/api"
    eol_warning_days: int = 90
    eol_auto_match: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get  # empty counts as unset: Helm renders a null value as ""
        return cls(
            database_url=e("DATABASE_URL", cls.database_url),
            config_file=e("CONFIG_FILE") or None,
            seed_overwrite=_bool(e("SEED_OVERWRITE"), False),
            github_token=e("GITHUB_TOKEN") or None,
            github_api_url=e("GITHUB_API_URL", cls.github_api_url).rstrip("/"),
            gitlab_token=e("GITLAB_TOKEN") or None,
            gitlab_url=e("GITLAB_URL", cls.gitlab_url).rstrip("/"),
            http_timeout=float(e("HTTP_TIMEOUT") or cls.http_timeout),
            max_release_pages=int(e("MAX_RELEASE_PAGES") or cls.max_release_pages),
            refresh_interval_minutes=int(e("REFRESH_INTERVAL_MINUTES") or cls.refresh_interval_minutes),
            refresh_on_startup=_bool(e("REFRESH_ON_STARTUP"), cls.refresh_on_startup),
            report_cron=e("REPORT_CRON", cls.report_cron),
            report_days=int(e("REPORT_DAYS") or cls.report_days),
            report_send_enabled=_bool(e("REPORT_SEND_ENABLED"), cls.report_send_enabled),
            timezone=e("TZ") or cls.timezone,
            public_url=(e("PUBLIC_URL", "") or "").rstrip("/"),
            auth_username=e("AUTH_USERNAME") or None,
            auth_password=e("AUTH_PASSWORD") or None,
            auth_disabled=_bool(e("AUTH_DISABLED"), False),
            metrics_token=e("METRICS_TOKEN") or None,
            scheduler_enabled=_bool(e("SCHEDULER_ENABLED"), cls.scheduler_enabled),
            metrics_cache_seconds=int(e("METRICS_CACHE_SECONDS") or cls.metrics_cache_seconds),
            log_level=e("LOG_LEVEL") or cls.log_level,
            language=e("APP_LANGUAGE") or cls.language,
            eol_enabled=_bool(e("EOL_ENABLED"), cls.eol_enabled),
            eol_api_url=e("EOL_API_URL", cls.eol_api_url).rstrip("/"),
            eol_warning_days=int(e("EOL_WARNING_DAYS") or cls.eol_warning_days),
            eol_auto_match=_bool(e("EOL_AUTO_MATCH"), cls.eol_auto_match),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


# The app's own credentials: a placeholder must never resolve to them, or anyone able to edit
# an environment/webhook could send them to a host of their choice.
_APP_SECRETS = frozenset({"AUTH_USERNAME", "AUTH_PASSWORD", "GITHUB_TOKEN", "GITLAB_TOKEN", "METRICS_TOKEN",
                          "DATABASE_URL"})


def expand_env(value: str | None) -> str | None:
    """Resolve a ``${VAR}`` / ``${VAR:-default}`` placeholder from the environment.

    Secrets (Prometheus tokens, webhook URLs) can therefore be stored as a placeholder and
    injected from a Kubernetes Secret. Only a value that is *entirely* a placeholder is
    expanded: composing one into a URL (``https://attacker/?t=${X}``) would leak it.
    """
    m = _PLACEHOLDER.fullmatch((value or "").strip())
    if not m:
        return value
    if m.group(1) in _APP_SECRETS:
        log.warning("placeholder ${%s} not allowed (the application's own credential)", m.group(1))
        return ""
    return os.environ.get(m.group(1), m.group(2) or "")


SECRET_MASK = "********"


def mask_secret(value: str | None) -> str | None:
    """Hide secrets in API responses (placeholders are shown as-is)."""
    return value if not value or _PLACEHOLDER.fullmatch(value.strip()) else SECRET_MASK
