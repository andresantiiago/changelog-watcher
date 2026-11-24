"""Decide a próxima versão (``vX.Y.Z``) e sincroniza pyproject.toml e Chart.yaml.

Chart (version/appVersion), tag, imagem e release usam ``vX.Y.Z[-rcN]``; o pyproject.toml
segue o padrão do pip (PEP 440), sem ``v``: ``X.Y.Z`` / ``X.Y.ZrcN``.

``--changes ARQ``: grava em ARQ as mudanças desde a última tag final (commits, arquivos e
diff truncado), que o Copilot classifica (linha 1: major|minor|patch) e resume (demais linhas).

Merge na main (sem argumento):
1. Se a versão declarada (pyproject.toml, ``version``/``appVersion`` do Chart.yaml) ou a
   de uma tag ``vX.Y.Z-rcN`` for maior que a última tag final ``vX.Y.Z``, usa a maior
   delas sempre sem o sufixo: o merge na main é a versão final (v0.2.0-rc3 -> v0.2.0).
2. Senão, incrementa a última tag final pelo tipo que o Copilot respondeu (arquivo em
   $AI_FILE); sem resposta válida, pelas mensagens de commit: ``BREAKING CHANGE`` /
   ``tipo!:`` / ``[major]`` -> major; ``feat:`` / ``[minor]`` -> minor; o resto -> patch.

Tag de release candidate (argumento ``vX.Y.Z-rcN``): só valida e grava essa versão nos
arquivos, para a imagem reportar a versão certa (sem commit).

Escreve ``version``, ``changed``, ``prerelease`` e ``notes`` em $GITHUB_OUTPUT (ou stdout).
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
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
RC = re.compile(r"^v?(\d+\.\d+\.\d+)-?rc\.?\d+$")  # v0.2.0-rc1, 0.2.0rc1 (PEP 440)
KINDS = ("major", "minor", "patch")
MAX_CHANGES = 60_000  # cabe num argumento de linha de comando (limite de 128 KiB)


def parse(text: str | None) -> tuple[int, int, int] | None:
    m = SEMVER.match((text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def base(text: str | None) -> tuple[int, int, int] | None:
    """Versão final de um vX.Y.Z ou vX.Y.Z-rcN."""
    m = RC.match((text or "").strip())
    return parse(m.group(1) if m else text)


def fmt(v: tuple[int, int, int]) -> str:
    return "v" + ".".join(map(str, v))


def bump_kind(messages: str) -> str:
    if re.search(r"BREAKING[ -]CHANGE|^\w+(\(.+\))?!:|\[major\]", messages, re.M | re.I):
        return "major"
    if re.search(r"^feat(\(.+\))?:|\[minor\]", messages, re.M | re.I):
        return "minor"
    return "patch"


def bump(v: tuple[int, int, int], kind: str) -> tuple[int, int, int]:
    major, minor, patch = v
    if kind == "major":
        return (major + 1, 0, 0)
    if kind == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def read_ai(text: str) -> tuple[str | None, str]:
    """Resposta do Copilot -> (tipo ou None, notas)."""
    lines = [ln for ln in text.strip().splitlines() if not ln.strip().startswith("```")]
    if not lines:
        return None, ""
    first = re.sub(r"[^a-z]", "", lines[0].lower())
    if first not in KINDS:
        return None, ""
    return first, "\n".join(lines[1:]).strip()


def next_version(declared: list[str], last_tag: str | None, messages: str, rc_tags: list[str] = (),
                 kind: str | None = None) -> str:
    last = parse(last_tag)
    rc_bases = [base(t) for t in rc_tags if RC.match(t)]
    ahead = [v for v in [*map(base, declared), *rc_bases] if v and (last is None or v > last)]
    if ahead:
        v = max(ahead)
    elif last is None:
        v = (0, 1, 0)
    else:
        v = bump(last, kind or bump_kind(messages))
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


def sync_files(version: str) -> bool:
    changed = _set(PYPROJECT, r'^(version\s*=\s*")[^"]*(")', pep440(version))
    changed |= _set(CHART, r"^(version:\s*)\S+()", version)
    changed |= _set(CHART, r'^(appVersion:\s*")[^"]*(")', version)
    return changed


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def last_final_tag() -> str | None:
    finals = [t for t in _git("tag", "--list", "v*", "--merged", "HEAD").split() if parse(t)]
    return max(finals, key=parse) if finals else None


def write_changes(path: str) -> None:
    last = last_final_tag()
    start = last or _git("rev-list", "--max-parents=0", "HEAD").split()[0]  # 1ª release: desde o 1º commit
    text = (f"Commits desde {last or 'o início'}:\n{_git('log', '--format=- %s%n%b', f'{start}..HEAD')}\n\n"
            f"Arquivos alterados:\n{_git('diff', '--stat', start, 'HEAD')}\n\n"
            f"Diff:\n{_git('diff', start, 'HEAD')}")
    Path(path).write_text(text[:MAX_CHANGES])


def _declared() -> list[str]:
    chart = CHART.read_text()
    found = [re.search(r'^version\s*=\s*"([^"]*)"', PYPROJECT.read_text(), re.M),
             re.search(r"^version:\s*(\S+)", chart, re.M), re.search(r'^appVersion:\s*"?([^"\s]+)', chart, re.M)]
    return [m.group(1) for m in found if m]


def main(argv: list[str]) -> None:
    if argv[:1] == ["--changes"]:
        write_changes(argv[1])
        return
    declared = _declared()
    ai = Path(os.environ["AI_FILE"]) if os.environ.get("AI_FILE") else None
    kind, notes = read_ai(ai.read_text()) if ai and ai.exists() else (None, "")
    if argv:  # build de uma tag de release candidate
        if not argv[0].startswith("v") or not RC.match(argv[0]):
            sys.exit(f"tag inválida: {argv[0]} (esperado vX.Y.Z-rcN)")
        version, last_tag, prerelease = argv[0], argv[0], True
    else:
        last_tag = last_final_tag()
        messages = _git("log", "--format=%B", f"{last_tag}..HEAD" if last_tag else "HEAD")
        rc_tags = _git("tag", "--list", "v*").split()  # rc pode estar em commits fora da main (squash)
        version, prerelease = next_version(declared, last_tag, messages, rc_tags, kind), False
    changed = sync_files(version)
    eof = f"EOF_{secrets.token_hex(8)}"
    out = (f"version={version}\nchanged={str(changed).lower()}\nprerelease={str(prerelease).lower()}\n"
           f"notes<<{eof}\n{notes}\n{eof}\n")
    if gh := os.environ.get("GITHUB_OUTPUT"):
        with open(gh, "a") as f:
            f.write(out)
    sys.stdout.write(f"última tag: {last_tag or '-'}, declaradas: {', '.join(declared)}, "
                     f"copilot: {kind or '-'}\n{out}")


if __name__ == "__main__":
    main(sys.argv[1:])
