# Capturas de tela do README

As imagens em `docs/*.png` são geradas a partir de um banco de demonstração com
dados sintéticos (releases, versões em uso e histórico relativos à data de hoje),
sem acesso à rede nem a um Prometheus de verdade. A tela **Descobrir no cluster**
usa uma resposta simulada do cenário `kube-prometheus-stack`.

Requer as dependências de desenvolvimento (`pip install -e ".[dev]"`) e o
Playwright para Node com o Chromium instalado.

```bash
python docs/screenshots/build_demo.py /tmp/demo.db /tmp/discover.json
```

```bash
DATABASE_URL=sqlite:////tmp/demo.db AUTH_DISABLED=true SCHEDULER_ENABLED=false REFRESH_ON_STARTUP=false changelog-watcher serve --port 8099
```

Em outro terminal:

```bash
node docs/screenshots/capture.mjs http://127.0.0.1:8099 /tmp/discover.json docs
```

Para reduzir o tamanho (paleta de 256 cores, sem perda visível em telas de UI):

```bash
python -c "
from PIL import Image; import sys
for f in sys.argv[1:]:
    Image.open(f).convert('RGB').quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(f, optimize=True)
" docs/*.png
```
