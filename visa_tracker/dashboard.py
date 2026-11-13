from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .config import POST_LABELS, Settings, load_settings
from .db import Store


def _bar_chart(values: list[float], labels: list[str], title: str) -> str:
    if not values:
        return f"<p class='sub'>{title}: no data yet</p>"
    width = 640
    height = 160
    pad = 24
    max_v = max(values) if max(values) > 0 else 1.0
    bar_w = max(4, int((width - 2 * pad) / max(len(values), 1)) - 2)
    bars = []
    for i, (v, label) in enumerate(zip(values, labels)):
        h = int((v / max_v) * (height - 40))
        x = pad + i * (bar_w + 2)
        y = height - 20 - h
        bars.append(
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h}" fill="#3d8bfd"></rect>'
            f'<title>{label}: {v:.2%}</title>'
        )
    return f"""
    <div class="chart">
      <div class="chart-title">{title}</div>
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{title}">
        {''.join(bars)}
      </svg>
    </div>
    """


def _html_page(metrics: dict[str, Any], settings: Settings) -> str:
    prob_rows = "".join(
        f"<tr><td>{POST_LABELS.get(r['post_id'], r['post_id'])}</td>"
        f"<td>{r['visa_class_id']}</td><td>{r['checks']}</td>"
        f"<td>{r['with_slots']}</td><td>{r['p_available']:.2%}</td></tr>"
        for r in metrics.get("probability", [])
    ) or "<tr><td colspan='5'>No observations yet — run a check or seed.</td></tr>"

    hour_map = {int(r["hour_utc"]): float(r["p_available"]) for r in metrics.get("probability_by_hour_utc", [])}
    hour_vals = [hour_map.get(h, 0.0) for h in range(24)]
    hour_labels = [f"{h:02d}:00 UTC" for h in range(24)]
    hour_chart = _bar_chart(hour_vals, hour_labels, "p_available by hour (UTC)")

    day_rows = metrics.get("probability_by_day", [])[-14:]
    day_chart = _bar_chart(
        [float(r["p_available"]) for r in day_rows],
        [str(r["day"]) for r in day_rows],
        "p_available by day (last 14)",
    )

    timeline = metrics.get("timeline_hourly", [])[-48:]
    timeline_rows = "".join(
        f"<tr><td>{t['hour_bucket']}</td><td>{t['observations']}</td>"
        f"<td>{t['openings']}</td><td>{t['slots_sum']}</td></tr>"
        for t in timeline
    ) or "<tr><td colspan='4'>None</td></tr>"

    source_rows = "".join(
        f"<tr><td>{s['source']}</td><td>{s['checks']}</td><td>{s['ok']}</td></tr>"
        for s in metrics.get("checks_by_source", [])
    ) or "<tr><td colspan='3'>None</td></tr>"

    life_rows = "".join(
        f"<tr><td>{l['fingerprint']}</td><td>{l['sightings']}</td>"
        f"<td>{l['lifetime_sec']}s</td><td>{l['first_seen']}</td><td>{l['last_seen']}</td></tr>"
        for l in metrics.get("slot_lifetimes", [])[:15]
    ) or "<tr><td colspan='5'>No openings recorded</td></tr>"

    obs_rows = "".join(
        f"<tr><td>{o['observed_at']}</td><td>{o['post_id']}</td>"
        f"<td>{o['visa_class_id']}</td><td>{o['slots_count']}</td>"
        f"<td>{o['earliest_date'] or '-'}</td></tr>"
        for o in metrics.get("recent_observations", [])
    ) or "<tr><td colspan='5'>None</td></tr>"

    alert_rows = "".join(
        f"<tr><td>{a['sent_at'] or '-'}</td><td>{a['channel']}</td>"
        f"<td>{a['status']}</td><td>{a['post_id']}</td>"
        f"<td>{a['visa_class_id']}</td><td>{a['slots_count']}</td></tr>"
        for a in metrics.get("recent_alerts", [])
    ) or "<tr><td colspan='6'>None</td></tr>"

    last = metrics.get("last_check") or {}
    interval = metrics.get("observed_interval_sec_median")
    p95 = metrics.get("observed_interval_sec_p95")
    interval_s = f"{interval}s" if interval is not None else "n/a"
    p95_s = f"{p95}s" if p95 is not None else "n/a"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="60" />
  <title>Visa Tracker Dashboard</title>
  <style>
    :root {{
      --bg: #0f1419; --panel: #171d25; --text: #e7ecf1;
      --muted: #9aa7b5; --line: #2a3440; --accent: #3d8bfd; --ok: #3ecf8e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: var(--bg); color: var(--text); padding: 28px;
    }}
    h1 {{ font-size: 1.6rem; margin: 0 0 6px; letter-spacing: -0.02em; }}
    .sub {{ color: var(--muted); margin-bottom: 22px; }}
    .grid {{ display: grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: 12px; }}
    .stat {{ background: var(--panel); border: 1px solid var(--line); padding: 14px 16px; }}
    .stat .v {{ font-size: 1.35rem; font-weight: 600; }}
    .stat .l {{ color: var(--muted); font-size: 0.85rem; margin-top: 4px; }}
    section {{ margin-top: 22px; }}
    h2 {{ font-size: 1.05rem; margin: 0 0 10px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); }}
    th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); font-size: 0.9rem; }}
    th {{ color: var(--muted); font-weight: 500; }}
    a {{ color: var(--accent); }}
    .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .chart {{ background: var(--panel); border: 1px solid var(--line); padding: 12px; }}
    .chart-title {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 8px; }}
    .warn {{ color: #e2b340; font-size: 0.9rem; }}
    @media (max-width: 980px) {{
      .grid {{ grid-template-columns: 1fr 1fr; }}
      .charts {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <h1>US Visa Appointment Tracker</h1>
  <p class="sub">Phases 1–3 · H1B/H4 · India · collector=<b>{settings.collector}</b> · interval=<b>{settings.interval_sec}s</b> · auth_interval=<b>{settings.auth_interval_sec}s</b></p>
  <p class="warn">Alert-first / check-only. Book manually on usvisascheduling.com. Auth portal automation is optional and feature-flagged.</p>
  <div class="grid">
    <div class="stat"><div class="v">{metrics.get("checks_total", 0)}</div><div class="l">Checks total</div></div>
    <div class="stat"><div class="v">{interval_s}</div><div class="l">Interval median</div></div>
    <div class="stat"><div class="v">{p95_s}</div><div class="l">Interval p95</div></div>
    <div class="stat"><div class="v">{metrics.get("check_success_rate", 0):.0%}</div><div class="l">Success rate</div></div>
    <div class="stat"><div class="v">{last.get("status", "n/a")}</div><div class="l">Last check</div></div>
  </div>

  <section>
    <h2>Phase 3 analytics</h2>
    <div class="charts">
      {hour_chart}
      {day_chart}
    </div>
  </section>

  <section>
    <h2>Availability probability by post × visa</h2>
    <table>
      <thead><tr><th>Post</th><th>Visa</th><th>Checks</th><th>With slots</th><th>p_available</th></tr></thead>
      <tbody>{prob_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Checks by source</h2>
    <table>
      <thead><tr><th>Source</th><th>Checks</th><th>OK</th></tr></thead>
      <tbody>{source_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Hourly timeline (recent)</h2>
    <table>
      <thead><tr><th>Hour (UTC)</th><th>Observations</th><th>Openings</th><th>Slots sum</th></tr></thead>
      <tbody>{timeline_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Slot lifetimes</h2>
    <table>
      <thead><tr><th>Fingerprint</th><th>Sightings</th><th>Lifetime</th><th>First</th><th>Last</th></tr></thead>
      <tbody>{life_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Recent observations</h2>
    <table>
      <thead><tr><th>When (UTC)</th><th>Post</th><th>Visa</th><th>Slots</th><th>Earliest</th></tr></thead>
      <tbody>{obs_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Recent alerts</h2>
    <table>
      <thead><tr><th>Sent</th><th>Channel</th><th>Status</th><th>Post</th><th>Visa</th><th>Slots</th></tr></thead>
      <tbody>{alert_rows}</tbody>
    </table>
  </section>

  <section>
    <p class="sub">API: <a href="/api/metrics">/api/metrics</a></p>
  </section>
</body>
</html>
"""


def make_handler(settings: Settings):
    store = Store(settings.db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            print("%s - %s" % (self.address_string(), fmt % args), flush=True)

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                html = _html_page(store.metrics(), settings)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/metrics":
                payload = json.dumps(store.metrics(), indent=2).encode("utf-8")
                self._send(200, payload, "application/json")
                return
            if path == "/healthz":
                self._send(200, b'{"ok":true}', "application/json")
                return
            self._send(404, b"not found", "text/plain")

    return Handler


def serve(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    Store(settings.db_path)
    handler = make_handler(settings)
    server = ThreadingHTTPServer((settings.dashboard_host, settings.dashboard_port), handler)
    print(
        f"dashboard on http://{settings.dashboard_host}:{settings.dashboard_port}",
        flush=True,
    )
    server.serve_forever()
