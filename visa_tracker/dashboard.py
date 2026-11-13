from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .config import POST_LABELS, Settings, load_settings
from .db import Store


def _html_page(metrics: dict[str, Any], settings: Settings) -> str:
    prob_rows = "".join(
        f"<tr><td>{POST_LABELS.get(r['post_id'], r['post_id'])}</td>"
        f"<td>{r['visa_class_id']}</td><td>{r['checks']}</td>"
        f"<td>{r['with_slots']}</td><td>{r['p_available']:.2%}</td></tr>"
        for r in metrics.get("probability", [])
    ) or "<tr><td colspan='5'>No observations yet — run a check.</td></tr>"

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
    interval_s = f"{interval}s" if interval is not None else "n/a"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Visa Tracker Dashboard</title>
  <style>
    :root {{
      --bg: #0f1419;
      --panel: #171d25;
      --text: #e7ecf1;
      --muted: #9aa7b5;
      --line: #2a3440;
      --accent: #3d8bfd;
      --ok: #3ecf8e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text); padding: 28px;
    }}
    h1 {{ font-size: 1.6rem; margin: 0 0 6px; letter-spacing: -0.02em; }}
    .sub {{ color: var(--muted); margin-bottom: 22px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }}
    .stat {{ background: var(--panel); border: 1px solid var(--line); padding: 14px 16px; }}
    .stat .v {{ font-size: 1.45rem; font-weight: 600; }}
    .stat .l {{ color: var(--muted); font-size: 0.85rem; margin-top: 4px; }}
    section {{ margin-top: 22px; }}
    h2 {{ font-size: 1.05rem; margin: 0 0 10px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); }}
    th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); font-size: 0.9rem; }}
    th {{ color: var(--muted); font-weight: 500; }}
    .ok {{ color: var(--ok); }}
    a {{ color: var(--accent); }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
  </style>
</head>
<body>
  <h1>US Visa Appointment Tracker</h1>
  <p class="sub">H1B / H4 · India posts · collector=<b>{settings.collector}</b> · configured interval=<b>{settings.interval_sec}s</b></p>
  <div class="grid">
    <div class="stat"><div class="v">{metrics.get("checks_total", 0)}</div><div class="l">Checks total</div></div>
    <div class="stat"><div class="v">{interval_s}</div><div class="l">Observed interval (median)</div></div>
    <div class="stat"><div class="v">{metrics.get("check_success_rate", 0):.0%}</div><div class="l">Check success rate</div></div>
    <div class="stat"><div class="v">{last.get("status", "n/a")}</div><div class="l">Last check status</div></div>
  </div>

  <section>
    <h2>Availability probability (empirical)</h2>
    <table>
      <thead><tr><th>Post</th><th>Visa</th><th>Checks</th><th>With slots</th><th>p_available</th></tr></thead>
      <tbody>{prob_rows}</tbody>
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
    <p class="sub">JSON API: <a href="/api/metrics">/api/metrics</a> · Alert-first: book manually on usvisascheduling.com</p>
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
                metrics = store.metrics()
                html = _html_page(metrics, settings)
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
    Store(settings.db_path)  # ensure schema
    handler = make_handler(settings)
    server = ThreadingHTTPServer((settings.dashboard_host, settings.dashboard_port), handler)
    print(
        f"dashboard on http://{settings.dashboard_host}:{settings.dashboard_port}",
        flush=True,
    )
    server.serve_forever()
