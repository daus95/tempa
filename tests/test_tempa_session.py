"""Tests for the pure parsing/formatting functions in tempa_session.py: turning a
stream-json event into readable log text, detecting the usage-limit and auth-error stop
conditions, and building the `claude` CLI argv. The functions that actually spawn a
subprocess (_run_claude_session, run_session, etc.) are explicitly out of scope."""

from __future__ import annotations

import pytest

import tempa_session as ts

# ---------------------------------------------------------------------------
# _format_stream_line
# ---------------------------------------------------------------------------

def test_format_stream_line_system_init():
    data = {"type": "system", "subtype": "init", "session_id": "abc123", "model": "claude-sonnet-5"}
    assert ts._format_stream_line(data) == "[session_id=abc123] [model=claude-sonnet-5]"


def test_format_stream_line_assistant_text_only():
    data = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello there"}]}}
    assert ts._format_stream_line(data) == "Hello there"


def test_format_stream_line_assistant_tool_use_only():
    data = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"path": "foo.py"}},
    ]}}
    result = ts._format_stream_line(data)
    assert result.startswith("[Tool: Read] ")
    assert '"path": "foo.py"' in result


def test_format_stream_line_assistant_text_and_tool_use_joined():
    data = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Thinking..."},
        {"type": "tool_use", "name": "Write", "input": {}},
    ]}}
    result = ts._format_stream_line(data)
    assert result == "Thinking...\n[Tool: Write] {}"


def test_format_stream_line_assistant_empty_content_returns_none():
    data = {"type": "assistant", "message": {"content": []}}
    assert ts._format_stream_line(data) is None


def test_format_stream_line_user_tool_result_ok():
    data = {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "file written", "is_error": False},
    ]}}
    assert ts._format_stream_line(data) == "[Result] file written"


def test_format_stream_line_user_tool_result_error():
    data = {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "permission denied", "is_error": True},
    ]}}
    assert ts._format_stream_line(data) == "[Error] permission denied"


def test_format_stream_line_user_tool_result_list_content_joined():
    data = {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": [{"text": "part1"}, {"text": "part2"}], "is_error": False},
    ]}}
    assert ts._format_stream_line(data) == "[Result] part1 part2"


def test_format_stream_line_user_tool_result_truncated_to_500_chars():
    long_content = "x" * 600
    data = {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": long_content, "is_error": False},
    ]}}
    result = ts._format_stream_line(data)
    assert result == "[Result] " + "x" * 500


def test_format_stream_line_user_no_tool_result_returns_none():
    data = {"type": "user", "message": {"content": [{"type": "something_else"}]}}
    assert ts._format_stream_line(data) is None


def test_format_stream_line_result_with_cost_and_turns():
    data = {"type": "result", "cost_usd": 0.42, "num_turns": 7}
    assert ts._format_stream_line(data) == "[Done] turns=7 cost=$0.42"


def test_format_stream_line_result_missing_fields_fallback_to_question_mark():
    data = {"type": "result"}
    assert ts._format_stream_line(data) == "[Done] turns=? cost=$?"


def test_format_stream_line_unknown_type_returns_none():
    assert ts._format_stream_line({"type": "progress"}) is None
    assert ts._format_stream_line({}) is None


# ---------------------------------------------------------------------------
# _is_usage_limit_text
# ---------------------------------------------------------------------------

def test_is_usage_limit_text_empty_string_false():
    assert ts._is_usage_limit_text("") is False


@pytest.mark.parametrize("marker", ts.USAGE_LIMIT_MARKERS)
def test_is_usage_limit_text_matches_every_marker(marker):
    assert ts._is_usage_limit_text(marker) is True
    assert ts._is_usage_limit_text(marker.upper()) is True


def test_is_usage_limit_text_substring_within_sentence():
    assert ts._is_usage_limit_text("Error: Claude AI usage limit reached, try later.") is True


def test_is_usage_limit_text_unrelated_text_false():
    assert ts._is_usage_limit_text("everything is fine") is False


# ---------------------------------------------------------------------------
# _is_auth_error_text
# ---------------------------------------------------------------------------

def test_is_auth_error_text_empty_string_false():
    assert ts._is_auth_error_text("") is False


@pytest.mark.parametrize("marker", ts.AUTH_ERROR_MARKERS)
def test_is_auth_error_text_matches_every_marker(marker):
    assert ts._is_auth_error_text(marker) is True
    assert ts._is_auth_error_text(marker.upper()) is True


def test_is_auth_error_text_unrelated_text_false():
    assert ts._is_auth_error_text("some generic error occurred") is False


# ---------------------------------------------------------------------------
# _friendly_auth_error_message
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Invalid API key provided",
    "invalid x-api-key header",
    "invalid bearer token supplied",
])
def test_friendly_auth_error_message_api_key_branch(text):
    message = ts._friendly_auth_error_message(text)
    assert "ANTHROPIC_API_KEY" in message


def test_friendly_auth_error_message_oauth_branch():
    message = ts._friendly_auth_error_message("authentication_error: oauth access token has expired")
    assert "/login" in message
    assert "ANTHROPIC_API_KEY" not in message


# ---------------------------------------------------------------------------
# build_claude_cmd
# ---------------------------------------------------------------------------

def test_build_claude_cmd_basic_no_resume_no_extra():
    cmd = ts.build_claude_cmd("claude", "claude-sonnet-5")
    assert cmd[0] == "claude"
    assert "--dangerously-skip-permissions" in cmd
    assert "--permission-mode" in cmd and "bypassPermissions" in cmd
    assert "--model" in cmd and "claude-sonnet-5" in cmd
    assert "--output-format" in cmd and "stream-json" in cmd
    assert "--verbose" in cmd
    assert cmd[-1] == "-p"
    assert "--resume" not in cmd


def test_build_claude_cmd_with_resume():
    cmd = ts.build_claude_cmd("claude", "claude-sonnet-5", resume_session_id="sess-123")
    idx = cmd.index("--resume")
    assert cmd[idx + 1] == "sess-123"
    assert cmd.index("--resume") < cmd.index("-p")


def test_build_claude_cmd_with_extra_args_appended_after_dash_p():
    cmd = ts.build_claude_cmd("claude", "claude-sonnet-5", extra_args=["--foo", "bar"])
    p_index = cmd.index("-p")
    assert cmd[p_index + 1:] == ["--foo", "bar"]


def test_build_claude_cmd_with_resume_and_extra_args_full_order():
    cmd = ts.build_claude_cmd(
        "claude", "claude-sonnet-5", resume_session_id="sess-1", extra_args=["--flag"]
    )
    assert cmd[0:6] == [
        "claude",
        "--dangerously-skip-permissions",
        "--permission-mode", "bypassPermissions",
        "--model", "claude-sonnet-5",
    ]
    assert cmd[6] == "--append-system-prompt"
    assert isinstance(cmd[7], str) and len(cmd[7]) > 0
    assert cmd[8:] == [
        "--output-format", "stream-json",
        "--verbose",
        "--resume", "sess-1",
        "-p",
        "--flag",
    ]


def test_build_claude_cmd_model_passed_through_unmodified():
    cmd = ts.build_claude_cmd("claude", "claude-opus-5-some-special-id")
    assert "claude-opus-5-some-special-id" in cmd
