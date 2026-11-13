from __future__ import annotations

import random
import time
from typing import Any

from .alerter import decide_alerts, dispatch_alerts
from .collectors import get_collector
from .config import Settings, load_settings
from .db import Store


def run_once(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    store = Store(settings.db_path)
    check_id = store.start_check(settings.collector)
    started = time.perf_counter()

    try:
        collector = get_collector(settings)
        # Update source now that collector is known.
        with store.connect() as conn:
            conn.execute(
                "UPDATE checks SET source = ? WHERE id = ?",
                (getattr(collector, "source", settings.collector), check_id),
            )
        result = collector.collect()
        obs_ids = store.insert_observations(check_id, result.observations)
        decisions = decide_alerts(store, settings, obs_ids, result.observations)
        alert_results = dispatch_alerts(store, settings, decisions)
        duration_ms = int((time.perf_counter() - started) * 1000)
        store.finish_check(check_id, status="ok", error_summary=None, duration_ms=duration_ms)
        open_rows = [r for r in result.observations if r.slots_count > 0]
        return {
            "ok": True,
            "check_id": check_id,
            "source": result.source,
            "observations": len(result.observations),
            "openings": len(open_rows),
            "alerts": alert_results,
            "duration_ms": duration_ms,
        }
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.perf_counter() - started) * 1000)
        store.finish_check(
            check_id, status="error", error_summary=str(exc)[:500], duration_ms=duration_ms
        )
        return {"ok": False, "check_id": check_id, "error": str(exc), "duration_ms": duration_ms}


def _sleep_seconds(settings: Settings) -> int:
    names = set(settings.collectors)
    authish = bool(names & {"auth", "auth_portal", "portal"})
    base = settings.auth_interval_sec if authish and settings.auth_enabled else settings.interval_sec
    jitter = settings.auth_jitter_sec if authish else min(30, base // 10)
    return base + random.randint(0, jitter)


def run_loop(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    print(
        f"visa_tracker loop: collector={settings.collector} "
        f"interval={settings.interval_sec}s auth_interval={settings.auth_interval_sec}s "
        f"db={settings.db_path}",
        flush=True,
    )
    while True:
        result = run_once(settings)
        print(result, flush=True)
        delay = _sleep_seconds(settings)
        print(f"sleeping {delay}s", flush=True)
        time.sleep(delay)
