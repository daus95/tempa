"""The Settings pane: reading config.json for the form, and validating + saving it back.

Saving is where the dashboard does its only real input validation, and every message here
is user-facing — it lands under the field that failed. The validation is deliberately kept
as plain functions over the request payload (no HTTP, no server state) so each rule can be
tested on its own; `save_settings` is the only part that touches disk.

Architecture Principles and the SMTP test live here too: both are Settings-pane actions,
and the principles document is workspace-wide configuration like the rest of it.
"""

from __future__ import annotations

from collections.abc import Callable

import tempa_backend
import tempa_config
from tempa_notifications import DEFAULT_ENABLED_EVENTS, send_test_email

Response = tuple[int, dict]

# Every stage that has its own backend/model/reasoning-effort setting.
STAGES = ("clarify", "clarify_apply", "plan", "implement")

# Numeric fields, in the order they are validated (which is the order they appear in the
# form) with the message shown when one isn't a positive whole number. `required` False
# means blank is allowed and means "no limit".
LIMIT_FIELDS = (
    ("features_per_session", False, "Features per Session must be empty or a positive whole number."),
    ("max_session_run", False, "Max Session Runs must be empty or a positive whole number."),
    ("max_clarification_run", True, "Max Finalize Clarification Round must be a positive whole number."),
    ("finalize_no_progress_rounds", True, "Max Finalize No-Progress Round must be a positive whole number."),
    ("usage_limit_retry_wait_sec", True, "Usage Limit Retry Wait must be a positive whole number."),
    ("usage_limit_heartbeat_sec", True, "Usage Limit Heartbeat Interval must be a positive whole number."),
    ("server_overloaded_retry_wait_sec", True, "Server Overload Retry Wait must be a positive whole number."),
    ("poll_interval_sec", True, "Implementation Poll Interval must be a positive whole number."),
)


def read_config(backend_status: dict) -> Response:
    """The Settings form's current values. `backend_status` is passed in so the
    writability probe (a real filesystem touch) runs once per request, not once per
    handler that wants it."""
    config = tempa_config.read_config_safe()
    return 200, {
        "ok": True,
        "config": {
            "models": tempa_config.get_models(config),
            "backends": tempa_config.get_backends(config),
            "reasoning_efforts": tempa_config.get_reasoning_efforts(config),
            "features_per_session": config.get("features_per_session"),
            "max_session_run": config.get("max_session_run"),
            "max_clarification_run": config.get("max_clarification_run"),
            "finalize_no_progress_rounds": tempa_config.get_finalize_no_progress_rounds(config),
            "allow_finalize_with_critical": bool(config.get("allow_finalize_with_critical")),
            "commit_after_qa_pass": tempa_config.get_commit_after_qa_pass(config),
            "implementation_start_requirement": tempa_config.get_implementation_start_requirement(config),
            "notifications": {"email": tempa_config.get_email_notifications(config)},
            "usage_limit_retry_wait_sec": tempa_config.get_usage_limit_retry_wait_sec(config),
            "usage_limit_heartbeat_sec": tempa_config.get_usage_limit_heartbeat_sec(config),
            "server_overloaded_retry_wait_sec": tempa_config.get_server_overloaded_retry_wait_sec(config),
            "poll_interval_sec": tempa_config.get_poll_interval_sec(config),
            "backends_status": backend_status,
        },
    }


def _validate_stage_settings(payload: dict) -> tuple[str | None, dict]:
    """Backend, model and reasoning effort per stage. Validated together because each one
    constrains the next: the backend decides which model strings are aliases, and
    backend+model together decide which reasoning-effort levels exist."""
    models_in = payload.get("models")
    backends_in = payload.get("backends")
    reasoning_efforts_in = payload.get("reasoning_efforts")
    if (not isinstance(models_in, dict) or not isinstance(backends_in, dict)
            or not isinstance(reasoning_efforts_in, dict)):
        return "Malformed request.", {}

    backends = {}
    for stage in STAGES:
        value = (backends_in.get(stage) or "").strip()
        if value not in tempa_backend.BACKENDS:
            return f"The {stage} backend must be one of: {', '.join(tempa_backend.BACKENDS)}.", {}
        backends[stage] = value

    models = {}
    for stage in STAGES:
        value = (models_in.get(stage) or "").strip()
        if not value:
            return f"The {stage} model cannot be empty.", {}
        # Friendly aliases (opus-5, sonnet-5, ...) are Claude-only — for copilot/codex
        # the model string is stored as-is (see tempa_config._resolve_model_alias).
        models[stage] = tempa_config._resolve_model_alias(value) if backends[stage] == "claude" else value

    reasoning_efforts = {}
    for stage in STAGES:
        value = (reasoning_efforts_in.get(stage) or "").strip()
        backend_def = tempa_backend.get_backend_def(backends[stage])
        if not tempa_backend.is_valid_reasoning_effort(backend_def, models[stage], value):
            choices = ", ".join(backend_def.reasoning_effort_choices(models[stage]))
            return f"The {stage} reasoning effort must be empty or one of: {choices}.", {}
        reasoning_efforts[stage] = value

    return None, {"backends": backends, "models": models, "reasoning_efforts": reasoning_efforts}


def _validate_limits(payload: dict) -> tuple[str | None, dict]:
    """The numeric run limits. A blank/absent optional field becomes None ("no limit")."""
    limits = {}
    for name, required, message in LIMIT_FIELDS:
        raw = payload.get(name)
        if raw is None or raw == "":
            if required:
                return message, {}
            limits[name] = None
            continue
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return message, {}
        if parsed < 1:
            return message, {}
        limits[name] = parsed
    return None, limits


def _validate_email(payload: dict, current_config: dict) -> tuple[str | None, dict]:
    """The SMTP notification settings. An absent/!dict block keeps whatever is on disk,
    so a client that doesn't render the notifications tab can't wipe it."""
    email_in = ((payload.get("notifications") or {}).get("email"))
    if not isinstance(email_in, dict):
        email_in = tempa_config.get_email_notifications(current_config)
    try:
        smtp_port = int(email_in.get("smtp_port", 587))
        timeout_seconds = int(email_in.get("timeout_seconds", 10))
    except (TypeError, ValueError):
        return "SMTP port and timeout must be whole numbers.", {}
    security = str(email_in.get("security", "starttls"))
    provider = str(email_in.get("provider", "custom"))
    recipients_in, events_in = email_in.get("recipients", []), email_in.get("events", [])
    if (security not in ("starttls", "ssl", "none") or provider not in ("gmail", "office365", "custom")
            or not 1 <= smtp_port <= 65535 or timeout_seconds < 1):
        return "Invalid SMTP security, port, or timeout.", {}
    if not isinstance(recipients_in, list) or not isinstance(events_in, list):
        return "Email recipients and events must be lists.", {}
    recipients = [value.strip() for value in recipients_in if isinstance(value, str) and value.strip()]
    email = {
        "enabled": bool(email_in.get("enabled")), "provider": provider,
        "smtp_host": str(email_in.get("smtp_host", "")).strip(),
        "smtp_port": smtp_port, "security": security, "from": str(email_in.get("from", "")).strip(),
        "smtp_username": str(email_in.get("smtp_username", "")),
        "smtp_password": str(email_in.get("smtp_password", "")),
        "recipients": recipients, "username_env": str(email_in.get("username_env", "TEMPA_SMTP_USERNAME")).strip() or "TEMPA_SMTP_USERNAME",
        "password_env": str(email_in.get("password_env", "TEMPA_SMTP_PASSWORD")).strip() or "TEMPA_SMTP_PASSWORD",
        "timeout_seconds": timeout_seconds,
        "events": [value for value in events_in if value in DEFAULT_ENABLED_EVENTS],
    }
    if email["enabled"] and (not email["smtp_host"] or not email["from"] or not recipients):
        return "Enabled email needs SMTP host, sender, and at least one recipient.", {}
    return None, email


def validate_settings(payload: dict | list | None, current_config: dict) -> tuple[str | None, dict]:
    """Validate a whole Settings form submission. Returns (error message, settings) — on
    the first failure, the message the form shows and an empty dict; otherwise None and
    exactly the keys to write into config.json."""
    if payload is None or not isinstance(payload, dict):
        return "Malformed request.", {}

    error, stage_settings = _validate_stage_settings(payload)
    if error:
        return error, {}
    error, limits = _validate_limits(payload)
    if error:
        return error, {}

    implementation_start_requirement = payload.get("implementation_start_requirement")
    if implementation_start_requirement not in tempa_config.IMPLEMENTATION_START_REQUIREMENTS:
        return ("Start Implementation requirement must be one of: "
                f"{', '.join(tempa_config.IMPLEMENTATION_START_REQUIREMENTS)}."), {}

    error, email = _validate_email(payload, current_config)
    if error:
        return error, {}

    return None, {
        **stage_settings,
        **limits,
        "allow_finalize_with_critical": bool(payload.get("allow_finalize_with_critical")),
        "commit_after_qa_pass": bool(payload.get("commit_after_qa_pass")),
        "implementation_start_requirement": implementation_start_requirement,
        "notifications": {"email": email},
    }


def save_settings(payload: dict | list | None, backend_status: dict,
                  finalize_limit_warning: Callable[[dict, dict], str | None]) -> Response:
    """Validate and persist the Settings form, echoing back what was saved.

    `finalize_limit_warning(previous, new)` is supplied by the caller: the save itself
    always succeeds, and the warning is an advisory note about a setting a run already in
    flight can no longer pick up — only the server knows what is running right now.
    """
    current_config = tempa_config.load_config()
    error, settings = validate_settings(payload, current_config)
    if error:
        return 400, {"ok": False, "error": error}

    config = current_config
    previous_finalize_limits = {
        "max_clarification_run": config.get("max_clarification_run"),
        "finalize_no_progress_rounds": config.get("finalize_no_progress_rounds"),
    }
    config.update(settings)
    tempa_config.save_config(config)
    print("[settings] configuration saved")
    warning = finalize_limit_warning(previous_finalize_limits, {
        "max_clarification_run": settings["max_clarification_run"],
        "finalize_no_progress_rounds": settings["finalize_no_progress_rounds"],
    })
    return 200, {
        "ok": True,
        "warning": warning,
        "config": {**settings, "backends_status": backend_status},
    }


def run_test_email() -> Response:
    """Send one test email with the SMTP settings currently on disk."""
    ok, message = send_test_email()
    return (200 if ok else 400), {"ok": ok, "message": message}


def save_principles(payload: dict | list | None) -> Response:
    """Save the Architecture Principles document. Blank content deletes the file, which
    is how the principles are unset (an absent file means nothing is injected)."""
    if payload is None or not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    content = payload.get("content", "")
    if not isinstance(content, str):
        return 400, {"ok": False, "error": "Content must be text."}
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    target = tempa_config.get_principles_path()
    try:
        if not content:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n" so Windows text mode doesn't reintroduce \r\n.
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(content + "\n")
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not save the principles: {e}"}
    print("[principles] " + ("cleared" if not content else "saved"))
    return 200, {"ok": True, "content": content}
