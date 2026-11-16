#!/usr/bin/env python3
"""Generates the Grafana dashboard for changelog-watcher.

    python deploy/grafana/build_dashboard.py

Writes ``deploy/grafana/changelog-watcher-dashboard.json`` and the copy used by
the Helm chart (``deploy/helm/changelog-watcher/dashboards/changelog-watcher.json``).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = [
    ROOT / "deploy/grafana/changelog-watcher-dashboard.json",
    ROOT / "deploy/helm/changelog-watcher/dashboards/changelog-watcher.json",
]

DS = {"type": "prometheus", "uid": "${datasource}"}
ENV = 'environment=~"$environment"'
PROJ = 'project=~"$project"'

# Status palette – same semantic colors as the web UI.
STATUS_COLORS = {
    "up_to_date": "#0ca30c",
    "patch": "#fab219",
    "minor": "#ec835a",
    "major": "#d03b3b",
    "unknown": "#898781",
}
STATUS_TEXT = {
    "up_to_date": "Atualizado",
    "patch": "Bugfix",
    "minor": "Minor",
    "major": "Major",
    "unknown": "Desconhecido",
}
STATUS_MAPPING = [{
    "type": "value",
    "options": {k: {"text": STATUS_TEXT[k], "color": c, "index": i} for i, (k, c) in enumerate(STATUS_COLORS.items())},
}]
LEVEL_MAPPING = [{
    "type": "value",
    "options": {
        "-1": {"text": "Desconhecido", "color": STATUS_COLORS["unknown"], "index": 0},
        "0": {"text": "Atualizado", "color": STATUS_COLORS["up_to_date"], "index": 1},
        "1": {"text": "Bugfix", "color": STATUS_COLORS["patch"], "index": 2},
        "2": {"text": "Minor", "color": STATUS_COLORS["minor"], "index": 3},
        "3": {"text": "Major", "color": STATUS_COLORS["major"], "index": 4},
    },
}]

_id = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def target(expr: str, legend: str = "", ref: str = "A", instant: bool = False, table: bool = False) -> dict:
    t = {"datasource": DS, "expr": expr, "refId": ref, "legendFormat": legend or "__auto", "range": not instant,
         "instant": instant}
    if table:
        t["format"] = "table"
    return t


def thresholds(steps: list[tuple[float | None, str]]) -> dict:
    return {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in steps]}


def stat(title, expr, x, y, w=4, h=4, unit="short", legend="", steps=None, decimals=None, description="",
         color_mode="value", text_mode="auto") -> dict:
    defaults = {"unit": unit, "thresholds": thresholds(steps or [(None, "text")]), "color": {"mode": "thresholds"}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": nid(), "type": "stat", "title": title, "description": description, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(expr, legend, instant=True)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": color_mode, "graphMode": "none", "textMode": text_mode, "justifyMode": "auto",
                    "orientation": "auto", "showPercentChange": False, "wideLayout": True},
    }


def timeseries(title, targets, x, y, w=12, h=8, unit="short", description="", max_=None, stack=False) -> dict:
    defaults = {
        "unit": unit,
        "color": {"mode": "palette-classic"},
        "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0, "showPoints": "never",
                   "spanNulls": True, "axisSoftMin": 0,
                   "stacking": {"mode": "normal" if stack else "none", "group": "A"}},
    }
    if max_ is not None:
        defaults["max"] = max_
    return {
        "id": nid(), "type": "timeseries", "title": title, "description": description, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
    }


def status_overrides() -> list[dict]:
    return [{"matcher": {"id": "byName", "options": k},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}},
                            {"id": "displayName", "value": STATUS_TEXT[k]}]}
            for k, c in STATUS_COLORS.items()]


def row(title, y, collapsed=False, panels=None) -> dict:
    return {"id": nid(), "type": "row", "title": title, "collapsed": collapsed,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": panels or []}


def build() -> dict:
    global _id
    _id = 0
    panels: list[dict] = []
    y = 0

    # ------------------------------------------------------------- overview
    panels.append(row("Visão geral", y)); y += 1
    panels.append(stat(
        "% atualizado", f"changelog_watcher_environment_up_to_date_ratio{{{ENV}}}", 0, y, w=6, h=5,
        unit="percentunit", legend="{{environment}}", decimals=0,
        steps=[(None, STATUS_COLORS["major"]), (0.5, STATUS_COLORS["minor"]), (0.8, STATUS_COLORS["up_to_date"])],
        description="Fração das implantações (com versão conhecida) que estão na versão mais recente.",
        color_mode="background"))
    panels.append(stat("Implantações monitoradas", f"sum(changelog_watcher_environment_apps_total{{{ENV}}})",
                       6, y, w=3, h=5))
    panels.append(stat("Bugfix pendente", f'sum(changelog_watcher_environment_apps_pending{{{ENV}, type="patch"}})',
                       9, y, w=3, h=5, steps=[(None, "text"), (1, STATUS_COLORS["patch"])],
                       description="Implantações com versão de bugfix (mesmo major.minor) disponível."))
    panels.append(stat("Minor pendente", f'sum(changelog_watcher_environment_apps_pending{{{ENV}, type="minor"}})',
                       12, y, w=3, h=5, steps=[(None, "text"), (1, STATUS_COLORS["minor"])]))
    panels.append(stat("Major pendente", f'sum(changelog_watcher_environment_apps_pending{{{ENV}, type="major"}})',
                       15, y, w=3, h=5, steps=[(None, "text"), (1, STATUS_COLORS["major"])]))
    panels.append(stat("Libyears", f"sum(changelog_watcher_environment_libyears_total{{{ENV}}})", 18, y, w=3, h=5,
                       decimals=1, description="Soma, em anos, do intervalo entre a versão em uso e a mais recente."))
    panels.append(stat("Apps com versões divergentes", "sum(changelog_watcher_project_version_mismatch)",
                       21, y, w=3, h=5, steps=[(None, "text"), (1, STATUS_COLORS["minor"])],
                       description="Aplicações rodando versões diferentes entre os ambientes."))
    y += 5

    status_bar = {
        "id": nid(), "type": "barchart", "title": "Status por ambiente", "datasource": DS,
        "description": "Maior atualização pendente de cada implantação.",
        "gridPos": {"x": 0, "y": y, "w": 12, "h": 8},
        "targets": [target(f"changelog_watcher_environment_apps{{{ENV}}}", instant=True, table=True)],
        "transformations": [{"id": "groupingToMatrix", "options": {
            "columnField": "status", "rowField": "environment", "valueField": "Value", "emptyValue": "zero"}}],
        "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}, "custom": {"fillOpacity": 90, "lineWidth": 0}},
                        "overrides": status_overrides()},
        "options": {"orientation": "horizontal", "stacking": "normal", "showValue": "auto", "barWidth": 0.6,
                    "groupWidth": 0.7, "xField": "environment\\status",
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi"}},
    }
    age_bar = {
        "id": nid(), "type": "barchart", "title": "Há quanto tempo estão desatualizadas", "datasource": DS,
        "description": "Implantações desatualizadas por tempo desde a publicação da primeira versão mais nova.",
        "gridPos": {"x": 12, "y": y, "w": 12, "h": 8},
        "targets": [target(f"changelog_watcher_environment_apps_by_age{{{ENV}}}", instant=True, table=True)],
        "transformations": [
            {"id": "groupingToMatrix", "options": {"columnField": "environment", "rowField": "bucket",
                                                   "valueField": "Value", "emptyValue": "zero"}},
            {"id": "sortBy", "options": {"sort": [{"field": "bucket\\environment"}]}},
        ],
        "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}, "custom": {"fillOpacity": 90, "lineWidth": 0}},
                        "overrides": []},
        "options": {"orientation": "vertical", "stacking": "none", "showValue": "auto", "barWidth": 0.8,
                    "groupWidth": 0.7, "xField": "bucket\\environment",
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi"}},
    }
    panels += [status_bar, age_bar]
    y += 8

    # ----------------------------------------------------------- aging trend
    panels.append(row("Envelhecimento do ambiente", y)); y += 1
    panels.append(timeseries("% atualizado ao longo do tempo",
                             [target(f"changelog_watcher_environment_up_to_date_ratio{{{ENV}}}", "{{environment}}")],
                             0, y, unit="percentunit", max_=1))
    panels.append(timeseries("Libyears acumulados",
                             [target(f"changelog_watcher_environment_libyears_total{{{ENV}}}", "{{environment}}")],
                             12, y, description="Quanto maior, mais antigo está o ambiente."))
    y += 8
    panels.append(timeseries(
        "Implantações desatualizadas por tipo",
        [target(f'sum by (environment, status) (changelog_watcher_environment_apps{{{ENV}, status=~"patch|minor|major"}})',
                "{{environment}} · {{status}}")], 0, y))
    panels.append(timeseries(
        "Atraso médio e máximo (dias)",
        [target(f"changelog_watcher_environment_outdated_days_avg{{{ENV}}}", "{{environment}} · média", "A"),
         target(f"changelog_watcher_environment_outdated_days_max{{{ENV}}}", "{{environment}} · máximo", "B")],
        12, y, unit="d"))
    y += 8

    # ------------------------------------------------------------- versions
    panels.append(row("Versões por aplicação", y)); y += 1
    versions_table = {
        "id": nid(), "type": "table", "title": "Versões em uso e atualizações disponíveis", "datasource": DS,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 12},
        "targets": [
            target(f"changelog_watcher_app_info{{{ENV}, {PROJ}}}", ref="A", instant=True, table=True),
            target(f"changelog_watcher_app_outdated_days{{{ENV}, {PROJ}}}", ref="B", instant=True, table=True),
            target(f'changelog_watcher_app_versions_behind{{{ENV}, {PROJ}, type="total"}}', ref="C", instant=True,
                   table=True),
            target(f"changelog_watcher_app_libyears{{{ENV}, {PROJ}}}", ref="D", instant=True, table=True),
        ],
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "Value #A": True, "__name__": True, "instance": True, "job": True,
                                  "type": True, "namespace": True, "pod": True, "service": True, "endpoint": True,
                                  "container": True},
                "indexByName": {"project": 0, "environment": 1, "status": 2, "current_version": 3, "latest_patch": 4,
                                "latest_minor": 5, "latest_major": 6, "latest_version": 7, "Value #B": 8,
                                "Value #C": 9, "Value #D": 10, "version_source": 11},
                "renameByName": {"project": "Aplicação", "environment": "Ambiente", "status": "Status",
                                 "current_version": "Atual", "latest_patch": "Bugfix", "latest_minor": "Minor",
                                 "latest_major": "Major", "latest_version": "Mais recente",
                                 "Value #B": "Atrasada há (dias)", "Value #C": "Versões atrás",
                                 "Value #D": "Libyears", "version_source": "Origem"}}},
            {"id": "sortBy", "options": {"sort": [{"field": "Atrasada há (dias)", "desc": True}]}},
        ],
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "filterable": True, "cellOptions": {"type": "auto"}},
                         "noValue": "—"},
            "overrides": [
                {"matcher": {"id": "byName", "options": "Status"},
                 "properties": [{"id": "mappings", "value": STATUS_MAPPING},
                                {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                                {"id": "custom.width", "value": 120}]},
                {"matcher": {"id": "byName", "options": "Atrasada há (dias)"},
                 "properties": [{"id": "decimals", "value": 0},
                                {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic",
                                                                       "valueDisplayMode": "text"}},
                                {"id": "color", "value": {"mode": "fixed", "fixedColor": "#2a78d6"}},
                                {"id": "min", "value": 0}]},
                {"matcher": {"id": "byName", "options": "Libyears"}, "properties": [{"id": "decimals", "value": 2}]},
            ],
        },
        "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
    }
    panels.append(versions_table)
    y += 12

    matrix = {
        "id": nid(), "type": "table", "title": "Comparação de versões entre ambientes", "datasource": DS,
        "description": "Uma coluna por ambiente. Use junto com o painel de divergências.",
        "gridPos": {"x": 0, "y": y, "w": 16, "h": 10},
        "targets": [target(f"changelog_watcher_app_info{{{ENV}, {PROJ}}}", instant=True, table=True)],
        "transformations": [
            {"id": "groupingToMatrix", "options": {"columnField": "environment", "rowField": "project",
                                                   "valueField": "current_version", "emptyValue": "empty"}},
            {"id": "organize", "options": {"renameByName": {"project\\environment": "Aplicação"}}},
        ],
        "fieldConfig": {"defaults": {"noValue": "—", "custom": {"filterable": True}}, "overrides": []},
        "options": {"showHeader": True, "cellHeight": "sm"},
    }
    drift = {
        "id": nid(), "type": "table", "title": "Divergências entre pares de ambientes", "datasource": DS,
        "gridPos": {"x": 16, "y": y, "w": 8, "h": 10},
        "targets": [target("changelog_watcher_environment_pair_different_apps", instant=True, table=True)],
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {"Time": True, "__name__": True, "instance": True, "job": True, "namespace": True,
                              "pod": True, "service": True, "endpoint": True, "container": True},
            "renameByName": {"source_environment": "Ambiente A", "target_environment": "Ambiente B",
                             "Value": "Apps diferentes"}}}],
        "fieldConfig": {"defaults": {}, "overrides": [
            {"matcher": {"id": "byName", "options": "Apps diferentes"},
             "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                            {"id": "thresholds", "value": thresholds([(None, STATUS_COLORS["up_to_date"]),
                                                                      (1, STATUS_COLORS["minor"])])}]}]},
        "options": {"showHeader": True, "cellHeight": "sm"},
    }
    panels += [matrix, drift]
    y += 10

    level_ts = {
        "id": nid(), "type": "state-timeline", "title": "Histórico de status por aplicação", "datasource": DS,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 10},
        "targets": [target(f"changelog_watcher_app_update_level{{{ENV}, {PROJ}}}", "{{project}} · {{environment}}")],
        "fieldConfig": {"defaults": {"mappings": LEVEL_MAPPING, "color": {"mode": "thresholds"},
                                     "thresholds": thresholds([(None, STATUS_COLORS["unknown"])]),
                                     "custom": {"fillOpacity": 85, "lineWidth": 0}}, "overrides": []},
        "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.85, "alignValue": "left",
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "single"}},
    }
    panels.append(level_ts)
    y += 10

    # -------------------------------------------------------- releases/health
    panels.append(row("Novas versões e saúde da coleta", y)); y += 1
    releases = {
        "id": nid(), "type": "table", "title": "Versões publicadas nos últimos 7 dias", "datasource": DS,
        "gridPos": {"x": 0, "y": y, "w": 12, "h": 9},
        "targets": [
            target(f"changelog_watcher_releases_last_7d{{{PROJ}}} > 0", ref="A", instant=True, table=True),
            target(f"changelog_watcher_release_latest_timestamp_seconds{{{PROJ}}} * 1000", ref="B", instant=True,
                   table=True),
        ],
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "filterByValue", "options": {"filters": [{"fieldName": "Value #A", "config": {
                "id": "greater", "options": {"value": 0}}}], "type": "include", "match": "all"}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "instance": True, "job": True, "namespace": True,
                                  "pod": True, "service": True, "endpoint": True, "container": True},
                "renameByName": {"project": "Aplicação", "version": "Mais recente", "Value #A": "Novas (7d)",
                                 "Value #B": "Publicada em"}}},
        ],
        "fieldConfig": {"defaults": {}, "overrides": [
            {"matcher": {"id": "byName", "options": "Publicada em"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}]}]},
        "options": {"showHeader": True, "cellHeight": "sm"},
    }
    errors = {
        "id": nid(), "type": "table", "title": "Falhas de coleta / detecção", "datasource": DS,
        "gridPos": {"x": 12, "y": y, "w": 12, "h": 9},
        "targets": [
            target("changelog_watcher_project_source_error == 1", ref="A", instant=True, table=True),
            target(f"changelog_watcher_app_detection_error{{{ENV}}} == 1", ref="B", instant=True, table=True),
        ],
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "instance": True, "job": True, "namespace": True,
                                  "pod": True, "service": True, "endpoint": True, "container": True,
                                  "Value #A": True, "Value #B": True},
                "renameByName": {"project": "Aplicação", "environment": "Ambiente (detecção)",
                                 "source_type": "Fonte (releases)"}}},
        ],
        "fieldConfig": {"defaults": {"noValue": "Nenhuma falha"}, "overrides": []},
        "options": {"showHeader": True, "cellHeight": "sm"},
    }
    panels += [releases, errors]
    y += 9
    panels.append(stat("Última atualização", "time() - changelog_watcher_last_refresh_timestamp_seconds",
                       0, y, w=6, h=4, unit="s", decimals=0,
                       steps=[(None, STATUS_COLORS["up_to_date"]), (43200, STATUS_COLORS["patch"]),
                              (86400, STATUS_COLORS["major"])], description="Tempo desde o fim da última atualização."))
    panels.append(stat("Última atualização sem erros", "changelog_watcher_last_refresh_success", 6, y, w=6, h=4,
                       steps=[(None, STATUS_COLORS["major"]), (1, STATUS_COLORS["up_to_date"])],
                       color_mode="background"))
    panels[-1]["fieldConfig"]["defaults"]["mappings"] = [{"type": "value", "options": {
        "0": {"text": "Com erros"}, "1": {"text": "OK"}}}]
    panels.append(timeseries("Duração das atualizações",
                             [target("rate(changelog_watcher_refresh_duration_seconds_sum[1h]) / "
                                     "rate(changelog_watcher_refresh_duration_seconds_count[1h])", "duração média")],
                             12, y, w=12, h=4, unit="s"))

    return {
        "uid": "changelog-watcher",
        "title": "Changelog Watcher – Atualizações de aplicações",
        "description": "Versões em uso x versões disponíveis por ambiente (bugfix/minor/major), envelhecimento e divergências.",
        "tags": ["changelog-watcher", "versions", "releases"],
        "timezone": "browser",
        "editable": True,
        "graphTooltip": 1,
        "schemaVersion": 39,
        "version": 1,
        "refresh": "5m",
        "time": {"from": "now-30d", "to": "now"},
        "annotations": {"list": []},
        "links": [],
        "templating": {"list": [
            {"name": "datasource", "label": "Prometheus", "type": "datasource", "query": "prometheus",
             "current": {}, "hide": 0, "refresh": 1},
            {"name": "environment", "label": "Ambiente", "type": "query", "datasource": DS,
             "query": {"query": "label_values(changelog_watcher_environment_apps_total, environment)", "refId": "env"},
             "definition": "label_values(changelog_watcher_environment_apps_total, environment)",
             "includeAll": True, "multi": True, "allValue": ".*", "current": {"text": "All", "value": "$__all"},
             "refresh": 2, "sort": 1},
            {"name": "project", "label": "Aplicação", "type": "query", "datasource": DS,
             "query": {"query": 'label_values(changelog_watcher_app_info{environment=~"$environment"}, project)',
                       "refId": "proj"},
             "definition": 'label_values(changelog_watcher_app_info{environment=~"$environment"}, project)',
             "includeAll": True, "multi": True, "allValue": ".*", "current": {"text": "All", "value": "$__all"},
             "refresh": 2, "sort": 1},
        ]},
        "panels": panels,
    }


def main() -> None:
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    for out in OUTPUTS:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
