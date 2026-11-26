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
        return "já atingido" if st.get("eol_status") == "eol" else "—"
    if left <= 0:
        return f"{when} (há {-left} dias)" if left else f"{when} (hoje)"
    return f"{when} (em {left} dias)"


def _pct(ratio) -> str:
    return "n/d" if ratio is None else f"{ratio * 100:.0f}%"


def _arrow(st: dict) -> str:
    kinds = (("patch", "bugfix"), ("minor", "minor"), ("major", "major"))
    return ", ".join(f"{name} **{st[f'latest_{k}']}**" for k, name in kinds if st.get(f"latest_{k}")) or "—"


def render_markdown(data: dict, max_releases: int = 30, max_outdated: int = 60) -> str:
    start = data["period_start"][:10]
    end = data["period_end"][:10]
    lines = [f"# 📦 Relatório de atualizações ({start} → {end})", ""]

    lines.append("## Resumo por ambiente")
    lines.append("")
    lines.append("| Ambiente | Atualizado | Apps | Com bugfix pendente | Com minor pendente | Com major pendente "
                 "| Sem versão | Libyears | Atraso médio (dias) |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s in data["summaries"]:
        c = s["counts"]
        lines.append(
            f"| {s['environment']} | {_pct(s['up_to_date_ratio'])} | {s['total']} | {s['patch_pending']} | "
            f"{s['minor_pending']} | {s['major_pending']} | {c['unknown']} | {s['libyears']:.2f} | "
            f"{s['avg_outdated_days']:.0f} |"
        )
    lines.append("")

    releases = data["new_releases"]
    lines.append(f"## Novas versões nos últimos {data['days']} dias ({len(releases)})")
    lines.append("")
    if not releases:
        lines.append("_Nenhuma nova versão publicada no período._")
    for r in releases[:max_releases]:
        icon = STATUS_ICON.get(r["level"], "•")
        link = f"[{r['tag']}]({r['url']})" if r.get("url") else r["tag"]
        deployed = ", ".join(f"{env}: {v or '?'}" for env, v in r["deployed"].items()) or "não implantado"
        lines.append(f"- {icon} **{r['project']}** {link} — {r['published_at'][:10] if r['published_at'] else ''} ({deployed})")
    if len(releases) > max_releases:
        lines.append(f"- … e mais {len(releases) - max_releases}")
    lines.append("")

    outdated = [o for o in data["outdated"] if o["status"] != "unknown"]
    lines.append(f"## Aplicações desatualizadas ({len(outdated)})")
    lines.append("")
    if outdated:
        lines.append("| Ambiente | Aplicação | Atual | Disponível | Atrasada há |")
        lines.append("|---|---|---|---|---|")
        for o in outdated[:max_outdated]:
            lines.append(
                f"| {o['environment']} | {STATUS_ICON[o['status']]} {o['project']} | {o['current_version']} | "
                f"{_arrow(o)} | {o['outdated_days']:.0f} dias |"
            )
        if len(outdated) > max_outdated:
            lines.append(f"\n_… e mais {len(outdated) - max_outdated}_")
    else:
        lines.append("_Tudo atualizado 🎉_")
    lines.append("")

    eol = data.get("eol") or []
    if eol:
        lines.append(f"## Fim de suporte (EOL) ({len(eol)})")
        lines.append("")
        lines.append("| Ambiente | Aplicação | Em uso | Ciclo | Fim de suporte |")
        lines.append("|---|---|---|---|---|")
        for o in eol[:max_outdated]:
            lines.append(f"| {o['environment']} | {EOL_ICON[o['eol_status']]} {o['project']} | {o['current_version']} | "
                         f"{o['eol_product']} {o['eol_cycle']} | {eol_when(o)} |")
        if len(eol) > max_outdated:
            lines.append(f"\n_… e mais {len(eol) - max_outdated}_")
        lines.append("")

    unknown = [o for o in data["outdated"] if o["status"] == "unknown"]
    if unknown:
        lines.append(f"## Sem versão identificada ({len(unknown)})")
        lines.append("")
        for o in unknown[:30]:
            reason = o.get("detection_error") or o.get("project_error") or "versão atual ou releases não encontradas"
            lines.append(f"- {o['environment']} / **{o['project']}**: {reason}")
        lines.append("")

    if data["drift"]:
        env_names = {str(e["id"]): e["name"] for e in data["environments"]}
        lines.append(f"## Diferenças entre ambientes ({len(data['drift'])})")
        lines.append("")
        lines.append("| Aplicação | " + " | ".join(env_names.values()) + " |")
        lines.append("|---|" + "---|" * len(env_names))
        for row in data["drift"]:
            cells = [(row["environments"].get(eid) or {}).get("version") or "—" for eid in env_names]
            lines.append(f"| {row['project']} | " + " | ".join(cells) + " |")
        lines.append("")

    if data.get("public_url"):
        lines.append(f"🔗 Detalhes: {data['public_url']}")
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
