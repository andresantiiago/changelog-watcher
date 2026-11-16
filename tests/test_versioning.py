import pytest

from changelog_watcher.versioning import classify, image_repository, image_tag, parse_version


@pytest.mark.parametrize("text,expected", [
    ("v1.2.3", "1.2.3"),
    ("1.2", "1.2.0"),
    ("release-1.2.3", "1.2.3"),
    ("argo-cd-5.46.7", "5.46.7"),
    ("1.25.3-alpine", "1.25.3"),
    ("1.2.3-rc.1", "1.2.3-rc.1"),
    ("v2.0.0-beta2", "2.0.0-beta.2"),
    ("1.2.3.4", "1.2.3.4"),
    ("v12", "12.0.0"),
])
def test_parse(text, expected):
    assert str(parse_version(text)) == expected


def test_parse_invalid_and_pattern():
    assert parse_version("latest") is None
    assert parse_version("") is None
    assert str(parse_version("helm-chart-1.0.0", r"^helm-chart-(.*)$")) == "1.0.0"
    assert parse_version("app-1.0.0", r"^helm-chart-(.*)$") is None
    assert str(parse_version("x-v2.3.4", r"x-v(?P<version>.+)")) == "2.3.4"


def test_ordering():
    assert parse_version("1.2.3-rc.1") < parse_version("1.2.3")
    assert parse_version("1.2.3-alpha") < parse_version("1.2.3-beta") < parse_version("1.2.3-rc.1")
    assert parse_version("1.10.0") > parse_version("1.9.9")
    assert parse_version("v1.2.3") == parse_version("1.2.3")
    assert not parse_version("1.25.3-alpine").is_prerelease


def test_classify_patch_minor_major():
    current = parse_version("1.1.3")
    available = [parse_version(v) for v in ["1.1.3", "1.1.5", "1.1.7", "1.2.0", "1.3.1", "2.0.0", "1.0.0"]]
    info = classify(current, available)
    assert info.status == "major"
    assert str(info.latest_patch) == "1.1.7"
    assert str(info.latest_minor) == "1.3.1"
    assert str(info.latest_major) == "2.0.0"
    assert (info.patch_behind, info.minor_behind, info.major_behind) == (2, 2, 1)
    assert info.versions_behind == 5


def test_classify_states():
    v = parse_version
    assert classify(v("1.1.7"), [v("1.1.7"), v("1.1.0")]).status == "up_to_date"
    assert classify(v("1.1.5"), [v("1.1.7")]).status == "patch"
    assert classify(v("1.1.7"), [v("1.2.0")]).status == "minor"
    assert classify(None, [v("1.0.0")]).status == "unknown"
    assert classify(v("1.0.0"), []).status == "unknown"


def test_image_helpers():
    img = "registry.local:5000/team/app:1.2.3@sha256:abc"
    assert image_tag(img) == "1.2.3"
    assert image_repository(img) == "registry.local:5000/team/app"
    assert image_tag("registry.local:5000/team/app") is None
    assert image_tag("nginx:1.25-alpine") == "1.25-alpine"


def test_two_part_scheme():
    v = lambda t: parse_version(t, two_part=True)  # noqa: E731
    assert str(v("16.2")) == "16.2"
    assert str(v("16.2.0")) == "16.2"
    info = classify(v("16.2"), [v("16.9"), v("17.5"), v("18.0")])
    assert str(info.latest_patch) == "16.9"
    assert str(info.latest_major) == "18.0"
    assert info.latest_minor is None
    assert info.status == "major"
