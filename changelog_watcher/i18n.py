"""Interface language (English by default, Brazilian Portuguese available).

Messages are written in English in the code and wrapped in ``_()``; ``_PT_BR`` holds their
translations. The language comes from the ``APP_LANGUAGE`` env var, overridden by the value saved
on the Settings page (``app_settings.language``). The SPA has its own catalog
(``static/i18n.js``) and reads the language from ``/api/meta``.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"
LANGUAGES = {"en": "English", "pt-BR": "Português (Brasil)"}
_ALIASES = {"en": "en", "en-us": "en", "en_us": "en", "pt": "pt-BR", "pt-br": "pt-BR", "pt_br": "pt-BR"}

_current: str | None = None


def normalize(value: str | None) -> str:
    """ "pt_BR" / "pt" / "PT-br" -> "pt-BR"; unknown values -> English."""
    lang = _ALIASES.get((value or "").strip().lower())
    if lang is None and value:
        log.warning("unsupported language %r, using %s", value, DEFAULT_LANGUAGE)
    return lang or DEFAULT_LANGUAGE


def _load() -> str:
    from .config import get_settings

    lang = get_settings().language
    try:
        from .db import session_scope
        from .models import AppSetting

        with session_scope() as s:
            row = s.get(AppSetting, "language")
            if row is not None and row.value:
                lang = row.value
    except Exception:  # DB not ready yet (CLI start-up, migrations): the env var is enough
        pass
    return normalize(lang)


def get_language() -> str:
    global _current
    if _current is None:
        _current = _load()
    return _current


def set_language(value: str | None) -> str:
    global _current
    _current = normalize(value)
    return _current


def reset() -> None:
    """Forget the cached language (re-read from env/DB on next use)."""
    global _current
    _current = None


def _(text: str, **params) -> str:
    """Translate an English message to the current language and fill its ``{placeholders}``."""
    if get_language() == "pt-BR":
        text = _PT_BR.get(text, text)
    return text.format(**params) if params else text


_PT_BR: dict[str, str] = {
    # api
    "{model} {id} not found": "{model} {id} não encontrado",
    "a record with this name already exists": "já existe um registro com esse nome",
    "enter {key} again when changing {url_field}": "informe {key} novamente ao trocar {url_field}",
    "endoflife.date unavailable: {error}": "endoflife.date indisponível: {error}",
    "{key} is required": "{key} é obrigatório",
    "environment {id} does not exist": "ambiente {id} não existe",
    "refresh already running": "atualização já em andamento",
    "refresh started": "atualização iniciada",
    "enter the URL": "informe a URL",
    " (test)": " (teste)",
    " (alerts)": " (alertas)",
    "**🚨 Changelog Watcher: {n} alert(s)**": "**🚨 Changelog Watcher: {n} alerta(s)**",
    "new major version": "nova versão major",
    "release mentions a security fix": "a versão menciona correção de segurança",
    "outdated for {n} days": "desatualizada há {n} dias",
    "alerts to {webhook}: {detail}": "alertas para {webhook}: {detail}",
    "alerts: {error}": "alertas: {error}",
    "invalid cron expression: {error}": "expressão cron inválida: {error}",
    "unsupported language: {value}": "idioma não suportado: {value}",
    "cross-origin request blocked": "requisição de outra origem bloqueada",
    # schemas
    "invalid regex: {error}": "regex inválida: {error}",
    "invalid product: use the endoflife.date identifier, e.g. postgresql": "produto inválido: use o identificador do endoflife.date, ex: postgresql",
    # refresh
    "unexpected error: {error}": "erro inesperado: {error}",
    "general failure: {error}": "falha geral: {error}",
    # sources
    "unknown source type: {type}": "tipo de fonte desconhecido: {type}",
    "not found: {url}": "não encontrado: {url}",
    "API rate limit reached (configure a token)": "limite de requisições da API atingido (configure um token)",
    "access denied ({status}) at {url}": "acesso negado ({status}) em {url}",
    "enter the URL of the changelog file": "informe a URL do arquivo de changelog",
    "no version found in the changelog": "nenhuma versão encontrada no changelog",
    "enter the URL of the releases page": "informe a URL da página de releases",
    "no version found on the page": "nenhuma versão encontrada na página",
    "invalid Docker Hub image: '{repo}'": "imagem Docker Hub inválida: '{repo}'",
    "invalid GitHub repository: '{repo}' (use owner/repo)": "repositório GitHub inválido: '{repo}' (use owner/repo)",
    "invalid GitLab project: '{repo}' (use group/project)": "projeto GitLab inválido: '{repo}' (use grupo/projeto)",
    "enter the chart name (chart_name) for Helm sources": "informe o nome do chart (chart_name) para fontes Helm",
    "chart '{chart}' not found in {url}": "chart '{chart}' não encontrado em {url}",
    "destination not allowed: {host}": "destino não permitido: {host}",
    # source suggestions
    "image label org.opencontainers.image.source": "label org.opencontainers.image.source da imagem",
    "label of the equivalent public image {ref}": "label da imagem pública equivalente {ref}",
    "equivalent public image {ref}": "imagem pública equivalente {ref}",
    "GitHub search (★ {stars})": "busca no GitHub (★ {stars})",
    "image on ghcr.io": "imagem no ghcr.io",
    "image on Docker Hub": "imagem no Docker Hub",
    # prometheus
    "set the namespace and workload name": "defina namespace e nome do workload",
    "set the namespace and Helm release name": "defina namespace e nome do release Helm",
    "set the image pattern": "defina o padrão de imagem",
    "set the PromQL query": "defina a consulta PromQL",
    "environment '{env}' has no Prometheus URL": "ambiente '{env}' sem URL do Prometheus",
    "Prometheus detection disabled for this application": "detecção via Prometheus desabilitada para esta aplicação",
    "failed to query Prometheus: {error}": "falha ao consultar Prometheus: {error}",
    "Prometheus answered HTTP {status}": "Prometheus respondeu HTTP {status}",
    "invalid response: not the Prometheus API (check the URL)": "resposta inválida: não é a API do Prometheus (confira a URL)",
    "query error: {error}": "erro na consulta: {error}",
    "no series found for: {query}": "nenhuma série encontrada para: {query}",
    "could not extract a version from image '{image}'": "não foi possível extrair versão da imagem '{image}'",
    # eol
    "unexpected response from endoflife.date (product list)": "resposta inesperada do endoflife.date (lista de produtos)",
    "invalid product: {product}": "produto inválido: {product}",
    "unexpected response from endoflife.date for {product}": "resposta inesperada do endoflife.date para {product}",
    "EOL: endoflife.date product list: {error}": "EOL: lista de produtos do endoflife.date: {error}",
    # labels sent to the UI (/api/meta)
    "GitHub Releases": "GitHub Releases",
    "GitHub Tags": "GitHub Tags",
    "GitLab Releases": "GitLab Releases",
    "GitLab Tags": "GitLab Tags",
    "Helm repository (index.yaml)": "Repositório Helm (index.yaml)",
    "Docker Hub (tags)": "Docker Hub (tags)",
    "CHANGELOG.md file": "Arquivo CHANGELOG.md",
    "Web page listing the versions": "Página web com as versões",
    "No detection (manual version)": "Sem detecção (versão manual)",
    "Workload (Deployment/StatefulSet/DaemonSet)": "Workload (Deployment/StatefulSet/DaemonSet)",
    "Helm release (all workloads of the release)": "Release Helm (todos os workloads do release)",
    "Image pattern": "Padrão de imagem",
    "Custom PromQL query": "Consulta PromQL personalizada",
    "Generic JSON": "JSON genérico",
    "Slack": "Slack",
    "Microsoft Teams (Workflows / Adaptive Card)": "Microsoft Teams (Workflows / Adaptive Card)",
    "Discord": "Discord",
    "Google Chat": "Google Chat",
    # report (Markdown)
    "📦 Update report ({start} → {end})": "📦 Relatório de atualizações ({start} → {end})",
    "Summary by environment": "Resumo por ambiente",
    "n/a": "n/d",
    "Environment": "Ambiente",
    "Up to date": "Atualizado",
    "Apps": "Apps",
    "With bugfix pending": "Com bugfix pendente",
    "With minor pending": "Com minor pendente",
    "With major pending": "Com major pendente",
    "No version": "Sem versão",
    "Libyears (average)": "Libyears (média)",
    "Average delay (days)": "Atraso médio (dias)",
    "New versions in the last {days} days ({n})": "Novas versões nos últimos {days} dias ({n})",
    "_No new version published in the period._": "_Nenhuma nova versão publicada no período._",
    "not deployed": "não implantado",
    "- … and {n} more": "- … e mais {n}",
    "Outdated applications ({n})": "Aplicações desatualizadas ({n})",
    "Application": "Aplicação",
    "Current": "Atual",
    "Available": "Disponível",
    "Outdated for": "Atrasada há",
    "{n} days": "{n} dias",
    "{n} day": "{n} dia",
    "_… and {n} more_": "_… e mais {n}_",
    "_Everything up to date 🎉_": "_Tudo atualizado 🎉_",
    "End of life (EOL) ({n})": "Fim de suporte (EOL) ({n})",
    "In use": "Em uso",
    "Cycle": "Ciclo",
    "End of life": "Fim de suporte",
    "already reached": "já atingido",
    "{date} ({n} days ago)": "{date} (há {n} dias)",
    "{date} (today)": "{date} (hoje)",
    "{date} (in {n} days)": "{date} (em {n} dias)",
    "No version identified ({n})": "Sem versão identificada ({n})",
    "current version or releases not found": "versão atual ou releases não encontradas",
    "Differences between environments ({n})": "Diferenças entre ambientes ({n})",
    "🔗 Details: {url}": "🔗 Detalhes: {url}",
    "bugfix": "bugfix",
    # webhooks (chat)
    "**📦 Update report – {start} → {end}**": "**📦 Relatório de atualizações – {start} → {end}**",
    "**{env}**: {pct} up to date · {total} apps · pending: ": "**{env}**: {pct} atualizado · {total} apps · pendentes: ",
    " · ⚪ {n} without version": " · ⚪ {n} sem versão",
    " · 🔵 {n} within tolerance": " · 🔵 {n} dentro da tolerância",
    "Within tolerance": "Dentro da tolerância",
    " · {n} libyears on average": " · {n} libyears em média",
    "**New versions ({n})**": "**Novas versões ({n})**",
    "• … and {n} more": "• … e mais {n}",
    "**Outdated ({n})**": "**Desatualizadas ({n})**",
    "**End of life (EOL) ({n})**": "**Fim de suporte (EOL) ({n})**",
    "**Differences between environments ({n})**": "**Diferenças entre ambientes ({n})**",
    "nothing outdated": "nada desatualizado",
    "empty URL": "URL vazia",
    "{n} message(s) sent": "{n} mensagem(ns) enviada(s)",
    "ERROR": "ERRO",
    # auth
    "read-only access": "acesso somente leitura",
    "Sign in": "Entrar",
    "The identity provider is unavailable, try again later.": "O provedor de identidade está indisponível, tente novamente mais tarde.",
    "Login expired or invalid, sign in again.": "Login expirado ou inválido, entre novamente.",
    "Login failed.": "Falha no login.",
    "Your account is not in a group allowed to use Changelog Watcher.": "Sua conta não está em um grupo com acesso ao Changelog Watcher.",
    "You have signed out.": "Você saiu.",
}
