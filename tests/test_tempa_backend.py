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
    for marker in (backend.usage_limit_markers + backend.auth_error_markers
                   + backend.overloaded_markers + backend.model_error_markers):
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
# reasoning_effort_advisory
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model,effort", [
    ("claude-haiku-4-5-20251001", "max"),   # no effort support at all
    ("claude-haiku-4-5-20251001", "low"),
    ("claude-opus-4-6", "xhigh"),           # supports max, but not xhigh
    ("claude-sonnet-4-6", "xhigh"),
    ("claude-opus-4-5-20251101", "max"),    # neither of the top two
])
def test_claude_advises_on_a_level_the_model_does_not_honour(model, effort):
    advisory = tb.CLAUDE.reasoning_effort_advisory(model, effort)
    assert advisory is not None
    assert model in advisory


@pytest.mark.parametrize("model,effort", [
    ("claude-opus-5", "max"),
    ("claude-opus-5", "xhigh"),
    ("claude-fable-5-1", "max"),
    ("claude-sonnet-5", "xhigh"),
    ("claude-opus-4-6", "max"),
    ("claude-opus-4-5-20251101", "high"),
])
def test_claude_says_nothing_about_a_supported_pair(model, effort):
    assert tb.CLAUDE.reasoning_effort_advisory(model, effort) is None


def test_claude_says_nothing_about_a_model_it_does_not_know():
    """Guessing would produce a confidently wrong note about a configuration that is fine.
    Going stale therefore costs a note, never a working setup."""
    assert tb.CLAUDE.reasoning_effort_advisory("claude-opus-9", "max") is None
    assert tb.CLAUDE.reasoning_effort_advisory("some-future-model", "xhigh") is None


def test_an_empty_effort_is_never_advised_about():
    """"" means "use the model's own default", which every model has."""
    assert tb.CLAUDE.reasoning_effort_advisory("claude-haiku-4-5-20251001", "") is None
    assert tb.CLAUDE.reasoning_effort_advisory("claude-opus-4-6", "  ") is None


def test_the_advisory_never_becomes_a_rejection():
    """The distinction the whole feature rests on. Verified live against claude 2.1.258:
    `--effort max` on Haiku 4.5 runs fine with no error, so refusing it here would block a
    configuration the CLI itself accepts. It just quietly does nothing."""
    assert tb.CLAUDE.reasoning_effort_advisory("claude-haiku-4-5-20251001", "max") is not None
    assert tb.is_valid_reasoning_effort(tb.CLAUDE, "claude-haiku-4-5-20251001", "max") is True


def test_codex_stays_a_hard_rejection_rather_than_an_advisory():
    """The opposite case, and why this is per-backend: Codex's API returns a real
    `[reasoning.effort]` error for an unsupported level, so there is something to prevent."""
    assert tb.is_valid_reasoning_effort(tb.CODEX, "gpt-5.4", "ultra") is False
    assert tb.CODEX.reasoning_effort_advisory("gpt-5.4", "ultra") is None


@pytest.mark.parametrize("backend", [tb.COPILOT, tb.CODEX], ids=lambda b: b.name)
def test_backends_without_published_per_model_effort_data_advise_nothing(backend):
    assert backend.reasoning_effort_advisory("claude-opus-5", "max") is None


def test_claude_effort_table_models_are_all_anthropic():
    """Ties the effort table to the vendor table: a model Claude has effort data for had
    better be one Claude is allowed to run."""
    for model in tb.CLAUDE_MODEL_EFFORT_LEVELS:
        assert tb.model_backend_mismatch(tb.CLAUDE, model) is None, model


def test_every_claude_effort_level_listed_is_a_real_cli_value():
    """The table says which of the CLI's levels a model honours — it can never introduce a
    level the flag would reject outright."""
    for model, levels in tb.CLAUDE_MODEL_EFFORT_LEVELS.items():
        for level in levels:
            assert level in tb.CLAUDE_EFFORT_LEVELS, (model, level)


# ---------------------------------------------------------------------------
# model_vendor / model_backend_mismatch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model,expected", [
    ("claude-opus-5", "anthropic"),
    ("claude-haiku-4-5-20251001", "anthropic"),
    ("gpt-5.6-sol", "openai"),
    ("codex-auto-review", "openai"),
    ("o3-mini", "openai"),
])
def test_model_vendor_recognizes_each_family(model, expected):
    assert tb.model_vendor(model) == expected


def test_model_vendor_normalizes_case_and_whitespace():
    assert tb.model_vendor("  Claude-Sonnet-5  ") == "anthropic"
    assert tb.model_vendor("GPT-5.6-Sol") == "openai"


@pytest.mark.parametrize("model", ["", "   ", "auto", "some-internal-model", "my-finetune-v3"])
def test_model_vendor_is_none_for_anything_it_cannot_place(model):
    """The model field is free text (docs/ai-models.md). An id from no known family belongs
    to nobody, so it must never be attributed — and therefore never blocked."""
    assert tb.model_vendor(model) is None


def test_model_vendor_recognizes_bare_claude_aliases():
    """Aliases are resolved only for a `claude` stage, so a literal "opus-5" really does
    reach config.json under backends.clarify = "codex"."""
    assert tb.model_vendor("opus-5") == "anthropic"
    assert tb.model_vendor("sonnet") == "anthropic"


def test_model_backend_mismatch_flags_the_other_vendors_model():
    assert tb.model_backend_mismatch(tb.CODEX, "claude-sonnet-5") == "anthropic"
    assert tb.model_backend_mismatch(tb.CODEX, "opus-5") == "anthropic"
    assert tb.model_backend_mismatch(tb.CLAUDE, "gpt-5.6-sol") == "openai"


def test_model_backend_mismatch_never_flags_copilot():
    """Copilot proxies several providers, so an Anthropic model on it is a valid pair. This
    is the regression that matters most: flagging it would break a working configuration."""
    assert tb.model_backend_mismatch(tb.COPILOT, "claude-sonnet-5") is None
    assert tb.model_backend_mismatch(tb.COPILOT, "gpt-5.6-sol") is None
    assert tb.model_backend_mismatch(tb.COPILOT, "auto") is None


@pytest.mark.parametrize("backend", tb.BACKENDS.values(), ids=lambda b: b.name)
def test_model_backend_mismatch_passes_its_own_vendors_and_unknown_ids(backend):
    assert tb.model_backend_mismatch(backend, "some-internal-model") is None
    # Empty is legal at runtime: copilot/codex omit --model entirely when it is falsy.
    assert tb.model_backend_mismatch(backend, "") is None


def test_model_mismatch_message_names_the_fix_and_every_alternative():
    message = tb.model_mismatch_message(tb.CODEX, "claude-sonnet-5", "anthropic")
    assert "claude-sonnet-5" in message
    assert "Anthropic" in message
    assert tb.CODEX.model_catalog_hint in message
    # Both backends that CAN run an Anthropic model are offered.
    assert tb.CLAUDE.label in message
    assert tb.COPILOT.label in message


@pytest.mark.parametrize("backend", tb.BACKENDS.values(), ids=lambda b: b.name)
def test_every_backend_declares_vendors_and_a_catalog_hint(backend):
    assert backend.model_vendors
    assert backend.model_catalog_hint


def test_model_vendor_tables_are_lowercase():
    for vendor, prefixes in tb.MODEL_VENDOR_PREFIXES:
        assert vendor in tb.MODEL_VENDOR_LABELS
        for prefix in prefixes:
            assert prefix == prefix.lower()
    for model_id in tb.MODEL_VENDOR_EXACT_IDS:
        assert model_id == model_id.lower()


def test_claude_aliases_do_not_drift_from_tempa_config():
    """MODEL_VENDOR_EXACT_IDS duplicates tempa_config.MODEL_ALIASES so tempa_backend can
    stay a dependency-free leaf module. This is what stops the copy from rotting."""
    import tempa_config

    for alias, full_id in tempa_config.MODEL_ALIASES.items():
        assert tb.model_vendor(alias) == "anthropic", alias
        assert tb.model_vendor(full_id) == "anthropic", full_id


def test_codex_reasoning_catalog_models_are_all_openai():
    """Ties the two hardcoded Codex catalogs together: a model Codex offers an effort list
    for had better be one Codex is allowed to run."""
    for model in tb.CODEX_MODEL_REASONING_LEVELS:
        assert tb.model_backend_mismatch(tb.CODEX, model) is None, model


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


# ---------------------------------------------------------------------------
# background_wait_env / background_terminated_markers
# ---------------------------------------------------------------------------

def test_claude_background_wait_env_converts_tempa_seconds_to_the_clis_milliseconds():
    assert tb.CLAUDE.background_wait_env(3600) == {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "3600000"}


def test_claude_background_wait_env_passes_zero_through_as_the_clis_wait_indefinitely_value():
    assert tb.CLAUDE.background_wait_env(0) == {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0"}


def test_claude_background_wait_env_renders_a_whole_number_not_a_float():
    # The value is exported into the environment verbatim, and the CLI parses it as an
    # integer count of milliseconds — "600000.0" would not survive that.
    assert tb.CLAUDE.background_wait_env(600.0) == {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "600000"}


@pytest.mark.parametrize("backend", [tb.COPILOT, tb.CODEX])
def test_backends_without_a_documented_knob_set_no_background_wait_environment(backend):
    assert backend.background_wait_env(3600) == {}


def test_claude_recognizes_its_own_background_task_termination_message():
    # Verbatim from the claude CLI, which is what tempa_session matches against.
    message = ("Background tasks still running after 600s; terminating. "
               "Set CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 to wait indefinitely.")
    assert any(marker in message.lower() for marker in tb.CLAUDE.background_terminated_markers)


def test_claude_background_terminated_marker_does_not_carry_the_seconds_count():
    # The count moves with whatever ceiling is configured, so a marker containing "600s"
    # would silently stop matching the moment backend_background_wait_sec is changed.
    assert all("600" not in marker for marker in tb.CLAUDE.background_terminated_markers)
