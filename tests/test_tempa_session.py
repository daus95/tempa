"""Tests for the pure functions in tempa_session.py: the generic backend-agnostic
engine pieces — usage-limit/auth-error detection driven by a Backend's marker lists,
feature-progress line formatting, invocation preparation (stdin vs file_ref prompt
delivery), and session-result logging. Per-backend argv/parsing specifics (claude/
copilot/codex) are tested in test_tempa_backend.py. Functions that actually spawn a
subprocess (_stream_backend_process, _run_backend_session, run_session, etc.) are
explicitly out of scope, except prepare_backend_invocation, which never spawns
anything — it only resolves an executable and (for file_ref backends) writes a file."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import tempa_backend as tb
import tempa_session as ts


@pytest.fixture(autouse=True)
def reset_runner_state():
    """_state is a module-level singleton (tempa_logging._state) shared across the whole
    process — reset the flags these tests touch before/after each test so one test's
    usage-limit/auth-error trip can't leak into the next."""
    ts._state.usage_limit_hit = False
    ts._state.auth_error_hit = False
    ts._state.auth_error_message = ""
    ts._state.stop_event.clear()
    yield
    ts._state.usage_limit_hit = False
    ts._state.auth_error_hit = False
    ts._state.auth_error_message = ""
    ts._state.stop_event.clear()


# ---------------------------------------------------------------------------
# _is_usage_limit_text / _is_auth_error_text
# ---------------------------------------------------------------------------

def test_is_usage_limit_text_empty_string_false():
    assert ts._is_usage_limit_text("", tb.CLAUDE) is False


@pytest.mark.parametrize("backend", tb.BACKENDS.values(), ids=lambda b: b.name)
def test_is_usage_limit_text_matches_every_marker_for_its_own_backend(backend):
    for marker in backend.usage_limit_markers:
        assert ts._is_usage_limit_text(marker, backend) is True
        assert ts._is_usage_limit_text(marker.upper(), backend) is True


def test_is_usage_limit_text_substring_within_sentence():
    assert ts._is_usage_limit_text("Error: Claude AI usage limit reached, try later.", tb.CLAUDE) is True


def test_is_usage_limit_text_unrelated_text_false():
    assert ts._is_usage_limit_text("everything is fine", tb.CLAUDE) is False


def test_is_usage_limit_text_one_backends_marker_does_not_match_another():
    # codex's marker text shouldn't accidentally trip claude's detector.
    codex_marker = tb.CODEX.usage_limit_markers[0]
    assert ts._is_usage_limit_text(codex_marker, tb.CLAUDE) is False


def test_is_auth_error_text_empty_string_false():
    assert ts._is_auth_error_text("", tb.CLAUDE) is False


@pytest.mark.parametrize("backend", tb.BACKENDS.values(), ids=lambda b: b.name)
def test_is_auth_error_text_matches_every_marker_for_its_own_backend(backend):
    for marker in backend.auth_error_markers:
        assert ts._is_auth_error_text(marker, backend) is True
        assert ts._is_auth_error_text(marker.upper(), backend) is True


def test_is_auth_error_text_unrelated_text_false():
    assert ts._is_auth_error_text("some generic error occurred", tb.CLAUDE) is False


# ---------------------------------------------------------------------------
# _session_feature_lines
# ---------------------------------------------------------------------------

def test_session_feature_lines_no_matching_epic_returns_empty():
    assert ts._session_feature_lines({"epic": []}, "EPIC-01", None) == []


def test_session_feature_lines_reports_progress_and_batch():
    config = {
        "epic": [{
            "epic_name": "EPIC-01",
            "total_features": 3,
            "completed_features": 1,
            "features": [
                {"id": "FEAT-01", "name": "a", "status": "done"},
                {"id": "FEAT-02", "name": "b", "status": "pending"},
                {"id": "FEAT-03", "name": "c", "status": "require_fixing"},
            ],
        }],
    }
    lines = ts._session_feature_lines(config, "EPIC-01", None)
    assert lines[0] == "Features done: 1/3"
    assert any("FEAT-02" in line and "⬜" in line for line in lines)
    assert any("FEAT-03" in line and "🔧" in line for line in lines)


def test_session_feature_lines_honors_features_override_truncation():
    config = {
        "epic": [{
            "epic_name": "EPIC-01",
            "total_features": 2,
            "completed_features": 0,
            "features": [
                {"id": "FEAT-01", "name": "a", "status": "pending"},
                {"id": "FEAT-02", "name": "b", "status": "pending"},
            ],
        }],
    }
    lines = ts._session_feature_lines(config, "EPIC-01", features_override=1)
    assert any("+1 more" in line for line in lines)


# ---------------------------------------------------------------------------
# prepare_backend_invocation
# ---------------------------------------------------------------------------

def test_prepare_invocation_stdin_backend_returns_full_prompt_as_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "resolve_exe", lambda backend: "codex")
    log_path = tmp_path / "session_20260101_000000.txt"
    cmd, stdin_text = ts.prepare_backend_invocation(tb.CODEX, "gpt-5.1-codex", None, "do the thing", log_path)
    assert cmd[-1] == "-"
    assert "do the thing" in stdin_text
    # codex has no dedicated system-prompt flag, so the banner is prepended to stdin.
    assert "<system>" in stdin_text
    assert not (tmp_path / "session_20260101_000000.prompt.md").exists()


def test_prepare_invocation_claude_keeps_prompt_bare_system_prompt_via_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "resolve_exe", lambda backend: "claude")
    log_path = tmp_path / "session_20260101_000000.txt"
    cmd, stdin_text = ts.prepare_backend_invocation(tb.CLAUDE, "claude-sonnet-5", None, "do the thing", log_path)
    assert stdin_text == "do the thing"
    assert "<system>" not in stdin_text
    assert "--append-system-prompt" in cmd


def test_prepare_invocation_file_ref_backend_writes_sidecar_file_and_returns_empty_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "resolve_exe", lambda backend: "copilot")
    log_path = tmp_path / "session_20260101_000000.txt"
    cmd, stdin_text = ts.prepare_backend_invocation(tb.COPILOT, "auto", None, "multi\nline\nprompt", log_path)

    assert stdin_text == ""
    prompt_file = tmp_path / "session_20260101_000000.prompt.md"
    assert prompt_file.exists()
    content = prompt_file.read_text(encoding="utf-8")
    assert "multi\nline\nprompt" in content
    assert "<system>" in content  # copilot has no system-prompt flag either

    assert cmd[-2] == "-p"
    assert str(prompt_file) in cmd[-1]


def test_prepare_invocation_raises_when_executable_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "resolve_exe", lambda backend: None)
    with pytest.raises(FileNotFoundError):
        ts.prepare_backend_invocation(tb.CODEX, "gpt-5.1-codex", None, "prompt", tmp_path / "log.txt")


def test_prepare_invocation_reasoning_effort_reaches_cmd_for_stdin_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "resolve_exe", lambda backend: "codex")
    log_path = tmp_path / "session_20260101_000000.txt"
    cmd, _ = ts.prepare_backend_invocation(tb.CODEX, "gpt-5.6-sol", None, "do the thing", log_path, "ultra")
    idx = cmd.index("-c")
    assert cmd[idx + 1] == 'model_reasoning_effort="ultra"'


def test_prepare_invocation_reasoning_effort_reaches_cmd_for_file_ref_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "resolve_exe", lambda backend: "copilot")
    log_path = tmp_path / "session_20260101_000000.txt"
    cmd, _ = ts.prepare_backend_invocation(tb.COPILOT, "auto", None, "do the thing", log_path, "high")
    idx = cmd.index("--reasoning-effort")
    assert cmd[idx + 1] == "high"


def test_prepare_invocation_no_reasoning_effort_omits_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "resolve_exe", lambda backend: "codex")
    log_path = tmp_path / "session_20260101_000000.txt"
    cmd, _ = ts.prepare_backend_invocation(tb.CODEX, "gpt-5.6-sol", None, "do the thing", log_path)
    assert "-c" not in cmd


# ---------------------------------------------------------------------------
# _handle_usage_limit / _handle_auth_error (state side effects, no subprocess)
# ---------------------------------------------------------------------------

def test_handle_usage_limit_sets_state_and_terminates_process():
    process = Mock()
    handled = ts._handle_usage_limit("usage limit reached", process, "label", tb.CLAUDE)
    assert handled is True
    assert ts._state.usage_limit_hit is True
    assert ts._state.stop_event.is_set()
    process.terminate.assert_called_once()


def test_handle_usage_limit_no_match_returns_false_without_side_effects():
    process = Mock()
    handled = ts._handle_usage_limit("all good", process, "label", tb.CLAUDE)
    assert handled is False
    assert ts._state.usage_limit_hit is False
    process.terminate.assert_not_called()


def test_handle_auth_error_sets_friendly_message():
    process = Mock()
    handled = ts._handle_auth_error("invalid api key", process, "label", tb.CLAUDE)
    assert handled is True
    assert ts._state.auth_error_hit is True
    assert "ANTHROPIC_API_KEY" in ts._state.auth_error_message
    process.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# _log_session_result
# ---------------------------------------------------------------------------

def test_log_session_result_success(tmp_path):
    assert ts._log_session_result("Session [X]", 0, tmp_path / "log.txt") is True


def test_log_session_result_usage_limit_stops_before_checking_exit_code(tmp_path):
    ts._state.usage_limit_hit = True
    assert ts._log_session_result("Session [X]", 0, tmp_path / "log.txt") is False


def test_log_session_result_auth_error_stops_before_checking_exit_code(tmp_path):
    ts._state.auth_error_hit = True
    assert ts._log_session_result("Session [X]", 0, tmp_path / "log.txt") is False


def test_log_session_result_nonzero_exit_fails(tmp_path):
    log_path = tmp_path / "log.txt"
    log_path.write_text("boom", encoding="utf-8")
    assert ts._log_session_result("Session [X]", 1, log_path) is False
