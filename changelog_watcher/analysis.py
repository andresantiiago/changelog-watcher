"""Turns raw releases + deployments into update status, summaries and diffs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Deployment, Environment, Project, Release
from .versioning import (
    STATUS_LEVEL,
    STATUS_ORDER,
    STATUS_UNKNOWN,
    STATUS_UP_TO_DATE,
    Version,
    classify,
    parse_version,
)

AGE_BUCKETS = [
    ("0-30d", 0, 30),
    ("30-90d", 30, 90),
    ("90-180d", 90, 180),
    ("180-365d", 180, 365),
    (">1 ano", 365, None),
]
# ponytail: limite fixo; vira setting se algum projeto precisar de outro valor.
STALE_AFTER = timedelta(days=730)


def pv(project: Project, text: str | None, use_pattern: bool = False) -> Version | None:
    """Parse a version following the project's scheme (and tag pattern for tags)."""
    return parse_version(text, project.tag_pattern if use_pattern else None,
                         two_part=project.version_scheme == "major_patch")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class VersionEntry:
    version: Version
    release: Release

    @property
    def published_at(self) -> datetime | None:
        return _aware(self.release.published_at)


def project_versions(project: Project, releases: list[Release] | None = None) -> dict[Version, VersionEntry]:
    """Parse and de-duplicate the releases of a project (pre-releases filtered)."""
    out: dict[Version, VersionEntry] = {}
    for rel in releases if releases is not None else project.releases:
        v = pv(project, rel.tag, use_pattern=True)
        if v is None:
            continue
        if (rel.prerelease or v.is_prerelease) and not project.include_prerelease:
            continue
        prev = out.get(v)
        if prev is None:
            out[v] = VersionEntry(v, rel)
            continue
        # Prefer the entry with notes, then the oldest publication date.
        a, b = prev.release, rel
        if (not a.notes and b.notes) or (
            bool(a.notes) == bool(b.notes) and b.published_at and (not a.published_at or _aware(b.published_at) < _aware(a.published_at))
        ):
            out[v] = VersionEntry(v, rel)
    return _drop_stale(out)


def _drop_stale(versions: dict[Version, VersionEntry]) -> dict[Version, VersionEntry]:
    """Drop versions whose whole "line and above" is much older than some lower version.

    CoreDNS has 11.0.0 from 2016 while the current line is 1.15 (2026): every version >= 11.0.0
    is ~10 years older than 1.15.0, so 11.0.0 is not an update. A maintained older line
    (PostgreSQL 13.x patched after 14.0) is kept, since higher versions (17, 18) are recent.
    """
    dated = sorted((v, e.published_at) for v, e in versions.items() if e.published_at)
    newest_below, below = [], None  # newest_below[i]: newest date among versions lower than dated[i]
    for _, d in dated:
        newest_below.append(below)
        below = d if below is None else max(below, d)
    stale, above = set(), None  # above: newest date among versions >= dated[i]
    for i in reversed(range(len(dated))):
        v, d = dated[i]
        above = d if above is None else max(above, d)
        if newest_below[i] and above < newest_below[i] - STALE_AFTER:
            stale.add(v)
    return {v: e for v, e in versions.items() if v not in stale}


@dataclass
class DeploymentStatus:
    project_id: int
    project: str
    environment_id: int
    environment: str
    version_source: str
    current_version: str | None
    status: str
    level: int
    latest_version: str | None = None
    latest_patch: str | None = None
    latest_minor: str | None = None
    latest_major: str | None = None
    latest_version_date: str | None = None
    latest_patch_date: str | None = None
    latest_minor_date: str | None = None
    latest_major_date: str | None = None
    latest_patch_url: str | None = None
    latest_minor_url: str | None = None
    latest_major_url: str | None = None
    versions_behind: int = 0
    patch_behind: int = 0
    minor_behind: int = 0
    major_behind: int = 0
    current_release_date: str | None = None
    current_in_releases: bool = False
    outdated_since: str | None = None
    outdated_days: float = 0.0
    libyears: float | None = None
    detected_image: str | None = None
    detected_at: str | None = None
    detection_error: str | None = None
    project_error: str | None = None
    tags: list = field(default_factory=list)
    namespace: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt else None


def deployment_status(
    project: Project,
    deployment: Deployment,
    environment: Environment,
    versions: dict[Version, VersionEntry],
    now: datetime | None = None,
) -> DeploymentStatus:
    now = now or datetime.now(timezone.utc)
    current_raw = deployment.current_version
    current = pv(project, current_raw)
    info = classify(current, versions.keys())
    st = DeploymentStatus(
        project_id=project.id,
        project=project.name,
        environment_id=environment.id,
        environment=environment.name,
        version_source=deployment.version_source,
        current_version=str(current) if current else current_raw,
        status=info.status,
        level=STATUS_LEVEL[info.status],
        versions_behind=info.versions_behind,
        patch_behind=info.patch_behind,
        minor_behind=info.minor_behind,
        major_behind=info.major_behind,
        detected_image=deployment.detected_image,
        detected_at=_iso(deployment.detected_at),
        detection_error=deployment.detection_error,
        project_error=project.last_error,
        tags=list(project.tags or []),
        namespace=deployment.namespace_override or project.workload_namespace,
    )
    for attr in ("latest", "latest_patch", "latest_minor", "latest_major"):
        v: Version | None = getattr(info, attr)
        if v is None:
            continue
        entry = versions[v]
        name = "latest_version" if attr == "latest" else attr
        setattr(st, name, str(v))
        setattr(st, f"{name}_date", _iso(entry.published_at))
        if attr != "latest":
            setattr(st, f"{attr}_url", entry.release.url)

    cur_entry = versions.get(current) if current else None
    if cur_entry:
        st.current_in_releases = True
        st.current_release_date = _iso(cur_entry.published_at)

    if info.newer:
        dates = [versions[v].published_at for v in info.newer if versions[v].published_at]
        if dates:
            first = min(dates)
            st.outdated_since = first.isoformat()
            st.outdated_days = round(max((now - first).total_seconds(), 0) / 86400, 1)
        latest_date = versions[info.latest].published_at if info.latest else None
        if latest_date and cur_entry and cur_entry.published_at:
            st.libyears = round(max((latest_date - cur_entry.published_at).days, 0) / 365.25, 3)
    elif info.status == STATUS_UP_TO_DATE:
        st.libyears = 0.0
    return st


def load_projects(session: Session) -> list[Project]:
    stmt = select(Project).where(Project.enabled.is_(True)).options(
        selectinload(Project.releases), selectinload(Project.deployments).selectinload(Deployment.environment)
    )
    return list(session.scalars(stmt.order_by(Project.name)))


def load_environments(session: Session) -> list[Environment]:
    return list(session.scalars(select(Environment).order_by(Environment.sort_order, Environment.name)))


def compute_statuses(
    session: Session, environment_ids: list[int] | None = None, now: datetime | None = None
) -> list[DeploymentStatus]:
    out: list[DeploymentStatus] = []
    for project in load_projects(session):
        versions = project_versions(project)
        for dep in project.deployments:
            if environment_ids is not None and dep.environment_id not in environment_ids:
                continue
            out.append(deployment_status(project, dep, dep.environment, versions, now))
    return out


def summarize(statuses: list[DeploymentStatus]) -> dict:
    counts = {s: 0 for s in STATUS_ORDER}
    for st in statuses:
        counts[st.status] += 1
    total = len(statuses)
    known = total - counts[STATUS_UNKNOWN]
    outdated = [s for s in statuses if s.status not in (STATUS_UP_TO_DATE, STATUS_UNKNOWN)]
    buckets = {label: 0 for label, *_ in AGE_BUCKETS}
    for s in outdated:
        for label, lo, hi in AGE_BUCKETS:
            if s.outdated_days >= lo and (hi is None or s.outdated_days < hi):
                buckets[label] += 1
                break
    days = [s.outdated_days for s in outdated]
    return {
        "total": total,
        "known": known,
        "counts": counts,
        "outdated": len(outdated),
        "patch_pending": sum(1 for s in statuses if s.latest_patch),
        "minor_pending": sum(1 for s in statuses if s.latest_minor),
        "major_pending": sum(1 for s in statuses if s.latest_major),
        "up_to_date_ratio": round(counts[STATUS_UP_TO_DATE] / known, 4) if known else None,
        "libyears": round(sum(s.libyears or 0 for s in statuses), 3),
        "avg_outdated_days": round(sum(days) / len(days), 1) if days else 0.0,
        "max_outdated_days": max(days) if days else 0.0,
        "versions_behind": sum(s.versions_behind for s in statuses),
        "age_buckets": buckets,
    }


def summaries_by_environment(statuses: list[DeploymentStatus], environments: list[Environment]) -> list[dict]:
    grouped: dict[int, list[DeploymentStatus]] = defaultdict(list)
    for st in statuses:
        grouped[st.environment_id].append(st)
    return [
        {"environment_id": env.id, "environment": env.name, **summarize(grouped.get(env.id, []))}
        for env in environments
    ]


# ---------------------------------------------------------------- comparison
def version_matrix(statuses: list[DeploymentStatus]) -> list[dict]:
    by_project: dict[int, dict] = {}
    for st in statuses:
        row = by_project.setdefault(
            st.project_id,
            {"project_id": st.project_id, "project": st.project, "latest_version": st.latest_version, "environments": {}},
        )
        row["environments"][str(st.environment_id)] = {
            "version": st.current_version,
            "status": st.status,
        }
    rows = sorted(by_project.values(), key=lambda r: r["project"].lower())
    for row in rows:
        versions = {v["version"] for v in row["environments"].values() if v["version"]}
        row["distinct_versions"] = len(versions)
        row["drift"] = len(versions) > 1
    return rows


def compare_environments(
    session: Session, source_id: int, target_id: int, only_differences: bool = False
) -> dict:
    rows = []
    for project in load_projects(session):
        versions = project_versions(project)
        deps = {d.environment_id: d for d in project.deployments}
        src, tgt = deps.get(source_id), deps.get(target_id)
        if not src and not tgt:
            continue
        sv = pv(project, src.current_version) if src else None
        tv = pv(project, tgt.current_version) if tgt else None
        if src and not tgt:
            relation = "only_source"
        elif tgt and not src:
            relation = "only_target"
        elif sv is None or tv is None:
            relation = "unknown"
        elif sv == tv:
            relation = "same"
        elif sv > tv:
            relation = "source_ahead"
        else:
            relation = "target_ahead"
        between = 0
        notes = []
        if sv and tv and sv != tv:
            lo, hi = sorted([sv, tv])
            between_versions = sorted((v for v in versions if lo < v <= hi), reverse=True)
            between = len(between_versions)
            notes = [
                {"version": str(v), "tag": versions[v].release.tag, "url": versions[v].release.url,
                 "published_at": _iso(versions[v].published_at)}
                for v in between_versions
            ]
        if only_differences and relation == "same":
            continue
        rows.append(
            {
                "project_id": project.id,
                "project": project.name,
                "source_version": str(sv) if sv else (src.current_version if src else None),
                "target_version": str(tv) if tv else (tgt.current_version if tgt else None),
                "relation": relation,
                "releases_between": between,
                "releases": notes,
                "latest_version": str(max(versions)) if versions else None,
            }
        )
    counts = defaultdict(int)
    for r in rows:
        counts[r["relation"]] += 1
    return {"source_id": source_id, "target_id": target_id, "rows": rows, "counts": dict(counts)}


def drift_pairs(statuses: list[DeploymentStatus], environments: list[Environment]) -> list[dict]:
    """Number of applications whose version differs between each pair of environments."""
    by_env: dict[int, dict[int, str | None]] = defaultdict(dict)
    for st in statuses:
        by_env[st.environment_id][st.project_id] = st.current_version
    out = []
    for a, b in combinations(sorted(environments, key=lambda e: e.id), 2):  # source = lower id
        common = set(by_env[a.id]) & set(by_env[b.id])
        differ = sum(1 for p in common if by_env[a.id][p] != by_env[b.id][p])
        out.append({"source": a.name, "target": b.name, "common": len(common), "different": differ})
    return out


def changelog_between(project: Project, from_version: str | None, to_version: str | None) -> list[dict]:
    versions = project_versions(project)
    lo = pv(project, from_version) if from_version else None
    hi = pv(project, to_version) if to_version else None
    selected = [v for v in versions if (lo is None or v > lo) and (hi is None or v <= hi)]
    return [release_dict(versions[v].release, v) for v in sorted(selected, reverse=True)]


def release_dict(rel: Release, version: Version | None = None) -> dict:
    return {
        "id": rel.id,
        "project_id": rel.project_id,
        "tag": rel.tag,
        "version": str(version) if version else rel.version,
        "title": rel.title,
        "prerelease": rel.prerelease,
        "published_at": _iso(rel.published_at),
        "url": rel.url,
        "notes": rel.notes,
    }


def recent_releases(session: Session, days: int = 7, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    out = []
    for project in load_projects(session):
        versions = project_versions(project)
        deployed = {}
        for d in project.deployments:
            deployed[d.environment.name] = d.current_version
        for v, entry in versions.items():
            if entry.published_at and entry.published_at >= since:
                item = release_dict(entry.release, v)
                item["project"] = project.name
                item["deployed"] = deployed
                item["level"] = _release_level(v, [pv(project, x) for x in deployed.values() if x])
                out.append(item)
    out.sort(key=lambda r: r["published_at"] or "", reverse=True)
    return out


def _release_level(v: Version, currents: list[Version | None]) -> str:
    """How a new release relates to what's deployed (worst case across envs)."""
    currents = [c for c in currents if c]
    if not currents:
        return STATUS_UNKNOWN
    if all(v < c for c in currents):
        return "older"  # new release of an older line (e.g. LTS backport)
    level = STATUS_UP_TO_DATE
    for c in currents:
        if v <= c:
            continue
        if v.major > c.major:
            return "major"
        if v.minor > c.minor:
            level = "minor"
        elif level == STATUS_UP_TO_DATE:
            level = "patch"
    return level
