from datetime import UTC

from changelog_watcher.analysis import compute_statuses
from changelog_watcher.db import session_scope


def test_compute_statuses_environment_filter(client, seeded):
    prod = seeded["prod"]["id"]
    with session_scope() as s:
        assert len(compute_statuses(s)) == 5
        assert compute_statuses(s, []) == []  # empty filter = no environment, not "all"
        assert {st.environment_id for st in compute_statuses(s, [prod])} == {prod}
    assert len(client.get("/api/status").json()) == 5  # no query param still means all


def test_old_higher_versions_are_ignored():
    from datetime import datetime
    from types import SimpleNamespace

    from changelog_watcher.analysis import project_versions
    from changelog_watcher.models import Release

    def rel(tag, year, month=1):
        return Release(tag=tag, published_at=datetime(year, month, 1, tzinfo=UTC), prerelease=False)

    proj = SimpleNamespace(tag_pattern=None, version_scheme="semver", include_prerelease=False)
    coredns = [rel("v11.0.0", 2016), rel("v10.0.0", 2016), rel("v1.14.0", 2026), rel("v1.15.0", 2026, 10)]
    assert sorted(str(v) for v in project_versions(proj, coredns)) == ["1.14.0", "1.15.0"]
    # Linha antiga mantida (13.x depois do 14.0): fica, porque há versões maiores recentes.
    pg = [rel("13.0.0", 2020), rel("14.0.0", 2021), rel("13.22.0", 2025), rel("18.0.0", 2025, 9)]
    assert len(project_versions(proj, pg)) == 4
    # Major abandonada há mais de 2 anos enquanto a anterior segue: ignorada.
    assert "3.0.0" not in {str(v) for v in project_versions(proj, [rel("3.0.0", 2020), rel("2.9.0", 2025)])}
