# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Changelog Watcher tracks releases of a list of apps, compares them with the version running in each environment (manual or detected via Prometheus/kube-state-metrics) and reports what is outdated (UI, weekly webhook report, `/metrics`, Grafana dashboard, Helm chart). The README is the user-facing spec: version classification rules, Prometheus detection modes, env vars, metrics table. It exists in English (`README.md`, default) and Brazilian Portuguese (`README.pt-BR.md`): keep both in sync (same for `docs/screenshots/README*.md`). The app is bilingual too (see **i18n** below).

## Commands

```bash
pip install -e ".[dev]"                                  # Python >= 3.15
CONFIG_FILE=config.example.yaml changelog-watcher serve  # http://localhost:8080, API docs at /docs
changelog-watcher refresh                                # one-off refresh
changelog-watcher report [--send] [--days N]

pytest -q                                                # all tests
pytest tests/test_versioning.py::test_name -q            # single test
pytest -q --cov                                          # coverage (fail_under in pyproject.toml)
ruff check .                                             # lint (rules in pyproject.toml)
ruff format .                                            # formatting (CI runs --check)
mypy                                                     # types (files listed in pyproject.toml)
python deploy/grafana/build_dashboard.py                 # regenerate Grafana dashboard JSONs (en + pt-BR)
helm lint deploy/helm/changelog-watcher
```

Local kind cluster with a locally built image: see `deploy/helm/values-kind.yaml` (header has the docker build / `kind load` / helm steps) and the README section "Local kind cluster".

## CI / releases

- **`.github/workflows/ci.yaml`** (every PR, and called by `release.yaml`): `ruff check`/`ruff format --check` + `mypy` + `pip-audit`, a `docker build` (not pushed) scanned by Trivy, pytest with coverage (JUnit XML rendered by `.github/scripts/test_summary.py`, plus a `coverage report` table, to the job summary and a sticky PR comment), regenerates the dashboard and fails on `git diff --exit-code deploy/`, then `helm lint` + `helm template` with all optional features enabled, `kubeconform` on several values combinations, and a `kind` job that installs the chart with `values-kind.yaml` and checks the API.
- **`.github/workflows/release.yaml`** (push to `main` and `v*-rc*` tags): calls `ci.yaml`, then `version` → `image` → `release`. Image and Helm chart are versioned separately by changed paths since each one's last tag (`PATHS` in `.github/scripts/release_version.py`): image (`changelog_watcher/`, `Dockerfile`, `.dockerignore`, `pyproject.toml`) → tag `vX.Y.Z`, GHCR image, release, updates pyproject `version` + Chart `appVersion`; chart (`deploy/helm/changelog-watcher/`) → tag `chart-vX.Y.Z`, release (not Latest), updates Chart `version`. A new image also bumps the chart (patch if the chart itself didn't change). Docs/tests/CI never release; README/`docs/` changes go into the next release's notes. With no `chart-v*` tag yet, the chart starts from the last `vX.Y.Z`. Copilot CLI (auth via `GH_TOKEN`, prompt inline in the workflow) only writes each component's release notes (`changes-<component>.txt` → `copilot-<component>.txt`). The version comes from the merged PR's branch (looked up via the commit's PR, passed as `HEAD_BRANCH`): `release-vX.Y.Z` → `vX.Y.Z` for the image (fails if not above its last tag) and the chart (patch if the chart is already there); any other branch or a direct push is a bugfix: the max of the declared version (and, for the image, `vX.Y.Z-rcN` tags, `-rcN` stripped) if above the last final tag, else a patch bump. It commits a sync of `pyproject.toml` + `Chart.yaml` if needed (`[skip ci]`), then tags, pushes the image to GHCR and creates the GitHub releases. A `vX.Y.Z-rcN` tag publishes only that image tag + a GitHub pre-release, no commit.
- **Version format**: `vX.Y.Z[-rcN]` for image tags/releases and the chart's `version`/`appVersion`; chart tags/releases are `chart-vX.Y.Z`; `pyproject.toml` stays PEP 440 without `v` (`X.Y.Z`, `X.Y.ZrcN`) and `__version__` reads it from package metadata.
- Never hand-edit `deploy/helm/changelog-watcher/dashboards/changelog-watcher*.json`; edit `build_dashboard.py` and rerun it. Panel texts there are written in pt-BR and its `EN` dict translates them (exact strings) into the default English `changelog-watcher.json`; `changelog-watcher.pt-BR.json` is the untranslated build. Helm picks one by `config.language`.

## Architecture

Single FastAPI process (`main.py:create_app`) that serves the REST API (`api.py`, under `/api`), the static SPA (`static/`, plain HTML/JS/CSS, no build step), `/metrics`, and runs an in-process APScheduler (`scheduler.py`: interval refresh + cron weekly report). Because the scheduler is in-process, the app must run with one replica.

Data flow:
1. **Refresh** (`refresh.py:run_refresh`): first `detect_versions` (for deployments with `version_source != "manual"`: Prometheus queries via `prometheus.py`, or the in-cluster Kubernetes API via `kube.py` for `kubernetes`/`argocd`/`flux`, which reuses the project's `workload_*` fields; `kube.detect` dispatches and `KubeError` subclasses `PrometheusError`), then fetch releases per project (`sources.fetch_releases`, dispatching on `Project.source_type`), upsert `Release` rows, and finally `store_snapshots` (one `Snapshot` per environment per day, feeding the UI history charts). Detection runs first because deployed versions are passed as `hints` to some sources (Docker Hub). A module-level `asyncio.Lock` + `STATE` serializes runs; API endpoints trigger refreshes via `_spawn`.
2. **Analysis** (`analysis.py`) is computed on demand from the DB, never stored: `compute_statuses` → `DeploymentStatus` per (project × environment) with latest bugfix/minor/major, status, outdated days, libyears. Dashboard, report, metrics and env comparison all build on this.
3. **Outputs**: `report.py` (report data + Markdown, saved as `Report`), `webhooks.py` (per-kind payload formatting), `alerts.py` (immediate per-webhook alerts at the end of `run_refresh`, deduplicated in `AlertSent`), `metrics.py` (`WatcherCollector`, a custom collector that computes gauges from the DB on each scrape, cached `METRICS_CACHE_SECONDS`). Process counters/histograms live in `refresh.py`.
4. **App logs**: `logstore.py:MemoryLogHandler` keeps the last 5000 INFO+ records in an in-memory `deque` (lost on restart), which `/api/logs` filters. Never write to the app DB from a log handler: it runs inside the caller's open transaction (SQLite lock wait; in-memory StaticPool shares the caller's connection). Old DBs keep an unused `log_entries` table.

Key conventions:
- **Version parsing** goes through `versioning.parse_version(tag, tag_pattern, two_part=...)`; in `analysis.py` use the `pv(project, text)` wrapper so the project's `tag_pattern` and `version_scheme` (`major_patch` for e.g. PostgreSQL) are respected. `Deployment.current_version` resolves detected vs manual version.
- **Adding a release source**: new module in `sources/` returning `list[ReleaseData]`, raising `SourceError` for expected failures (use `base.get_json` for HTTP), then register it in `SOURCE_TYPES` and the dispatch in `sources/__init__.py`. `changelog_url` enrichment of missing notes/dates happens generically there.
- **Secrets**: fields like Prometheus tokens/passwords and webhook URLs may hold `${VAR}` placeholders, expanded only at use time with `config.expand_env`, and only when the whole value is the placeholder and the var isn't one of the app's own credentials (`_APP_SECRETS`). The API masks them with `mask_secret`, and an incoming `SECRET_MASK` value means "keep the existing value" (refused when the bound URL changes, see `api._apply_body`). GitHub/GitLab tokens go only to `GITHUB_API_URL`/`GITLAB_URL`, never to a project's `source_url`.
- **Outbound HTTP** to user-configured URLs goes through `safehttp.client()`, which refuses loopback/link-local destinations (redirects included); never echo a response body from such a URL back to the API.
- **Auth** (`auth.py`) is fail-closed: `auth.check_config` makes `create_app` raise without Basic Auth (`AUTH_USERNAME`/`AUTH_PASSWORD`) or OIDC (`OIDC_*`, incomplete OIDC config also raises) unless `AUTH_DISABLED=true` (tests set it in `conftest.py`). OIDC = Authorization Code + PKCE, ID token checked with PyJWT against the JWKS (asymmetric algorithms only), session in a signed `__Host-` cookie; viewer-group users only get safe methods (403 otherwise). No session: `/api/*` → 401, HTML navigation → redirect to `/auth/login`. `/api/me` tells the SPA who is logged in and whether they can edit. A `same_origin` middleware blocks cross-site non-GET requests (CSRF with Basic Auth/cookies).
- **DB / migrations**: SQLAlchemy 2.0 models in `models.py`; `db.init_engine` does `create_all` plus `_add_missing_columns`, a forward-only migration that only adds columns. New columns must be nullable or have a scalar default; renames/removals are not supported. SQLite only (`make_engine` rejects other URLs): a file, or `sqlite:///:memory:` (data lost on restart). Helm `persistence.type`: `pvc` renders a StatefulSet with a volumeClaimTemplate, `inMemory` a Deployment; always 1 replica.
- **Seed** (`seed.py`): optional YAML (`CONFIG_FILE`) loaded at startup and by CLI commands. Accepted fields are derived from the Pydantic `*In` schemas in `schemas.py`, so a new schema field is automatically seedable. Only creates missing records unless `SEED_OVERWRITE=true`.
- **Environment filters**: in `compute_statuses` (and its callers) `None` means all environments and `[]` means none; but `Webhook.environment_ids == []` means the webhook receives all environments. Don't mix the two.
- **i18n**: English is the default language, Brazilian Portuguese the other one. Backend user-facing messages (API errors, source/Prometheus errors, report Markdown, webhook chat text, `/api/meta` labels) are written in English and wrapped in `i18n._()`, with the translation in `i18n._PT_BR`; the language comes from `APP_LANGUAGE`, overridden by `app_settings.language` (Settings page), cached in `i18n._current` (`create_app` resets it). The SPA uses `t("English text", {params})` with the catalog in `static/i18n.js`; module-level labels (STATUS, RELATION, EOL) are getters so they translate after `/api/meta` loads; static `index.html` text uses `data-i18n*` attributes. `tests/test_i18n.py` fails on any `_()`/`t()` string missing from its catalog. Logs and metric HELP texts are English only. Metric label values (statuses, age buckets like `>365d`) are language-neutral identifiers and are never translated (dashboards and alerts depend on them).
- **Settings** come from env vars only (`config.Settings.from_env`, `lru_cache`d `get_settings()`). When adding a setting, also document it in the README table and, if relevant, `deploy/helm/changelog-watcher/values.yaml`.

## Tests

`tests/conftest.py` sets env vars *before* importing the app (in-memory SQLite, scheduler and startup refresh disabled, metrics cache 0). The `client` fixture builds a fresh app + DB; `seeded` creates two environments, three projects and releases through the API. HTTP to external sources is mocked with `respx`.

`tests/test_deploy.py` couples code and deploy artifacts:
- every `changelog_watcher_*` metric used in the Grafana dashboard must be exposed by `/metrics` (renaming/removing a metric means updating `build_dashboard.py`);
- `config.example.yaml` must load with exact counts (`{"environments": 2, "projects": 6, "webhooks": 2}`), so update the assertion when changing the example.

`tests/test_release.py` loads the CI scripts in `.github/scripts/` via importlib; changes to `release_version.py` or `test_summary.py` must keep it passing.
