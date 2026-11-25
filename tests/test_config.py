from changelog_watcher.config import Settings


def test_empty_env_vars_fall_back_to_defaults(monkeypatch):
    # Helm renders a null value as "": that must not crash startup.
    for var in ("HTTP_TIMEOUT", "MAX_RELEASE_PAGES", "REFRESH_INTERVAL_MINUTES", "REPORT_DAYS",
                "METRICS_CACHE_SECONDS", "LOG_LEVEL", "TZ"):
        monkeypatch.setenv(var, "")
    s = Settings.from_env()
    d = Settings()
    assert (s.http_timeout, s.max_release_pages, s.refresh_interval_minutes, s.report_days,
            s.metrics_cache_seconds, s.log_level, s.timezone) == (
        d.http_timeout, d.max_release_pages, d.refresh_interval_minutes, d.report_days,
        d.metrics_cache_seconds, d.log_level, d.timezone)
