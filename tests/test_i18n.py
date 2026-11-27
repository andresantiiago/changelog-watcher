"""Language support: catalog coverage (backend and UI) and switching the language."""

import json
import re
from pathlib import Path

from changelog_watcher import i18n

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "changelog_watcher/static"
PLACEHOLDER = re.compile(r"(?<!\$)\{(\w+)\}")  # not ${VAR} examples


def _ui_catalog() -> dict[str, str]:
    text = (STATIC / "i18n.js").read_text()
    body = text[text.index('"pt-BR": {') + len('"pt-BR": {') : text.index("\n  },")]
    return json.loads("{" + body.strip().rstrip(",") + "}")


def _ui_keys() -> set[str]:
    js = (STATIC / "app.js").read_text()
    keys = {json.loads(m.group(1)) for m in re.finditer(r'\bt\(("(?:[^"\\]|\\.)*")', js)}
    # labels resolved through t(variable): STATUS/RELATION/EOL getters and route titles
    keys |= {json.loads(m.group(1)) for m in re.finditer(r'\blabel\(("(?:[^"\\]|\\.)*")', js)}
    keys |= {json.loads(m.group(1)) for m in re.finditer(r'\b\w+: \[("(?:[^"\\]|\\.)*"), view\w+\]', js)}
    html = (STATIC / "index.html").read_text()
    keys |= set(re.findall(r'data-i18n(?:-title|-label)?="([^"]+)"', html))
    return keys


def test_ui_catalog_covers_every_string():
    catalog = _ui_catalog()
    missing = sorted(_ui_keys() - set(catalog))
    assert not missing, f"strings without a pt-BR translation in static/i18n.js: {missing}"


def test_backend_catalog_covers_every_string():
    keys = set()
    for path in (ROOT / "changelog_watcher").rglob("*.py"):
        if path.name == "i18n.py":
            continue
        for m in re.finditer(r'\b_\(\s*("(?:[^"\\]|\\.)*")', path.read_text()):
            keys.add(json.loads(m.group(1)))
    from changelog_watcher.schemas import DETECTION_MODES
    from changelog_watcher.sources import SOURCE_TYPES
    from changelog_watcher.webhooks import WEBHOOK_KINDS

    keys |= set(SOURCE_TYPES.values()) | set(DETECTION_MODES.values()) | set(WEBHOOK_KINDS.values())
    missing = sorted(keys - set(i18n._PT_BR))
    assert not missing, f"messages without a pt-BR translation in i18n._PT_BR: {missing}"


def test_translations_keep_placeholders():
    for catalog in (i18n._PT_BR, _ui_catalog()):
        for en, pt in catalog.items():
            assert set(PLACEHOLDER.findall(en)) == set(PLACEHOLDER.findall(pt)), (en, pt)


def test_normalize():
    assert i18n.normalize("pt_BR") == "pt-BR"
    assert i18n.normalize("PT") == "pt-BR"
    assert i18n.normalize("en-US") == "en"
    assert i18n.normalize("") == "en"
    assert i18n.normalize("fr") == "en"


def test_switch_language(client, seeded):
    meta = client.get("/api/meta").json()
    assert meta["language"] == "en" and set(meta["languages"]) == {"en", "pt-BR"}
    assert meta["source_types"]["webpage"] == "Web page listing the versions"
    assert client.get("/api/projects/999").json()["detail"] == "Project 999 not found"
    md = client.post("/api/reports", json={}).json()["markdown"]
    assert md.startswith("# 📦 Update report")

    body = {"report_cron": "0 9 * * mon", "refresh_interval_minutes": 360, "language": "pt-BR"}
    r = client.put("/api/settings", json=body)
    assert r.status_code == 200 and r.json()["language"] == "pt-BR"
    meta = client.get("/api/meta").json()
    assert meta["language"] == "pt-BR"
    assert meta["source_types"]["webpage"] == "Página web com as versões"
    assert client.get("/api/projects/999").json()["detail"] == "Project 999 não encontrado"
    md = client.post("/api/reports", json={}).json()["markdown"]
    assert md.startswith("# 📦 Relatório de atualizações") and "## Resumo por ambiente" in md

    # persisted: a new process (cache cleared) reads it back from the database
    i18n.reset()
    assert i18n.get_language() == "pt-BR"

    assert client.put("/api/settings", json=body | {"language": "fr"}).status_code == 422
    # omitted language: unchanged
    assert (
        client.put("/api/settings", json={k: v for k, v in body.items() if k != "language"}).json()["language"]
        == "pt-BR"
    )


def test_language_env_var(monkeypatch, client):
    from changelog_watcher.config import get_settings

    monkeypatch.setenv("APP_LANGUAGE", "pt_BR")
    get_settings.cache_clear()
    i18n.reset()
    try:
        assert client.get("/api/meta").json()["language"] == "pt-BR"
    finally:
        monkeypatch.delenv("APP_LANGUAGE")
        get_settings.cache_clear()
        i18n.reset()
