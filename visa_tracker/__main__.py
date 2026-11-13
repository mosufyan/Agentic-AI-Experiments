from __future__ import annotations

import argparse
import json
import sys

from .config import load_settings
from .dashboard import serve
from .db import Store
from .runner import run_loop, run_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="US visa appointment tracker (Phases 1–3)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("once", help="Run a single availability check")
    sub.add_parser("loop", help="Run checks forever (public + optional auth cadence)")
    sub.add_parser("serve", help="Serve the analytics dashboard")

    seed = sub.add_parser("seed", help="Seed synthetic history for Phase 3 dashboard testing")
    seed.add_argument("--days", type=int, default=14)
    seed.add_argument("--interval-minutes", type=int, default=60)

    args = parser.parse_args(argv)
    settings = load_settings()

    if args.cmd == "once":
        result = run_once(settings)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if args.cmd == "loop":
        run_loop(settings)
        return 0
    if args.cmd == "serve":
        serve(settings)
        return 0
    if args.cmd == "seed":
        store = Store(settings.db_path)
        result = store.seed_history(
            posts=settings.posts,
            visas=settings.visa_classes,
            days=args.days,
            interval_minutes=args.interval_minutes,
        )
        print(json.dumps(result, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
