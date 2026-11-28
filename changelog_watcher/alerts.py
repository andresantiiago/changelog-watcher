"""Immediate alerts after each refresh, outside the weekly report.

Rules, enabled per webhook: new major version, newer release whose notes mention a security fix,
outdated for more than N days. Each rule fires once per webhook, deployment and version
(``AlertSent``); a failed delivery is retried on the next refresh.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import safehttp
from .analysis import compute_statuses, project_versions, pv
from .config import expand_env, get_settings
from .i18n import _
from .models import AlertSent, Project, Webhook
from .versioning import STATUS_OUTDATED
from .webhooks import CHAT_KINDS, chat_payloads, post_payloads, record_status

log = logging.getLogger(__name__)

# ponytail: keyword match on title/notes; a vulnerability feed (OSV/GHSA) if this misses too much.
SECURITY_RE = re.compile(r"\bsecurity\b|\bCVE-\d{4}-\d{4,}\b|vulnerab|segurança", re.IGNORECASE)
ICONS = {"major": "🔴", "security": "🛡️", "overdue": "⏰"}


def find_alerts(session: Session, webhook: Webhook, statuses: list) -> list[dict]:
    """Alerts this webhook's rules raise now (already-sent ones included)."""
    envs = set(webhook.environment_ids or [])  # empty = all environments
    versions_cache: dict[int, dict] = {}
    out = []
    for st in statuses:
        if envs and st.environment_id not in envs:
            continue
        project = session.get(Project, st.project_id)
        versions = versions_cache.setdefault(project.id, project_versions(project))
        current = pv(project, st.current_version)
        hits: list[tuple[str, str]] = []  # (rule, version)
        if webhook.alert_major and st.major_behind and st.latest_major:
            hits.append(("major", st.latest_major))
        if webhook.alert_security and current:
            fixes = [
                v
                for v, e in versions.items()
                if v > current and SECURITY_RE.search(f"{e.release.title or ''}\n{e.release.notes or ''}")
            ]
            if fixes:
                hits.append(("security", str(max(fixes))))
        days = webhook.alert_overdue_days
        if days and st.status in STATUS_OUTDATED and st.outdated_days > days and st.latest_version:
            hits.append(("overdue", st.latest_version))
        for rule, version in hits:
            entry = versions.get(pv(project, version))
            out.append(
                {
                    "rule": rule,
                    "project_id": st.project_id,
                    "project": st.project,
                    "environment_id": st.environment_id,
                    "environment": st.environment,
                    "current_version": st.current_version,
                    "version": version,
                    "url": entry.release.url if entry else None,
                    "days": round(st.outdated_days),
                }
            )
    return out


def alert_lines(alerts: list[dict]) -> list[str]:
    lines = [_("**🚨 Changelog Watcher: {n} alert(s)**", n=len(alerts)), ""]
    for a in alerts:
        version = f"[{a['version']}]({a['url']})" if a["url"] else a["version"]
        reason = {
            "major": _("new major version"),
            "security": _("release mentions a security fix"),
            "overdue": _("outdated for {n} days", n=a["days"]),
        }[a["rule"]]
        lines.append(
            f"• {ICONS[a['rule']]} {a['environment']} · {a['project']} "
            f"{a['current_version'] or '?'} → {version}: {reason}"
        )
    if url := get_settings().public_url:
        lines += ["", f"🔗 {url}"]
    return lines


def _key(a: dict) -> tuple:
    return a["project_id"], a["environment_id"], a["rule"], a["version"]


async def send_alerts(session: Session) -> list[dict]:
    """Evaluate every enabled webhook with alert rules and deliver what is new. Returns delivery results."""
    hooks = list(
        session.scalars(
            select(Webhook).where(
                Webhook.enabled.is_(True),
                or_(
                    Webhook.alert_major.is_(True),
                    Webhook.alert_security.is_(True),
                    Webhook.alert_overdue_days.is_not(None),
                ),
            )
        )
    )
    if not hooks:
        return []
    statuses = compute_statuses(session)
    results = []
    async with safehttp.client(timeout=get_settings().http_timeout) as client:
        for wh in hooks:
            sent = {
                (r.project_id, r.environment_id, r.rule, r.version)
                for r in session.scalars(select(AlertSent).where(AlertSent.webhook_id == wh.id))
            }
            alerts = [a for a in find_alerts(session, wh, statuses) if _key(a) not in sent]
            if not alerts:
                continue
            url = expand_env(wh.url)
            if not url:
                result = {"webhook": wh.name, "ok": False, "detail": _("empty URL")}
            else:
                lines = alert_lines(alerts)
                payloads = (
                    chat_payloads(wh.kind, lines)
                    if wh.kind in CHAT_KINDS
                    else [{"event": "changelog_watcher.alert", "markdown": "\n".join(lines), "alerts": alerts}]
                )
                result = await post_payloads(wh.name, url, payloads, client)
            record_status(wh, result, _(" (alerts)"))
            results.append(result)
            if result["ok"]:
                session.add_all(
                    AlertSent(
                        webhook_id=wh.id,
                        project_id=a["project_id"],
                        environment_id=a["environment_id"],
                        rule=a["rule"],
                        version=a["version"],
                    )
                    for a in alerts
                )
            else:
                log.warning("alert webhook %s failed: %s", wh.name, result.get("detail"))
    return results
