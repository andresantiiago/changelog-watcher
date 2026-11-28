import importlib.util
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "release_version", Path(__file__).parents[1] / ".github/scripts/release_version.py"
)
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)


def test_first_release_uses_declared_version():
    assert rv.next_version(["0.1.0", "v0.1.0-rc1", "v0.1.0-rc1"], None) == "v0.1.0"
    assert rv.next_version(["not-semver"], None) == "v0.1.0"


def test_declared_version_wins_over_patch():
    assert rv.next_version(["0.3.0"], "v0.2.5") == "v0.3.0"
    assert rv.next_version(["0.2.5", "v0.3.0-rc2", "v0.3.0-rc2"], "v0.2.5") == "v0.3.0"
    assert rv.next_version(["0.3.0rc1"], "v0.2.5") == "v0.3.0"


def test_otherwise_bumps_patch():
    assert rv.next_version(["0.2.5"], "v0.2.5") == "v0.2.6"
    assert rv.next_version(["0.1.0", "v0.2.5-rc1"], "v0.2.5") == "v0.2.6"


def test_read_ai():
    assert rv.read_ai("- Adiciona X\n- Corrige Y\n") == "- Adiciona X\n- Corrige Y"
    assert rv.read_ai("```\n- Corrige Z\n```") == "- Corrige Z"
    assert rv.read_ai("") == ""


def test_release_branch(monkeypatch):
    for branch, want in {"release-v1.2.0": "v1.2.0", "release-1.2.0": None, "fix/x": None, "": None}.items():
        monkeypatch.setenv("HEAD_BRANCH", branch)
        assert rv.release_branch() == want


def test_rc_tag_becomes_the_final_version():
    rcs = ["v0.2.0-rc1", "v0.2.0-rc3", "v0.1.0-rc9", "v0.1.0", "vbogus"]
    assert rv.next_version(["0.1.0"], "v0.1.0", rcs) == "v0.2.0"
    assert rv.next_version(["0.1.0"], None, ["v0.1.0-rc.2"]) == "v0.1.0"
    # rc of an already released version is ignored
    assert rv.next_version(["0.2.0"], "v0.2.0", rcs) == "v0.2.1"
    # a declared bump above the rc wins
    assert rv.next_version(["0.3.0"], "v0.1.0", rcs) == "v0.3.0"


@pytest.fixture
def files(tmp_path, monkeypatch):
    py, chart = tmp_path / "pyproject.toml", tmp_path / "Chart.yaml"
    py.write_text('[project]\nname = "x"\nversion = "0.1.0"\n[tool.x]\nversion = "keep"\n')
    chart.write_text('apiVersion: v2\nversion: v0.1.0-rc1\nappVersion: "v0.1.0-rc1"\n')
    monkeypatch.setattr(rv, "PYPROJECT", py)
    monkeypatch.setattr(rv, "CHART", chart)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("AI_DIR", raising=False)
    monkeypatch.delenv("HEAD_BRANCH", raising=False)
    return py, chart


def test_sync_files_only_touches_each_component(files):
    py, chart = files
    assert rv.sync_files("v0.2.0") is True  # imagem: pyproject (sem v) + appVersion
    assert 'version = "0.2.0"' in py.read_text() and 'version = "keep"' in py.read_text()
    assert chart.read_text() == 'apiVersion: v2\nversion: v0.1.0-rc1\nappVersion: "v0.2.0"\n'
    assert rv.sync_files("v0.2.0") is False
    assert rv.sync_files(chart_version="v0.1.1") is True  # chart: só version
    assert chart.read_text() == 'apiVersion: v2\nversion: v0.1.1\nappVersion: "v0.2.0"\n'
    assert 'version = "0.2.0"' in py.read_text()


def test_rc_tag_mode(files, capsys):
    py, chart = files
    rv.main(["v0.2.0-rc1"])
    out = capsys.readouterr().out
    assert "app_version=v0.2.0-rc1\n" in out and "chart_tag=\n" in out and "prerelease=true" in out
    assert 'version = "0.2.0rc1"' in py.read_text()
    assert chart.read_text() == 'apiVersion: v2\nversion: v0.1.0-rc1\nappVersion: "v0.2.0-rc1"\n'
    for bad in ("v0.2.0", "0.2.0-rc1"):
        with pytest.raises(SystemExit):
            rv.main([bad])


def test_notes_from_copilot_go_to_output(files, tmp_path, monkeypatch):
    (tmp_path / "copilot-app.txt").write_text("- Adiciona release automática\n")
    gh = tmp_path / "out"
    monkeypatch.setenv("AI_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    rv.main(["v0.2.0-rc1"])
    out = gh.read_text()
    assert "app_notes<<EOF_" in out and "- Adiciona release automática\nEOF_" in out


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Repositório git com imagem, chart e docs, já na tag combinada v0.2.0 (antes da separação)."""

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    def commit(path, text, msg):
        f = tmp_path / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        git("add", "-A")
        git("commit", "-qm", msg)

    py, chart = tmp_path / "pyproject.toml", tmp_path / "deploy/helm/changelog-watcher/Chart.yaml"
    git("init", "-q")
    commit("README.md", "x", "init")
    chart.parent.mkdir(parents=True)
    chart.write_text('version: v0.2.0\nappVersion: "v0.2.0"\n')
    commit("pyproject.toml", 'version = "0.2.0"\n', "versões")
    commit("changelog_watcher/app.py", "a", "app")
    git("tag", "v0.2.0")
    for name, value in {"ROOT": tmp_path, "PYPROJECT": py, "CHART": chart}.items():
        monkeypatch.setattr(rv, name, value)
    monkeypatch.delenv("AI_DIR", raising=False)
    monkeypatch.delenv("HEAD_BRANCH", raising=False)
    monkeypatch.chdir(tmp_path)  # write_changes grava no diretório atual
    return commit, git


def test_docs_only_generates_no_version(repo):
    commit, _ = repo
    commit("README.md", "docs", "docs: explica X")
    commit("tests/test_x.py", "t", "test: cobre X")
    assert rv.plan() == {"app_version": "", "chart_version": "", "app_notes": "", "chart_notes": ""}
    assert rv.write_changes() is False


def test_chart_only_bumps_only_the_chart(repo):  # fora de release-vX.Y.Z: bugfix (patch)
    commit, _ = repo
    commit("README.md", "docs", "docs: explica X")
    commit("deploy/helm/changelog-watcher/templates/np.yaml", "np", "feat(helm): networkpolicy")
    p = rv.plan()
    assert (p["app_version"], p["chart_version"]) == ("", "v0.2.1")  # parte da tag combinada v0.2.0
    assert rv.write_changes() is True
    changes = (rv.ROOT / "changes-chart.txt").read_text()
    assert "chart Helm" in changes and "docs: explica X" in changes  # docs entram nas notas
    assert not (rv.ROOT / "changes-app.txt").exists()
    assert p["chart_notes"] == "- feat(helm): networkpolicy"  # sem Copilot: assuntos dos commits do chart


def test_ui_only_bumps_only_the_chart(repo, capsys):  # UI vai na imagem do frontend: backend não reinicia
    commit, git = repo
    commit("changelog_watcher/static/app.js", "ui", "feat(ui): novo filtro")
    p = rv.plan()
    assert (p["app_version"], p["chart_version"]) == ("", "v0.2.1")
    assert p["chart_notes"] == f"- feat(ui): novo filtro\n{rv.UI_NOTE}"  # avisa que depende de frontend.enabled
    assert rv.write_changes() is True
    assert "novo filtro" in (rv.ROOT / "changes-chart.txt").read_text()
    assert not (rv.ROOT / "changes-app.txt").exists()
    git("tag", "chart-v0.2.1")
    commit("frontend/default.conf.template", "conf", "fix(frontend): cache")
    rv.main([])
    out = capsys.readouterr().out
    assert "app_version=\nchart_version=v0.2.2\nchart_tag=chart-v0.2.2\n" in out
    assert rv.UI_NOTE not in out  # a UI não mudou desde chart-v0.2.1
    commit("changelog_watcher/api.py", "api", "fix: api")  # backend continua gerando imagem
    p = rv.plan()
    assert p["app_version"] == "v0.2.1"
    # a imagem do backend leva a UI (frontend desligado): a mudança de UI entra nas notas dela
    assert p["app_notes"] == "- fix: api\n- feat(ui): novo filtro"
    rv.write_changes()
    assert "novo filtro" in (rv.ROOT / "changes-app.txt").read_text()


def test_image_change_bumps_image_and_chart_patch(repo, tmp_path, monkeypatch):
    commit, git = repo
    commit("deploy/helm/changelog-watcher/templates/np.yaml", "np", "fix(helm): x")
    git("tag", "chart-v0.2.1")
    commit("changelog_watcher/app.py", "b", "feat: nova fonte")
    (tmp_path / "copilot-app.txt").write_text("- Adiciona fonte\n")
    monkeypatch.setenv("AI_DIR", str(tmp_path))
    p = rv.plan()
    assert (p["app_version"], p["chart_version"]) == ("v0.2.1", "v0.2.2")  # feat fora de release: patch
    assert p["app_notes"] == "- Adiciona fonte"
    assert p["chart_notes"] == "- Atualiza a imagem padrão (appVersion) para v0.2.1"
    rv.main([])
    assert rv.CHART.read_text() == 'version: v0.2.2\nappVersion: "v0.2.1"\n'
    assert rv.PYPROJECT.read_text() == 'version = "0.2.1"\n'


def test_release_branch_sets_both_versions(repo, monkeypatch):
    commit, git = repo
    commit("deploy/helm/changelog-watcher/templates/np.yaml", "np", "fix(helm): x")
    git("tag", "chart-v0.2.1")
    commit("changelog_watcher/app.py", "b", "fix: x")
    monkeypatch.setenv("HEAD_BRANCH", "release-v1.0.0")
    p = rv.plan()
    assert (p["app_version"], p["chart_version"]) == ("v1.0.0", "v1.0.0")
    monkeypatch.setenv("HEAD_BRANCH", "release-v0.2.0")  # não é maior que a última tag
    with pytest.raises(SystemExit, match="release-v0.2.0"):
        rv.plan()


_s = importlib.util.spec_from_file_location(
    "test_summary", Path(__file__).parents[1] / ".github/scripts/test_summary.py"
)
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
