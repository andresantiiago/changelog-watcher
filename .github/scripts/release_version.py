"""Decide as próximas versões da imagem e do chart e sincroniza pyproject.toml e Chart.yaml.

Dois componentes, cada um com sua tag e só os seus arquivos:

- **imagem** (``changelog_watcher/``, ``Dockerfile``, ``.dockerignore``, ``pyproject.toml``):
  tag ``vX.Y.Z``, imagem no GHCR e release; atualiza ``version`` do pyproject.toml (PEP 440,
  sem ``v``) e ``appVersion`` do Chart.yaml.
- **chart** (``deploy/helm/changelog-watcher/``): tag ``chart-vX.Y.Z`` e release; atualiza
  ``version`` do Chart.yaml. Uma nova imagem muda o ``appVersion`` (imagem padrão do chart),
  então também gera uma versão do chart (patch, se o chart em si não mudou).

O resto (docs, testes, CI) não gera versão: README e ``docs/`` entram nas notas da próxima
release. Antes da separação as tags ``vX.Y.Z`` valiam para os dois; sem nenhuma tag
``chart-v*``, o chart parte da última ``vX.Y.Z``.

``--changes``: grava ``changes-<componente>.txt`` (commits, arquivos e diff truncado
desde a última tag do componente) para cada componente com mudanças, que o Copilot resume em
``copilot-<componente>.txt`` (notas da release). Escreve ``any=true|false`` em $GITHUB_OUTPUT.

Merge na main (sem argumento), para cada componente com mudanças; $HEAD_BRANCH é a branch do PR:
1. Branch ``release-vX.Y.Z``: a versão é ``vX.Y.Z``. A imagem falha se ela não for maior que a
   última tag final; o chart, nesse caso, só incrementa o patch.
2. Outra branch (ou push direto): bugfix. Se a versão declarada (pyproject.toml/``appVersion``
   para a imagem, ``version`` do Chart.yaml para o chart) ou, só na imagem, a de uma tag
   ``vX.Y.Z-rcN`` for maior que a última tag final, usa a maior delas sempre sem o sufixo: o
   merge na main é a versão final (v0.2.0-rc3 -> v0.2.0). Senão incrementa o patch.

Tag de release candidate da imagem (argumento ``vX.Y.Z-rcN``): só valida e grava essa versão
nos arquivos da imagem, para ela reportar a versão certa (sem commit).

Escreve ``app_version``, ``chart_tag``, ``changed``, ``prerelease``, ``app_notes`` e
``chart_notes`` em $GITHUB_OUTPUT (ou stdout); versão vazia = sem release. Sem notas do Copilot,
as notas são os assuntos dos commits do componente desde a sua última tag (``--generate-notes``
partiria da release anterior por data, que pode ser a do outro componente).
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
CHART = ROOT / "deploy/helm/changelog-watcher/Chart.yaml"
PATHS = {
    "app": ("changelog_watcher", "Dockerfile", ".dockerignore", "pyproject.toml"),
    "chart": ("deploy/helm/changelog-watcher",),
}
LABELS = {"app": "imagem da aplicação", "chart": "chart Helm"}
DOC_PATHS = ("README.md", "README.pt-BR.md", "docs")
CHART_TAG = "chart-"
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
RC = re.compile(r"^v?(\d+\.\d+\.\d+)-?rc\.?\d+$")  # v0.2.0-rc1, 0.2.0rc1 (PEP 440)
RELEASE_BRANCH = re.compile(r"^release-(v\d+\.\d+\.\d+)$")
MAX_CHANGES = 60_000  # cabe num argumento de linha de comando (limite de 128 KiB)


def parse(text: str | None) -> tuple[int, int, int] | None:
    m = SEMVER.match((text or "").strip().removeprefix(CHART_TAG))
    return tuple(int(x) for x in m.groups()) if m else None


def base(text: str | None) -> tuple[int, int, int] | None:
    """Versão final de um vX.Y.Z ou vX.Y.Z-rcN."""
    m = RC.match((text or "").strip())
    return parse(m.group(1) if m else text)


def fmt(v: tuple[int, int, int]) -> str:
    return "v" + ".".join(map(str, v))


def read_ai(text: str) -> str:
    """Resposta do Copilot -> notas (sem cercas de código)."""
    return "\n".join(ln for ln in text.strip().splitlines() if not ln.strip().startswith("```")).strip()


def release_branch() -> str | None:
    """vX.Y.Z de um PR vindo de ``release-vX.Y.Z``; None para outras branches (bugfix)."""
    m = RELEASE_BRANCH.match(os.environ.get("HEAD_BRANCH", ""))
    return m.group(1) if m else None


def next_version(declared: list[str], last_tag: str | None, rc_tags: list[str] = ()) -> str:
    last = parse(last_tag)
    rc_bases = [base(t) for t in rc_tags if RC.match(t)]
    ahead = [v for v in [*map(base, declared), *rc_bases] if v and (last is None or v > last)]
    if ahead:
        v = max(ahead)
    elif last is None:
        v = (0, 1, 0)
    else:
        v = (last[0], last[1], last[2] + 1)
    return fmt(v)


def _set(path: Path, pattern: str, value: str) -> bool:
    text = path.read_text()
    new = re.sub(pattern, lambda m: m.group(1) + value + m.group(2), text, count=1, flags=re.M)
    if new != text:
        path.write_text(new)
    return new != text


def pep440(version: str) -> str:
    """v0.2.0-rc1 -> 0.2.0rc1 (forma canônica do pip)."""
    return re.sub(r"-?rc\.?", "rc", version.removeprefix("v"))


def sync_files(app_version: str = "", chart_version: str = "") -> bool:
    changed = False
    if app_version:
        changed |= _set(PYPROJECT, r'^(version\s*=\s*")[^"]*(")', pep440(app_version))
        changed |= _set(CHART, r'^(appVersion:\s*")[^"]*(")', app_version)
    if chart_version:
        changed |= _set(CHART, r"^(version:\s*)\S+()", chart_version)
    return changed


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def last_final_tag(prefix: str = "") -> str | None:
    finals = [t for t in _git("tag", "--list", f"{prefix}v*", "--merged", "HEAD").split() if parse(t)]
    return max(finals, key=parse) if finals else None


def last_tags() -> dict[str, str | None]:
    app = last_final_tag()
    return {"app": app, "chart": last_final_tag(CHART_TAG) or app}  # tags vX.Y.Z antigas valiam para os dois


def _since(tag: str | None) -> str:
    return tag or _git("rev-list", "--max-parents=0", "HEAD").split()[0]  # 1ª release: desde o 1º commit


def changed(component: str, tag: str | None) -> bool:
    return bool(_git("diff", "--name-only", _since(tag), "HEAD", "--", *PATHS[component]))


def commit_log(component: str, tag: str | None, fmt: str = "%B") -> str:
    return _git("log", f"--format={fmt}", f"{tag}..HEAD" if tag else "HEAD", "--", *PATHS[component])


def write_changes() -> bool:
    any_changes = False
    for component, tag in last_tags().items():
        if not changed(component, tag):
            continue
        any_changes = True
        start, paths = _since(tag), (*PATHS[component], *DOC_PATHS)
        text = (
            f"Componente: {LABELS[component]}\n\n"
            f"Commits desde {tag or 'o início'}:\n{_git('log', '--format=- %s%n%b', f'{start}..HEAD', '--', *paths)}\n\n"
            f"Arquivos alterados:\n{_git('diff', '--stat', start, 'HEAD', '--', *paths)}\n\n"
            f"Diff:\n{_git('diff', start, 'HEAD', '--', *paths)}"
        )
        Path(f"changes-{component}.txt").write_text(text[:MAX_CHANGES])
    return any_changes


def _declared() -> dict[str, list[str]]:
    chart = CHART.read_text()
    py = re.search(r'^version\s*=\s*"([^"]*)"', PYPROJECT.read_text(), re.M)
    app = re.search(r'^appVersion:\s*"?([^"\s]+)', chart, re.M)
    ver = re.search(r"^version:\s*(\S+)", chart, re.M)
    return {"app": [m.group(1) for m in (py, app) if m], "chart": [ver.group(1)] if ver else []}


def _ai(component: str) -> str:
    d = os.environ.get("AI_DIR")
    path = d and Path(d, f"copilot-{component}.txt")
    return read_ai(path.read_text()) if path and path.exists() else ""


def plan() -> dict[str, str]:
    """Versões (vazio = sem release) e notas de cada componente desde a última tag."""
    declared, tags, release = _declared(), last_tags(), release_branch()
    out = {"app_version": "", "chart_version": "", "app_notes": "", "chart_notes": ""}
    if changed("app", tags["app"]):
        if release and tags["app"] and parse(release) <= parse(tags["app"]):
            sys.exit(f"branch release-{release}: a versão precisa ser maior que a última tag {tags['app']}")
        rc_tags = [] if release else _git("tag", "--list", "v*").split()  # rc pode estar fora da main (squash)
        out["app_version"] = next_version([release] if release else declared["app"], tags["app"], rc_tags)
        out["app_notes"] = _ai("app") or commit_log("app", tags["app"], "- %s")
    chart_changed = changed("chart", tags["chart"])
    if chart_changed or out["app_version"]:
        notes = (_ai("chart") or commit_log("chart", tags["chart"], "- %s")) if chart_changed else ""
        out["chart_version"] = next_version([release] if release else declared["chart"], tags["chart"])
        if out["app_version"]:
            notes = f"{notes}\n- Atualiza a imagem padrão (appVersion) para {out['app_version']}".strip()
        out["chart_notes"] = notes
    return out


def _emit(text: str) -> None:
    if gh := os.environ.get("GITHUB_OUTPUT"):
        with open(gh, "a") as f:
            f.write(text)
    sys.stdout.write(text)


def main(argv: list[str]) -> None:
    if argv[:1] == ["--changes"]:
        return _emit(f"any={str(write_changes()).lower()}\n")
    if argv:  # build de uma tag de release candidate da imagem
        if not argv[0].startswith("v") or not RC.match(argv[0]):
            sys.exit(f"tag inválida: {argv[0]} (esperado vX.Y.Z-rcN)")
        notes = _ai("app") or commit_log("app", last_final_tag(), "- %s")
        result = {"app_version": argv[0], "chart_version": "", "app_notes": notes, "chart_notes": ""}
        prerelease = True
    else:
        result, prerelease = plan(), False
    changed_files = sync_files(result["app_version"], result["chart_version"])
    chart_tag = CHART_TAG + result["chart_version"] if result["chart_version"] else ""
    out = (
        f"app_version={result['app_version']}\nchart_tag={chart_tag}\n"
        f"changed={str(changed_files).lower()}\nprerelease={str(prerelease).lower()}\n"
    )
    for key in ("app_notes", "chart_notes"):
        eof = f"EOF_{secrets.token_hex(8)}"
        out += f"{key}<<{eof}\n{result[key]}\n{eof}\n"
    _emit(out)


if __name__ == "__main__":
    main(sys.argv[1:])
