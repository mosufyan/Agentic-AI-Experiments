from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from .config import Settings
from .db import ObservationRow


@dataclass
class CollectResult:
    source: str
    observations: list[ObservationRow]
    raw_meta: dict[str, Any]


def fingerprint(post_id: str, visa: str, slots: int, earliest: str | None) -> str:
    return f"{post_id}|{visa}|{slots}|{earliest or '-'}"


class DemoCollector:
    """Deterministic offline collector so the app is testable without external feeds."""

    source = "demo"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def collect(self) -> CollectResult:
        # Simulate rare openings: every ~12th minute-of-hour for Mumbai H1B only.
        minute = datetime.now(timezone.utc).minute
        open_now = minute % 12 == 0
        rows: list[ObservationRow] = []
        for post in self.settings.posts:
            for visa in self.settings.visa_classes:
                slots = 0
                earliest = None
                if open_now and post == "mumbai" and visa == "H1B":
                    slots = 2
                    earliest = date.today().isoformat()
                rows.append(
                    ObservationRow(
                        post_id=post,
                        visa_class_id=visa,
                        slots_count=slots,
                        earliest_date=earliest,
                        fingerprint=fingerprint(post, visa, slots, earliest),
                        raw={"demo": True, "open_now": open_now},
                    )
                )
        return CollectResult(source=self.source, observations=rows, raw_meta={"mode": "demo"})


class HttpJsonCollector:
    """
    Generic JSON feed adapter.

    Expected JSON shapes (first match wins):
      {"slots":[{"post":"mumbai","visa":"H1B","slots_count":1,"earliest_date":"2026-12-01"}, ...]}
      {"data":[...]}  # same object fields
      [ ... ]         # bare list

    Field aliases accepted: post|location|post_id, visa|visa_class|visa_class_id,
    slots_count|slots|count, earliest_date|earliest|date.
    """

    source = "public:http_json"

    def __init__(self, settings: Settings) -> None:
        if not settings.feed_url:
            raise ValueError("VISA_TRACKER_FEED_URL is required for http_json collector")
        self.settings = settings

    def collect(self) -> CollectResult:
        req = urllib.request.Request(
            self.settings.feed_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "visa-tracker/0.1 (+personal-monitor)",
            },
            method="GET",
        )
        if self.settings.feed_api_key:
            req.add_header("Authorization", f"Bearer {self.settings.feed_api_key}")
            req.add_header("X-Api-Key", self.settings.feed_api_key)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"feed HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"feed network error: {exc}") from exc

        payload = json.loads(body)
        items = _extract_items(payload)
        wanted_posts = set(self.settings.posts)
        wanted_visas = set(self.settings.visa_classes)
        rows: list[ObservationRow] = []
        seen: set[tuple[str, str]] = set()

        for item in items:
            post = _norm_post(_pick(item, "post", "location", "post_id", "consulate"))
            visa = _norm_visa(_pick(item, "visa", "visa_class", "visa_class_id", "category"))
            if not post or not visa:
                continue
            if post not in wanted_posts or visa not in wanted_visas:
                continue
            key = (post, visa)
            if key in seen:
                continue
            seen.add(key)
            slots = int(_pick(item, "slots_count", "slots", "count") or 0)
            earliest = _pick(item, "earliest_date", "earliest", "date")
            if earliest is not None:
                earliest = str(earliest)[:10]
            rows.append(
                ObservationRow(
                    post_id=post,
                    visa_class_id=visa,
                    slots_count=slots,
                    earliest_date=earliest if slots > 0 else None,
                    fingerprint=fingerprint(post, visa, slots, earliest if slots > 0 else None),
                    raw=item,
                )
            )

        # Ensure every configured post×visa appears (zeros) so probability stays honest.
        for post in self.settings.posts:
            for visa in self.settings.visa_classes:
                if (post, visa) in seen:
                    continue
                rows.append(
                    ObservationRow(
                        post_id=post,
                        visa_class_id=visa,
                        slots_count=0,
                        earliest_date=None,
                        fingerprint=fingerprint(post, visa, 0, None),
                        raw={"inferred_empty": True},
                    )
                )

        return CollectResult(
            source=self.source,
            observations=rows,
            raw_meta={"item_count": len(items), "url": self.settings.feed_url},
        )


def get_collector(settings: Settings):
    if settings.collector in {"demo", "mock"}:
        return DemoCollector(settings)
    if settings.collector in {"http_json", "http", "json"}:
        return HttpJsonCollector(settings)
    raise ValueError(
        f"Unknown collector '{settings.collector}'. Use 'demo' or 'http_json'."
    )


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("slots", "data", "results", "appointments", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        # Single object
        if any(k in payload for k in ("post", "location", "visa", "visa_class")):
            return [payload]
    return []


def _pick(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
        # case-insensitive
        for k, v in item.items():
            if k.lower() == key.lower() and v is not None:
                return v
    return None


def _norm_post(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "delhi": "new_delhi",
        "newdelhi": "new_delhi",
        "new_delhi_consular": "new_delhi",
        "mumbai_consular": "mumbai",
        "chennai_consular": "chennai",
        "hyderabad_consular": "hyderabad",
        "kolkata_consular": "kolkata",
        "calcutta": "kolkata",
    }
    s = aliases.get(s, s)
    for prefix in ("consular_", "vac_"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    if s.endswith("_consular"):
        s = s[: -len("_consular")]
    if s.endswith("_vac"):
        s = s[: -len("_vac")]
    return s


def _norm_visa(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip().upper().replace("-", "").replace(" ", "")
    if s in {"H1", "H1B", "H1BCO", "H1BTCN"}:
        return "H1B"
    if s in {"H4", "H4CO", "H4TCN"}:
        return "H4"
    return s
