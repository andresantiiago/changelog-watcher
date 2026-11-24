from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from . import logstore
from . import prometheus as prom
from .analysis import (
    AGE_BUCKETS,
    _iso,
    changelog_between,
    compare_environments,
    compute_statuses,
    drift_pairs,
    load_environments,
    project_versions,
    recent_releases,
    release_dict,
    summaries_by_environment,
    summarize,
    version_matrix,
)
from .config import SECRET_MASK, get_settings, mask_secret
from .db import get_db
from .models import (
    SNAPSHOT_FIELDS,
    AppSetting,
    Deployment,
    Environment,
    LogEntry,
    Project,
    Release,
    Report,
    Snapshot,
    Webhook,
    utcnow,
)
from .refresh import STATE, run_refresh
from .report import create_report
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, Field

from .scheduler import effective_schedule, next_runs, reschedule
from .schemas import (
    DETECTION_MODES,
    EnvironmentIn,
    EnvironmentOut,
    ProjectIn,
    ProjectOut,
    RefreshRequest,
    ReportRequest,
    WebhookIn,
    WebhookOut,
)
from .sources import SOURCE_TYPES
from .suggest import suggest_sources
from .webhooks import WEBHOOK_KINDS, deliver_report, record_status, send_webhook

router = APIRouter(prefix="/api")
_background: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


def _get(session: Session, model, obj_id: int):
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {obj_id} não encontrado")
    return obj


def _commit(session: Session):
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "já existe um registro com esse nome") from exc


# ------------------------------------------------------------------- meta
@router.get("/meta")
def meta():
    s = get_settings()
    sch = effective_schedule()
    return {
        "source_types": SOURCE_TYPES,
        "webhook_kinds": WEBHOOK_KINDS,
        "detection_modes": DETECTION_MODES,
        "age_buckets": [b[0] for b in AGE_BUCKETS],
        "schedule": {
            "refresh_interval_minutes": sch["refresh_interval_minutes"],
            "report_cron": sch["report_cron"],
            "timezone": s.timezone,
            "next_runs": next_runs(),
        },
        "github_token": bool(s.github_token),
        "public_url": s.public_url,
    }


# ----------------------------------------------------------- environments
def _env_out(env: Environment) -> dict:
    data = EnvironmentOut.model_validate(env).model_dump()
    data["prometheus_token"] = mask_secret(env.prometheus_token)
    data["prometheus_password"] = mask_secret(env.prometheus_password)
    return data


def _apply_body(obj, body: BaseModel, secrets: tuple[str, ...]) -> None:
    for key, value in body.model_dump().items():
        if key in secrets and value == SECRET_MASK:
            continue  # unchanged secret
        setattr(obj, key, value)


ENV_SECRETS = ("prometheus_token", "prometheus_password")


@router.get("/environments")
def list_environments(db: Session = Depends(get_db)):
    return [_env_out(e) for e in load_environments(db)]


@router.post("/environments", status_code=201)
def create_environment(body: EnvironmentIn, db: Session = Depends(get_db)):
    env = Environment()
    _apply_body(env, body, ENV_SECRETS)
    db.add(env)
    _commit(db)
    return _env_out(env)


@router.put("/environments/{env_id}")
def update_environment(env_id: int, body: EnvironmentIn, db: Session = Depends(get_db)):
    env = _get(db, Environment, env_id)
    _apply_body(env, body, ENV_SECRETS)
    _commit(db)
    return _env_out(env)


@router.delete("/environments/{env_id}", status_code=204)
def delete_environment(env_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, Environment, env_id))
    return Response(status_code=204)


@router.post("/environments/{env_id}/test")
async def test_environment(env_id: int, db: Session = Depends(get_db)):
    env = _get(db, Environment, env_id)
    try:
        return await prom.test_connection(env)
    except prom.PrometheusError as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/environments/{env_id}/discover")
async def discover_environment(env_id: int, namespace: str | None = None, db: Session = Depends(get_db)):
    """Workloads of a namespace grouped by Helm release, with the project that already tracks each group."""
    env = _get(db, Environment, env_id)
    try:
        groups = await prom.discover(env, (namespace or "").strip() or None)
    except prom.PrometheusError as exc:
        return {"ok": False, "error": str(exc)}
    tracked = {(p.workload_namespace, p.workload_name): p for p in db.scalars(select(Project))}
    for g in groups:
        s = g["suggestion"]
        p = tracked.get((s["workload_namespace"], s.get("workload_name")))
        g["project"] = {"id": p.id, "name": p.name} if p else None
    return {"ok": True, "groups": groups}


@router.get("/suggest-source")
async def suggest_source(name: str | None = None, image: str | None = None, chart: str | None = None):
    """Likely release sources for an app (image label, Artifact Hub, Docker Hub, GitHub search)."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=s.http_timeout, follow_redirects=True,
                                 headers={"User-Agent": "changelog-watcher"}) as client:
        return {"candidates": await suggest_sources(client, s, name=name, image=image, chart=chart)}


# --------------------------------------------------------------- projects
def _project_out(p: Project, release_count: int = 0) -> dict:
    return ProjectOut.model_validate(p).model_dump() | {"release_count": release_count}


def _release_counts(db: Session) -> dict[int, int]:
    return dict(db.execute(select(Release.project_id, func.count()).group_by(Release.project_id)).all())


def _apply_project(db: Session, project: Project, body: ProjectIn) -> None:
    data = body.model_dump(exclude={"deployments"})
    for key, value in data.items():
        if isinstance(value, str):
            value = value.strip() or None
            if key in ("name", "repository") and value is None:
                raise HTTPException(422, f"{key} é obrigatório")
        setattr(project, key, value)
    if body.deployments is None:
        return
    _commit(db)  # project.id is needed below; duplicate names become a 409
    env_ids = {e.id for e in db.scalars(select(Environment))}
    existing = {d.environment_id: d for d in project.deployments}
    for env_id, dep_in in body.deployments.items():
        if env_id not in env_ids:
            raise HTTPException(422, f"ambiente {env_id} não existe")
        dep = existing.pop(env_id, None)
        if dep is None:
            dep = Deployment(project_id=project.id, environment_id=env_id)
            project.deployments.append(dep)
        _apply_deployment(dep, dep_in)
    for dep in existing.values():
        project.deployments.remove(dep)


def _apply_deployment(dep: Deployment, dep_in) -> None:
    old = dep.current_version
    for key, value in dep_in.model_dump().items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(dep, key, value)
    if dep.current_version != old:
        dep.version_changed_at = utcnow()


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    counts = _release_counts(db)
    projects = db.scalars(select(Project).options(selectinload(Project.deployments)).order_by(Project.name))
    return [_project_out(p, counts.get(p.id, 0)) for p in projects]


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    p = _get(db, Project, project_id)
    return _project_out(p, _release_counts(db).get(p.id, 0))


@router.post("/projects", status_code=201)
async def create_project(body: ProjectIn, refresh: bool = True, db: Session = Depends(get_db)):
    project = Project(deployments=[])
    db.add(project)
    _apply_project(db, project, body)
    _commit(db)
    out = _project_out(project)
    db.commit()
    if refresh:
        _spawn(run_refresh(project_ids=[project.id]))
    return out


@router.put("/projects/{project_id}")
async def update_project(project_id: int, body: ProjectIn, refresh: bool = True, db: Session = Depends(get_db)):
    project = _get(db, Project, project_id)
    _apply_project(db, project, body)
    _commit(db)
    out = _project_out(project, _release_counts(db).get(project.id, 0))
    db.commit()
    if refresh:
        _spawn(run_refresh(project_ids=[project.id]))
    return out


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, Project, project_id))
    return Response(status_code=204)


@router.post("/projects/{project_id}/refresh")
async def refresh_project(project_id: int, db: Session = Depends(get_db)):
    _get(db, Project, project_id)
    db.close()
    return await run_refresh(project_ids=[project_id])


@router.get("/projects/{project_id}/releases")
def project_releases(project_id: int, limit: int = 100, db: Session = Depends(get_db)):
    project = _get(db, Project, project_id)
    versions = project_versions(project)
    ordered = sorted(versions, reverse=True)[:limit]
    return [release_dict(versions[v].release, v) for v in ordered]


@router.get("/projects/{project_id}/changelog")
def project_changelog(project_id: int, from_version: str | None = Query(None, alias="from"),
                      to_version: str | None = Query(None, alias="to"), db: Session = Depends(get_db)):
    project = _get(db, Project, project_id)
    return changelog_between(project, from_version, to_version)


@router.post("/projects/{project_id}/detect")
async def preview_detection(project_id: int, environment_id: int, db: Session = Depends(get_db)):
    """Run the Prometheus detection once without saving (used by the UI 'testar' button)."""
    project = _get(db, Project, project_id)
    env = _get(db, Environment, environment_id)
    dep = db.scalar(select(Deployment).where(Deployment.project_id == project_id,
                                             Deployment.environment_id == environment_id))
    try:
        det = await prom.detect(project, dep, env)
        return {"ok": True, "query": det.query, "image": det.image, "version": det.version, "images": det.images}
    except prom.PrometheusError as exc:
        query = None
        try:
            query = prom.build_query(project, dep, env)
        except prom.PrometheusError:
            pass
        return {"ok": False, "error": str(exc), "query": query}


# ----------------------------------------------------------------- status
@router.get("/status")
def status(environment_id: list[int] | None = Query(None), db: Session = Depends(get_db)):
    return [s.to_dict() for s in compute_statuses(db, environment_id)]


@router.get("/dashboard")
def dashboard(environment_id: list[int] | None = Query(None), namespace: str | None = None, db: Session = Depends(get_db)):
    envs = [e for e in load_environments(db) if not environment_id or e.id in environment_id]
    statuses = compute_statuses(db, [e.id for e in envs])
    namespaces = sorted({st.namespace for st in statuses if st.namespace})
    if namespace:
        statuses = [st for st in statuses if st.namespace == namespace]
    overall = summarize(statuses)
    most_outdated = sorted(
        (s for s in statuses if s.status not in ("up_to_date", "unknown")),
        key=lambda s: (-s.outdated_days, -s.level),
    )[:15]
    counts_projects = db.scalar(select(func.count()).select_from(Project))
    return {
        "namespaces": namespaces,
        "overall": overall,
        "environments": summaries_by_environment(statuses, envs),
        "most_outdated": [s.to_dict() for s in most_outdated],
        "drift_pairs": drift_pairs(statuses, envs),
        "drift_count": sum(1 for r in version_matrix(statuses) if r["drift"]),
        "projects": counts_projects,
        "refresh": STATE.to_dict(),
    }


@router.get("/history")
def history(days: int = 90, environment_id: list[int] | None = Query(None), db: Session = Depends(get_db)):
    since = utcnow().date() - timedelta(days=days)  # snapshots are keyed by UTC day
    stmt = select(Snapshot, Environment.name).join(Environment).where(Snapshot.day >= since)
    if environment_id:
        stmt = stmt.where(Snapshot.environment_id.in_(environment_id))
    return [
        {"environment_id": snap.environment_id, "environment": env_name, "day": snap.day.isoformat(),
         **{k: getattr(snap, k) for k in SNAPSHOT_FIELDS}}
        for snap, env_name in db.execute(stmt.order_by(Snapshot.day))
    ]


@router.get("/matrix")
def matrix(db: Session = Depends(get_db)):
    envs = load_environments(db)
    statuses = compute_statuses(db)
    return {"environments": [{"id": e.id, "name": e.name} for e in envs], "rows": version_matrix(statuses)}


@router.get("/compare")
def compare(source: int, target: int, only_differences: bool = False, db: Session = Depends(get_db)):
    _get(db, Environment, source)
    _get(db, Environment, target)
    return compare_environments(db, source, target, only_differences)


@router.get("/releases/recent")
def releases_recent(days: int = 7, db: Session = Depends(get_db)):
    return recent_releases(db, days=days)


# ---------------------------------------------------------------- refresh
@router.post("/refresh")
async def refresh(body: RefreshRequest | None = None):
    body = body or RefreshRequest()
    if body.wait:
        return await run_refresh(body.project_ids, body.fetch, body.detect)
    if STATE.running:
        return {**STATE.to_dict(), "message": "atualização já em andamento"}
    _spawn(run_refresh(body.project_ids, body.fetch, body.detect))
    await asyncio.sleep(0)
    return {**STATE.to_dict(), "running": True, "message": "atualização iniciada"}


@router.get("/refresh/status")
def refresh_status():
    return STATE.to_dict()


# ---------------------------------------------------------------- reports
def _report_summary(r: Report) -> dict:
    return {
        "id": r.id, "created_at": _iso(r.created_at), "trigger": r.trigger,
        "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
        "delivery": r.delivery,
        "new_releases": len(r.data.get("new_releases", [])),
        "outdated": sum(1 for o in r.data.get("outdated", []) if o["status"] != "unknown"),
    }


@router.get("/reports")
def list_reports(limit: int = 50, db: Session = Depends(get_db)):
    return [_report_summary(r) for r in db.scalars(select(Report).order_by(Report.created_at.desc()).limit(limit))]


@router.get("/reports/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db)):
    r = _get(db, Report, report_id)
    return {**_report_summary(r), "markdown": r.markdown, "data": r.data}


@router.get("/reports/{report_id}/markdown")
def get_report_markdown(report_id: int, db: Session = Depends(get_db)):
    r = _get(db, Report, report_id)
    return Response(r.markdown, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="relatorio-{r.id}.md"'})


@router.post("/reports", status_code=201)
async def generate_report(body: ReportRequest | None = None, db: Session = Depends(get_db)):
    body = body or ReportRequest()
    report = create_report(db, trigger="manual", days=body.days, environment_ids=body.environment_ids)
    if body.send:
        await deliver_report(db, report, _hooks(db, body.webhook_ids))
    db.flush()
    return {**_report_summary(report), "markdown": report.markdown, "data": report.data}


@router.post("/reports/{report_id}/send")
async def send_report(report_id: int, webhook_ids: list[int] | None = None, db: Session = Depends(get_db)):
    report = _get(db, Report, report_id)
    results = await deliver_report(db, report, _hooks(db, webhook_ids))
    return {"delivery": results}


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(report_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, Report, report_id))
    return Response(status_code=204)


def _hooks(db: Session, ids: list[int] | None) -> list[Webhook]:
    stmt = select(Webhook).where(Webhook.enabled.is_(True))
    if ids:
        stmt = stmt.where(Webhook.id.in_(ids))
    return list(db.scalars(stmt))


# --------------------------------------------------------------- webhooks
def _hook_out(w: Webhook) -> dict:
    data = WebhookOut.model_validate(w).model_dump()
    data["url"] = mask_secret(w.url)
    return data


@router.get("/webhooks")
def list_webhooks(db: Session = Depends(get_db)):
    return [_hook_out(w) for w in db.scalars(select(Webhook).order_by(Webhook.name))]


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookIn, db: Session = Depends(get_db)):
    if body.url == SECRET_MASK:
        raise HTTPException(422, "informe a URL")
    w = Webhook()
    _apply_body(w, body, ("url",))
    db.add(w)
    _commit(db)
    return _hook_out(w)


@router.put("/webhooks/{hook_id}")
def update_webhook(hook_id: int, body: WebhookIn, db: Session = Depends(get_db)):
    w = _get(db, Webhook, hook_id)
    _apply_body(w, body, ("url",))
    _commit(db)
    return _hook_out(w)


@router.delete("/webhooks/{hook_id}", status_code=204)
def delete_webhook(hook_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, Webhook, hook_id))
    return Response(status_code=204)


@router.post("/webhooks/{hook_id}/test")
async def test_webhook(hook_id: int, db: Session = Depends(get_db)):
    from .report import build_report_data, render_markdown

    w = _get(db, Webhook, hook_id)
    data = build_report_data(db)
    result = await send_webhook(w, data, render_markdown(data))
    record_status(w, result, " (teste)")
    return result


# --------------------------------------------------------------- settings
class SettingsIn(BaseModel):
    report_cron: str = Field(min_length=1)
    # upper bound: an overflowing interval is saved, then breaks the scheduler on every startup
    refresh_interval_minutes: int = Field(ge=0, le=1_000_000)


def _settings_out() -> dict:
    return {**effective_schedule(), "timezone": get_settings().timezone, "next_runs": next_runs()}


@router.get("/settings")
def get_app_settings():
    return _settings_out()


@router.put("/settings")
def update_app_settings(body: SettingsIn, db: Session = Depends(get_db)):
    cron = body.report_cron.strip()
    try:
        CronTrigger.from_crontab(cron)
    except ValueError as exc:
        raise HTTPException(422, f"expressão cron inválida: {exc}") from exc
    for key, value in (("report_cron", cron), ("refresh_interval_minutes", str(body.refresh_interval_minutes))):
        row = db.get(AppSetting, key) or AppSetting(key=key, value=value)
        row.value = value
        db.add(row)
    db.commit()
    reschedule()
    return _settings_out()


# ------------------------------------------------------------------- logs
@router.get("/logs")
def list_logs(level: str | None = None, q: str | None = None, limit: int = Query(300, le=1000)):
    logstore.flush()  # records are written by a background thread
    db = logstore.session()
    if db is None:
        return []
    stmt = select(LogEntry).order_by(LogEntry.id.desc()).limit(limit)
    if level:
        stmt = stmt.where(LogEntry.level == level.upper())
    if q:
        stmt = stmt.where(LogEntry.message.ilike(f"%{q}%"))
    with db:
        return [
            # _iso: SQLite returns naive datetimes; without the offset the browser reads UTC as local time
            {"id": e.id, "created_at": _iso(e.created_at), "level": e.level, "logger": e.logger,
             "message": e.message, "detail": e.detail}
            for e in db.scalars(stmt)
        ]
