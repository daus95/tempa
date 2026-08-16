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
    ts._state.background_tasks_terminated_hit = False
    ts._state.stop_event.clear()
    yield
    ts._state.usage_limit_hit = False
    ts._state.auth_error_hit = False
    ts._state.auth_error_message = ""
    ts._state.server_overloaded_hit = False
    ts._state.backend_stuck_after_done_hit = False
    ts._state.background_tasks_terminated_hit = False
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
# _is_background_terminated_text / _handle_background_terminated / _backend_env
# ---------------------------------------------------------------------------
# The backend CLI kills the background work a turn left running once its own wait
# ceiling expires, then exits 0. Tempa has to recognize that line, because "exit 0
# and completed_features didn't move" otherwise reads as an epic blocked on something
# outside itself -- which, after implement_no_progress_rounds of them, fails the epic.

_CLAUDE_BG_TERMINATED = (
    "Background tasks still running after 600s; terminating. "
    "Set CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 to wait indefinitely."
)


def test_is_background_terminated_text_matches_the_real_cli_message():
    assert ts._is_background_terminated_text(_CLAUDE_BG_TERMINATED, tb.CLAUDE) is True


def test_is_background_terminated_text_empty_string_false():
    assert ts._is_background_terminated_text("", tb.CLAUDE) is False


def test_is_background_terminated_text_unrelated_text_false():
    assert ts._is_background_terminated_text("running the test suite", tb.CLAUDE) is False


@pytest.mark.parametrize("backend", [tb.COPILOT, tb.CODEX])
def test_is_background_terminated_text_no_markers_for_backend_without_them(backend):
    assert ts._is_background_terminated_text(_CLAUDE_BG_TERMINATED, backend) is False


def test_handle_background_terminated_sets_state_without_stopping_the_runner():
    # Unlike every other handler here it must NOT set stop_event or terminate anything:
    # the CLI is already tearing itself down and the epic stays resumable.
    handled = ts._handle_background_terminated(_CLAUDE_BG_TERMINATED, "label", tb.CLAUDE)
    assert handled is True
    assert ts._state.background_tasks_terminated_hit is True
    assert not ts._state.stop_event.is_set()


def test_handle_background_terminated_no_match_returns_false_without_side_effects():
    handled = ts._handle_background_terminated("all good", "label", tb.CLAUDE)
    assert handled is False
    assert ts._state.background_tasks_terminated_hit is False


def test_background_terminated_marker_inside_a_successful_json_event_is_not_eligible():
    # A session that merely READS a log containing that line (or explains the failure in
    # its own prose) must not be mistaken for one that actually hit the ceiling --
    # _failure_marker_text is what keeps successful JSON events out of marker matching.
    event = {"type": "assistant", "message": {"content": [{"type": "text", "text": _CLAUDE_BG_TERMINATED}]}}
    marker_text = ts._failure_marker_text(json.dumps(event), event)
    assert ts._is_background_terminated_text(marker_text, tb.CLAUDE) is False


def test_backend_env_sets_the_backends_background_wait_variable(monkeypatch):
    monkeypatch.setattr(ts, "load_config", lambda: {"backend_background_wait_sec": 3600})
    monkeypatch.delenv("CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", raising=False)

    env = ts._backend_env(tb.CLAUDE)

    assert env["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] == "3600000"


def test_backend_env_keeps_the_rest_of_the_process_environment(monkeypatch):
    monkeypatch.setattr(ts, "load_config", lambda: {})
    monkeypatch.setenv("TEMPA_TEST_MARKER", "kept")

    assert ts._backend_env(tb.CLAUDE)["TEMPA_TEST_MARKER"] == "kept"


def test_backend_env_never_overrides_a_value_the_user_already_exported(monkeypatch):
    # Tempa's value is a default, not a policy -- someone who pinned the variable by hand
    # (or in CI) keeps exactly the behaviour they asked for.
    monkeypatch.setattr(ts, "load_config", lambda: {"backend_background_wait_sec": 3600})
    monkeypatch.setenv("CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", "0")

    assert ts._backend_env(tb.CLAUDE)["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] == "0"


def test_backend_env_adds_nothing_for_a_backend_without_a_documented_knob(monkeypatch):
    monkeypatch.setattr(ts, "load_config", lambda: {"backend_background_wait_sec": 3600})

    assert ts._backend_env(tb.COPILOT) == dict(ts.os.environ)


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


