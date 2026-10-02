"""Tests for JobPilot notification system (jobpilot.notify)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import respx

from jobpilot.config import AppConfig, EnvSettings, NotificationsConfig
from jobpilot.notify import (
    Notifier,
    send_desktop_notification,
    send_telegram_message,
)


class TestNotifyChannels:
    def test_console_summary_always_sent(self, capsys: pytest.CaptureFixture) -> None:
        """Console notification channel is always executed."""
        cfg = AppConfig(notifications=NotificationsConfig(desktop=False, telegram=False))
        notifier = Notifier(config=cfg)

        res = notifier.notify("Test Alert", "JobPilot test body")
        assert res["console"] is True
        assert "desktop" not in res
        assert "telegram" not in res

        captured = capsys.readouterr()
        assert "Test Alert" in captured.out
        assert "JobPilot test body" in captured.out

    def test_desktop_notification_enabled_and_disabled(self) -> None:
        """Desktop notification only fires when enabled in config."""
        # 1. Disabled
        cfg_disabled = AppConfig(notifications=NotificationsConfig(desktop=False))
        notifier_disabled = Notifier(config=cfg_disabled)
        with patch("jobpilot.notify.send_desktop_notification") as mock_desk:
            res = notifier_disabled.notify("Title", "Message")
            assert "desktop" not in res
            mock_desk.assert_not_called()

        # 2. Enabled
        cfg_enabled = AppConfig(notifications=NotificationsConfig(desktop=True))
        notifier_enabled = Notifier(config=cfg_enabled)
        with patch("jobpilot.notify.send_desktop_notification", return_value=True) as mock_desk:
            res = notifier_enabled.notify("Title", "Message")
            assert res.get("desktop") is True
            mock_desk.assert_called_once_with("Title", "Message")

    @respx.mock
    def test_telegram_notification_enabled_and_disabled(self) -> None:
        """Telegram outbound message fires when enabled with credentials, else silent."""
        bot_token = "mock-bot-token"
        chat_id = "12345678"

        # 1. Disabled in config
        cfg_disabled = AppConfig(notifications=NotificationsConfig(telegram=False))
        env = EnvSettings(telegram_bot_token=bot_token, telegram_chat_id=chat_id)
        notifier_disabled = Notifier(config=cfg_disabled, env=env)
        res_dis = notifier_disabled.notify("Title", "Message")
        assert "telegram" not in res_dis

        # 2. Enabled in config, but missing env credentials
        cfg_enabled = AppConfig(notifications=NotificationsConfig(telegram=True))
        env_empty = EnvSettings(telegram_bot_token="", telegram_chat_id="")
        notifier_no_creds = Notifier(config=cfg_enabled, env=env_empty)
        res_no_creds = notifier_no_creds.notify("Title", "Message")
        assert "telegram" not in res_no_creds

        # 3. Enabled with credentials -> sends HTTP POST to Telegram Bot API
        telegram_route = respx.post(f"https://api.telegram.org/bot{bot_token}/sendMessage").respond(
            status_code=200, json={"ok": True}
        )
        notifier_enabled = Notifier(config=cfg_enabled, env=env)
        res_enabled = notifier_enabled.notify("Title", "Message")
        assert res_enabled.get("telegram") is True
        assert telegram_route.called

        # Verify sent payload
        req = telegram_route.calls.last.request
        import json
        body = json.loads(req.content.decode("utf-8"))
        assert body["chat_id"] == chat_id
        assert "*Title*" in body["text"]
        assert "Message" in body["text"]


class TestNotifyRedaction:
    def test_contact_details_are_redacted_in_notifications(self) -> None:
        """Notifications never leak candidate contact details (emails, phones)."""
        cfg = AppConfig(notifications=NotificationsConfig(desktop=True, telegram=False))
        notifier = Notifier(config=cfg)

        raw_title = "Applied for John Doe (john.doe@example.com)"
        raw_msg = "Contact phone: +1 555-123-4567, address: 123 Main Street"

        with patch("jobpilot.notify.send_desktop_notification") as mock_desk:
            notifier.notify(raw_title, raw_msg)
            mock_desk.assert_called_once()
            call_title, call_msg = mock_desk.call_args[0]

            assert "john.doe@example.com" not in call_title
            assert "[REDACTED_EMAIL]" in call_title
            assert "+1 555-123-4567" not in call_msg
            assert "[REDACTED_PHONE]" in call_msg


class TestNotificationTriggers:
    def test_send_run_daily_summary_success(self) -> None:
        """send_run_daily_summary formats counts and reports cleanly."""
        cfg = AppConfig(notifications=NotificationsConfig(desktop=False, telegram=False))
        notifier = Notifier(config=cfg)

        with patch.object(notifier, "notify", return_value={"console": True}) as mock_notify:
            res = notifier.send_run_daily_summary(
                new_jobs=4,
                queued_count=10,
                tier_a_count=3,
                needs_jd_count=1,
                stage_failures=None,
            )
            assert res == {"console": True}
            mock_notify.assert_called_once()
            title, msg = mock_notify.call_args[0]
            assert "Daily Run Complete" in title
            assert "New jobs discovered: 4" in msg
            assert "Daily queue: 10 (Tier A: 3)" in msg
            assert "Needs JD: 1" in msg
            assert "Stage Failures" not in msg

    def test_send_run_daily_summary_with_stage_failures(self) -> None:
        """send_run_daily_summary includes stage failures by stage name."""
        cfg = AppConfig(notifications=NotificationsConfig(desktop=False, telegram=False))
        notifier = Notifier(config=cfg)

        failures = [
            ("ingest-alerts", "Auth failed (token expired)"),
            ("fetch", "Connection timeout"),
        ]
        with patch.object(notifier, "notify", return_value={"console": True}) as mock_notify:
            notifier.send_run_daily_summary(
                new_jobs=0,
                queued_count=5,
                tier_a_count=1,
                needs_jd_count=0,
                stage_failures=failures,
            )
            title, msg = mock_notify.call_args[0]
            assert "Stage Failures" in msg
            assert "ingest-alerts" in msg
            assert "fetch" in msg

    def test_send_follow_ups_due(self) -> None:
        """send_follow_ups_due notifies with counts only."""
        cfg = AppConfig(notifications=NotificationsConfig(desktop=False, telegram=False))
        notifier = Notifier(config=cfg)

        with patch.object(notifier, "notify", return_value={"console": True}) as mock_notify:
            notifier.send_follow_ups_due(count=2)
            mock_notify.assert_called_once()
            title, msg = mock_notify.call_args[0]
            assert "2 Application Follow-Up(s) Due" in title
            assert "2 application(s)" in msg

    def test_notify_never_includes_job_titles_or_companies(self) -> None:
        """Notifications strictly contain counts and stage names, never company names or job titles."""
        cfg = AppConfig(notifications=NotificationsConfig(desktop=False, telegram=False))
        notifier = Notifier(config=cfg)

        with patch.object(notifier, "notify") as mock_notify:
            # 1. run_daily summary
            notifier.send_run_daily_summary(
                new_jobs=3,
                queued_count=7,
                tier_a_count=2,
                needs_jd_count=1,
                stage_failures=[("fetch", "error fetching from Greenhouse for Stripe")],
            )
            call_title, call_msg = mock_notify.call_args[0]
            forbidden = ["Stripe", "Greenhouse", "Software Engineer", "Backend Developer"]
            for term in forbidden:
                assert term not in call_title
                assert term not in call_msg
            assert "fetch" in call_msg

            # 2. follow_ups_due
            notifier.send_follow_ups_due(count=5, jobs_summary=["Staff Dev at Netflix", "Lead at Google"])
            call_title_fu, call_msg_fu = mock_notify.call_args[0]
            forbidden_fu = ["Netflix", "Google", "Staff Dev", "Lead"]
            for term in forbidden_fu:
                assert term not in call_title_fu
                assert term not in call_msg_fu
            assert "5" in call_title_fu
            assert "5" in call_msg_fu


class TestDesktopNotificationFallback:
    def test_send_desktop_notification_fails_silently_on_error(self) -> None:
        """send_desktop_notification catches exceptions and returns False."""
        with (
            patch("subprocess.run", side_effect=OSError("Command not found")),
            patch.dict("sys.modules", {"plyer": None}),
        ):
            ok = send_desktop_notification("Title", "Message")
            assert ok is False

    def test_send_telegram_fails_silently_on_error(self) -> None:
        """send_telegram_message catches httpx errors and returns False."""
        with patch("httpx.post", side_effect=Exception("Network down")):
            ok = send_telegram_message("token", "chat", "msg")
            assert ok is False
