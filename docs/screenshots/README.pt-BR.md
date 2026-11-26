# Capturas de tela do README

🇺🇸 [English](README.md) · 🇧🇷 Português

As imagens em `docs/*.png` (inglês, usadas no `README.md`) e `docs/pt-BR/*.png` (português,
usadas no `README.pt-BR.md`) são geradas a partir de um banco de demonstração com dados
sintéticos (releases, versões em uso e histórico relativos à data de hoje), sem acesso à rede
nem a um Prometheus de verdade. A tela **Descobrir no cluster** usa uma resposta simulada do
cenário `kube-prometheus-stack`.

Requer as dependências de desenvolvimento (`pip install -e ".[dev]"`) e o
Playwright para Node com o Chromium instalado.

O terceiro argumento é o idioma dos dados e da interface (`en` ou `pt-BR`):

```bash
python docs/screenshots/build_demo.py /tmp/demo.db /tmp/discover.json pt-BR
```

```bash
DATABASE_URL=sqlite:////tmp/demo.db AUTH_DISABLED=true SCHEDULER_ENABLED=false REFRESH_ON_STARTUP=false EOL_ENABLED=false changelog-watcher serve --port 8099
```

Em outro terminal (`docs` para o inglês, `docs/pt-BR` para o português):

```bash
node docs/screenshots/capture.mjs http://127.0.0.1:8099 /tmp/discover.json docs/pt-BR
```

Para reduzir o tamanho (paleta de 256 cores, sem perda visível em telas de UI):

```bash
python -c "
from PIL import Image; import sys
for f in sys.argv[1:]:
    Image.open(f).convert('RGB').quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(f, optimize=True)
" docs/*.png docs/pt-BR/*.png
```
