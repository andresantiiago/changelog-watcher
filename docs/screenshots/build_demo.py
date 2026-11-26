"""Monta um banco SQLite de demonstração para as capturas de tela do README.

Releases, versões em uso e histórico são sintéticos (datas relativas a hoje), então
as telas ficam parecidas a cada execução e não dependem de rede.

    python docs/screenshots/build_demo.py /tmp/demo.db /tmp/discover.json

O segundo argumento recebe a resposta de /api/environments/{id}/discover para o
cenário kube-prometheus-stack (a captura a intercepta, sem Prometheus de verdade).
Veja docs/screenshots/README.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

db_path, discover_path = sys.argv[1], sys.argv[2]
Path(db_path).unlink(missing_ok=True)
os.environ.update(DATABASE_URL=f"sqlite:///{db_path}", AUTH_DISABLED="true", SCHEDULER_ENABLED="false",
                  REFRESH_ON_STARTUP="false")

import httpx  # noqa: E402
import respx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from changelog_watcher import prometheus as prom  # noqa: E402
from changelog_watcher.analysis import compute_statuses, load_environments, summarize  # noqa: E402
from changelog_watcher.db import init_engine, session_scope  # noqa: E402
from changelog_watcher.models import Deployment, Environment, Project, Release, Snapshot, Webhook  # noqa: E402
from changelog_watcher.refresh import store_snapshots  # noqa: E402
from changelog_watcher.report import create_report  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
ENVS = ["desenvolvimento", "homologacao", "producao"]
PROM_URL = "http://prometheus.monitoring:9090"

# name, fields, [(versão, dias atrás)], {ambiente: versão em uso}; "*" = detectada via Prometheus
PROJECTS = [
    ("argo-cd", dict(tags=["plataforma", "gitops"], repository="argoproj/argo-cd", detection_mode="helm",
                     workload_namespace="argocd", workload_name="argocd", image_pattern="quay.io/argoproj/argocd",
                     description="GitOps do cluster"),
     [("2.13.0", 400), ("2.13.3", 330), ("2.14.0", 300), ("2.14.9", 220), ("3.0.0", 190), ("3.0.6", 150),
      ("3.1.0", 110), ("3.1.8", 60), ("3.2.0", 30), ("3.2.1", 4)],
     {"desenvolvimento": "*3.2.0", "homologacao": "*3.1.8", "producao": "*2.14.9"}, "quay.io/argoproj/argocd:v"),
    ("cert-manager", dict(tags=["plataforma"], repository="cert-manager/cert-manager", detection_mode="image",
                          image_pattern="quay.io/jetstack/cert-manager-controller"),
     [("1.16.0", 400), ("1.16.3", 320), ("1.17.0", 280), ("1.17.2", 200), ("1.18.0", 160), ("1.18.2", 100),
      ("1.19.0", 45), ("1.19.1", 20), ("1.19.2", 1)],
     {"desenvolvimento": "*1.19.1", "homologacao": "*1.19.1", "producao": "*1.19.1"},
     "quay.io/jetstack/cert-manager-controller:v"),
    ("kube-prometheus-stack", dict(tags=["observabilidade"], source_type="helm_index",
                                   repository="https://prometheus-community.github.io/helm-charts",
                                   chart_name="kube-prometheus-stack"),
     [("70.0.0", 260), ("72.0.0", 220), ("75.0.0", 160), ("77.0.0", 110), ("78.0.0", 70), ("79.0.0", 40),
      ("79.1.0", 18)],
     {"desenvolvimento": "79.1.0", "homologacao": "78.0.0", "producao": "75.0.0"}, None),
    ("kube-prometheus-stack-prometheus", dict(tags=["observabilidade"], repository="prometheus/prometheus",
                                              detection_mode="helm", workload_namespace="kube-monitoring",
                                              workload_name="kube-prometheus-stack-prometheus",
                                              image_pattern="registry.intranet/prometheus/prometheus"),
     [("3.4.0", 200), ("3.5.0", 140), ("3.6.0", 90), ("3.7.0", 55), ("3.7.3", 30), ("3.8.0", 10)],
     {"desenvolvimento": "*3.8.0", "homologacao": "*3.7.3", "producao": "*3.7.0"},
     "registry.intranet/prometheus/prometheus:v"),
    ("kube-prometheus-stack-prometheus (thanos)", dict(tags=["observabilidade"], repository="thanos-io/thanos",
                                                       detection_mode="helm", workload_namespace="kube-monitoring",
                                                       workload_name="kube-prometheus-stack-prometheus",
                                                       image_pattern="registry.intranet/thanosio/thanos"),
     [("0.38.0", 250), ("0.39.0", 150), ("0.39.2", 110), ("0.40.0", 50), ("0.40.1", 25)],
     {"desenvolvimento": "*0.40.1", "homologacao": "*0.40.1", "producao": "*0.39.2"},
     "registry.intranet/thanosio/thanos:v"),
    ("grafana", dict(tags=["observabilidade"], repository="grafana/grafana"),
     [("11.5.0", 300), ("12.0.0", 200), ("12.1.0", 140), ("12.2.0", 80), ("12.3.0", 25), ("12.3.1", 3)],
     {"desenvolvimento": "12.3.0", "homologacao": "12.2.0", "producao": "12.1.0"}, None),
    ("ingress-nginx", dict(tags=["rede"], repository="kubernetes/ingress-nginx", tag_pattern=r"^controller-v(.*)$"),
     [("1.11.3", 380), ("1.12.0", 280), ("1.12.1", 230), ("1.13.0", 130), ("1.13.3", 60), ("1.14.0", 25)],
     {"desenvolvimento": "1.14.0", "homologacao": "1.13.3", "producao": "1.12.1"}, None),
    ("keycloak", dict(tags=["seguranca"], repository="keycloak/keycloak"),
     [("25.0.6", 400), ("26.0.0", 380), ("26.1.0", 300), ("26.2.0", 220), ("26.3.0", 130), ("26.4.0", 60),
      ("26.4.5", 20), ("26.4.6", 2)],
     {"desenvolvimento": "26.4.5", "homologacao": "26.4.0", "producao": "26.1.0"}, None),
    ("postgres", dict(tags=["banco-de-dados"], source_type="dockerhub", repository="library/postgres",
                      version_scheme="major_patch", detection_mode="workload", workload_kind="statefulset",
                      workload_namespace="database", workload_name="postgres"),
     [("16.4", 450), ("16.6", 370), ("16.8", 250), ("16.9", 190), ("16.10", 90), ("17.0", 420), ("17.2", 370),
      ("17.4", 250), ("17.5", 190), ("17.6", 90), ("18.0", 60), ("18.1", 5)],
     {"desenvolvimento": "*17.6", "homologacao": "*17.5", "producao": "*16.8"}, "postgres:"),
    ("redis", dict(tags=["banco-de-dados"], source_type="dockerhub", repository="library/redis"),
     [("7.4.0", 450), ("7.4.2", 320), ("8.0.0", 200), ("8.0.3", 120), ("8.2.0", 80), ("8.2.2", 30)],
     {"desenvolvimento": "8.2.2", "homologacao": "8.0.3", "producao": "7.4.2"}, None),
    ("minha-api", dict(tags=["produto"], source_type="changelog",
                       repository="https://github.com/minha-org/minha-api/blob/main/CHANGELOG.md"),
     [("2.1.0", 300), ("2.2.0", 200), ("2.3.0", 120), ("2.3.1", 90), ("2.4.0", 20)],
     {"desenvolvimento": "2.4.0", "homologacao": "2.4.0", "producao": "2.3.1"}, None),
]

NOTES = {
    0: "### Correções\n- Corrige vazamento de memória ao reconciliar recursos grandes\n- Ajusta timeout padrão das chamadas à API",
    1: "### Novidades\n- Novo filtro por namespace na interface\n- Métricas de latência por operação\n\n### Correções\n- Logs duplicados ao reiniciar o pod",
    2: "### Mudanças incompatíveis\n- Remove flags obsoletas desde a versão anterior\n- Requer Kubernetes 1.30 ou superior\n\n### Novidades\n- Nova API de configuração",
}


def kind_of(prev: str | None, ver: str, two_part: bool) -> int:
    if prev is None:
        return 1
    a, b = prev.split("."), ver.split(".")
    if a[0] != b[0]:
        return 2
    return 0 if (two_part or a[1] == b[1]) else 1


init_engine()
with session_scope() as s:
    envs = {}
    for i, name in enumerate(ENVS):
        envs[name] = Environment(name=name, sort_order=i, prometheus_url=PROM_URL,
                                 description={"desenvolvimento": "Cluster de desenvolvimento",
                                              "homologacao": "Cluster de homologação",
                                              "producao": "Cluster de produção"}[name])
        s.add(envs[name])
    s.flush()
    for name, fields, versions, deployed, image in PROJECTS:
        p = Project(name=name, last_checked_at=NOW - timedelta(minutes=random.Random(name).randint(3, 50)), **fields)
        s.add(p)
        s.flush()
        tag = "controller-v{}" if fields.get("tag_pattern") else ("{}" if fields.get("source_type") in ("dockerhub", "changelog", "helm_index") else "v{}")
        two = fields.get("version_scheme") == "major_patch"
        prev = None
        for ver, ago in sorted(versions, key=lambda v: -v[1]):
            s.add(Release(project_id=p.id, tag=tag.format(ver), version=ver, title=tag.format(ver),
                          published_at=NOW - timedelta(days=ago, hours=random.Random(ver).randint(0, 20)),
                          notes=NOTES[kind_of(prev, ver, two)], first_seen_at=NOW - timedelta(days=ago)))
            prev = ver
        for env_name, ver in deployed.items():
            detected = ver.startswith("*")
            ver = ver.lstrip("*")
            dep = Deployment(project_id=p.id, environment_id=envs[env_name].id,
                             version_source="prometheus" if detected else "manual",
                             manual_version=None if detected else ver)
            if detected:
                dep.detected_version, dep.detected_at = ver, NOW - timedelta(minutes=12)
                dep.detected_image = f"{image}{ver}" if image else None
            s.add(dep)
    s.add(Webhook(name="slack-plataforma", kind="slack", url="${SLACK_WEBHOOK_URL}",
                  last_sent_at=NOW - timedelta(days=3), last_status="HTTP 200"))
    s.add(Webhook(name="teams-producao", kind="teams", url="${TEAMS_WEBHOOK_URL}", only_outdated=True,
                  environment_ids=[envs["producao"].id], last_sent_at=NOW - timedelta(days=3), last_status="HTTP 200"))

store_snapshots()

# Histórico de 90 dias: parte dos valores de hoje e anda para trás com uma tendência suave.
with session_scope() as s:
    statuses = compute_statuses(s)
    rnd = random.Random(42)
    for env in load_environments(s):
        today = summarize([st for st in statuses if st.environment_id == env.id])
        ratio, libyears = today["up_to_date_ratio"] or 0.0, today["libyears"]
        total = today["total"]
        today_up = round(ratio * total)
        drops = sorted(rnd.sample(range(5, 90), min(today_up, 3)))  # dias (para trás) em que houve atualização
        for back in range(1, 91):
            up = today_up - sum(1 for d in drops if d <= back)
            rest = total - up
            major = round(rest * 0.4)
            minor = round(rest * 0.4)
            snap = Snapshot(environment_id=env.id, day=(NOW - timedelta(days=back)).date(), total=total,
                            up_to_date=up, patch=rest - major - minor, minor=minor, major=major, unknown=0,
                            up_to_date_ratio=up / total if total else None,
                            libyears=max(0.0, libyears * (1 + back * 0.004) * rnd.uniform(0.99, 1.01)),
                            avg_outdated_days=today["avg_outdated_days"] * (1 + back * 0.002),
                            max_outdated_days=today["max_outdated_days"], versions_behind=today["versions_behind"])
            s.add(snap)
    create_report(s, trigger="schedule")

# Resposta da descoberta (cenário kube-prometheus-stack), com os projetos acima já monitorando parte das imagens.
NS = "kube-monitoring"
POD, AM = "prometheus-kube-prometheus-stack-prometheus-0", "alertmanager-kube-prometheus-stack-alertmanager-0"
OP, GF = "kube-prometheus-stack-operator-5d9c7b-abcde", "kube-prometheus-stack-grafana-6c7f-zxcvb"
REG = "registry.intranet"
SERIES = {
    "kube_pod_container_info": [
        {"pod": POD, "container": "prometheus", "image": f"{REG}/prometheus/prometheus:v3.7.0"},
        {"pod": POD, "container": "config-reloader", "image": f"{REG}/prometheus-operator/prometheus-config-reloader:v0.87.1"},
        {"pod": POD, "container": "thanos-sidecar", "image": f"{REG}/thanosio/thanos:v0.39.2"},
        {"pod": AM, "container": "alertmanager", "image": f"{REG}/prometheus/alertmanager:v0.29.0"},
        {"pod": AM, "container": "config-reloader", "image": f"{REG}/prometheus-operator/prometheus-config-reloader:v0.87.1"},
        {"pod": OP, "container": "operator", "image": f"{REG}/prometheus-operator/prometheus-operator:v0.87.1"},
        {"pod": GF, "container": "grafana", "image": f"{REG}/grafana/grafana:12.1.0"},
        {"pod": GF, "container": "grafana-sc-dashboard", "image": f"{REG}/kiwigrid/k8s-sidecar:1.30.10"},
    ],
    "kube_pod_owner": [
        {"pod": POD, "owner_kind": "StatefulSet", "owner_name": "prometheus-kube-prometheus-stack-prometheus"},
        {"pod": AM, "owner_kind": "StatefulSet", "owner_name": "alertmanager-kube-prometheus-stack-alertmanager"},
        {"pod": OP, "owner_kind": "ReplicaSet", "owner_name": "kube-prometheus-stack-operator-5d9c7b"},
        {"pod": GF, "owner_kind": "ReplicaSet", "owner_name": "kube-prometheus-stack-grafana-6c7f"},
    ],
    "kube_pod_labels": [
        {"pod": POD, "label_app_kubernetes_io_instance": "kube-prometheus-stack-prometheus"},
        {"pod": AM, "label_app_kubernetes_io_instance": "kube-prometheus-stack-alertmanager"},
        {"pod": OP, "label_app_kubernetes_io_instance": "kube-prometheus-stack", "label_helm_sh_chart": "kube-prometheus-stack-75.0.0"},
        {"pod": GF, "label_app_kubernetes_io_instance": "kube-prometheus-stack", "label_helm_sh_chart": "grafana-9.2.10"},
    ],
    "kube_secret_info": [{"secret": "sh.helm.release.v1.kube-prometheus-stack.v3"}],
}


def answer(request):
    series = SERIES.get(request.url.params["query"].split("{", 1)[0], [])
    return httpx.Response(200, json={"status": "success", "data": {"result": [
        {"metric": {"namespace": NS, **m}, "value": [0, "1"]} for m in series]}})


with session_scope() as s, respx.mock:
    respx.get(f"{PROM_URL}/api/v1/query").mock(side_effect=answer)
    env = s.scalar(select(Environment).where(Environment.name == "producao"))
    groups = asyncio.run(prom.discover(env, NS, s.scalars(select(Project)).all()))
Path(discover_path).write_text(json.dumps({"ok": True, "groups": groups}))
print(f"banco: {db_path} · descoberta: {discover_path}")
