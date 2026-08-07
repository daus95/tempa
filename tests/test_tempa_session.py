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
    ts._state.server_overloaded_hit = False
    ts._state.stop_event.clear()
    yield
    ts._state.usage_limit_hit = False
    ts._state.auth_error_hit = False
    ts._state.auth_error_message = ""
    ts._state.server_overloaded_hit = False
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


def test_is_usage_limit_text_weekly_limit_message():
    # Real CLI text seen in the wild: "Agent terminated early due to an API error:
    # You've hit your weekly limit · resets 11am (Asia/Jakarta)". Regression guard —
    # this used to fall through as a plain error instead of triggering wait/retry.
    text = "Agent terminated early due to an API error: You've hit your weekly limit · resets 11am (Asia/Jakarta)"
    assert ts._is_usage_limit_text(text, tb.CLAUDE) is True


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


def test_is_overloaded_text_empty_string_false():
    assert ts._is_overloaded_text("", tb.CLAUDE) is False


def test_is_overloaded_text_matches_real_cli_message():
    # Real CLI text seen in the wild: "API Error: 529 Overloaded. This is a server-side
    # issue, usually temporary — try again in a moment. If it persists, check
    # https://status.claude.com."
    text = ("API Error: 529 Overloaded. This is a server-side issue, usually temporary — "
            "try again in a moment. If it persists, check https://status.claude.com.")
    assert ts._is_overloaded_text(text, tb.CLAUDE) is True


def test_is_overloaded_text_matches_every_marker_for_claude():
    for marker in tb.CLAUDE.overloaded_markers:
        assert ts._is_overloaded_text(marker, tb.CLAUDE) is True
        assert ts._is_overloaded_text(marker.upper(), tb.CLAUDE) is True


def test_is_overloaded_text_unrelated_text_false():
    assert ts._is_overloaded_text("everything is fine", tb.CLAUDE) is False


def test_is_overloaded_text_no_markers_for_backend_without_them():
    assert ts._is_overloaded_text("anything at all", tb.COPILOT) is False


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


def test_handle_overloaded_sets_state_and_terminates_process():
    process = Mock()
    handled = ts._handle_overloaded("API Error: 529 Overloaded.", process, "label", tb.CLAUDE)
    assert handled is True
    assert ts._state.server_overloaded_hit is True
    assert ts._state.stop_event.is_set()
    process.terminate.assert_called_once()


def test_handle_overloaded_no_match_returns_false_without_side_effects():
    process = Mock()
    handled = ts._handle_overloaded("all good", process, "label", tb.CLAUDE)
    assert handled is False
    assert ts._state.server_overloaded_hit is False
    process.terminate.assert_not_called()


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


def test_log_session_result_overloaded_stops_before_checking_exit_code(tmp_path):
    ts._state.server_overloaded_hit = True
    assert ts._log_session_result("Session [X]", 0, tmp_path / "log.txt") is False


# ---------------------------------------------------------------------------
# wait_out_usage_limit / run_with_usage_limit_retry
# ---------------------------------------------------------------------------
# A usage-limit stop is a pause, not a failure: run_with_usage_limit_retry keeps
# re-calling its run_fn, waiting out the limit between attempts, for as long as
# _state.usage_limit_hit stays set. The wait/heartbeat durations now come from
# config.json (see tempa_config.get_usage_limit_retry_wait_sec &c.) — load_config is
# monkeypatched to return ~0-second values here so these tests don't actually sleep
# 30 minutes.

@pytest.fixture(autouse=True)
def _no_real_wait(monkeypatch):
    monkeypatch.setattr(ts, "load_config", lambda: {
        "usage_limit_retry_wait_sec": 0.01,
        "usage_limit_heartbeat_sec": 0.01,
        "server_overloaded_retry_wait_sec": 0.01,
    })


def _fake_run_fn(results):
    """A run_fn stub that pops one (ok, sets_usage_limit) pair per call, setting
    _state.usage_limit_hit accordingly before returning `ok` — mirrors how a real
    run_*_session leaves usage_limit_hit set alongside a False return."""
    calls = []

    def run_fn() -> bool:
        ok, hits_limit = results[len(calls)]
        calls.append(ok)
        ts._state.usage_limit_hit = hits_limit
        return ok

    run_fn.calls = calls
    return run_fn


def test_run_with_usage_limit_retry_returns_immediately_on_success():
    run_fn = _fake_run_fn([(True, False)])
    assert ts.run_with_usage_limit_retry(run_fn, "Thing") is True
    assert len(run_fn.calls) == 1


def test_run_with_usage_limit_retry_returns_immediately_on_a_real_failure():
    run_fn = _fake_run_fn([(False, False)])
    assert ts.run_with_usage_limit_retry(run_fn, "Thing") is False
    assert len(run_fn.calls) == 1


def test_run_with_usage_limit_retry_retries_until_success():
    run_fn = _fake_run_fn([(False, True), (False, True), (True, False)])
    assert ts.run_with_usage_limit_retry(run_fn, "Thing") is True
    assert len(run_fn.calls) == 3
    # The wait clears the flags so the caller sees a clean state after retrying succeeds.
    assert ts._state.usage_limit_hit is False


def test_run_with_usage_limit_retry_stops_retrying_once_a_real_failure_occurs():
    run_fn = _fake_run_fn([(False, True), (False, False)])
    assert ts.run_with_usage_limit_retry(run_fn, "Thing") is False
    assert len(run_fn.calls) == 2


def test_run_with_usage_limit_retry_does_not_retry_an_auth_error():
    # An auth error never sets usage_limit_hit — run_fn should only be called once.
    ts._state.auth_error_hit = True
    run_fn = _fake_run_fn([(False, False)])
    assert ts.run_with_usage_limit_retry(run_fn, "Thing") is False
    assert len(run_fn.calls) == 1


def test_wait_out_usage_limit_clears_state():
    ts._state.usage_limit_hit = True
    ts._state.stop_event.set()
    ts.wait_out_usage_limit("Thing", 1)
    assert ts._state.usage_limit_hit is False
    assert not ts._state.stop_event.is_set()


# ---------------------------------------------------------------------------
# wait_out_server_overload / run_with_usage_limit_retry (overload path)
# ---------------------------------------------------------------------------
# Mirrors the usage-limit tests above: a server-overload stop (e.g. Anthropic's 529) is
# also a pause, not a failure, and goes through the very same run_with_usage_limit_retry
# loop — just waiting out server_overloaded_retry_wait_sec (monkeypatched to ~0 above)
# instead of usage_limit_retry_wait_sec between attempts.

def _fake_run_fn_overload(results):
    """Like _fake_run_fn, but the second element of each pair sets
    _state.server_overloaded_hit instead of _state.usage_limit_hit."""
    calls = []

    def run_fn() -> bool:
        ok, hits_overload = results[len(calls)]
        calls.append(ok)
        ts._state.server_overloaded_hit = hits_overload
        return ok

    run_fn.calls = calls
    return run_fn


def test_run_with_usage_limit_retry_retries_on_overload_until_success():
    run_fn = _fake_run_fn_overload([(False, True), (False, True), (True, False)])
    assert ts.run_with_usage_limit_retry(run_fn, "Thing") is True
    assert len(run_fn.calls) == 3
    assert ts._state.server_overloaded_hit is False


def test_run_with_usage_limit_retry_stops_retrying_overload_once_a_real_failure_occurs():
    run_fn = _fake_run_fn_overload([(False, True), (False, False)])
    assert ts.run_with_usage_limit_retry(run_fn, "Thing") is False
    assert len(run_fn.calls) == 2


def test_run_with_usage_limit_retry_does_not_retry_overload_for_an_auth_error():
    # An auth error never sets server_overloaded_hit — run_fn should only be called once.
    ts._state.auth_error_hit = True
    run_fn = _fake_run_fn_overload([(False, False)])
    assert ts.run_with_usage_limit_retry(run_fn, "Thing") is False
    assert len(run_fn.calls) == 1


def test_wait_out_server_overload_clears_state():
    ts._state.server_overloaded_hit = True
    ts._state.stop_event.set()
    ts.wait_out_server_overload("Thing", 1)
    assert ts._state.server_overloaded_hit is False
    assert not ts._state.stop_event.is_set()
