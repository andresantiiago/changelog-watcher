"""Refresh pipeline: fetch releases, detect running versions, store snapshots."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from prometheus_client import Counter, Histogram
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import prometheus as prom
from .analysis import compute_statuses, load_environments, summarize
from .config import get_settings
from .db import session_scope
from .models import Deployment, Project, Release, Snapshot, utcnow
from .sources import SourceError, fetch_releases
from .versioning import parse_version

log = logging.getLogger(__name__)

REFRESH_TOTAL = Counter("changelog_watcher_refresh_runs", "Refresh runs", ["result"])
SOURCE_FETCH = Counter("changelog_watcher_source_fetches", "Release source fetches", ["source_type", "result"])
DETECTIONS = Counter("changelog_watcher_prometheus_detections", "Version detections via Prometheus", ["environment", "result"])
REFRESH_DURATION = Histogram(
    "changelog_watcher_refresh_duration_seconds", "Duration of a refresh run", buckets=(1, 5, 15, 30, 60, 120, 300, 600)
)


@dataclass
class RefreshState:
    running: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    success: bool | None = None
    projects_ok: int = 0
    projects_failed: int = 0
    detections_ok: int = 0
    detections_failed: int = 0
    new_releases: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "projects_ok": self.projects_ok,
            "projects_failed": self.projects_failed,
            "detections_ok": self.detections_ok,
            "detections_failed": self.detections_failed,
            "new_releases": self.new_releases,
            "errors": self.errors[-50:],
        }


STATE = RefreshState()
_LOCK = asyncio.Lock()


async def refresh_project_releases(project_id: int, client: httpx.AsyncClient) -> int:
    """Fetch releases of one project and upsert them. Returns # of new releases."""
    settings = get_settings()
    with session_scope() as s:
        project = s.get(Project, project_id)
        if project is None:  # deleted while the refresh was running
            return 0
        known = {r.tag: r.published_at for r in project.releases}
        hints = [d.current_version for d in project.deployments if d.current_version]
        s.expunge(project)
    try:
        data = await fetch_releases(project, client, settings, known_dates=known, hints=hints)
    except Exception as exc:  # anything (bad JSON, unexpected payload...) must show up as the project's error
        SOURCE_FETCH.labels(project.source_type, "error").inc()
        with session_scope() as s:
            p = s.get(Project, project_id)
            if p is not None:
                p.last_error = str(exc) or exc.__class__.__name__
                p.last_checked_at = utcnow()
        raise SourceError(f"{project.name}: {exc}") from exc

    SOURCE_FETCH.labels(project.source_type, "ok").inc()
    new = 0
    with session_scope() as s:
        p = s.get(Project, project_id)
        if p is None:
            return 0
        existing = {r.tag: r for r in s.scalars(select(Release).where(Release.project_id == project_id))}
        for item in data:
            parsed = parse_version(item.tag, p.tag_pattern, two_part=p.version_scheme == "major_patch")
            rel = existing.get(item.tag)
            if rel is None:
                rel = Release(project_id=project_id, tag=item.tag)
                s.add(rel)
                existing[item.tag] = rel
                new += 1
            rel.version = str(parsed) if parsed else None
            rel.title = item.title or rel.title
            rel.prerelease = item.prerelease
            rel.published_at = item.published_at or rel.published_at
            rel.url = item.url or rel.url
            if item.notes:
                rel.notes = item.notes
            rel.commit_sha = item.commit_sha or rel.commit_sha
        p.last_error = None
        p.last_checked_at = utcnow()
    return new


async def detect_versions(project_ids: list[int] | None = None) -> tuple[int, int, list[str]]:
    ok = failed = 0
    errors: list[str] = []
    with session_scope() as s:
        stmt = (
            select(Deployment)
            .options(selectinload(Deployment.project), selectinload(Deployment.environment))
            .where(Deployment.version_source == "prometheus")
        )
        deps = [d for d in s.scalars(stmt) if d.project.enabled and (not project_ids or d.project_id in project_ids)]
        for d in deps:
            s.expunge(d)

    sem = asyncio.Semaphore(8)

    async def one(dep: Deployment):
        async with sem:
            try:
                det = await prom.detect(dep.project, dep, dep.environment)
                return dep.id, det, None
            except prom.PrometheusError as exc:
                return dep.id, None, str(exc)
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("detection failed")
                return dep.id, None, f"erro inesperado: {exc}"

    results = await asyncio.gather(*(one(d) for d in deps))
    env_names = {d.id: d.environment.name for d in deps}
    names = {d.id: f"{d.project.name}@{d.environment.name}" for d in deps}
    with session_scope() as s:
        for dep_id, det, err in results:
            dep = s.get(Deployment, dep_id)
            if dep is None:
                continue
            dep.detected_at = utcnow()
            if det:
                if det.version != dep.detected_version:
                    dep.version_changed_at = utcnow()
                dep.detected_version = det.version
                dep.detected_image = det.image
                dep.detection_error = None
                ok += 1
                DETECTIONS.labels(env_names[dep_id], "ok").inc()
            else:
                dep.detection_error = err
                failed += 1
                errors.append(f"{names[dep_id]}: {err}")
                log.warning("detecção falhou: %s: %s", names[dep_id], err)
                DETECTIONS.labels(env_names[dep_id], "error").inc()
    return ok, failed, errors


def store_snapshots() -> None:
    today = datetime.now(timezone.utc).date()
    with session_scope() as s:
        statuses = compute_statuses(s)
        for env in load_environments(s):
            summary = summarize([st for st in statuses if st.environment_id == env.id])
            snap = s.scalar(select(Snapshot).where(Snapshot.environment_id == env.id, Snapshot.day == today))
            if snap is None:
                snap = Snapshot(environment_id=env.id, day=today)
                s.add(snap)
            c = summary["counts"]
            snap.total = summary["total"]
            snap.up_to_date = c["up_to_date"]
            snap.patch = c["patch"]
            snap.minor = c["minor"]
            snap.major = c["major"]
            snap.unknown = c["unknown"]
            snap.up_to_date_ratio = summary["up_to_date_ratio"]
            snap.libyears = summary["libyears"]
            snap.avg_outdated_days = summary["avg_outdated_days"]
            snap.max_outdated_days = summary["max_outdated_days"]
            snap.versions_behind = summary["versions_behind"]
            snap.updated_at = utcnow()


async def run_refresh(project_ids: list[int] | None = None, fetch: bool = True, detect: bool = True) -> dict:
    """Run a full (or partial) refresh. Concurrent calls wait for the running one."""
    if _LOCK.locked() and project_ids is None:
        return {**STATE.to_dict(), "message": "atualização já em andamento"}
    async with _LOCK:
        settings = get_settings()
        STATE.running = True
        STATE.started_at = utcnow()
        STATE.errors = []
        STATE.projects_ok = STATE.projects_failed = STATE.new_releases = 0
        STATE.detections_ok = STATE.detections_failed = 0
        t0 = time.monotonic()
        try:
            # Detect first: the running versions are hints for some release sources.
            if detect:
                ok, failed, errors = await detect_versions(project_ids)
                STATE.detections_ok, STATE.detections_failed = ok, failed
                STATE.errors.extend(errors)
            if fetch:
                with session_scope() as s:
                    stmt = select(Project.id).where(Project.enabled.is_(True))
                    if project_ids:
                        stmt = stmt.where(Project.id.in_(project_ids))
                    ids = list(s.scalars(stmt))
                sem = asyncio.Semaphore(6)
                async with httpx.AsyncClient(
                    timeout=settings.http_timeout,
                    follow_redirects=True,
                    headers={"User-Agent": "changelog-watcher"},
                ) as client:

                    async def one(pid: int):
                        async with sem:
                            try:
                                n = await refresh_project_releases(pid, client)
                                STATE.projects_ok += 1
                                STATE.new_releases += n
                            except Exception as exc:
                                STATE.projects_failed += 1
                                STATE.errors.append(str(exc))
                                log.warning("release fetch failed: %s", exc)

                    await asyncio.gather(*(one(pid) for pid in ids))
            store_snapshots()
            STATE.success = STATE.projects_failed == 0 and STATE.detections_failed == 0
            log.info("atualização concluída: %s projeto(s) ok, %s com erro, %s novas versões", STATE.projects_ok, STATE.projects_failed, STATE.new_releases)
            REFRESH_TOTAL.labels("ok" if STATE.success else "partial").inc()
        except Exception as exc:
            log.exception("refresh failed")
            STATE.success = False
            STATE.errors.append(f"falha geral: {exc}")
            REFRESH_TOTAL.labels("error").inc()
        finally:
            STATE.running = False
            STATE.finished_at = utcnow()
            STATE.duration_seconds = round(time.monotonic() - t0, 2)
            REFRESH_DURATION.observe(STATE.duration_seconds)
        return STATE.to_dict()
