"""Version parsing and update classification.

Tags in the wild look like ``v1.2.3``, ``1.2``, ``release-1.2.3``, ``1.2.3-rc.1``,
``1.25.3-alpine`` or ``argo-cd-5.46.7``. We extract a semver-like version from them
(major.minor.patch plus an optional fourth "build" number), detect pre-releases
by keyword and ignore other suffixes (``-alpine``, ``-debian`` ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import total_ordering
from typing import Iterable

_PRE_KEYWORDS = "alpha|beta|rc|pre|preview|dev|snapshot|milestone|nightly|canary|a|b|m"
_PRE_RANK = {
    "dev": 0, "snapshot": 0, "nightly": 0, "canary": 0,
    "alpha": 1, "a": 1,
    "beta": 2, "b": 2,
    "milestone": 3, "m": 3,
    "pre": 4, "preview": 4,
    "rc": 5,
}

_VERSION_RE = re.compile(
    r"(?<![\d.])"
    r"(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?:\.(?P<build>\d+))?"
    rf"(?:[-_.+~]?(?P<pre>(?:{_PRE_KEYWORDS})(?![a-z])[-_.]?(?P<prenum>\d+)?))?",
    re.IGNORECASE,
)


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    major: int
    minor: int
    patch: int = 0
    build: int = 0
    pre: str | None = None  # e.g. "rc", "beta"
    pre_num: int = 0
    raw: str = field(default="")
    # "major.patch" scheme (e.g. PostgreSQL 16.2): displayed with two parts.
    two_part: bool = False

    @property
    def is_prerelease(self) -> bool:
        return self.pre is not None

    def _key(self) -> tuple:
        if self.pre is None:
            pre_key = (1, 0, 0)
        else:
            pre_key = (0, _PRE_RANK.get(self.pre.lower(), 0), self.pre_num)
        return (self.major, self.minor, self.patch, self.build, pre_key)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self._key() == other._key()

    def __lt__(self, other: "Version") -> bool:
        return self._key() < other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __str__(self) -> str:
        base = f"{self.major}.{self.patch}" if self.two_part else f"{self.major}.{self.minor}.{self.patch}"
        if self.build:
            base += f".{self.build}"
        if self.pre:
            base += f"-{self.pre.lower()}"
            if self.pre_num:
                base += f".{self.pre_num}"
        return base


def parse_version(text: str | None, pattern: str | None = None, two_part: bool = False) -> Version | None:
    """Parse a version out of ``text``.

    ``pattern`` is an optional regex. When given, the text must match it; if the
    regex has a named group ``version`` (or at least one group) that group is
    parsed, otherwise the whole match is.

    ``two_part`` handles "major.patch" schemes (PostgreSQL ``16.2``): the second
    number is treated as a bugfix, so 16.2 -> 16.9 is a *patch* update.
    """
    v = _parse(text, pattern)
    if v is None or not two_part:
        return v
    # A third number (old PostgreSQL 9.6.24) is kept as build so distinct releases don't collapse.
    return Version(v.major, 0, v.minor, v.patch, v.pre, v.pre_num, v.raw, two_part=True)


def _parse(text: str | None, pattern: str | None) -> Version | None:
    if not text:
        return None
    candidate = text.strip()
    if pattern:
        m = re.search(pattern, candidate)
        if not m:
            return None
        if "version" in m.groupdict():
            candidate = m.group("version") or ""
        elif m.groups():
            candidate = m.group(1) or ""
        else:
            candidate = m.group(0)
    m = _VERSION_RE.search(candidate)
    if not m:
        # Allow plain "v12" / "12" style versions only when nothing else matched.
        # Zero-padded ("v011", "007", CoreDNS 2016) is a build counter, not a major. Only here:
        # dotted versions may legitimately pad (Ubuntu 22.04, 2024.01.15).
        single = re.fullmatch(r"[vV]?([1-9]\d*|0)", candidate)
        if single:
            return Version(int(single.group(1)), 0, 0, raw=text)
        return None
    pre = m.group("pre")
    pre_word = None
    if pre:
        pre_word = re.match(r"[a-zA-Z]+", pre).group(0).lower()
    return Version(
        major=int(m.group("major")),
        minor=int(m.group("minor")),
        patch=int(m.group("patch") or 0),
        build=int(m.group("build") or 0),
        pre=pre_word,
        pre_num=int(m.group("prenum") or 0),
        raw=text,
    )


STATUS_UP_TO_DATE = "up_to_date"
STATUS_PATCH = "patch"
STATUS_MINOR = "minor"
STATUS_MAJOR = "major"
STATUS_UNKNOWN = "unknown"
STATUS_ORDER = [STATUS_UP_TO_DATE, STATUS_PATCH, STATUS_MINOR, STATUS_MAJOR, STATUS_UNKNOWN]
STATUS_LEVEL = {STATUS_UNKNOWN: -1, STATUS_UP_TO_DATE: 0, STATUS_PATCH: 1, STATUS_MINOR: 2, STATUS_MAJOR: 3}


@dataclass
class UpdateInfo:
    status: str
    latest: Version | None = None
    latest_patch: Version | None = None
    latest_minor: Version | None = None
    latest_major: Version | None = None
    newer: list[Version] = field(default_factory=list)
    patch_behind: int = 0
    minor_behind: int = 0
    major_behind: int = 0

    @property
    def versions_behind(self) -> int:
        return len(self.newer)


def classify(current: Version | None, available: Iterable[Version]) -> UpdateInfo:
    """Compare ``current`` with the ``available`` versions.

    * latest_patch – highest version on the same major.minor line (bugfix)
    * latest_minor – highest version on the same major with a higher minor
    * latest_major – highest version with a higher major
    """
    versions = sorted(set(available))
    latest = versions[-1] if versions else None
    if current is None or not versions:
        return UpdateInfo(status=STATUS_UNKNOWN, latest=latest)

    newer = [v for v in versions if v > current]
    info = UpdateInfo(status=STATUS_UP_TO_DATE, latest=latest, newer=newer)
    for v in newer:
        if v.major > current.major:
            info.major_behind += 1
            info.latest_major = v
        elif v.major == current.major and v.minor > current.minor:
            info.minor_behind += 1
            info.latest_minor = v
        else:  # newer, same major.minor: bugfix
            info.patch_behind += 1
            info.latest_patch = v

    if info.latest_major:
        info.status = STATUS_MAJOR
    elif info.latest_minor:
        info.status = STATUS_MINOR
    elif info.latest_patch:
        info.status = STATUS_PATCH
    return info


def image_tag(image: str | None) -> str | None:
    """Extract the tag of a container image reference.

    ``registry:5000/org/app:1.2.3@sha256:...`` -> ``1.2.3``
    """
    if not image:
        return None
    ref = image.split("@", 1)[0]
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        return None
    return last.rsplit(":", 1)[1] or None


def ghcr_repository(repo: str) -> str:
    """"ghcr.io/org/app" -> "org/app" (o repositório GitHub da imagem); senão vazio."""
    return repo.removeprefix("ghcr.io/") if repo.startswith("ghcr.io/") and repo.count("/") == 2 else ""


def image_repository(image: str | None) -> str | None:
    if not image:
        return None
    ref = image.split("@", 1)[0]
    head, _, last = ref.rpartition("/")
    name = last.split(":", 1)[0]
    return f"{head}/{name}" if head else name
