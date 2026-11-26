"""Prometheus metrics computed from the database at scrape time."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from .analysis import (
    compute_statuses,
    drift_pairs,
    load_environments,
    load_projects,
    project_versions,
    summaries_by_environment,
    version_matrix,
)
from .config import get_settings
from .db import session_scope
from .eol import EOL_LEVEL
from .versioning import STATUS_ORDER

log = logging.getLogger(__name__)
P = "changelog_watcher_"


class WatcherCollector(Collector):
    def __init__(self) -> None:
        self._cache: list | None = None
        self._cache_at = 0.0
        self._lock = threading.Lock()

    def collect(self):
        ttl = get_settings().metrics_cache_seconds
        with self._lock:
            if self._cache is None or time.monotonic() - self._cache_at > ttl:
                try:
                    self._cache = list(self._build())
                    self._cache_at = time.monotonic()
                except Exception:  # pragma: no cover - never break /metrics
                    log.exception("failed to build metrics")
                    self._cache = self._cache or []
            yield from self._cache

    def _build(self):
        from .refresh import STATE

        now = datetime.now(timezone.utc)
        with session_scope() as s:
            envs = load_environments(s)
            statuses = compute_statuses(s, now=now)
            summaries = summaries_by_environment(statuses, envs)
            projects = load_projects(s)
            latest_release = []
            for p in projects:
                versions = project_versions(p)
                week = sum(1 for e in versions.values() if e.published_at and e.published_at >= now - timedelta(days=7))
                latest = max(versions) if versions else None
                latest_release.append((p, latest, versions.get(latest) if latest else None, week))
            pairs = drift_pairs(statuses, envs)

        info = GaugeMetricFamily(
            P + "app_info",
            "Versões da aplicação em um ambiente (valor sempre 1)",
            labels=["project", "environment", "current_version", "latest_version", "latest_patch", "latest_minor",
                    "latest_major", "status", "version_source"],
        )
        level = GaugeMetricFamily(P + "app_update_level",
                                  "Nível da atualização pendente: -1 desconhecido, 0 atualizado, 1 bugfix, 2 minor, 3 major",
                                  labels=["project", "environment"])
        available = GaugeMetricFamily(P + "app_update_available", "1 se há atualização do tipo indicado",
                                      labels=["project", "environment", "type"])
        behind = GaugeMetricFamily(P + "app_versions_behind", "Quantidade de versões atrás",
                                   labels=["project", "environment", "type"])
        outdated_days = GaugeMetricFamily(P + "app_outdated_days",
                                          "Dias desde a publicação da primeira versão mais nova que a implantada",
                                          labels=["project", "environment"])
        libyears = GaugeMetricFamily(P + "app_libyears",
                                     "Anos entre a versão implantada e a mais recente (libyear)",
                                     labels=["project", "environment"])
        det_err = GaugeMetricFamily(P + "app_detection_error", "1 se a detecção via Prometheus falhou",
                                    labels=["project", "environment"])
        eol_level = GaugeMetricFamily(P + "app_eol_level",
                                      "Fim de suporte do ciclo em uso: 0 com suporte, 1 EOL próximo, 2 EOL atingido",
                                      labels=["project", "environment", "product", "cycle"])
        eol_days = GaugeMetricFamily(P + "app_eol_days_left",
                                     "Dias até o fim de suporte do ciclo em uso (negativo quando já passou)",
                                     labels=["project", "environment", "product", "cycle"])
        for st in statuses:
            lbl = [st.project, st.environment]
            info.add_metric(lbl + [st.current_version or "", st.latest_version or "", st.latest_patch or "",
                                   st.latest_minor or "", st.latest_major or "", st.status, st.version_source], 1)
            level.add_metric(lbl, st.level)
            for kind in ("patch", "minor", "major"):
                available.add_metric(lbl + [kind], 1 if getattr(st, f"latest_{kind}") else 0)
                behind.add_metric(lbl + [kind], getattr(st, f"{kind}_behind"))
            behind.add_metric(lbl + ["total"], st.versions_behind)
            outdated_days.add_metric(lbl, st.outdated_days)
            if st.libyears is not None:
                libyears.add_metric(lbl, st.libyears)
            if st.version_source == "prometheus":
                det_err.add_metric(lbl, 1 if st.detection_error else 0)
            if st.eol_status:
                eol_lbl = lbl + [st.eol_product or "", st.eol_cycle or ""]
                eol_level.add_metric(eol_lbl, EOL_LEVEL[st.eol_status])
                if st.eol_days_left is not None:
                    eol_days.add_metric(eol_lbl, st.eol_days_left)
        yield from (info, level, available, behind, outdated_days, libyears, det_err, eol_level, eol_days)

        env_apps = GaugeMetricFamily(P + "environment_apps", "Aplicações por status no ambiente",
                                     labels=["environment", "status"])
        env_pending = GaugeMetricFamily(P + "environment_apps_pending",
                                        "Aplicações com atualização do tipo indicado (não exclusivo)",
                                        labels=["environment", "type"])
        env_ratio = GaugeMetricFamily(P + "environment_up_to_date_ratio",
                                      "Fração de aplicações atualizadas (desconsidera desconhecidas)", labels=["environment"])
        env_total = GaugeMetricFamily(P + "environment_apps_total", "Total de aplicações no ambiente", labels=["environment"])
        env_lib = GaugeMetricFamily(P + "environment_libyears_total", "Soma de libyears no ambiente", labels=["environment"])
        env_lib_avg = GaugeMetricFamily(P + "environment_libyears_avg",
                                        "Média de libyears por implantação no ambiente", labels=["environment"])
        env_avg = GaugeMetricFamily(P + "environment_outdated_days_avg", "Média de dias desatualizado", labels=["environment"])
        env_max = GaugeMetricFamily(P + "environment_outdated_days_max", "Maior atraso em dias", labels=["environment"])
        env_behind = GaugeMetricFamily(P + "environment_versions_behind_total", "Soma de versões atrás",
                                       labels=["environment"])
        env_eol = GaugeMetricFamily(P + "environment_apps_eol",
                                    "Implantações com fim de suporte próximo (soon) ou atingido (eol)",
                                    labels=["environment", "status"])
        env_age = GaugeMetricFamily(P + "environment_apps_by_age", "Aplicações desatualizadas por faixa de atraso",
                                    labels=["environment", "bucket"])
        for summ in summaries:
            env = summ["environment"]
            for status in STATUS_ORDER:
                env_apps.add_metric([env, status], summ["counts"][status])
            for kind in ("patch", "minor", "major"):
                env_pending.add_metric([env, kind], summ[f"{kind}_pending"])
            if summ["up_to_date_ratio"] is not None:
                env_ratio.add_metric([env], summ["up_to_date_ratio"])
            env_total.add_metric([env], summ["total"])
            env_lib.add_metric([env], summ["libyears"])
            if summ["libyears_avg"] is not None:
                env_lib_avg.add_metric([env], summ["libyears_avg"])
            env_avg.add_metric([env], summ["avg_outdated_days"])
            env_max.add_metric([env], summ["max_outdated_days"])
            env_behind.add_metric([env], summ["versions_behind"])
            env_eol.add_metric([env, "soon"], summ["eol_soon"])
            env_eol.add_metric([env, "eol"], summ["eol_reached"])
            for bucket, n in summ["age_buckets"].items():
                env_age.add_metric([env, bucket], n)
        yield from (env_apps, env_pending, env_ratio, env_total, env_lib, env_lib_avg, env_avg, env_max, env_behind,
                    env_eol, env_age)

        # Drift between environments.
        mismatch = GaugeMetricFamily(P + "project_version_mismatch",
                                     "1 se a aplicação roda versões diferentes entre ambientes", labels=["project"])
        distinct = GaugeMetricFamily(P + "project_distinct_versions", "Versões distintas implantadas", labels=["project"])
        for row in version_matrix(statuses):
            mismatch.add_metric([row["project"]], int(row["drift"]))
            distinct.add_metric([row["project"]], row["distinct_versions"])
        pair = GaugeMetricFamily(P + "environment_pair_different_apps",
                                 "Aplicações com versões diferentes entre dois ambientes",
                                 labels=["source_environment", "target_environment"])
        for p in pairs:
            pair.add_metric([p["source"], p["target"]], p["different"])
        yield from (mismatch, distinct, pair)

        latest_ts = GaugeMetricFamily(P + "release_latest_timestamp_seconds",
                                      "Data de publicação da versão mais recente", labels=["project", "version"])
        week = GaugeMetricFamily(P + "releases_last_7d", "Versões publicadas nos últimos 7 dias", labels=["project"])
        src_err = GaugeMetricFamily(P + "project_source_error", "1 se a última coleta de releases falhou",
                                    labels=["project", "source_type"])
        for p, latest, entry, n in latest_release:
            if latest and entry and entry.published_at:
                latest_ts.add_metric([p.name, str(latest)], entry.published_at.timestamp())
            week.add_metric([p.name], n)
            src_err.add_metric([p.name, p.source_type], 1 if p.last_error else 0)
        yield from (latest_ts, week, src_err)

        last = GaugeMetricFamily(P + "last_refresh_timestamp_seconds", "Fim da última atualização")
        if STATE.finished_at:
            last.add_metric([], STATE.finished_at.timestamp())
        ok = GaugeMetricFamily(P + "last_refresh_success", "1 se a última atualização terminou sem erros")
        if STATE.success is not None:
            ok.add_metric([], 1 if STATE.success else 0)
        yield from (last, ok)
