from changelog_watcher.analysis import compute_statuses
from changelog_watcher.db import session_scope


def test_compute_statuses_environment_filter(client, seeded):
    prod = seeded["prod"]["id"]
    with session_scope() as s:
        assert len(compute_statuses(s)) == 5
        assert compute_statuses(s, []) == []  # empty filter = no environment, not "all"
        assert {st.environment_id for st in compute_statuses(s, [prod])} == {prod}
    assert len(client.get("/api/status").json()) == 5  # no query param still means all
