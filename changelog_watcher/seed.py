"""Load environments/projects/webhooks from a YAML file (GitOps friendly).

By default existing records are left untouched (the UI owns them after the first
start). Set ``SEED_OVERWRITE=true`` to make the file the source of truth for the
fields it declares.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Deployment, Environment, Project, Webhook

log = logging.getLogger(__name__)

ENV_FIELDS = {"description", "sort_order", "prometheus_url", "prometheus_auth", "prometheus_token",
              "prometheus_username", "prometheus_password", "prometheus_verify_tls", "prometheus_extra_selector"}
PROJECT_FIELDS = {"description", "enabled", "tags", "source_type", "repository", "source_url", "chart_name",
                  "use_app_version", "tag_pattern", "version_scheme", "include_prerelease", "changelog_url", "detection_mode",
                  "workload_kind", "workload_namespace", "workload_name", "container_name", "image_pattern",
                  "promql_query", "image_version_pattern"}
DEPLOYMENT_FIELDS = {"version_source", "manual_version", "namespace_override", "workload_name_override"}
WEBHOOK_FIELDS = {"kind", "url", "enabled", "only_outdated"}


def _apply(obj, data: dict, fields: set[str]) -> None:
    for key, value in data.items():
        if key in fields:
            setattr(obj, key, value)


def load_seed(session: Session, path: str, overwrite: bool = False) -> dict:
    file = Path(path)
    if not file.exists():
        log.warning("config file %s not found, skipping seed", path)
        return {}
    doc = yaml.safe_load(file.read_text()) or {}
    stats = {"environments": 0, "projects": 0, "webhooks": 0}

    envs: dict[str, Environment] = {e.name: e for e in session.scalars(select(Environment))}
    for i, item in enumerate(doc.get("environments") or []):
        env = envs.get(item["name"])
        if env is None:
            env = Environment(name=item["name"], sort_order=i)
            session.add(env)
            envs[env.name] = env
            _apply(env, item, ENV_FIELDS)
            stats["environments"] += 1
        elif overwrite:
            _apply(env, item, ENV_FIELDS)
    session.flush()

    projects = {p.name: p for p in session.scalars(select(Project))}
    for item in doc.get("projects") or []:
        project = projects.get(item["name"])
        created = project is None
        if created:
            project = Project(name=item["name"], repository=item.get("repository", ""))
            session.add(project)
            projects[project.name] = project
            stats["projects"] += 1
        if created or overwrite:
            _apply(project, item, PROJECT_FIELDS)
        session.flush()
        existing = {d.environment_id: d for d in project.deployments}
        deployments = item.get("deployments") or {}
        if isinstance(deployments, list):
            deployments = {d["environment"]: d for d in deployments}
        for env_name, dep_data in deployments.items():
            env = envs.get(env_name)
            if env is None:
                log.warning("project %s references unknown environment %s", project.name, env_name)
                continue
            dep_data = dep_data or {}
            if isinstance(dep_data, str):
                dep_data = {"version_source": "manual", "manual_version": dep_data}
            dep = existing.get(env.id)
            if dep is None:
                dep = Deployment(project_id=project.id, environment_id=env.id,
                                 version_source="prometheus" if project.detection_mode != "none" else "manual")
                session.add(dep)
                _apply(dep, dep_data, DEPLOYMENT_FIELDS)
            elif overwrite:
                _apply(dep, dep_data, DEPLOYMENT_FIELDS)

    hooks = {w.name: w for w in session.scalars(select(Webhook))}
    for item in doc.get("webhooks") or []:
        hook = hooks.get(item["name"])
        env_ids = [envs[n].id for n in item.get("environments", []) if n in envs]
        if hook is None:
            hook = Webhook(name=item["name"], url=item["url"], environment_ids=env_ids)
            session.add(hook)
            _apply(hook, item, WEBHOOK_FIELDS)
            stats["webhooks"] += 1
        elif overwrite:
            _apply(hook, item, WEBHOOK_FIELDS)
            hook.environment_ids = env_ids
    return stats
