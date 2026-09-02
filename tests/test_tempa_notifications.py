from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import Mock

import tempa_config
import tempa_notifications as tn

_SETTINGS_FORM_JS = Path(__file__).resolve().parents[1] / "src" / "assets" / "js" / "80-settings-form.js"


def test_every_event_type_is_renderable_in_the_settings_form():
    """Load-bearing, not cosmetic: selectedEmailEvents() collects only the checkboxes the
    form actually rendered, so an event type missing from EMAIL_ALERT_EVENTS is silently
    stripped out of config.json on the user's next Settings save — even if they had it
    enabled."""
    js = _SETTINGS_FORM_JS.read_text(encoding="utf-8")
    block = js.split("const EMAIL_ALERT_EVENTS = [", 1)[1].split("];", 1)[0]
    rendered = set(re.findall(r'\["([a-z_]+)"', block))
    missing = {event.value for event in tn.AttentionEventType} - rendered
    assert not missing, f"event types the Settings form cannot render: {sorted(missing)}"


def test_default_enabled_events_are_all_real_event_types():
    """DEFAULT_EMAIL_NOTIFICATION_EVENTS is a curated subset (purely informational events
    are off by default), but every name in it still has to be one that exists."""
    known = {event.value for event in tn.AttentionEventType}
    assert set(tempa_config.DEFAULT_EMAIL_NOTIFICATION_EVENTS) <= known


def _settings(**overrides):
    base = {
        "enabled": True, "smtp_host": "smtp.example.test", "smtp_port": 587,
        "security": "starttls", "from": "tempa@example.test", "recipients": ["owner@example.test"],
        "username_env": "TEMPA_SMTP_USERNAME", "password_env": "TEMPA_SMTP_PASSWORD",
        "timeout_seconds": 10, "events": list(tn.DEFAULT_ENABLED_EVENTS),
    }
    base.update(overrides)
    return base


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(tn, "outbox_path", lambda: tmp_path / "notification-outbox.json")
    monkeypatch.setattr(tn, "_smtp_settings", lambda: _settings())
    monkeypatch.setattr(tn, "get_workspace", lambda config: {"root": str(tmp_path / "workspace")})
    monkeypatch.setattr(tn, "load_config", lambda: {})


def test_attention_event_is_queued_sent_and_deduplicated(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    send = Mock()
    monkeypatch.setattr(tn, "_send", send)

    assert tn.notify_attention(tn.AttentionEventType.IMPLEMENTATION_FAILED, "Implementation", "EPIC-01 failed", "Fix it.", epic="EPIC-01")
    assert not tn.notify_attention(tn.AttentionEventType.IMPLEMENTATION_FAILED, "Implementation", "EPIC-01 failed", "Fix it.", epic="EPIC-01")
    assert send.call_count == 1
    entries = tn._read_outbox()
    assert len(entries) == 1
    assert entries[0]["sent"] is True


def test_disabled_or_unselected_event_is_not_queued(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(tn, "_smtp_settings", lambda: _settings(enabled=False))
    assert not tn.notify_attention(tn.AttentionEventType.AUTHENTICATION_REQUIRED, "Implementation", "Auth", "Log in.")
    assert not tn.outbox_path().exists()


def test_pending_event_remains_after_delivery_failure_and_retries(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    failing = Mock(side_effect=OSError("offline"))
    monkeypatch.setattr(tn, "_send", failing)
    assert tn.notify_attention(tn.AttentionEventType.VERIFICATION_FAILED, "Verification", "Failed", "Review it.")
    assert tn._read_outbox()[0]["sent"] is False
    sent = Mock()
    monkeypatch.setattr(tn, "_send", sent)
    assert tn.flush_pending_notifications() == 1
    assert tn._read_outbox()[0]["sent"] is True


def test_email_content_omits_raw_prompt_and_contains_action():
    event = tn.AttentionEvent("implementation_failed", "Implementation", "EPIC-01 failed", "Run reset.", "/work", details={"exit_code": 1})
    message = tn._render_message(event, _settings())
    text = message.get_content()
    assert "Run reset." in text
    assert "prompt" not in text.lower()
    assert "EPIC-01 failed" in message["Subject"]


def test_send_uses_starttls_and_environment_credentials(monkeypatch):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    smtp = Mock(return_value=client)
    monkeypatch.setattr(tn.smtplib, "SMTP", smtp)
    monkeypatch.setenv("TEMPA_SMTP_USERNAME", "mailer")
    monkeypatch.setenv("TEMPA_SMTP_PASSWORD", "secret")
    event = tn.AttentionEvent("verification_failed", "Verification", "Failed", "Review it.", "/work")

    tn._send(event, _settings())

    smtp.assert_called_once_with("smtp.example.test", 587, timeout=10)
    client.starttls.assert_called_once()
    client.login.assert_called_once_with("mailer", "secret")
    client.send_message.assert_called_once()


def test_saved_credentials_override_environment_credentials(monkeypatch):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(tn.smtplib, "SMTP", Mock(return_value=client))
    monkeypatch.setenv("TEMPA_SMTP_USERNAME", "environment-user")
    monkeypatch.setenv("TEMPA_SMTP_PASSWORD", "environment-password")
    settings = _settings(smtp_username="saved-user", smtp_password="saved-password")

    tn._send(tn.AttentionEvent("test", "Settings", "Test", "None.", "/work"), settings)

    client.login.assert_called_once_with("saved-user", "saved-password")
