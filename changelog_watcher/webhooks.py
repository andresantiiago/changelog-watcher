"""Deliver reports to webhooks (generic JSON, Slack, Microsoft Teams, Discord, Google Chat)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

from .config import expand_env, get_settings
from .models import Report, Webhook, utcnow
from .report import STATUS_ICON, _pct

log = logging.getLogger(__name__)

WEBHOOK_KINDS = {
    "generic": "JSON genérico",
    "slack": "Slack",
    "teams": "Microsoft Teams (Workflows / Adaptive Card)",
    "discord": "Discord",
    "google_chat": "Google Chat",
}


def filter_data(data: dict, environment_ids: list[int] | None) -> dict:
    if not environment_ids:
        return data
    ids = set(environment_ids)
    names = {e["name"] for e in data["environments"] if e["id"] in ids}
    out = dict(data)
    out["environments"] = [e for e in data["environments"] if e["id"] in ids]
    out["summaries"] = [s for s in data["summaries"] if s["environment_id"] in ids]
    out["outdated"] = [o for o in data["outdated"] if o["environment_id"] in ids]
    out["new_releases"] = [
        {**r, "deployed": {k: v for k, v in r["deployed"].items() if k in names}} for r in data["new_releases"]
    ]
    out["drift"] = [
        {**row, "environments": {k: v for k, v in row["environments"].items() if int(k) in ids}}
        for row in data["drift"]
    ]
    out["drift"] = [r for r in out["drift"]
                    if len({v["version"] for v in r["environments"].values() if v["version"]}) > 1]
    out["drift_pairs"] = [p for p in data["drift_pairs"] if p["source"] in names and p["target"] in names]
    return out


def chat_lines(data: dict, max_items: int = 25) -> list[str]:
    """Compact, table-free text used by chat integrations (standard markdown)."""
    lines = [f"**📦 Relatório de atualizações – {data['period_start'][:10]} → {data['period_end'][:10]}**", ""]
    for s in data["summaries"]:
        c = s["counts"]
        lines.append(
            f"**{s['environment']}**: {_pct(s['up_to_date_ratio'])} atualizado · {s['total']} apps · pendentes: "
            f"🟡 {s['patch_pending']} bugfix · 🟠 {s['minor_pending']} minor · 🔴 {s['major_pending']} major"
            + (f" · ⚪ {c['unknown']} sem versão" if c["unknown"] else "")
            + f" · {s['libyears']:.1f} libyears"
        )
    releases = data["new_releases"]
    lines += ["", f"**Novas versões ({len(releases)})**"]
    for r in releases[:max_items]:
        tag = f"[{r['tag']}]({r['url']})" if r.get("url") else r["tag"]
        lines.append(f"• {STATUS_ICON.get(r['level'], '•')} {r['project']} {tag}")
    if len(releases) > max_items:
        lines.append(f"• … e mais {len(releases) - max_items}")
    outdated = [o for o in data["outdated"] if o["status"] != "unknown"]
    lines += ["", f"**Desatualizadas ({len(outdated)})**"]
    for o in outdated[:max_items]:
        avail = " / ".join(
            f"{k} {o[f'latest_{k}']}" for k in ("patch", "minor", "major") if o.get(f"latest_{k}")
        ).replace("patch", "bugfix")
        lines.append(
            f"• {STATUS_ICON[o['status']]} {o['environment']} · {o['project']} {o['current_version']} → {avail} "
            f"({o['outdated_days']:.0f}d)"
        )
    if len(outdated) > max_items:
        lines.append(f"• … e mais {len(outdated) - max_items}")
    if data["drift"]:
        env_names = {str(e["id"]): e["name"] for e in data["environments"]}
        lines += ["", f"**Diferenças entre ambientes ({len(data['drift'])})**"]
        for row in data["drift"][:max_items]:
            vs = ", ".join(f"{env_names.get(k, k)}={v['version'] or '?'}" for k, v in row["environments"].items())
            lines.append(f"• {row['project']}: {vs}")
    if data.get("public_url"):
        lines += ["", f"🔗 {data['public_url']}"]
    return lines


def to_slack(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", text)
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)


def build_payloads(kind: str, data: dict, markdown: str) -> list[dict]:
    if kind == "slack":
        return [{"text": to_slack("\n".join(chat_lines(data))), "mrkdwn": True}]
    if kind == "google_chat":
        return [{"text": to_slack("\n".join(chat_lines(data)))}]
    if kind == "discord":
        chunks, current = [], ""
        for line in chat_lines(data):
            if len(current) + len(line) + 1 > 1900:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current.strip():
            chunks.append(current)
        return [{"content": c, "allowed_mentions": {"parse": []}} for c in chunks]
    if kind == "teams":
        body = [{"type": "TextBlock", "text": line or " ", "wrap": True,
                 **({"weight": "Bolder", "size": "Medium"} if i == 0 else {})}
                for i, line in enumerate(chat_lines(data))]
        return [{
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "msteams": {"width": "Full"},
                    "body": body,
                },
            }],
        }]
    return [{"event": "changelog_watcher.report", "markdown": markdown, "report": data}]


async def send_webhook(webhook: Webhook, data: dict, markdown: str, client: httpx.AsyncClient) -> dict:
    data = filter_data(data, webhook.environment_ids)
    if webhook.only_outdated and not any(o["status"] != "unknown" for o in data["outdated"]):
        return {"webhook": webhook.name, "ok": True, "skipped": True, "detail": "nada desatualizado"}
    url = expand_env(webhook.url)
    if not url:
        return {"webhook": webhook.name, "ok": False, "detail": "URL vazia"}
    from .report import render_markdown

    if webhook.environment_ids:
        markdown = render_markdown(data)
    payloads = build_payloads(webhook.kind, data, markdown)
    try:
        for payload in payloads:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 300:
                return {"webhook": webhook.name, "ok": False, "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        return {"webhook": webhook.name, "ok": True, "detail": f"{len(payloads)} mensagem(ns) enviada(s)"}
    except (httpx.HTTPError, httpx.InvalidURL) as exc:  # InvalidURL is not an HTTPError
        return {"webhook": webhook.name, "ok": False, "detail": str(exc)}


def record_status(webhook: Webhook, result: dict, suffix: str = "") -> None:
    webhook.last_sent_at = utcnow()
    webhook.last_status = ("OK" if result["ok"] else "ERRO") + f": {result.get('detail', '')}{suffix}"


async def deliver_report(session, report: Report, webhooks: list[Webhook]) -> list[dict]:
    results = []
    async with httpx.AsyncClient(timeout=get_settings().http_timeout) as client:
        for wh in webhooks:
            if not wh.enabled:
                continue
            result = await send_webhook(wh, report.data, report.markdown, client)
            result["sent_at"] = datetime.now(timezone.utc).isoformat()
            record_status(wh, result)
            results.append(result)
            if not result["ok"]:
                log.warning("webhook %s failed: %s", wh.name, result.get("detail"))
    report.delivery = list(report.delivery or []) + results
    return results
