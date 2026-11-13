from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


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

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


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
    )


POST_LABELS = {
    "mumbai": "Mumbai",
    "new_delhi": "New Delhi",
    "chennai": "Chennai",
    "hyderabad": "Hyderabad",
    "kolkata": "Kolkata",
}
