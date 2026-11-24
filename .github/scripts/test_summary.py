"""Resumo do pytest (JUnit XML) em Markdown para o $GITHUB_STEP_SUMMARY.

    python3 .github/scripts/test_summary.py pytest.xml >> "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

STATUS = {"failure": "❌ falhou", "error": "💥 erro", "skipped": "⏭️ ignorado", None: "✅ passou"}


def summarize(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    cases = root.iter("testcase")
    rows, problems = [], []
    counts = dict.fromkeys(STATUS, 0)
    for c in cases:
        name = f"{c.get('classname', '').removeprefix('tests.')}::{c.get('name')}"
        res = next((ch for ch in c if ch.tag in ("failure", "error", "skipped")), None)
        kind = res.tag if res is not None else None
        counts[kind] += 1
        rows.append(f"| {STATUS[kind]} | `{name}` | {float(c.get('time') or 0):.2f}s |")
        if kind in ("failure", "error"):
            detail = ((res.get("message") or "") + "\n" + (res.text or "")).strip()[-3000:]
            problems.append(f"<details><summary>{STATUS[kind]} <code>{name}</code></summary>\n\n"
                            f"```\n{detail}\n```\n</details>")
    total = sum(counts.values())
    head = ("## Testes\n\n"
            f"**{total}** testes: ✅ {counts[None]} passaram · ❌ {counts['failure']} falharam · "
            f"💥 {counts['error']} com erro · ⏭️ {counts['skipped']} ignorados\n")
    parts = [head]
    if problems:
        parts += ["### Falhas\n", *problems, ""]
    parts += ["<details><summary>Todos os testes</summary>\n",
              "| Resultado | Teste | Tempo |", "|---|---|---|", *rows, "</details>"]
    return "\n".join(parts) + "\n"


def main(path: str) -> None:
    file = Path(path)
    if not file.exists():  # pytest não chegou a rodar (erro de instalação/coleta)
        sys.stdout.write("## Testes\n\n💥 Relatório não gerado: veja o log do passo de testes.\n")
        return
    sys.stdout.write(summarize(file.read_text()))


if __name__ == "__main__":
    main(sys.argv[1])
