"""Tests for the pure functions in tempa_session.py: the generic backend-agnostic
engine pieces — usage-limit/auth-error detection driven by a Backend's marker lists,
feature-progress line formatting, invocation preparation (stdin vs file_ref prompt
delivery), and session-result logging. Per-backend argv/parsing specifics (claude/
copilot/codex) are tested in test_tempa_backend.py. Functions that actually spawn a
subprocess (_stream_backend_process, _run_backend_session, run_session, etc.) are
explicitly out of scope, except prepare_backend_invocation, which never spawns
anything — it only resolves an executable and (for file_ref backends) writes a file.
_read_process_stdout is also in scope: it takes a `process`-like object as a plain
parameter rather than spawning one, so it's exercised here against fakes."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
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
    ts._state.backend_stuck_after_done_hit = False
    ts._state.stop_event.clear()
    yield
    ts._state.usage_limit_hit = False
    ts._state.auth_error_hit = False
    ts._state.auth_error_message = ""
    ts._state.server_overloaded_hit = False
    ts._state.backend_stuck_after_done_hit = False
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


def test_is_usage_limit_text_session_limit_message():
    # Real CLI text seen in the wild: "You've hit your session limit · resets 1:30am
    # (Asia/Jakarta)". Regression guard, same shape as the weekly-limit one above — this
    # used to fall through as a plain error instead of triggering wait/retry.
    text = "You've hit your session limit · resets 1:30am (Asia/Jakarta)"
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


def test_failure_marker_text_ignores_unauthorized_in_successful_codex_command_output():
    # Regression for process_20260807_111444.txt: Codex successfully printed an OpenAPI
    # document, but Tempa scanned the complete item.completed JSON line and mistook this
    # schema reference for a CLI credential failure.
    pattern = '$ref: "#/components/responses/Unauthorized"'
    data = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "aggregated_output": pattern,
            "exit_code": 0,
            "status": "completed",
        },
    }
    raw_line = json.dumps(data)

    assert data["item"]["aggregated_output"] == pattern
    assert "Unauthorized" in raw_line
    assert ts._failure_marker_text(raw_line, data) == ""
    assert ts._is_auth_error_text(ts._failure_marker_text(raw_line, data), tb.CODEX) is False


def test_failure_marker_text_keeps_unauthorized_in_codex_failure_event():
    data = {"type": "turn.failed", "error": {"message": "Unauthorized"}}
    raw_line = json.dumps(data)

    assert ts._failure_marker_text(raw_line, data) == raw_line
    assert ts._is_auth_error_text(ts._failure_marker_text(raw_line, data), tb.CODEX) is True


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


def test_usage_limit_recovery_never_sends_an_attention_notification(monkeypatch):
    notifier = Mock()
    monkeypatch.setattr(ts, "notify_attention", notifier)
    assert ts._handle_usage_limit("usage limit reached", Mock(), "label", tb.CLAUDE) is True
    notifier.assert_not_called()


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


def test_log_session_result_stuck_after_done_is_not_reported_as_a_failure(tmp_path, capsys):
    # The non-zero exit is Tempa's own force-terminate, not a task failure -- reporting it as
    # "FAILED (exit code 1)" made a routine, self-healing cleanup hang look alarming.
    ts._state.backend_stuck_after_done_hit = True
    log_path = tmp_path / "log.txt"
    log_path.write_text("boom", encoding="utf-8")

    assert ts._log_session_result("Session [X]", 1, log_path) is False

    out = capsys.readouterr().out
    assert "FAILED" not in out
    assert "force-terminated" in out


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


# ---------------------------------------------------------------------------
# _read_process_stdout
# ---------------------------------------------------------------------------

class _FakeHangingProcess:
    """Stand-in for subprocess.Popen: `stdout` yields a fixed set of lines and then never
    raises StopIteration — like a real OS pipe a lingering grandchild process still holds
    open — while `poll()` reports the process itself has already exited once `mark_exited`
    is called."""

    def __init__(self, lines):
        self._lines = list(lines)
        self._exited = threading.Event()

    def mark_exited(self):
        self._exited.set()

    def poll(self):
        return 0 if self._exited.is_set() else None

    @property
    def stdout(self):
        yield from self._lines
        threading.Event().wait(60)  # never reaches EOF on its own


class _FakeFinitePipeProcess:
    """Stand-in for subprocess.Popen whose stdout pipe closes normally (raises
    StopIteration) right after its last line, as a real short-lived process would."""

    def __init__(self, lines):
        self._lines = lines

    def poll(self):
        return 0

    @property
    def stdout(self):
        return iter(self._lines)


def test_read_process_stdout_stops_after_grace_period_once_process_exited():
    # Regression for a real hang: a backend CLI on Windows can leave a grandchild process
    # (e.g. a build tool, a leftover `dotnet run` server) holding its stdout pipe open even
    # after the CLI itself exited and finished printing everything — without a bound,
    # iterating `process.stdout` blocks forever.
    process = _FakeHangingProcess(["line one\n", "line two\n"])
    lines = []

    def consume():
        for raw_line in ts._read_process_stdout(process, drain_grace_sec=0.05):
            lines.append(raw_line)
            if len(lines) == len(process._lines):
                process.mark_exited()

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    thread.join(timeout=5.0)

    assert not thread.is_alive(), "must give up on a pipe that never reaches EOF once the process has exited"
    assert lines == ["line one\n", "line two\n"]


def test_read_process_stdout_keeps_waiting_while_process_has_not_exited():
    # No grandchild involved — the process is just still running. Even past the grace
    # period, a pipe that hasn't exited yet must not be abandoned early.
    process = _FakeHangingProcess(["only line\n"])
    lines = []

    def consume():
        for raw_line in ts._read_process_stdout(process, drain_grace_sec=0.05):
            lines.append(raw_line)

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    thread.join(timeout=0.3)

    assert thread.is_alive(), "must keep waiting on the pipe while the process itself hasn't exited"
    assert lines == ["only line\n"]

    process.mark_exited()  # let the background thread wind down instead of spinning forever
    thread.join(timeout=2.0)


def test_read_process_stdout_returns_promptly_on_a_normal_pipe_close():
    process = _FakeFinitePipeProcess(["a\n", "b\n"])

    start = time.monotonic()
    result = list(ts._read_process_stdout(process, drain_grace_sec=5.0))
    elapsed = time.monotonic() - start

    assert result == ["a\n", "b\n"]
    assert elapsed < 1.0, "a normal EOF must not wait out the full drain grace period"


# ---------------------------------------------------------------------------
# _update_no_progress_tracking
# ---------------------------------------------------------------------------

def test_update_no_progress_tracking_resets_counter_on_progress():
    epic = {"completed_features": 3, "no_progress_rounds": 1}
    stalled = ts._update_no_progress_tracking(epic, completed_before=2, limit=2)
    assert stalled is False
    assert epic["no_progress_rounds"] == 0


def test_update_no_progress_tracking_also_resets_total_run_on_progress():
    # Regression: a long-running epic that legitimately needs many resumes across its
    # natural lifecycle (many features_per_session batches, several QA fix-rounds) must not
    # keep accumulating toward max_session_run for reasons unrelated to being stuck — every
    # real forward-progress round resets that lifetime counter too, not just no_progress_rounds.
    epic = {"completed_features": 3, "no_progress_rounds": 1, "total_run": 29}
    ts._update_no_progress_tracking(epic, completed_before=2, limit=2)
    assert epic["total_run"] == 0


def test_update_no_progress_tracking_leaves_total_run_untouched_when_stalled():
    epic = {"completed_features": 1, "no_progress_rounds": 0, "total_run": 29}
    ts._update_no_progress_tracking(epic, completed_before=1, limit=2)
    assert epic["total_run"] == 29


def test_update_no_progress_tracking_increments_counter_when_stalled():
    epic = {"completed_features": 1, "no_progress_rounds": 0}
    stalled = ts._update_no_progress_tracking(epic, completed_before=1, limit=2)
    assert stalled is False
    assert epic["no_progress_rounds"] == 1


def test_update_no_progress_tracking_reaches_limit():
    epic = {"completed_features": 1, "no_progress_rounds": 1}
    stalled = ts._update_no_progress_tracking(epic, completed_before=1, limit=2)
    assert stalled is True
    assert epic["no_progress_rounds"] == 2


def test_update_no_progress_tracking_starts_from_zero_when_absent():
    epic = {"completed_features": 1}
    stalled = ts._update_no_progress_tracking(epic, completed_before=1, limit=1)
    assert stalled is True
    assert epic["no_progress_rounds"] == 1


# ---------------------------------------------------------------------------
# _last_meaningful_log_lines
# ---------------------------------------------------------------------------

def test_last_meaningful_log_lines_drops_done_accounting_line(tmp_path):
    log_path = tmp_path / "session.txt"
    log_path.write_text("line one\nline two\n[Done] input=123 output=45\n", encoding="utf-8")
    assert ts._last_meaningful_log_lines(log_path) == "line one\nline two"


def test_last_meaningful_log_lines_skips_blank_lines(tmp_path):
    log_path = tmp_path / "session.txt"
    log_path.write_text("line one\n\n\nline two\n", encoding="utf-8")
    assert ts._last_meaningful_log_lines(log_path) == "line one\nline two"


def test_last_meaningful_log_lines_caps_to_max_lines(tmp_path):
    log_path = tmp_path / "session.txt"
    log_path.write_text("\n".join(f"line {i}" for i in range(10)), encoding="utf-8")
    result = ts._last_meaningful_log_lines(log_path, max_lines=3)
    assert result == "line 7\nline 8\nline 9"


def test_last_meaningful_log_lines_missing_file_returns_empty(tmp_path):
    assert ts._last_meaningful_log_lines(tmp_path / "missing.txt") == ""


# ---------------------------------------------------------------------------
# _try_reorder_for_dependency
# ---------------------------------------------------------------------------

def _epics(*names_and_statuses):
    return [{"epic_name": n, "status": s} for n, s in names_and_statuses]


def test_try_reorder_for_dependency_moves_target_before_stuck_epic():
    config = {"epic": _epics(("EPIC-16", "on_progress"), ("EPIC-17", "pending"), ("EPIC-18", "pending"))}
    result = ts._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-17")
    assert result is None
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-17", "EPIC-16", "EPIC-18"]
    assert config["epic_reorder_history"] == [["EPIC-17", "EPIC-16"]]


def test_try_reorder_for_dependency_refuses_self_reference():
    config = {"epic": _epics(("EPIC-16", "on_progress"))}
    result = ts._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-16")
    assert result is not None and "can't be blocked on itself" in result
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-16"]
    assert "epic_reorder_history" not in config


def test_try_reorder_for_dependency_refuses_unknown_epic():
    config = {"epic": _epics(("EPIC-16", "on_progress"))}
    result = ts._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-99")
    assert result is not None and "not a known epic" in result
    assert [e["epic_name"] for e in config["epic"]] == ["EPIC-16"]


def test_try_reorder_for_dependency_refuses_already_done_target():
    config = {"epic": _epics(("EPIC-16", "on_progress"), ("EPIC-17", "done"))}
    result = ts._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-17")
    assert result is not None and "already done" in result


def test_try_reorder_for_dependency_refuses_target_already_before_stuck_epic():
    config = {"epic": _epics(("EPIC-17", "pending"), ("EPIC-16", "on_progress"))}
    result = ts._try_reorder_for_dependency(config, stuck_index=1, blocked_by_epic="EPIC-17")
    assert result is not None and "already scheduled before" in result


def test_try_reorder_for_dependency_refuses_circular_reversal():
    # EPIC-17 was already moved ahead of EPIC-16 once (history reflects that). Now EPIC-17
    # itself stalls claiming the reverse ("blocked on EPIC-16") — moving EPIC-16 back ahead
    # of EPIC-17 would just undo the first move, a likely circular dependency, so refuse.
    config = {
        "epic": _epics(("EPIC-17", "pending"), ("EPIC-16", "on_progress")),
        "epic_reorder_history": [["EPIC-17", "EPIC-16"]],
    }
    result = ts._try_reorder_for_dependency(config, stuck_index=0, blocked_by_epic="EPIC-16")
    assert result is not None and "circular dependency" in result


# ---------------------------------------------------------------------------
# _epic_genuinely_complete / _repair_qa_state_desync
# ---------------------------------------------------------------------------

def test_epic_genuinely_complete_true_when_all_features_done():
    epic = {
        "total_features": 2, "completed_features": 2,
        "features": [{"status": "done"}, {"status": "done"}],
    }
    assert ts._epic_genuinely_complete(epic) is True


def test_epic_genuinely_complete_false_when_features_incomplete():
    epic = {
        "total_features": 2, "completed_features": 1,
        "features": [{"status": "done"}, {"status": "pending"}],
    }
    assert ts._epic_genuinely_complete(epic) is False


def test_epic_genuinely_complete_false_when_total_features_zero():
    # No features ever recorded — _epic_features_actually_done alone would vacuously
    # return True here; the total>0 guard is what prevents a false "genuinely complete".
    epic = {"total_features": 0, "completed_features": 0, "features": []}
    assert ts._epic_genuinely_complete(epic) is False


def test_epic_genuinely_complete_false_when_feature_status_disagrees():
    # completed_features/total_features agree at the epic level, but a feature's own
    # status still says otherwise — the underlying integrity check must still catch this.
    epic = {
        "total_features": 2, "completed_features": 2,
        "features": [{"status": "done"}, {"status": "require_fixing"}],
    }
    assert ts._epic_genuinely_complete(epic) is False


def test_repair_qa_state_desync_routes_back_through_qa_gate():
    epic = {
        "status": "require_fixing", "qa_passed": False, "qa_status": "idle",
        "no_progress_rounds": 2, "total_features": 3, "completed_features": 3,
    }
    ts._repair_qa_state_desync(epic)
    assert epic["status"] == "done"
    assert epic["qa_passed"] is False
    assert epic["qa_status"] == "idle"
    assert epic["no_progress_rounds"] == 0


# ---------------------------------------------------------------------------
# _reason_with_counterpart_context
# ---------------------------------------------------------------------------

def test_reason_with_counterpart_context_appends_when_counterpart_has_a_reason():
    epics = _epics(("EPIC-16", "failed"), ("EPIC-17", "failed"))
    epics[1]["blocked_reason"] = "needs something from EPIC-16"
    result = ts._reason_with_counterpart_context("needs something from EPIC-17", epics, "EPIC-17")
    assert result == (
        "needs something from EPIC-17\n\n"
        "For context, 'EPIC-17' itself previously reported being blocked:\n"
        "needs something from EPIC-16"
    )


def test_reason_with_counterpart_context_unchanged_when_counterpart_has_no_reason():
    epics = _epics(("EPIC-16", "failed"), ("EPIC-17", "pending"))
    result = ts._reason_with_counterpart_context("needs something from EPIC-17", epics, "EPIC-17")
    assert result == "needs something from EPIC-17"


def test_reason_with_counterpart_context_unchanged_when_no_target_named():
    epics = _epics(("EPIC-16", "failed"))
    result = ts._reason_with_counterpart_context("some reason", epics, None)
    assert result == "some reason"


# ---------------------------------------------------------------------------
# _terminate_if_stuck_after_done
# ---------------------------------------------------------------------------

class _FakePollingProcess:
    """Stand-in for subprocess.Popen whose poll() returns None (still running) until
    `exit_after_calls` calls have been made, then returns a fixed exit code -- lets a test
    control exactly when the "process" appears to exit relative to done_event/sleep calls."""

    def __init__(self, exit_after_calls=None):
        self._calls = 0
        self._exit_after_calls = exit_after_calls
        self.terminated = False

    def exit_now(self):
        """Make every subsequent poll() report the process as exited -- for tests that need
        the exit to happen at a specific point in the watchdog's own loop rather than after a
        fixed number of poll() calls."""
        self._exit_after_calls = self._calls

    def poll(self):
        self._calls += 1
        if self._exit_after_calls is not None and self._calls >= self._exit_after_calls:
            return 0
        return None

    def terminate(self):
        self.terminated = True


def test_terminate_if_stuck_after_done_terminates_when_process_never_exits():
    process = _FakePollingProcess(exit_after_calls=None)  # never exits on its own
    done_event = threading.Event()
    done_event.set()
    sleeps = []

    ts._terminate_if_stuck_after_done(
        process, done_event, "Session [e1]", grace_sec=42, poll_interval=7, sleep_fn=sleeps.append,
    )

    assert process.terminated is True
    assert ts._state.backend_stuck_after_done_hit is True
    assert ts._state.stop_event.is_set()
    # The grace period is slept in poll_interval slices (so it can be cut short the moment
    # more output arrives) -- what matters is that the full grace_sec was waited out.
    assert sum(sleeps) == 42


def test_terminate_if_stuck_after_done_does_nothing_if_process_already_exited():
    process = _FakePollingProcess(exit_after_calls=1)  # already exited by the first poll
    done_event = threading.Event()
    done_event.set()

    ts._terminate_if_stuck_after_done(process, done_event, "Session [e1]", sleep_fn=lambda s: None)

    assert process.terminated is False
    assert ts._state.backend_stuck_after_done_hit is False
    assert not ts._state.stop_event.is_set()


def test_terminate_if_stuck_after_done_does_nothing_if_process_exits_during_grace_period():
    # Exits by the time the first in-grace-period check happens (2nd poll call), even though
    # it hadn't exited yet at the pre-grace check (1st poll call).
    process = _FakePollingProcess(exit_after_calls=2)
    done_event = threading.Event()
    done_event.set()

    ts._terminate_if_stuck_after_done(process, done_event, "Session [e1]", sleep_fn=lambda s: None)

    assert process.terminated is False
    assert ts._state.backend_stuck_after_done_hit is False


def test_terminate_if_stuck_after_done_waits_for_done_event_before_grace_period():
    # The process is still running and done_event isn't set yet -- the watchdog must keep
    # polling (not immediately terminate) until either happens.
    process = _FakePollingProcess(exit_after_calls=None)
    done_event = threading.Event()
    poll_sleeps = []

    def fake_sleep(seconds):
        poll_sleeps.append(seconds)
        if len(poll_sleeps) >= 3:
            done_event.set()  # let the wait-for-done loop finish after a few polls

    ts._terminate_if_stuck_after_done(
        process, done_event, "Session [e1]", grace_sec=5, poll_interval=5, sleep_fn=fake_sleep,
    )

    assert process.terminated is True
    assert len(poll_sleeps) > 1  # the wait-for-done polling actually happened first
    assert sum(poll_sleeps[:-1]) == 5 * (len(poll_sleeps) - 1)  # ...then one grace slice


def test_terminate_if_stuck_after_done_does_not_terminate_a_session_that_resumes_working():
    # The live regression (session_EPIC-05_20260815_032220.txt): a resumed session emitted a
    # "[Done] turns=0" on its 2nd line and only then started working, so done_event was set
    # while the backend was perfectly healthy. Clearing it mid-grace (what _apply_done_signal
    # now does on the next output line) must call the watchdog off, not merely delay it.
    process = _FakePollingProcess(exit_after_calls=None)
    done_event = threading.Event()
    done_event.set()
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            done_event.clear()  # more agent output arrived -- it wasn't finished after all
        elif len(sleeps) == 4:
            # ...and the session later ends normally, on its own, while the watchdog is back
            # in its wait-for-the-next-[Done] loop.
            process.exit_now()

    ts._terminate_if_stuck_after_done(
        process, done_event, "Session [e1]", grace_sec=100, poll_interval=1, sleep_fn=fake_sleep,
    )

    assert process.terminated is False
    assert ts._state.backend_stuck_after_done_hit is False
    assert sum(sleeps) < 100  # never sat out a full grace period


def test_terminate_if_stuck_after_done_rearms_and_fires_on_a_later_done():
    # Same shape as above, but the backend does eventually go silent for a full grace period
    # after a later "[Done]" -- the watchdog must still catch that.
    process = _FakePollingProcess(exit_after_calls=None)
    done_event = threading.Event()
    done_event.set()
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            done_event.clear()  # output resumed -> watchdog goes back to waiting
        elif len(sleeps) == 2:
            done_event.set()  # a later, genuinely final [Done]

    ts._terminate_if_stuck_after_done(
        process, done_event, "Session [e1]", grace_sec=10, poll_interval=10, sleep_fn=fake_sleep,
    )

    assert process.terminated is True
    assert ts._state.backend_stuck_after_done_hit is True


# ---------------------------------------------------------------------------
# _apply_done_signal
# ---------------------------------------------------------------------------

def test_apply_done_signal_arms_on_done():
    done_event = threading.Event()
    ts._apply_done_signal("[Done] turns=140 cost=$?", done_event)
    assert done_event.is_set()


def test_apply_done_signal_disarms_on_any_later_output():
    # Claude Code emits a `result` event per re-invocation inside one `-p` run, so a "[Done]"
    # is only ever the end of a *turn*; work after it means the process isn't finished.
    done_event = threading.Event()
    ts._apply_done_signal("[Done] turns=0 cost=$?", done_event)
    ts._apply_done_signal("[Tool: Bash] {\"command\": \"dotnet test\"}", done_event)
    assert not done_event.is_set()


# ---------------------------------------------------------------------------
# run_qa_session — commit-after-QA-pass hook
#
# The backend session itself is stubbed out (real spawning is out of scope, per the
# module docstring) via `ts._run_backend_session`; `ts.commit_workspace_changes` is
# stubbed too, since its own real-git-repo behavior belongs to test_tempa_git.py — these
# tests only cover whether run_qa_session decides to call it, and with what. `ts.load_config`
# is also stubbed directly (the file-wide `_no_real_wait` autouse fixture above already
# replaces it with a wait-durations-only stub, so these tests override it back to
# something with the "epic"/"workspace" shape run_qa_session actually needs) rather than
# going through a real config.json on disk.
# ---------------------------------------------------------------------------

def _stub_qa_backend_session(monkeypatch):
    monkeypatch.setattr(ts, "_run_backend_session", lambda *a, **k: (0, Path("unused.log")))


def test_run_qa_session_commits_after_genuine_qa_pass(monkeypatch, tmp_path):
    commit = Mock(return_value=("committed", "abc123"))
    monkeypatch.setattr(ts, "commit_workspace_changes", commit)
    _stub_qa_backend_session(monkeypatch)
    workspace_root = str(tmp_path / "workspace")
    monkeypatch.setattr(ts, "load_config", lambda: {
        "workspace": {"root": workspace_root},
        "epic": [{"epic_name": "EPIC-01", "status": "done", "qa_passed": True, "qa_status": "done"}],
    })

    ts.run_qa_session(0, "prompt", "EPIC-01")

    commit.assert_called_once()
    args, _ = commit.call_args
    assert args[0] == workspace_root
    assert "EPIC-01" in args[1]


def test_run_qa_session_skips_commit_when_disabled_in_config(monkeypatch, tmp_path):
    commit = Mock()
    monkeypatch.setattr(ts, "commit_workspace_changes", commit)
    _stub_qa_backend_session(monkeypatch)
    monkeypatch.setattr(ts, "load_config", lambda: {
        "workspace": {"root": str(tmp_path / "workspace")},
        "commit_after_qa_pass": False,
        "epic": [{"epic_name": "EPIC-01", "status": "done", "qa_passed": True, "qa_status": "done"}],
    })

    ts.run_qa_session(0, "prompt", "EPIC-01")

    commit.assert_not_called()


def test_run_qa_session_skips_commit_when_qa_did_not_actually_pass(monkeypatch, tmp_path):
    commit = Mock()
    monkeypatch.setattr(ts, "commit_workspace_changes", commit)
    _stub_qa_backend_session(monkeypatch)
    monkeypatch.setattr(ts, "load_config", lambda: {
        "workspace": {"root": str(tmp_path / "workspace")},
        "epic": [{"epic_name": "EPIC-01", "status": "require_fixing", "qa_passed": False, "qa_status": "idle"}],
    })

    ts.run_qa_session(0, "prompt", "EPIC-01")

    commit.assert_not_called()


# ---------------------------------------------------------------------------
# _record_qa_verdict_and_guard — QA round history + loop guard
# ---------------------------------------------------------------------------

def _qa_config(epic: dict) -> dict:
    return {"epic": [epic]}


def test_record_qa_verdict_records_a_failing_round_with_the_flagged_features(tmp_path):
    config = _qa_config({
        "epic_name": "EPIC-03", "status": "require_fixing", "qa_passed": False,
        "qa_status": "done", "qa_report_filename": "r1.md",
        "features": [
            {"id": "F1", "status": "require_fixing"},
            {"id": "F2", "status": "require_fixing"},
            {"id": "F3", "status": "done"},
        ],
    })

    ts._record_qa_verdict_and_guard(config, 0, "EPIC-03", tmp_path / "qa.log")

    history = config["epic"][0]["qa_history"]
    assert len(history) == 1
    assert history[0]["verdict"] == "fail"
    assert history[0]["failed"] == ["F1", "F2"]
    assert history[0]["report"] == "r1.md"


def test_record_qa_verdict_records_a_pass_with_no_failed_features(tmp_path):
    config = _qa_config({
        "epic_name": "EPIC-03", "status": "done", "qa_passed": True, "qa_status": "done",
        "features": [{"id": "F1", "status": "done"}],
    })

    ts._record_qa_verdict_and_guard(config, 0, "EPIC-03", tmp_path / "qa.log")

    assert config["epic"][0]["qa_history"][0]["verdict"] == "pass"
    assert config["epic"][0]["qa_history"][0]["failed"] == []


def test_record_qa_verdict_ignores_an_interrupted_session(tmp_path):
    # qa_status is still "ongoing", so check_and_run will resume this as the SAME round —
    # recording it would count one round twice and manufacture a cycle out of an interruption.
    config = _qa_config({
        "epic_name": "EPIC-03", "status": "done", "qa_passed": False, "qa_status": "ongoing",
    })

    ts._record_qa_verdict_and_guard(config, 0, "EPIC-03", tmp_path / "qa.log")

    assert "qa_history" not in config["epic"][0]


@pytest.mark.parametrize(
    "flag",
    ["usage_limit_hit", "auth_error_hit", "server_overloaded_hit", "backend_stuck_after_done_hit"],
)
def test_record_qa_verdict_ignores_a_session_cut_short_by_a_stop_flag(tmp_path, flag):
    setattr(ts._state, flag, True)
    config = _qa_config({
        "epic_name": "EPIC-03", "status": "require_fixing", "qa_passed": False, "qa_status": "done",
    })

    ts._record_qa_verdict_and_guard(config, 0, "EPIC-03", tmp_path / "qa.log")

    assert "qa_history" not in config["epic"][0]


def test_record_qa_verdict_fails_the_epic_and_stops_the_runner_on_a_detected_loop(tmp_path, monkeypatch):
    notify = Mock()
    monkeypatch.setattr(ts, "notify_attention", notify)
    epic = {
        "epic_name": "EPIC-03", "status": "require_fixing", "qa_passed": False, "qa_status": "done",
        "qa_loop_strikes": 1,
        "qa_history": [
            {"round": 1, "verdict": "fail", "failed": ["F1", "F2"]},
            {"round": 2, "verdict": "fail", "failed": ["F3", "F4"]},
            {"round": 3, "verdict": "fail", "failed": ["F1", "F2"]},
        ],
        "features": [
            {"id": "F3", "status": "require_fixing"},
            {"id": "F4", "status": "require_fixing"},
        ],
    }

    ts._record_qa_verdict_and_guard(_qa_config(epic), 0, "EPIC-03", tmp_path / "qa.log")

    assert epic["status"] == "failed"
    assert "cycling through QA" in epic["blocked_reason"]
    assert ts._state.stop_event.is_set()
    notify.assert_called_once()


def test_record_qa_verdict_leaves_a_converging_epic_alone(tmp_path):
    epic = {
        "epic_name": "EPIC-03", "status": "require_fixing", "qa_passed": False, "qa_status": "done",
        "qa_history": [
            {"round": 1, "verdict": "fail", "failed": ["F1", "F2", "F3"]},
            {"round": 2, "verdict": "fail", "failed": ["F1", "F2"]},
        ],
        "features": [{"id": "F1", "status": "require_fixing"}],
    }

    ts._record_qa_verdict_and_guard(_qa_config(epic), 0, "EPIC-03", tmp_path / "qa.log")

    assert epic["status"] == "require_fixing"
    assert "blocked_reason" not in epic
    assert not ts._state.stop_event.is_set()


def test_run_qa_session_records_the_round_it_just_finished(monkeypatch, tmp_path):
    _stub_qa_backend_session(monkeypatch)
    monkeypatch.setattr(ts, "commit_workspace_changes", Mock())
    monkeypatch.setattr(ts, "load_config", lambda: {
        "workspace": {"root": str(tmp_path / "workspace")},
        "epic": [{
            "epic_name": "EPIC-01", "status": "require_fixing", "qa_passed": False,
            "qa_status": "done", "features": [{"id": "F1", "status": "require_fixing"}],
        }],
    })
    saved = {}
    monkeypatch.setattr(ts, "save_config", lambda config: saved.update(config))

    ts.run_qa_session(0, "prompt", "EPIC-01")

    assert saved["epic"][0]["qa_history"][0]["failed"] == ["F1"]
