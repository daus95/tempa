"""Best-effort email notifications for Tempa states that need a human.

The runner must never depend on mail delivery.  Events are therefore persisted before an
immediate, bounded SMTP attempt; unsent events are retried the next time Tempa starts.
Automatic recovery states deliberately have no event type and must not call this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import smtplib
import ssl
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from enum import StrEnum
from pathlib import Path

from tempa_config import get_config_path, get_email_notifications, get_workspace, load_config
from tempa_logging import log


class AttentionEventType(StrEnum):
    AUTHENTICATION_REQUIRED = "authentication_required"
    IMPLEMENTATION_FAILED = "implementation_failed"
    IMPLEMENTATION_AUTO_REORDERED = "implementation_auto_reordered"
    IMPLEMENTATION_QA_STATE_REPAIRED = "implementation_qa_state_repaired"
    PLAN_FAILED = "plan_failed"
    SESSION_LIMIT_REACHED = "session_limit_reached"
    QA_LIMIT_REACHED = "qa_limit_reached"
    QA_OSCILLATION_DETECTED = "qa_oscillation_detected"
    CLARIFICATION_ANSWERS_REQUIRED = "clarification_answers_required"
    CLARIFICATION_LIMIT_REACHED = "clarification_limit_reached"
    CLARIFICATION_FAILED = "clarification_failed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    VERIFICATION_FAILED = "verification_failed"
    BACKEND_TEST_FAILED = "backend_test_failed"


DEFAULT_ENABLED_EVENTS = tuple(event.value for event in AttentionEventType)
_OUTBOX_LOCK = threading.Lock()


@dataclass(frozen=True)
class AttentionEvent:
    event_type: str
    process: str
    title: str
    action: str
    workspace: str
    epic: str | None = None
    log_path: str | None = None
    details: dict[str, str | int] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def dedupe_key(self) -> str:
        identity = "|".join((
            str(get_config_path()), self.event_type, self.process, self.epic or "",
            self.log_path or "", json.dumps(self.details, sort_keys=True, ensure_ascii=False),
        ))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def outbox_path() -> Path:
    return get_config_path().parent / "notification-outbox.json"


def _read_outbox() -> list[dict]:
    try:
        data = json.loads(outbox_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_outbox(entries: list[dict]) -> None:
    path = outbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _smtp_settings(config: dict | None = None) -> dict:
    return get_email_notifications(config if config is not None else load_config())


def _is_configured(settings: dict) -> bool:
    return bool(settings.get("enabled") and settings.get("smtp_host") and settings.get("from")
                and settings.get("recipients"))


def _render_message(event: AttentionEvent, settings: dict) -> EmailMessage:
    message = EmailMessage()
    workspace_name = Path(event.workspace).name or event.workspace or "workspace"
    message["Subject"] = f"[Tempa][{workspace_name}] Action required: {event.title}"
    message["From"] = settings["from"]
    message["To"] = ", ".join(settings["recipients"])
    lines = [
        f"Workspace: {event.workspace or 'Not configured'}",
        f"Process: {event.process}",
    ]
    if event.epic:
        lines.append(f"Epic: {event.epic}")
    lines.append(f"Time (UTC): {event.created_at}")
    lines.extend(["", event.action])
    if event.details:
        lines.extend(["", "Details:"])
        lines.extend(f"- {key}: {value}" for key, value in event.details.items())
    if event.log_path:
        lines.extend(["", f"Log: {event.log_path}"])
    lines.extend(["", "Tempa does not accept approvals by email. Use the dashboard or CLI to continue."])
    message.set_content("\n".join(lines))
    return message


def _send(event: AttentionEvent, settings: dict) -> None:
    message = _render_message(event, settings)
    security = settings.get("security", "starttls")
    timeout = int(settings.get("timeout_seconds", 10))
    client_cls = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
    with client_cls(settings["smtp_host"], int(settings["smtp_port"]), timeout=timeout) as client:
        if security == "starttls":
            client.starttls(context=ssl.create_default_context())
        username = settings.get("smtp_username") or os.environ.get(settings.get("username_env", "TEMPA_SMTP_USERNAME"), "")
        password = settings.get("smtp_password") or os.environ.get(settings.get("password_env", "TEMPA_SMTP_PASSWORD"), "")
        if username:
            client.login(username, password)
        client.send_message(message)


def _event_from_dict(data: dict) -> AttentionEvent:
    return AttentionEvent(**{key: data[key] for key in AttentionEvent.__dataclass_fields__ if key in data})


def flush_pending_notifications() -> int:
    """Attempt pending delivery once. Delivery failures are logged and remain queued."""
    with _OUTBOX_LOCK:
        settings = _smtp_settings()
        if not _is_configured(settings):
            return 0
        entries = _read_outbox()
        delivered = 0
        changed = False
        for entry in entries:
            if entry.get("sent"):
                continue
            try:
                _send(_event_from_dict(entry["event"]), settings)
            except (OSError, smtplib.SMTPException, ValueError) as exc:
                entry["last_error"] = str(exc)
                changed = True
                log(f"Email notification delivery failed: {exc}", to_console=False)
                continue
            entry["sent"] = True
            entry.pop("last_error", None)
            changed = True
            delivered += 1
        if changed:
            _write_outbox(entries)
        return delivered


def notify_attention(
    event_type: AttentionEventType | str,
    process: str,
    title: str,
    action: str,
    *,
    epic: str | None = None,
    log_path: Path | str | None = None,
    details: dict[str, str | int] | None = None,
) -> bool:
    """Queue and attempt one human-attention email. Returns whether it was queued."""
    settings = _smtp_settings()
    event_name = str(event_type)
    if not _is_configured(settings) or event_name not in settings.get("events", []):
        return False
    workspace = get_workspace(load_config()).get("root", "")
    event = AttentionEvent(event_name, process, title, action, workspace, epic,
                           str(log_path) if log_path else None, details or {})
    key = event.dedupe_key()
    with _OUTBOX_LOCK:
        entries = _read_outbox()
        if any(entry.get("key") == key for entry in entries):
            return False
        entries.append({"key": key, "event": asdict(event), "sent": False})
        _write_outbox(entries)
    flush_pending_notifications()
    return True


def send_test_email() -> tuple[bool, str]:
    settings = _smtp_settings()
    if not _is_configured(settings):
        return False, "Enable email and set SMTP host, sender, and at least one recipient first."
    event = AttentionEvent("test", "Settings", "Test email", "No action is required.",
                           get_workspace(load_config()).get("root", ""))
    try:
        _send(event, settings)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        return False, str(exc)
    return True, "Test email sent."
