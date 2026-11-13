from __future__ import annotations

import argparse
import json
import sys

from .config import load_settings
from .dashboard import serve
from .runner import run_loop, run_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="US visa appointment tracker (Phase 1)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("once", help="Run a single availability check")
    sub.add_parser("loop", help="Run checks forever at VISA_TRACKER_INTERVAL_SEC")
    sub.add_parser("serve", help="Serve the local metrics dashboard")

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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
