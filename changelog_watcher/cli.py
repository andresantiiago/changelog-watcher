"""Command line entry point.

    changelog-watcher serve            # web UI + API + scheduler
    changelog-watcher refresh          # one-off refresh
    changelog-watcher report [--send]  # build (and send) the report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="changelog-watcher")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    sub.add_parser("refresh")
    rep = sub.add_parser("report")
    rep.add_argument("--send", action="store_true")
    rep.add_argument("--days", type=int, default=None)
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("changelog_watcher.main:app", host=args.host, port=args.port, proxy_headers=True,
                    forwarded_allow_ips="*")
        return

    from .config import get_settings
    from .db import init_engine, session_scope
    from .seed import load_seed

    init_engine()
    settings = get_settings()
    if settings.config_file:
        with session_scope() as s:
            load_seed(s, settings.config_file, overwrite=settings.seed_overwrite)

    if args.cmd == "refresh":
        from .refresh import run_refresh

        print(json.dumps(asyncio.run(run_refresh()), indent=2, ensure_ascii=False))
    elif args.cmd == "report":
        from sqlalchemy import select

        from .models import Webhook
        from .report import create_report
        from .webhooks import deliver_report

        async def run():
            with session_scope() as s:
                report = create_report(s, days=args.days)
                print(report.markdown)
                if args.send:
                    hooks = list(s.scalars(select(Webhook).where(Webhook.enabled.is_(True))))
                    print(json.dumps(await deliver_report(s, report, hooks), indent=2, ensure_ascii=False))

        asyncio.run(run())


if __name__ == "__main__":
    main()
