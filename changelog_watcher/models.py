from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # Prometheus connection used to discover the image running in the cluster.
    prometheus_url: Mapped[str | None] = mapped_column(String(500), default=None)
    prometheus_auth: Mapped[str] = mapped_column(String(20), default="none")  # none|bearer|basic
    prometheus_token: Mapped[str | None] = mapped_column(Text, default=None)
    prometheus_username: Mapped[str | None] = mapped_column(String(200), default=None)
    prometheus_password: Mapped[str | None] = mapped_column(Text, default=None)
    prometheus_verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    # Extra label matchers added to every query, e.g. 'cluster="prod"' when one
    # Prometheus/Thanos holds several clusters.
    prometheus_extra_selector: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    deployments: Mapped[list["Deployment"]] = relationship(
        back_populates="environment", cascade="all, delete-orphan"
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    # Where releases / changelog come from.
    source_type: Mapped[str] = mapped_column(String(40), default="github_releases")
    repository: Mapped[str] = mapped_column(String(500))  # owner/repo, gitlab path, helm repo URL, docker image, changelog URL
    source_url: Mapped[str | None] = mapped_column(String(500), default=None)  # custom API base (GHE / self-hosted GitLab)
    chart_name: Mapped[str | None] = mapped_column(String(200), default=None)  # helm_index
    use_app_version: Mapped[bool] = mapped_column(Boolean, default=False)  # helm_index: compare appVersion
    tag_pattern: Mapped[str | None] = mapped_column(String(300), default=None)
    version_scheme: Mapped[str] = mapped_column(String(20), default="semver")  # semver|major_patch
    include_prerelease: Mapped[bool] = mapped_column(Boolean, default=False)
    changelog_url: Mapped[str | None] = mapped_column(String(500), default=None)

    # How to discover the running version through Prometheus (kube-state-metrics).
    detection_mode: Mapped[str] = mapped_column(String(20), default="none")  # none|workload|image|query
    workload_kind: Mapped[str] = mapped_column(String(20), default="deployment")
    workload_namespace: Mapped[str | None] = mapped_column(String(200), default=None)
    workload_name: Mapped[str | None] = mapped_column(String(200), default=None)
    container_name: Mapped[str | None] = mapped_column(String(200), default=None)
    image_pattern: Mapped[str | None] = mapped_column(String(500), default=None)
    promql_query: Mapped[str | None] = mapped_column(Text, default=None)
    image_version_pattern: Mapped[str | None] = mapped_column(String(300), default=None)

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    releases: Mapped[list["Release"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    deployments: Mapped[list["Deployment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Release(Base):
    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("project_id", "tag", name="uq_release_project_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(String(200))
    version: Mapped[str | None] = mapped_column(String(100), default=None)
    title: Mapped[str | None] = mapped_column(String(500), default=None)
    prerelease: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, index=True)
    url: Mapped[str | None] = mapped_column(String(1000), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    commit_sha: Mapped[str | None] = mapped_column(String(64), default=None)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="releases")


class Deployment(Base):
    """A project as it runs in one environment."""

    __tablename__ = "deployments"
    __table_args__ = (UniqueConstraint("project_id", "environment_id", name="uq_deployment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), index=True)
    version_source: Mapped[str] = mapped_column(String(20), default="manual")  # manual|prometheus
    manual_version: Mapped[str | None] = mapped_column(String(100), default=None)
    namespace_override: Mapped[str | None] = mapped_column(String(200), default=None)
    workload_name_override: Mapped[str | None] = mapped_column(String(200), default=None)
    detected_version: Mapped[str | None] = mapped_column(String(100), default=None)
    detected_image: Mapped[str | None] = mapped_column(String(1000), default=None)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    detection_error: Mapped[str | None] = mapped_column(Text, default=None)
    # When the running version last changed (used for "time since last update").
    version_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    project: Mapped[Project] = relationship(back_populates="deployments")
    environment: Mapped[Environment] = relationship(back_populates="deployments")

    @property
    def current_version(self) -> str | None:
        if self.version_source == "prometheus":
            return self.detected_version or self.manual_version
        return self.manual_version


class Snapshot(Base):
    """Daily aggregated state of an environment – feeds the "age" trend charts."""

    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("environment_id", "day", name="uq_snapshot_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    up_to_date: Mapped[int] = mapped_column(Integer, default=0)
    patch: Mapped[int] = mapped_column(Integer, default=0)
    minor: Mapped[int] = mapped_column(Integer, default=0)
    major: Mapped[int] = mapped_column(Integer, default=0)
    unknown: Mapped[int] = mapped_column(Integer, default=0)
    up_to_date_ratio: Mapped[float | None] = mapped_column(Float, default=None)
    libyears: Mapped[float] = mapped_column(Float, default=0.0)
    avg_outdated_days: Mapped[float] = mapped_column(Float, default=0.0)
    max_outdated_days: Mapped[float] = mapped_column(Float, default=0.0)
    versions_behind: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="generic")  # generic|slack|teams|discord|google_chat
    url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    only_outdated: Mapped[bool] = mapped_column(Boolean, default=False)
    environment_ids: Mapped[list] = mapped_column(JSON, default=list)  # empty = all
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_status: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual|schedule
    markdown: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON)
    delivery: Mapped[list] = mapped_column(JSON, default=list)


class AppSetting(Base):
    """Runtime overrides of env-var settings, editable in the UI (key/value)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class LogEntry(Base):
    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10), index=True)
    logger: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text, default=None)  # traceback
