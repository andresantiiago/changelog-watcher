from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .sources import SOURCE_TYPES
from .webhooks import WEBHOOK_KINDS

DETECTION_MODES = {
    "none": "Sem detecção (versão manual)",
    "workload": "Workload (Deployment/StatefulSet/DaemonSet)",
    "helm": "Release Helm (todos os workloads do release)",
    "image": "Padrão de imagem",
    "query": "Consulta PromQL personalizada",
}

SourceType = Literal[tuple(SOURCE_TYPES)]  # type: ignore[valid-type]
DetectionMode = Literal[tuple(DETECTION_MODES)]  # type: ignore[valid-type]
WorkloadKind = Literal["deployment", "statefulset", "daemonset", "cronjob"]
VersionSource = Literal["manual", "prometheus"]
WebhookKind = Literal[tuple(WEBHOOK_KINDS)]  # type: ignore[valid-type]


_EOL_SLUG = re.compile(r"[a-z0-9][a-z0-9._+-]*")


def _check_regex(value: str | None) -> str | None:
    if value:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"regex inválida: {exc}") from exc
    return value or None


class EnvironmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    sort_order: int = 0
    prometheus_url: str | None = None
    prometheus_auth: Literal["none", "bearer", "basic"] = "none"
    prometheus_token: str | None = None
    prometheus_username: str | None = None
    prometheus_password: str | None = None
    prometheus_verify_tls: bool = True
    prometheus_extra_selector: str | None = None


class EnvironmentOut(EnvironmentIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime | None = None


class DeploymentIn(BaseModel):
    version_source: VersionSource = "manual"
    manual_version: str | None = None
    namespace_override: str | None = None
    workload_name_override: str | None = None


class DeploymentOut(DeploymentIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    environment_id: int
    project_id: int
    current_version: str | None = None
    detected_version: str | None = None
    detected_image: str | None = None
    detected_at: datetime | None = None
    detection_error: str | None = None
    version_changed_at: datetime | None = None


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    source_type: SourceType = "github_releases"
    repository: str = Field(min_length=1)
    source_url: str | None = None
    chart_name: str | None = None
    use_app_version: bool = False
    tag_pattern: str | None = None
    version_scheme: Literal["semver", "major_patch"] = "semver"
    include_prerelease: bool = False
    changelog_url: str | None = None
    detection_mode: DetectionMode = "none"
    workload_kind: WorkloadKind = "deployment"
    workload_namespace: str | None = None
    workload_name: str | None = None
    container_name: str | None = None
    image_pattern: str | None = None
    promql_query: str | None = None
    image_version_pattern: str | None = None
    # endoflife.date product (e.g. "postgresql"); empty = automatic match, "none" = no EOL check.
    eol_product: str | None = Field(default=None, max_length=100)
    # environment_id -> deployment settings. Missing environments are removed
    # when ``deployments`` is sent; ``None`` leaves deployments untouched.
    deployments: dict[int, DeploymentIn] | None = None

    _tag = field_validator("tag_pattern", "image_version_pattern")(_check_regex)

    @field_validator("eol_product")
    @classmethod
    def _eol_product(cls, value: str | None) -> str | None:
        value = (value or "").strip().lower()
        if value and not _EOL_SLUG.fullmatch(value):
            raise ValueError("produto inválido: use o identificador do endoflife.date, ex: postgresql")
        return value or None


class ProjectOut(ProjectIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    repository: str  # no min_length: seeded projects may lack one
    last_checked_at: datetime | None = None
    last_error: str | None = None
    eol_data: dict | None = None
    eol_checked_at: datetime | None = None
    eol_error: str | None = None
    release_count: int = 0
    deployments: list[DeploymentOut] = Field(default_factory=list)


class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: WebhookKind = "generic"
    url: str = Field(min_length=1)
    enabled: bool = True
    only_outdated: bool = False
    environment_ids: list[int] = Field(default_factory=list)


class WebhookOut(WebhookIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    last_sent_at: datetime | None = None
    last_status: str | None = None


class ReportRequest(BaseModel):
    days: int | None = Field(default=None, ge=1, le=365)
    environment_ids: list[int] | None = None
    send: bool = False
    webhook_ids: list[int] | None = None


class RefreshRequest(BaseModel):
    project_ids: list[int] | None = None
    detect: bool = True
    wait: bool = False
