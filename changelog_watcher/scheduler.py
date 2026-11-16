from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .models import Webhook
from .refresh import run_refresh
from .report import create_report
from .webhooks import deliver_report

log = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


async def scheduled_report() -> dict:
    """Refresh, build the weekly report and send it to every enabled webhook."""
    settings = get_settings()
    await run_refresh()
    with session_scope() as s:
        report = create_report(s, trigger="schedule")
        if settings.report_send_enabled:
            hooks = list(s.scalars(select(Webhook).where(Webhook.enabled.is_(True))))
            await deliver_report(s, report, hooks)
        return {"report_id": report.id, "delivery": report.delivery}


def start_scheduler() -> AsyncIOScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        return None
    _scheduler = AsyncIOScheduler(timezone=settings.timezone)
    if settings.refresh_interval_minutes > 0:
        _scheduler.add_job(run_refresh, IntervalTrigger(minutes=settings.refresh_interval_minutes),
                           id="refresh", max_instances=1, coalesce=True)
    if settings.report_cron:
        _scheduler.add_job(scheduled_report, CronTrigger.from_crontab(settings.report_cron, timezone=settings.timezone),
                           id="weekly_report", max_instances=1, coalesce=True, misfire_grace_time=3600)
    _scheduler.start()
    log.info("scheduler started (refresh every %s min, report '%s' %s)",
             settings.refresh_interval_minutes, settings.report_cron, settings.timezone)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_runs() -> dict:
    if not _scheduler:
        return {}
    return {job.id: job.next_run_time.isoformat() if job.next_run_time else None for job in _scheduler.get_jobs()}
