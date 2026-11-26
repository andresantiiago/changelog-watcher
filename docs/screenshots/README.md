# README screenshots

🇺🇸 English · 🇧🇷 [Português](README.pt-BR.md)

The images in `docs/*.png` (English, used by `README.md`) and `docs/pt-BR/*.png` (Portuguese,
used by `README.pt-BR.md`) are generated from a demo database with synthetic data (releases,
versions in use and history relative to today), with no network access and no real Prometheus.
The **Discover in the cluster** screen uses a simulated answer of the `kube-prometheus-stack`
scenario.

Requires the development dependencies (`pip install -e ".[dev]"`) and Playwright for Node
with Chromium installed.

The third argument is the language of the data and of the interface (`en` or `pt-BR`):

```bash
python docs/screenshots/build_demo.py /tmp/demo.db /tmp/discover.json en
```

```bash
DATABASE_URL=sqlite:////tmp/demo.db AUTH_DISABLED=true SCHEDULER_ENABLED=false REFRESH_ON_STARTUP=false EOL_ENABLED=false changelog-watcher serve --port 8099
```

In another terminal (`docs` for English, `docs/pt-BR` for Portuguese):

```bash
node docs/screenshots/capture.mjs http://127.0.0.1:8099 /tmp/discover.json docs
```

To reduce their size (256-color palette, no visible loss on UI screens):

```bash
python -c "
from PIL import Image; import sys
for f in sys.argv[1:]:
    Image.open(f).convert('RGB').quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(f, optimize=True)
" docs/*.png docs/pt-BR/*.png
```
