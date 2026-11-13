from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import POST_LABELS, Settings
from .db import ObservationRow, Store


@dataclass
class AlertDecision:
    observation_id: int
    row: ObservationRow
    send: bool
    reason: str


def decide_alerts(
    store: Store,
    settings: Settings,
    observation_ids: list[int],
    rows: list[ObservationRow],
) -> list[AlertDecision]:
    from datetime import datetime, timedelta, timezone

    since = (
        datetime.now(timezone.utc) - timedelta(seconds=settings.alert_cooldown_sec)
    ).replace(microsecond=0).isoformat()

    decisions: list[AlertDecision] = []
    for obs_id, row in zip(observation_ids, rows):
        if row.slots_count <= 0:
            decisions.append(AlertDecision(obs_id, row, False, "empty"))
            continue
        if store.recent_alert_for_fingerprint(row.fingerprint, since):
            decisions.append(AlertDecision(obs_id, row, False, "cooldown"))
            continue
        decisions.append(AlertDecision(obs_id, row, True, "new_slots"))
    return decisions


def format_message(row: ObservationRow) -> str:
    post = POST_LABELS.get(row.post_id, row.post_id)
    earliest = row.earliest_date or "unknown date"
    return (
        f"US visa slots seen\n"
        f"Post: {post}\n"
        f"Visa: {row.visa_class_id}\n"
        f"Slots: {row.slots_count}\n"
        f"Earliest: {earliest}\n"
        f"Book now on usvisascheduling.com (manual login)."
    )


def send_telegram(settings: Settings, text: str) -> dict[str, Any]:
    if not settings.telegram_enabled:
        return {"ok": False, "skipped": True, "reason": "telegram_not_configured"}

    url = (
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    )
    payload = urllib.parse.urlencode(
        {
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"telegram HTTP {exc.code}: {detail}") from exc


def dispatch_alerts(
    store: Store,
    settings: Settings,
    decisions: list[AlertDecision],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for decision in decisions:
        if not decision.send:
            continue
        text = format_message(decision.row)
        payload = {
            "text": text,
            "fingerprint": decision.row.fingerprint,
            "reason": decision.reason,
        }
        try:
            if settings.telegram_enabled:
                tg = send_telegram(settings, text)
                ok = bool(tg.get("ok"))
                store.record_alert(
                    decision.observation_id,
                    channel="telegram",
                    status="sent" if ok else "error",
                    payload={"response": tg, **payload},
                )
                results.append({"observation_id": decision.observation_id, "ok": ok})
            else:
                store.record_alert(
                    decision.observation_id,
                    channel="log",
                    status="sent",
                    payload=payload,
                )
                results.append(
                    {
                        "observation_id": decision.observation_id,
                        "ok": True,
                        "channel": "log",
                        "text": text,
                    }
                )
        except Exception as exc:  # noqa: BLE001 — record and continue
            store.record_alert(
                decision.observation_id,
                channel="telegram" if settings.telegram_enabled else "log",
                status="error",
                payload={**payload, "error": str(exc)},
            )
            results.append(
                {"observation_id": decision.observation_id, "ok": False, "error": str(exc)}
            )
    return results
