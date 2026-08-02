"""Tests for tempa_backend.py — the per-CLI adapters (claude/copilot/codex): argv
building, JSON-event → readable-line parsing, session-id extraction, and the
usage-limit/auth-error marker lists + friendly messages. Nothing here spawns a
subprocess — build_cmd/parse_line/extract_session_id are pure functions."""

from __future__ import annotations

import pytest

import tempa_backend as tb

# ---------------------------------------------------------------------------
# BACKENDS registry / get_backend_def
# ---------------------------------------------------------------------------

def test_backends_registry_has_all_three():
    assert set(tb.BACKENDS) == {"claude", "copilot", "codex"}


def test_get_backend_def_known_name_returns_matching_backend():
    assert tb.get_backend_def("copilot") is tb.COPILOT
    assert tb.get_backend_def("codex") is tb.CODEX
    assert tb.get_backend_def("claude") is tb.CLAUDE


def test_get_backend_def_unknown_name_falls_back_to_claude():
    assert tb.get_backend_def("does-not-exist") is tb.CLAUDE


# ---------------------------------------------------------------------------
# claude — build_cmd / parse_line / extract_session_id
# ---------------------------------------------------------------------------

def test_claude_build_cmd_basic_no_resume():
    cmd = tb.CLAUDE.build_cmd("claude", "claude-sonnet-5", None, None, "")
    assert cmd[0] == "claude"
    assert "--dangerously-skip-permissions" in cmd
    assert "--permission-mode" in cmd and "bypassPermissions" in cmd
    assert "--model" in cmd and "claude-sonnet-5" in cmd
    assert "--append-system-prompt" in cmd
    assert "--output-format" in cmd and "stream-json" in cmd
    assert cmd[-1] == "-p"
    assert "--resume" not in cmd
    assert "--effort" not in cmd


def test_claude_build_cmd_with_resume():
    cmd = tb.CLAUDE.build_cmd("claude", "claude-sonnet-5", "sess-123", None, "")
    idx = cmd.index("--resume")
    assert cmd[idx + 1] == "sess-123"
    assert cmd.index("--resume") < cmd.index("-p")


def test_claude_build_cmd_with_reasoning_effort():
    cmd = tb.CLAUDE.build_cmd("claude", "claude-sonnet-5", None, None, "high")
    idx = cmd.index("--effort")
    assert cmd[idx + 1] == "high"


def test_claude_parse_line_system_init():
    data = {"type": "system", "subtype": "init", "session_id": "abc123", "model": "claude-sonnet-5"}
    assert tb.CLAUDE.parse_line(data) == "[session_id=abc123] [model=claude-sonnet-5]"


def test_claude_parse_line_assistant_text_and_tool_use():
    data = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Thinking..."},
        {"type": "tool_use", "name": "Write", "input": {}},
    ]}}
    assert tb.CLAUDE.parse_line(data) == "Thinking...\n[Tool: Write] {}"


def test_claude_parse_line_result_with_cost_and_turns():
    data = {"type": "result", "cost_usd": 0.42, "num_turns": 7}
    assert tb.CLAUDE.parse_line(data) == "[Done] turns=7 cost=$0.42"


def test_claude_parse_line_unknown_type_returns_none():
    assert tb.CLAUDE.parse_line({"type": "progress"}) is None


def test_claude_extract_session_id():
    assert tb.CLAUDE.extract_session_id({"session_id": "abc"}) == "abc"
    assert tb.CLAUDE.extract_session_id({}) is None


@pytest.mark.parametrize("backend", tb.BACKENDS.values(), ids=lambda b: b.name)
def test_marker_lists_are_lowercase(backend):
    # tempa_session lowercases the scanned text before matching, so an
    # uppercase-containing marker here would never match anything.
    for marker in backend.usage_limit_markers + backend.auth_error_markers:
        assert marker == marker.lower()


def test_claude_friendly_auth_error_message_api_key_branch():
    message = tb.CLAUDE.friendly_auth_error_message("Invalid API key provided")
    assert "ANTHROPIC_API_KEY" in message


def test_claude_friendly_auth_error_message_oauth_branch():
    message = tb.CLAUDE.friendly_auth_error_message("authentication_error: oauth access token has expired")
    assert "/login" in message
    assert "ANTHROPIC_API_KEY" not in message


# ---------------------------------------------------------------------------
# copilot — build_cmd / parse_line / extract_session_id
# ---------------------------------------------------------------------------

def test_copilot_build_cmd_basic():
    cmd = tb.COPILOT.build_cmd("copilot", "auto", None, "read the file", "")
    assert cmd[0] == "copilot"
    assert "--allow-all-tools" in cmd
    assert "--output-format" in cmd and "json" in cmd
    assert "--model" in cmd and "auto" in cmd
    assert cmd[-2:] == ["-p", "read the file"]
    assert not any(a.startswith("--resume") for a in cmd)
    assert "--reasoning-effort" not in cmd


def test_copilot_build_cmd_no_model_omits_flag():
    cmd = tb.COPILOT.build_cmd("copilot", "", None, "do it", "")
    assert "--model" not in cmd


def test_copilot_build_cmd_with_resume():
    cmd = tb.COPILOT.build_cmd("copilot", "auto", "sess-abc", "do it", "")
    assert "--resume=sess-abc" in cmd


def test_copilot_build_cmd_with_reasoning_effort():
    cmd = tb.COPILOT.build_cmd("copilot", "auto", None, "do it", "minimal")
    idx = cmd.index("--reasoning-effort")
    assert cmd[idx + 1] == "minimal"


def test_copilot_parse_line_assistant_message_with_content_and_tool_requests():
    data = {"type": "assistant.message", "data": {"content": "Hello", "toolRequests": [{"name": "view"}]}}
    result = tb.COPILOT.parse_line(data)
    assert result.startswith("Hello\n[Tool] ")
    assert '"name": "view"' in result


def test_copilot_parse_line_result_event():
    data = {"type": "result", "exitCode": 0, "usage": {"premiumRequests": 1, "totalApiDurationMs": 500}}
    result = tb.COPILOT.parse_line(data)
    assert "exit=0" in result and "premium_requests=1" in result


def test_copilot_parse_line_unknown_event_returns_none():
    assert tb.COPILOT.parse_line({"type": "session.skills_loaded"}) is None


def test_copilot_extract_session_id_only_from_result_event():
    assert tb.COPILOT.extract_session_id({"type": "result", "sessionId": "xyz"}) == "xyz"
    assert tb.COPILOT.extract_session_id({"type": "assistant.message", "sessionId": "xyz"}) is None


# ---------------------------------------------------------------------------
# codex — build_cmd / parse_line / extract_session_id
# ---------------------------------------------------------------------------

def test_codex_build_cmd_fresh_session():
    cmd = tb.CODEX.build_cmd("codex", "gpt-5.1-codex", None, None, "")
    assert cmd[:2] == ["codex", "exec"]
    assert "--json" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--skip-git-repo-check" in cmd
    assert "--model" in cmd and "gpt-5.1-codex" in cmd
    assert cmd[-1] == "-"
    assert "-c" not in cmd


def test_codex_build_cmd_resume_uses_subcommand_shape():
    cmd = tb.CODEX.build_cmd("codex", "gpt-5.1-codex", "thread-123", None, "")
    assert cmd[:4] == ["codex", "exec", "resume", "thread-123"]
    assert cmd[-1] == "-"


def test_codex_build_cmd_no_model_omits_flag():
    cmd = tb.CODEX.build_cmd("codex", "", None, None, "")
    assert "--model" not in cmd


def test_codex_build_cmd_with_reasoning_effort():
    cmd = tb.CODEX.build_cmd("codex", "gpt-5.6-sol", None, None, "ultra")
    idx = cmd.index("-c")
    assert cmd[idx + 1] == 'model_reasoning_effort="ultra"'


def test_codex_build_cmd_reasoning_effort_also_applies_on_resume():
    cmd = tb.CODEX.build_cmd("codex", "gpt-5.6-sol", "thread-123", None, "high")
    idx = cmd.index("-c")
    assert cmd[idx + 1] == 'model_reasoning_effort="high"'


def test_codex_parse_line_thread_started():
    assert tb.CODEX.parse_line({"type": "thread.started", "thread_id": "t-1"}) == "[thread_id=t-1]"


def test_codex_parse_line_item_completed_with_text():
    data = {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "OK"}}
    assert tb.CODEX.parse_line(data) == "OK"


def test_codex_parse_line_item_completed_without_text_falls_back_to_type_tag():
    data = {"type": "item.completed", "item": {"type": "command_execution"}}
    assert tb.CODEX.parse_line(data) == "[command_execution]"


def test_codex_parse_line_turn_completed_usage():
    data = {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}
    assert tb.CODEX.parse_line(data) == "[Done] input=10 output=5"


def test_codex_parse_line_failed_event_surfaced_as_error():
    result = tb.CODEX.parse_line({"type": "turn.failed", "error": {"message": "boom"}})
    assert result.startswith("[Error]")
    assert "boom" in result


def test_codex_extract_session_id_only_from_thread_started():
    assert tb.CODEX.extract_session_id({"type": "thread.started", "thread_id": "t-1"}) == "t-1"
    assert tb.CODEX.extract_session_id({"type": "turn.started", "thread_id": "t-1"}) is None


# ---------------------------------------------------------------------------
# reasoning_effort_choices / is_valid_reasoning_effort
# ---------------------------------------------------------------------------

def test_claude_reasoning_effort_choices_uniform_regardless_of_model():
    assert tb.CLAUDE.reasoning_effort_choices("claude-opus-5") == tb.CLAUDE_EFFORT_LEVELS
    assert tb.CLAUDE.reasoning_effort_choices("claude-haiku-4-5-20251001") == tb.CLAUDE_EFFORT_LEVELS


def test_copilot_reasoning_effort_choices_uniform_regardless_of_model():
    assert tb.COPILOT.reasoning_effort_choices("auto") == tb.COPILOT_EFFORT_LEVELS
    assert tb.COPILOT.reasoning_effort_choices("claude-opus-5") == tb.COPILOT_EFFORT_LEVELS


def test_codex_reasoning_effort_choices_known_model():
    assert tb.CODEX.reasoning_effort_choices("gpt-5.4") == tb.CODEX_MODEL_REASONING_LEVELS["gpt-5.4"]
    assert "ultra" not in tb.CODEX.reasoning_effort_choices("gpt-5.4")
    assert "ultra" in tb.CODEX.reasoning_effort_choices("gpt-5.6-sol")


def test_codex_reasoning_effort_choices_known_model_case_insensitive():
    assert tb.CODEX.reasoning_effort_choices("GPT-5.4") == tb.CODEX_MODEL_REASONING_LEVELS["gpt-5.4"]


def test_codex_reasoning_effort_choices_unknown_model_falls_back_to_default():
    assert tb.CODEX.reasoning_effort_choices("some-future-model") == tb.CODEX_DEFAULT_EFFORT_LEVELS


@pytest.mark.parametrize("backend", tb.BACKENDS.values(), ids=lambda b: b.name)
def test_is_valid_reasoning_effort_empty_string_always_valid(backend):
    assert tb.is_valid_reasoning_effort(backend, "anything", "") is True


def test_is_valid_reasoning_effort_valid_and_invalid_per_backend():
    assert tb.is_valid_reasoning_effort(tb.CLAUDE, "claude-opus-5", "xhigh") is True
    assert tb.is_valid_reasoning_effort(tb.CLAUDE, "claude-opus-5", "ultra") is False
    assert tb.is_valid_reasoning_effort(tb.COPILOT, "auto", "none") is True
    assert tb.is_valid_reasoning_effort(tb.CODEX, "gpt-5.4", "ultra") is False
    assert tb.is_valid_reasoning_effort(tb.CODEX, "gpt-5.6-sol", "ultra") is True


# ---------------------------------------------------------------------------
# resolve_exe
# ---------------------------------------------------------------------------

def test_resolve_exe_uses_shutil_which_over_exe_names(monkeypatch):
    calls = []

    def fake_which(name):
        calls.append(name)
        return "/usr/bin/copilot" if name == "copilot.cmd" else None

    monkeypatch.setattr(tb.shutil, "which", fake_which)
    assert tb.resolve_exe(tb.COPILOT) == "/usr/bin/copilot"
    assert calls == ["copilot", "copilot.cmd"]


def test_resolve_exe_not_found_returns_none(monkeypatch):
    monkeypatch.setattr(tb.shutil, "which", lambda name: None)
    assert tb.resolve_exe(tb.CODEX) is None


# ---------------------------------------------------------------------------
# get_backend_status
# ---------------------------------------------------------------------------

def test_get_backend_status_all_installed_and_writable(monkeypatch):
    monkeypatch.setattr(tb.shutil, "which", lambda name: f"/usr/bin/{name}")
    status = tb.get_backend_status(workspace_writable=True)
    assert set(status) == {"claude", "copilot", "codex"}
    for name, backend in tb.BACKENDS.items():
        assert status[name] == {
            "label": backend.label, "installed": True, "writable": True, "ready": True,
        }


def test_get_backend_status_not_installed_is_never_ready_even_if_writable(monkeypatch):
    monkeypatch.setattr(tb.shutil, "which", lambda name: None)
    status = tb.get_backend_status(workspace_writable=True)
    for name in tb.BACKENDS:
        assert status[name]["installed"] is False
        assert status[name]["ready"] is False


def test_get_backend_status_not_writable_is_never_ready_even_if_installed(monkeypatch):
    monkeypatch.setattr(tb.shutil, "which", lambda name: f"/usr/bin/{name}")
    status = tb.get_backend_status(workspace_writable=False)
    for name in tb.BACKENDS:
        assert status[name]["installed"] is True
        assert status[name]["writable"] is False
        assert status[name]["ready"] is False
