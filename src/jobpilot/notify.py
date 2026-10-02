"""Notification service for JobPilot.

Implements multi-channel alerting:
- Console summary (always)
- Desktop notification (optional, via notifications.desktop, fails silently if unsupported)
- Telegram outbound (optional, via notifications.telegram + TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)

Triggers:
- End of run_daily (new jobs, queued, Tier A count, needs-JD count, stage failures including auth failures)
- Follow-ups due
Notifications never include contact details or full JDs.
"""

from __future__ import annotations

import logging
import platform
import subprocess

import httpx

from jobpilot.config import AppConfig, EnvSettings, load_config, load_env
from jobpilot.llm.redact import redact_text

logger = logging.getLogger(__name__)


def send_desktop_notification(title: str, message: str) -> bool:
    """Send a desktop notification using plyer if installed, or native OS tool with no shell. Fail silently if unsupported."""
    # 1. Try plyer if available
    try:
        import plyer  # type: ignore

        plyer.notification.notify(title=title, message=message, app_name="JobPilot")
        return True
    except Exception:
        pass

    # 2. Native OS fallback with argument list and no shell
    system = platform.system()
    try:
        if system == "Darwin":
            cmd = [
                "osascript",
                "-e",
                "on run argv",
                "-e",
                "display notification (item 1 of argv) with title (item 2 of argv)",
                "-e",
                "end run",
                message,
                title,
            ]
            subprocess.run(cmd, check=False, capture_output=True, shell=False)
            return True
        elif system == "Linux":
            cmd = ["notify-send", title, message]
            subprocess.run(cmd, check=False, capture_output=True, shell=False)
            return True
    except Exception:
        pass
    return False


def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    """Send an outbound Telegram message via Bot API."""
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=10.0)
        return resp.is_success
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)
        return False


def send_console_summary(title: str, message: str) -> bool:
    """Output notification summary to console. Always active."""
    logger.info("[NOTIFICATION] %s: %s", title, message)
    print(f"\n📢 [{title}]\n{message}")
    return True


class Notifier:
    """Multi-channel notifier honoring configuration and privacy rules."""

    def __init__(self, config: AppConfig | None = None, env: EnvSettings | None = None):
        self.config = config or load_config()
        self.env = env or load_env()

    def notify(self, title: str, message: str) -> dict[str, bool]:
        """Dispatch notification across configured channels with redaction."""
        safe_title = redact_text(title)
        safe_message = redact_text(message)

        results: dict[str, bool] = {}

        # 1. Console (always)
        results["console"] = send_console_summary(safe_title, safe_message)

        # 2. Desktop notification (optional)
        if self.config.notifications.desktop:
            results["desktop"] = send_desktop_notification(safe_title, safe_message)

        # 3. Telegram (optional, outbound only)
        if (
            self.config.notifications.telegram
            and self.env.telegram_bot_token
            and self.env.telegram_chat_id
        ):
            full_text = f"*{safe_title}*\n\n{safe_message}"
            results["telegram"] = send_telegram_message(
                self.env.telegram_bot_token,
                self.env.telegram_chat_id,
                full_text,
            )

        return results

    def send_run_daily_summary(
        self,
        new_jobs: int = 0,
        queued_count: int = 0,
        tier_a_count: int = 0,
        needs_jd_count: int = 0,
        stage_failures: list[tuple[str, str]] | None = None,
    ) -> dict[str, bool]:
        """Trigger notification at the end of run_daily with counts and stage names only."""
        title = "JobPilot Daily Run Complete"
        lines = [
            f"• New jobs discovered: {new_jobs}",
            f"• Daily queue: {queued_count} (Tier A: {tier_a_count})",
            f"• Needs JD: {needs_jd_count}",
        ]
        if stage_failures:
            lines.append("\n⚠️ Stage Failures:")
            for stage_name, _ in stage_failures:
                lines.append(f"• {stage_name}")

        message = "\n".join(lines)
        return self.notify(title, message)

    def send_follow_ups_due(self, count: int, jobs_summary: list[str] | None = None) -> dict[str, bool]:
        """Trigger notification when applications are due for follow-up (counts only)."""
        title = f"JobPilot: {count} Application Follow-Up(s) Due"
        message = f"You have {count} application(s) awaiting response past the threshold."
        return self.notify(title, message)
