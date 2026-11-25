"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache


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
    scheduler_enabled: bool = True
    metrics_cache_seconds: int = 30
    log_level: str = "INFO"

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
            scheduler_enabled=_bool(e("SCHEDULER_ENABLED"), cls.scheduler_enabled),
            metrics_cache_seconds=int(e("METRICS_CACHE_SECONDS") or cls.metrics_cache_seconds),
            log_level=e("LOG_LEVEL") or cls.log_level,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(value: str | None) -> str | None:
    """Expand ``${VAR}`` / ``${VAR:-default}`` placeholders using the environment.

    Secrets (Prometheus tokens, webhook URLs) can therefore be stored as a
    placeholder and injected from a Kubernetes Secret.
    """
    if value is None:
        return None
    return _PLACEHOLDER.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)


def is_placeholder(value: str | None) -> bool:
    return bool(value) and bool(_PLACEHOLDER.fullmatch(value.strip()))


SECRET_MASK = "********"


def mask_secret(value: str | None) -> str | None:
    """Hide secrets in API responses (placeholders are shown as-is)."""
    if not value:
        return value
    if is_placeholder(value):
        return value
    return SECRET_MASK
