from __future__ import annotations

import random
import re
from datetime import date, datetime, timezone
from typing import Any

from .collectors import CollectResult, fingerprint
from .config import POST_LABELS, Settings, load_auth_selectors
from .db import ObservationRow


class AuthDemoCollector:
    """
    Phase 2 stand-in for tests: behaves like an authenticated source without
    touching the real portal. Enable with collector=auth_demo.
    """

    source = "auth:demo"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def collect(self) -> CollectResult:
        hour = datetime.now(timezone.utc).hour
        # Sparse synthetic openings: Delhi H4 around UTC evening hours.
        open_now = hour in {13, 14, 15} and datetime.now(timezone.utc).minute < 20
        rows: list[ObservationRow] = []
        for post in self.settings.posts:
            for visa in self.settings.visa_classes:
                slots = 0
                earliest = None
                if open_now and post == "new_delhi" and visa == "H4":
                    slots = 1
                    earliest = date.today().isoformat()
                rows.append(
                    ObservationRow(
                        post_id=post,
                        visa_class_id=visa,
                        slots_count=slots,
                        earliest_date=earliest,
                        fingerprint=fingerprint(post, visa, slots, earliest),
                        raw={"auth_demo": True, "open_now": open_now},
                    )
                )
        return CollectResult(
            source=self.source,
            observations=rows,
            raw_meta={"mode": "auth_demo", "check_only": True},
        )


class AuthPortalCollector:
    """
    Phase 2 authenticated watcher for usvisascheduling.com.

    CHECK-ONLY: never clicks book / confirm / pay.
    Requires: playwright + VISA_TRACKER_AUTH_ENABLED=1 + username/password.
    Portal DOM drifts — tune VISA_TRACKER_AUTH_SELECTORS_JSON during testing.
    """

    source = "auth:portal"

    def __init__(self, settings: Settings) -> None:
        if not settings.auth_enabled:
            raise RuntimeError(
                "Auth collector disabled. Set VISA_TRACKER_AUTH_ENABLED=1 to enable "
                "(check-only; accept portal ToS/account risk)."
            )
        if not settings.auth_username or not settings.auth_password:
            raise RuntimeError(
                "VISA_TRACKER_USERNAME and VISA_TRACKER_PASSWORD are required for auth collector"
            )
        self.settings = settings
        self.selectors = load_auth_selectors()

    def collect(self) -> CollectResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright not installed. Run: pip install -r requirements-auth.txt "
                "&& playwright install chromium"
            ) from exc

        settings = self.settings
        settings.session_dir.mkdir(parents=True, exist_ok=True)
        observations: list[ObservationRow] = []
        page_dump_meta: dict[str, Any] = {"posts": {}, "check_only": True}

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(settings.session_dir),
                headless=settings.auth_headless,
                viewport={"width": 1280, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                self._ensure_logged_in(page)
                # Soft navigate toward scheduling area; never confirm a booking.
                self._goto_schedule_area(page)
                for post in settings.posts:
                    for visa in settings.visa_classes:
                        result = self._probe_post_visa(page, post, visa)
                        observations.append(result["row"])
                        page_dump_meta["posts"][f"{post}:{visa}"] = result["meta"]
            finally:
                context.close()

        return CollectResult(
            source=self.source,
            observations=observations,
            raw_meta=page_dump_meta,
        )

    def _ensure_logged_in(self, page) -> None:
        page.goto(f"{self.settings.auth_base_url}/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1000 + random.randint(0, 800))

        user_sel = self.selectors["username"]
        if page.locator(user_sel).count() > 0:
            page.locator(user_sel).first.fill(self.settings.auth_username)
            page.locator(self.selectors["password"]).first.fill(self.settings.auth_password)
            page.locator(self.selectors["login_submit"]).first.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1200)

        self._answer_security_questions(page)

    def _answer_security_questions(self, page) -> None:
        if not self.settings.security_qa:
            return
        # Best-effort: find visible question text and match configured answers.
        body = page.inner_text("body")
        for question, answer in self.settings.security_qa.items():
            if question and question.lower() in body.lower():
                ans = page.locator(self.selectors["security_answer"])
                if ans.count() > 0:
                    ans.first.fill(answer)
                    submit = page.locator(self.selectors["login_submit"])
                    if submit.count() > 0:
                        submit.first.click()
                        page.wait_for_load_state("domcontentloaded")
                        page.wait_for_timeout(800)
                break

    def _goto_schedule_area(self, page) -> None:
        nav = page.locator(self.selectors["schedule_nav"])
        if nav.count() > 0:
            # Click navigation only — still check-only afterward.
            nav.first.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1000)

    def _probe_post_visa(self, page, post: str, visa: str) -> dict[str, Any]:
        """
        Probe availability for one post × visa.

        Strategy: search visible text for the post label + visa, then detect
        either explicit 'no slots' language or date-like strings. This is
        intentionally conservative and returns zeros when unsure.
        """
        label = POST_LABELS.get(post, post)
        # Attempt to select post/visa controls if present (no booking confirm).
        for text in (label, post.replace("_", " "), visa, f"H-{visa[1:]}" if visa.startswith("H") else visa):
            loc = page.get_by_text(re.compile(re.escape(text), re.I))
            if loc.count() > 0:
                try:
                    loc.first.click(timeout=1500)
                    page.wait_for_timeout(600)
                except Exception:
                    pass

        body = page.inner_text("body")
        no_slots_re = re.compile(self.selectors["no_slots_text"], re.I)
        date_re = re.compile(self.selectors["date_like"], re.I)

        slots = 0
        earliest = None
        if no_slots_re.search(body):
            slots = 0
        else:
            dates = date_re.findall(body)
            # Filter out copyright/year-only noise already handled by regex.
            if dates:
                slots = len(dates)
                earliest = _normalize_date(dates[0])

        row = ObservationRow(
            post_id=post,
            visa_class_id=visa,
            slots_count=slots,
            earliest_date=earliest,
            fingerprint=fingerprint(post, visa, slots, earliest),
            raw={
                "post_label": label,
                "visa": visa,
                "detected_dates": date_re.findall(body)[:10],
                "no_slots_matched": bool(no_slots_re.search(body)),
            },
        )
        return {
            "row": row,
            "meta": {
                "slots": slots,
                "earliest": earliest,
                "body_chars": len(body),
            },
        }


def _normalize_date(raw: str) -> str:
    raw = raw.strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", raw):
        return raw
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw[:32]
