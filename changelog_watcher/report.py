"""Weekly report: structured data + Markdown rendering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .analysis import (
    compute_statuses,
    drift_pairs,
    load_environments,
    recent_releases,
    summaries_by_environment,
    version_matrix,
)
from .config import get_settings
from .i18n import _
from .models import Report

STATUS_ICON = {"up_to_date": "✅", "patch": "🟡", "minor": "🟠", "major": "🔴", "unknown": "⚪", "older": "▫️"}


def build_report_data(session: Session, days: int | None = None, environment_ids: list[int] | None = None,
                      now: datetime | None = None) -> dict:
    settings = get_settings()
    days = days or settings.report_days
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    environments = [e for e in load_environments(session) if not environment_ids or e.id in environment_ids]
    statuses = compute_statuses(session, [e.id for e in environments], now=now)
    summaries = summaries_by_environment(statuses, environments)
    releases = recent_releases(session, days=days, now=now)
    outdated = sorted(
        (s.to_dict() for s in statuses if s.status not in ("up_to_date",)),
        key=lambda s: (s["environment"], -s["level"], -s["outdated_days"], s["project"]),
    )
    matrix = version_matrix(statuses)
    eol = sorted(
        (s.to_dict() for s in statuses if s.eol_status in ("eol", "soon")),
        key=lambda s: (s["eol_status"] != "eol", s["eol_days_left"] if s["eol_days_left"] is not None else -10**6,
                       s["environment"], s["project"]),
    )
    return {
        "generated_at": now.isoformat(),
        "period_start": start.isoformat(),
        "period_end": now.isoformat(),
        "days": days,
        "public_url": settings.public_url or None,
        "environments": [{"id": e.id, "name": e.name} for e in environments],
        "summaries": summaries,
        "new_releases": releases,
        "outdated": outdated,
        "drift": [r for r in matrix if r["drift"]],
        "drift_pairs": drift_pairs(statuses, environments),
        "eol": eol,
        "eol_warning_days": settings.eol_warning_days,
    }


EOL_ICON = {"eol": "⛔", "soon": "⏳"}


def eol_when(st: dict) -> str:
    """Human text for a deployment's EOL: date and days left/ago."""
    left, when = st.get("eol_days_left"), st.get("eol_date")
    if left is None:
        return _("already reached") if st.get("eol_status") == "eol" else "—"
    if left <= 0:
        return _("{date} ({n} days ago)", date=when, n=-left) if left else _("{date} (today)", date=when)
    return _("{date} (in {n} days)", date=when, n=left)


def _days(n: float) -> str:
    n = round(n)
    return _("{n} day", n=n) if n == 1 else _("{n} days", n=n)


def _row(*cells: str) -> str:
    return "| " + " | ".join(cells) + " |"


def _pct(ratio) -> str:
    return _("n/a") if ratio is None else f"{ratio * 100:.0f}%"


def _arrow(st: dict) -> str:
    kinds = (("patch", _("bugfix")), ("minor", "minor"), ("major", "major"))
    return ", ".join(f"{name} **{st[f'latest_{k}']}**" for k, name in kinds if st.get(f"latest_{k}")) or "—"


def _libyears(summary: dict) -> str:
    avg = summary.get("libyears_avg")
    return "—" if avg is None else f"{avg:.2f}"


def render_markdown(data: dict, max_releases: int = 30, max_outdated: int = 60) -> str:
    start = data["period_start"][:10]
    end = data["period_end"][:10]
    lines = ["# " + _("📦 Update report ({start} → {end})", start=start, end=end), ""]

    lines.append("## " + _("Summary by environment"))
    lines.append("")
    lines.append(_row(_("Environment"), _("Up to date"), _("Apps"), _("With bugfix pending"), _("With minor pending"),
                      _("With major pending"), _("No version"), _("Libyears (average)"), _("Average delay (days)")))
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s in data["summaries"]:
        c = s["counts"]
        lines.append(
            f"| {s['environment']} | {_pct(s['up_to_date_ratio'])} | {s['total']} | {s['patch_pending']} | "
            f"{s['minor_pending']} | {s['major_pending']} | {c['unknown']} | {_libyears(s)} | "
            f"{s['avg_outdated_days']:.0f} |"
        )
    lines.append("")

    releases = data["new_releases"]
    lines.append("## " + _("New versions in the last {days} days ({n})", days=data["days"], n=len(releases)))
    lines.append("")
    if not releases:
        lines.append(_("_No new version published in the period._"))
    for r in releases[:max_releases]:
        icon = STATUS_ICON.get(r["level"], "•")
        link = f"[{r['tag']}]({r['url']})" if r.get("url") else r["tag"]
        deployed = ", ".join(f"{env}: {v or '?'}" for env, v in r["deployed"].items()) or _("not deployed")
        lines.append(f"- {icon} **{r['project']}** {link} — {r['published_at'][:10] if r['published_at'] else ''} ({deployed})")
    if len(releases) > max_releases:
        lines.append(_("- … and {n} more", n=len(releases) - max_releases))
    lines.append("")

    outdated = [o for o in data["outdated"] if o["status"] != "unknown"]
    lines.append("## " + _("Outdated applications ({n})", n=len(outdated)))
    lines.append("")
    if outdated:
        lines.append(_row(_("Environment"), _("Application"), _("Current"), _("Available"), _("Outdated for")))
        lines.append("|---|---|---|---|---|")
        for o in outdated[:max_outdated]:
            lines.append(
                f"| {o['environment']} | {STATUS_ICON[o['status']]} {o['project']} | {o['current_version']} | "
                f"{_arrow(o)} | " + _days(o["outdated_days"]) + " |"
            )
        if len(outdated) > max_outdated:
            lines.append("\n" + _("_… and {n} more_", n=len(outdated) - max_outdated))
    else:
        lines.append(_("_Everything up to date 🎉_"))
    lines.append("")

    eol = data.get("eol") or []
    if eol:
        lines.append("## " + _("End of life (EOL) ({n})", n=len(eol)))
        lines.append("")
        lines.append(_row(_("Environment"), _("Application"), _("In use"), _("Cycle"), _("End of life")))
        lines.append("|---|---|---|---|---|")
        for o in eol[:max_outdated]:
            lines.append(f"| {o['environment']} | {EOL_ICON[o['eol_status']]} {o['project']} | {o['current_version']} | "
                         f"{o['eol_product']} {o['eol_cycle']} | {eol_when(o)} |")
        if len(eol) > max_outdated:
            lines.append("\n" + _("_… and {n} more_", n=len(eol) - max_outdated))
        lines.append("")

    unknown = [o for o in data["outdated"] if o["status"] == "unknown"]
    if unknown:
        lines.append("## " + _("No version identified ({n})", n=len(unknown)))
        lines.append("")
        for o in unknown[:30]:
            reason = o.get("detection_error") or o.get("project_error") or _("current version or releases not found")
            lines.append(f"- {o['environment']} / **{o['project']}**: {reason}")
        lines.append("")

    if data["drift"]:
        env_names = {str(e["id"]): e["name"] for e in data["environments"]}
        lines.append("## " + _("Differences between environments ({n})", n=len(data["drift"])))
        lines.append("")
        lines.append(_row(_("Application"), *env_names.values()))
        lines.append("|---|" + "---|" * len(env_names))
        for row in data["drift"]:
            cells = [(row["environments"].get(eid) or {}).get("version") or "—" for eid in env_names]
            lines.append(f"| {row['project']} | " + " | ".join(cells) + " |")
        lines.append("")

    if data.get("public_url"):
        lines.append(_("🔗 Details: {url}", url=data["public_url"]))
    return "\n".join(lines).strip() + "\n"


def create_report(session: Session, trigger: str = "manual", days: int | None = None,
                  environment_ids: list[int] | None = None) -> Report:
    data = build_report_data(session, days=days, environment_ids=environment_ids)
    report = Report(
        trigger=trigger,
        period_start=datetime.fromisoformat(data["period_start"]),
        period_end=datetime.fromisoformat(data["period_end"]),
        markdown=render_markdown(data),
        data=data,
        delivery=[],
    )
    session.add(report)
    session.flush()
    return report
