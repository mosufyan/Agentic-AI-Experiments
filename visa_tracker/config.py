from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _parse_security_qa(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("VISA_TRACKER_SECURITY_QA must be a JSON object of question->answer")
    return {str(k).strip().lower(): str(v) for k, v in data.items()}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    collector: str
    feed_url: str
    feed_api_key: str
    interval_sec: int
    posts: tuple[str, ...]
    visa_classes: tuple[str, ...]
    telegram_bot_token: str
    telegram_chat_id: str
    alert_cooldown_sec: int
    dashboard_host: str
    dashboard_port: int
    # Phase 2 — authenticated watcher (check-only)
    auth_enabled: bool
    auth_username: str
    auth_password: str
    security_qa: dict[str, str] = field(default_factory=dict)
    auth_base_url: str = "https://www.usvisascheduling.com"
    auth_headless: bool = True
    auth_interval_sec: int = 900
    auth_jitter_sec: int = 120
    session_dir: Path = field(default_factory=lambda: Path("data/browser_session"))

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def collectors(self) -> tuple[str, ...]:
        return tuple(c.strip().lower() for c in self.collector.split(",") if c.strip())


def load_settings() -> Settings:
    root = Path(_env("VISA_TRACKER_DATA_DIR", str(Path.cwd() / "data")))
    posts = tuple(
        p.strip().lower()
        for p in _env(
            "VISA_TRACKER_POSTS",
            "mumbai,new_delhi,chennai,hyderabad,kolkata",
        ).split(",")
        if p.strip()
    )
    visas = tuple(
        v.strip().upper()
        for v in _env("VISA_TRACKER_VISAS", "H1B,H4").split(",")
        if v.strip()
    )
    qa_raw = _env("VISA_TRACKER_SECURITY_QA", "{}")
    try:
        security_qa = _parse_security_qa(qa_raw)
    except json.JSONDecodeError as exc:
        raise ValueError("VISA_TRACKER_SECURITY_QA must be valid JSON") from exc

    return Settings(
        data_dir=root,
        db_path=root / "tracker.db",
        collector=_env("VISA_TRACKER_COLLECTOR", "demo").lower(),
        feed_url=_env("VISA_TRACKER_FEED_URL"),
        feed_api_key=_env("VISA_TRACKER_FEED_API_KEY"),
        interval_sec=max(60, _env_int("VISA_TRACKER_INTERVAL_SEC", 300)),
        posts=posts,
        visa_classes=visas,
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        alert_cooldown_sec=_env_int("VISA_TRACKER_ALERT_COOLDOWN_SEC", 900),
        dashboard_host=_env("VISA_TRACKER_DASHBOARD_HOST", "127.0.0.1"),
        dashboard_port=_env_int("VISA_TRACKER_DASHBOARD_PORT", 8080),
        auth_enabled=_env_bool("VISA_TRACKER_AUTH_ENABLED", False),
        auth_username=_env("VISA_TRACKER_USERNAME"),
        auth_password=_env("VISA_TRACKER_PASSWORD"),
        security_qa=security_qa,
        auth_base_url=_env("VISA_TRACKER_AUTH_BASE_URL", "https://www.usvisascheduling.com").rstrip(
            "/"
        ),
        auth_headless=_env_bool("VISA_TRACKER_AUTH_HEADLESS", True),
        auth_interval_sec=max(600, _env_int("VISA_TRACKER_AUTH_INTERVAL_SEC", 900)),
        auth_jitter_sec=max(0, _env_int("VISA_TRACKER_AUTH_JITTER_SEC", 120)),
        session_dir=root / "browser_session",
    )


POST_LABELS = {
    "mumbai": "Mumbai",
    "new_delhi": "New Delhi",
    "chennai": "Chennai",
    "hyderabad": "Hyderabad",
    "kolkata": "Kolkata",
}

# Selectors are overridable via JSON env for portal UI drift during testing.
DEFAULT_AUTH_SELECTORS: dict[str, Any] = {
    "username": 'input[name="username"], input[type="email"], #username, input[name="email"]',
    "password": 'input[name="password"], input[type="password"], #password',
    "login_submit": 'button[type="submit"], input[type="submit"], button:has-text("Sign In"), button:has-text("Log In")',
    "security_question": "label, .security-question, [data-testid='security-question']",
    "security_answer": 'input[name*="answer" i], input[type="text"]',
    "schedule_nav": 'a:has-text("Schedule"), a:has-text("Reschedule"), text=Schedule Appointment',
    "no_slots_text": "no appointments are available|no appointment slots|currently no available",
    "date_like": r"\b20\d{2}-\d{2}-\d{2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2}\b",
}


def load_auth_selectors() -> dict[str, Any]:
    raw = _env("VISA_TRACKER_AUTH_SELECTORS_JSON")
    merged = dict(DEFAULT_AUTH_SELECTORS)
    if raw:
        override = json.loads(raw)
        if not isinstance(override, dict):
            raise ValueError("VISA_TRACKER_AUTH_SELECTORS_JSON must be a JSON object")
        merged.update(override)
    return merged
