import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "release_version", Path(__file__).parents[1] / ".github/scripts/release_version.py")
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)


def test_first_release_uses_declared_version():
    assert rv.next_version(["0.1.0", "v0.1.0-rc1", "v0.1.0-rc1"], None, "") == "v0.1.0"
    assert rv.next_version(["not-semver"], None, "") == "v0.1.0"


def test_declared_bump_wins_over_commits():
    assert rv.next_version(["0.3.0"], "v0.2.5", "fix: x") == "v0.3.0"
    # bumped only in the chart, or left as an rc: the release is the final version
    assert rv.next_version(["0.2.5", "v0.3.0-rc2", "v0.3.0-rc2"], "v0.2.5", "") == "v0.3.0"
    assert rv.next_version(["0.3.0rc1"], "v0.2.5", "") == "v0.3.0"


def test_bump_from_copilot_then_commits():
    assert rv.next_version(["0.2.5"], "v0.2.5", "fix: x", kind="minor") == "v0.3.0"
    assert rv.next_version(["0.2.5"], "v0.2.5", "Fix crash") == "v0.2.6"
    assert rv.next_version(["0.2.5"], "v0.2.5", "feat(api): new endpoint") == "v0.3.0"
    assert rv.next_version(["0.2.5"], "v0.2.5", "Drop field [minor]") == "v0.3.0"
    assert rv.next_version(["0.2.5"], "v0.2.5", "feat!: rename") == "v1.0.0"
    assert rv.next_version(["0.2.5"], "v0.2.5", "x\n\nBREAKING CHANGE: y") == "v1.0.0"
    # declared lower than (or an rc of) the last tag is ignored
    assert rv.next_version(["0.1.0", "v0.2.5-rc1"], "v0.2.5", "") == "v0.2.6"


def test_read_ai():
    assert rv.read_ai("Minor\n- Adiciona X\n- Corrige Y\n") == ("minor", "- Adiciona X\n- Corrige Y")
    assert rv.read_ai("```\n**patch**\n- Corrige Z\n```") == ("patch", "- Corrige Z")
    assert rv.read_ai("Não sei classificar") == (None, "")
    assert rv.read_ai("") == (None, "")


def test_rc_tag_becomes_the_final_version():
    rcs = ["v0.2.0-rc1", "v0.2.0-rc3", "v0.1.0-rc9", "v0.1.0", "vbogus"]
    assert rv.next_version(["0.1.0"], "v0.1.0", "fix: x", rcs) == "v0.2.0"
    assert rv.next_version(["0.1.0"], None, "", ["v0.1.0-rc.2"]) == "v0.1.0"
    # rc of an already released version is ignored
    assert rv.next_version(["0.2.0"], "v0.2.0", "fix", rcs) == "v0.2.1"
    # a declared bump above the rc wins
    assert rv.next_version(["0.3.0"], "v0.1.0", "", rcs) == "v0.3.0"


@pytest.fixture
def files(tmp_path, monkeypatch):
    py, chart = tmp_path / "pyproject.toml", tmp_path / "Chart.yaml"
    py.write_text('[project]\nname = "x"\nversion = "0.1.0"\n[tool.x]\nversion = "keep"\n')
    chart.write_text('apiVersion: v2\nversion: v0.1.0-rc1\nappVersion: "v0.1.0-rc1"\n')
    monkeypatch.setattr(rv, "PYPROJECT", py)
    monkeypatch.setattr(rv, "CHART", chart)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("AI_FILE", raising=False)
    return py, chart


def test_sync_files_python_without_v(files):
    py, chart = files
    assert rv.sync_files("v0.2.0") is True
    assert 'version = "0.2.0"' in py.read_text() and 'version = "keep"' in py.read_text()
    assert chart.read_text() == 'apiVersion: v2\nversion: v0.2.0\nappVersion: "v0.2.0"\n'
    assert rv.sync_files("v0.2.0") is False


def test_rc_tag_mode(files, capsys):
    py, chart = files
    rv.main(["v0.2.0-rc1"])
    out = capsys.readouterr().out
    assert "version=v0.2.0-rc1\n" in out and "prerelease=true" in out
    assert 'version = "0.2.0rc1"' in py.read_text()
    assert chart.read_text() == 'apiVersion: v2\nversion: v0.2.0-rc1\nappVersion: "v0.2.0-rc1"\n'
    for bad in ("v0.2.0", "0.2.0-rc1"):
        with pytest.raises(SystemExit):
            rv.main([bad])


def test_notes_from_copilot_go_to_output(files, tmp_path, monkeypatch):
    ai = tmp_path / "copilot.txt"
    ai.write_text("minor\n- Adiciona release automática\n")
    gh = tmp_path / "out"
    monkeypatch.setenv("AI_FILE", str(ai))
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    rv.main(["v0.2.0-rc1"])
    out = gh.read_text()
    assert "notes<<EOF_" in out and "- Adiciona release automática\nEOF_" in out


_s = importlib.util.spec_from_file_location("test_summary", Path(__file__).parents[1] / ".github/scripts/test_summary.py")
ts = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ts)


def test_test_summary():
    xml = """<testsuites><testsuite name="pytest">
      <testcase classname="tests.test_a" name="test_ok" time="0.01"/>
      <testcase classname="tests.test_a" name="test_bad" time="0.02"><failure message="assert 1 == 2">trace</failure></testcase>
      <testcase classname="tests.test_b" name="test_skip"><skipped message="x"/></testcase>
    </testsuite></testsuites>"""
    md = ts.summarize(xml)
    assert "**3** testes: ✅ 1 passaram · ❌ 1 falharam · 💥 0 com erro · ⏭️ 1 ignorados" in md
    assert "<code>test_a::test_bad</code>" in md and "assert 1 == 2\ntrace" in md
    assert "| ✅ passou | `test_a::test_ok` | 0.01s |" in md
