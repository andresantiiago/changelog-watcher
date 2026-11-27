from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .models import AppSetting, Webhook
from .refresh import run_refresh
from .report import create_report
from .webhooks import deliver_report

log = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


async def scheduled_report() -> None:
    """Refresh, build the weekly report and send it to every enabled webhook."""
    settings = get_settings()
    await run_refresh()
    with session_scope() as s:
        report = create_report(s, trigger="schedule")
        if settings.report_send_enabled:
            hooks = list(s.scalars(select(Webhook).where(Webhook.enabled.is_(True))))
            await deliver_report(s, report, hooks)


def effective_schedule() -> dict:
    """Env-var defaults overridden by values saved in the UI (app_settings)."""
    settings = get_settings()
    out = {"refresh_interval_minutes": settings.refresh_interval_minutes, "report_cron": settings.report_cron}
    with session_scope() as s:
        for row in s.scalars(select(AppSetting)):
            if row.key in out:
                out[row.key] = int(row.value) if row.key == "refresh_interval_minutes" else row.value
    return out


def _schedule_jobs() -> None:
    settings = get_settings()
    sch = effective_schedule()
    for job_id in ("refresh", "weekly_report"):
        if _scheduler.get_job(job_id):
            _scheduler.remove_job(job_id)
    if sch["refresh_interval_minutes"] > 0:
        _scheduler.add_job(
            run_refresh,
            IntervalTrigger(minutes=sch["refresh_interval_minutes"]),
            id="refresh",
            max_instances=1,
            coalesce=True,
        )
    if sch["report_cron"]:
        _scheduler.add_job(
            scheduled_report,
            CronTrigger.from_crontab(sch["report_cron"], timezone=settings.timezone),
            id="weekly_report",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    log.info(
        "scheduler configured (refresh every %s min, report '%s' %s)",
        sch["refresh_interval_minutes"],
        sch["report_cron"],
        settings.timezone,
    )


def start_scheduler() -> None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        return
    _scheduler = AsyncIOScheduler(timezone=settings.timezone)
    _schedule_jobs()
    _scheduler.start()


def reschedule() -> None:
    if _scheduler:
        _schedule_jobs()


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_runs() -> dict:
    if not _scheduler:
        return {}
    return {job.id: job.next_run_time.isoformat() if job.next_run_time else None for job in _scheduler.get_jobs()}
