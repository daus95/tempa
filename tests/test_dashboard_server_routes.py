"""Characterization tests for dashboard_server._DashboardHandler's HTTP surface.

This is a SAFETY NET, not a design document: it pins down what every /api/* route
currently answers — status code, JSON keys, and the exact error strings the dashboard's
JS shows the user — so the handler can be split into per-domain modules without silently
changing any of it. Written against the real thing: a ThreadingHTTPServer bound to
127.0.0.1:0, driven over http.client, exactly like dashboard_ui.run_dashboard wires it.

Anything that would leave the test process (a backend probe, a `tempa.py` subprocess, the
GitHub release check, the native folder picker, SMTP) is patched at the seam
dashboard_server itself uses, so what's under test stays the handler's own routing and
validation logic.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import dashboard_api_settings
import dashboard_api_status
import dashboard_runs
import dashboard_server
import dashboard_verify
import tempa_backend
import tempa_config
import tempa_update

STAGES = ("clarify", "clarify_apply", "plan", "implement")

FAKE_BACKEND_STATUS = {
    "claude": {"installed": True, "ready": True, "label": "Claude Code"},
    "copilot": {"installed": False, "ready": False, "label": "GitHub Copilot CLI"},
    "codex": {"installed": False, "ready": False, "label": "OpenAI Codex CLI"},
}


def _item(item_id: str, severity: str, answer: str) -> str:
    return (
        f'<!-- clarify:item id="{item_id}" severity="{severity}" -->\n'
        f"### Title {item_id}\n"
        f"**Where:** somewhere\n"
        f"**Question:** what?\n"
        f"**Recommendation:** do X\n"
        f"**Your answer:** <!-- clarify:answer-start -->\n{answer}\n<!-- clarify:answer-end -->\n"
        f"<!-- clarify:enditem -->\n"
    )


class _Client:
    """Tiny HTTP helper returning (status, parsed-json-or-raw-text) per request."""

    def __init__(self, server: ThreadingHTTPServer):
        self.host, self.port = server.server_address[0], server.server_address[1]
        self.server = server

    def _request(self, method: str, path: str, body: bytes | None = None) -> tuple[int, object]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            headers = {"Content-Length": str(len(body))} if body is not None else {}
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
        finally:
            conn.close()
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw

    def get(self, path: str) -> tuple[int, object]:
        return self._request("GET", path)

    def post(self, path: str, payload=None, raw: bytes | None = None) -> tuple[int, object]:
        if raw is not None:
            body = raw
        elif payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        return self._request("POST", path, body)


@pytest.fixture
def dash(tmp_path, monkeypatch):
    """A live dashboard server over a tmp workspace, with every out-of-process seam faked."""
    workspace = tmp_path / "workspace"
    prd_dir = workspace / "specs" / "prd"
    clar_dir = workspace / "specs" / "clarifications"
    prd_dir.mkdir(parents=True)
    clar_dir.mkdir(parents=True)
    tempa_config.set_active_workspace_root(workspace)

    # Backend readiness shells out to `which`/`--version` probes; pin it so responses
    # don't depend on what happens to be installed on the machine running the suite.
    monkeypatch.setattr(tempa_backend, "get_backend_status", lambda writable: FAKE_BACKEND_STATUS)

    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server._DashboardHandler)
    server.prd_dir = prd_dir
    server.clar_dir = clar_dir
    server.page_html = "<html><body>dashboard</body></html>"
    server.any_saved = False
    server.clarify_run = dashboard_runs._new_clarify_run_state()
    server.implement_run = dashboard_runs._new_implement_run_state()
    server.verify_runs = {}

    # A short poll interval keeps the per-test shutdown() from costing serve_forever's
    # 0.5s default tick — with ~100 tests that alone dominated the suite's runtime.
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    client = _Client(server)
    client.prd_dir = prd_dir
    client.clar_dir = clar_dir
    client.workspace = workspace
    try:
        yield client
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def test_unknown_get_route_is_404_plain_text(dash):
    assert dash.get("/api/nope") == (404, "Not found")


def test_unknown_post_route_is_404_plain_text(dash):
    assert dash.post("/api/nope", {}) == (404, "Not found")


def test_root_serves_the_prerendered_page(dash):
    status, body = dash.get("/")
    assert status == 200
    assert body == dash.server.page_html


def test_guide_pages_are_served(dash):
    for route in ("/architecture-principles", "/spec-guide"):
        status, body = dash.get(route)
        assert status == 200
        assert "<html" in body.lower()


# ---------------------------------------------------------------------------
# GET /api/tree
# ---------------------------------------------------------------------------
def test_tree_returns_the_full_first_paint_payload(dash):
    (dash.prd_dir / "PRD.md").write_text("# PRD\n", encoding="utf-8")
    status, body = dash.get("/api/tree")
    assert status == 200
    assert body["ok"] is True
    assert set(body) == {"ok", "workspace", "spec", "clarify", "principles", "backends"}
    assert set(body["workspace"]) == {"initialized", "root", "canClose"}
    assert body["spec"]["tree"]["name"] == dash.prd_dir.name
    assert set(body["clarify"]) == {
        "unanswered", "answered", "findings", "finalize", "implementReadiness",
        "pendingOverlay", "overlayWarnThreshold", "skipMinorFindings",
    }
    assert body["backends"] == FAKE_BACKEND_STATUS


# ---------------------------------------------------------------------------
# GET /api/spec/file
# ---------------------------------------------------------------------------
def test_spec_file_returns_markdown_content(dash):
    (dash.prd_dir / "PRD.md").write_text("# Hello\n", encoding="utf-8")
    status, body = dash.get("/api/spec/file?path=PRD.md")
    assert status == 200
    assert body == {"ok": True, "path": "PRD.md", "markdown": True, "text": True, "content": "# Hello\n"}


def test_spec_file_missing_is_404(dash):
    assert dash.get("/api/spec/file?path=nope.md") == (404, {"ok": False, "error": "File not found."})


def test_spec_file_rejects_traversal(dash):
    (dash.prd_dir.parent / "outside.md").write_text("secret", encoding="utf-8")
    assert dash.get("/api/spec/file?path=../outside.md") == (
        404, {"ok": False, "error": "File not found."})


def test_spec_file_reports_non_text_types_without_content(dash):
    (dash.prd_dir / "diagram.png").write_bytes(b"\x89PNG\r\n")
    status, body = dash.get("/api/spec/file?path=diagram.png")
    assert status == 200
    assert body["ok"] is True and body["text"] is False and body["content"] == ""
    assert body["reason"] == "This file type is not viewable as text."


# ---------------------------------------------------------------------------
# GET /api/clarify/file
# ---------------------------------------------------------------------------
def test_clarify_file_summarizes_findings(dash):
    path = dash.clar_dir / "clarification-20260101-000000.md"
    path.write_text(_item("1", "critical", "answered") + _item("2", "minor", ""), encoding="utf-8")
    status, body = dash.get("/api/clarify/file?path=clarification-20260101-000000.md")
    assert status == 200
    assert body["ok"] is True
    assert body["name"] == path.name
    assert body["summary"] == "2 finding(s) — 1 critical · 0 major · 1 minor"
    assert (body["answered"], body["total"]) == (1, 2)
    assert "<" in body["html"]


def test_clarify_file_without_items_says_so(dash):
    (dash.clar_dir / "plain.md").write_text("just prose\n", encoding="utf-8")
    status, body = dash.get("/api/clarify/file?path=plain.md")
    assert status == 200
    assert body["summary"] == "No recognized clarification items in this file."
    assert body["html"] == "<p>No recognized clarification items in this file.</p>"
    assert (body["answered"], body["total"]) == (0, 0)


def test_clarify_file_missing_is_404(dash):
    assert dash.get("/api/clarify/file?path=nope.md") == (
        404, {"ok": False, "error": "File not found."})


# ---------------------------------------------------------------------------
# GET /api/log-file and /api/qa-report
# ---------------------------------------------------------------------------
def test_log_file_serves_a_txt_log(dash):
    logs = tempa_config.get_logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "session_EPIC-01_20260101_000000.txt").write_text("line\n", encoding="utf-8")
    status, body = dash.get("/api/log-file?name=session_EPIC-01_20260101_000000.txt")
    assert status == 200
    assert body == {"ok": True, "name": "session_EPIC-01_20260101_000000.txt",
                    "content": "line\n", "truncated": False}


def test_log_file_rejects_non_txt(dash):
    logs = tempa_config.get_logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "notes.md").write_text("x", encoding="utf-8")
    assert dash.get("/api/log-file?name=notes.md") == (
        404, {"ok": False, "error": "Log file not found."})


def test_log_file_truncates_to_the_tail(dash, monkeypatch):
    monkeypatch.setattr(dashboard_api_status, "LOG_FILE_MAX_CHARS", 10)
    logs = tempa_config.get_logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "big.txt").write_text("0123456789ABCDE", encoding="utf-8")
    status, body = dash.get("/api/log-file?name=big.txt")
    assert status == 200
    assert body["truncated"] is True
    assert body["content"] == "56789ABCDE"


def test_qa_report_serves_markdown(dash):
    qa = tempa_config.get_qa_dir()
    qa.mkdir(parents=True, exist_ok=True)
    (qa / "EPIC-01-qa-20260101_000000.md").write_text("# QA\n", encoding="utf-8")
    status, body = dash.get("/api/qa-report?name=EPIC-01-qa-20260101_000000.md")
    assert status == 200
    assert body["ok"] is True and body["content"] == "# QA\n"


def test_qa_report_rejects_non_markdown(dash):
    qa = tempa_config.get_qa_dir()
    qa.mkdir(parents=True, exist_ok=True)
    (qa / "report.txt").write_text("x", encoding="utf-8")
    assert dash.get("/api/qa-report?name=report.txt") == (
        404, {"ok": False, "error": "QA report not found."})


# ---------------------------------------------------------------------------
# GET run-status / config / principles / backends / update
# ---------------------------------------------------------------------------
def test_clarify_run_status_shape_when_idle(dash):
    status, body = dash.get("/api/clarify/run?since=0")
    assert status == 200
    assert set(body) == {"ok", "running", "mode", "returncode", "lines", "next", "progress",
                         "gracefulStopRequested", "finalizeRound", "maxRound", "finalizePhase"}
    assert body["running"] is False and body["lines"] == [] and body["next"] == 0


def test_clarify_run_status_tolerates_a_bad_since(dash):
    status, body = dash.get("/api/clarify/run?since=abc")
    assert status == 200 and body["next"] == 0


def test_implement_run_status_shape_when_idle(dash):
    status, body = dash.get("/api/implement/run?since=0")
    assert status == 200
    assert set(body) == {"ok", "running", "returncode", "lines", "next", "progress",
                         "gracefulStopRequested", "epics", "started"}
    assert body["epics"] == [] and body["started"] is False


def test_config_get_returns_every_settings_field(dash):
    status, body = dash.get("/api/config")
    assert status == 200
    assert set(body["config"]) == {
        "models", "backends", "reasoning_efforts", "features_per_session", "max_session_run",
        "max_clarification_run", "finalize_no_progress_rounds", "allow_finalize_with_critical",
        "commit_after_qa_pass", "implementation_start_requirement", "notifications",
        "usage_limit_retry_wait_sec", "usage_limit_heartbeat_sec",
        "server_overloaded_retry_wait_sec", "poll_interval_sec", "backends_status",
    }
    assert body["config"]["backends_status"] == FAKE_BACKEND_STATUS


def test_backends_status_endpoint(dash):
    assert dash.get("/api/backends/status") == (200, {"ok": True, "backends": FAKE_BACKEND_STATUS})


def test_principles_get_is_empty_when_unset(dash):
    assert dash.get("/api/principles") == (200, {"ok": True, "content": ""})


def test_update_status_reports_an_available_update(dash, monkeypatch):
    monkeypatch.setattr(tempa_update, "get_local_version", lambda: "0.1.0")
    monkeypatch.setattr(tempa_update, "get_latest_release_version", lambda: "0.2.0")
    assert dash.get("/api/update/status") == (
        200, {"ok": True, "current": "0.1.0", "latest": "0.2.0", "updateAvailable": True})


def test_update_status_when_github_is_unreachable(dash, monkeypatch):
    monkeypatch.setattr(tempa_update, "get_local_version", lambda: "0.1.0")
    monkeypatch.setattr(tempa_update, "get_latest_release_version", lambda: None)
    status, body = dash.get("/api/update/status")
    assert status == 200
    assert body["updateAvailable"] is False
    assert body["error"] == "Could not reach GitHub to check the latest release."


# ---------------------------------------------------------------------------
# GET verify
# ---------------------------------------------------------------------------
def test_verify_runs_is_empty_initially(dash):
    assert dash.get("/api/verify/runs") == (200, {"ok": True, "runs": []})


def test_verify_detail_missing_is_404(dash):
    assert dash.get("/api/verify/detail?id=nope") == (
        404, {"ok": False, "error": "Verification run not found."})


# ---------------------------------------------------------------------------
# POST /api/spec/*
# ---------------------------------------------------------------------------
def test_spec_save_writes_lf_normalized_content(dash):
    target = dash.prd_dir / "PRD.md"
    target.write_text("old\n", encoding="utf-8")
    status, body = dash.post("/api/spec/save", {"path": "PRD.md", "content": "a\r\nb\rc"})
    assert (status, body) == (200, {"ok": True, "path": "PRD.md"})
    assert target.read_bytes() == b"a\nb\nc"


def test_spec_save_rejects_a_malformed_body(dash):
    assert dash.post("/api/spec/save", raw=b"not json") == (
        400, {"ok": False, "error": "Malformed request."})


def test_spec_save_rejects_non_text_content(dash):
    (dash.prd_dir / "PRD.md").write_text("x", encoding="utf-8")
    assert dash.post("/api/spec/save", {"path": "PRD.md", "content": 5}) == (
        400, {"ok": False, "error": "Content must be text."})


def test_spec_save_rejects_a_path_outside_the_prd_folder(dash):
    assert dash.post("/api/spec/save", {"path": "../escape.md", "content": "x"}) == (
        400, {"ok": False, "error": "Invalid path."})


def test_spec_save_missing_file_is_404(dash):
    assert dash.post("/api/spec/save", {"path": "gone.md", "content": "x"}) == (
        404, {"ok": False, "error": "File no longer exists."})


def test_spec_upload_creates_nested_folders(dash):
    status, body = dash.post("/api/spec/upload?path=sub/dir/new.md", raw=b"content")
    assert (status, body) == (200, {"ok": True, "path": "sub/dir/new.md"})
    assert (dash.prd_dir / "sub" / "dir" / "new.md").read_bytes() == b"content"


def test_spec_upload_rejects_traversal(dash):
    assert dash.post("/api/spec/upload?path=../evil.md", raw=b"x") == (
        400, {"ok": False, "error": "Invalid path."})


def test_spec_delete_removes_a_folder_tree(dash):
    (dash.prd_dir / "sub").mkdir()
    (dash.prd_dir / "sub" / "a.md").write_text("x", encoding="utf-8")
    assert dash.post("/api/spec/delete", {"path": "sub"}) == (200, {"ok": True, "path": "sub"})
    assert not (dash.prd_dir / "sub").exists()


def test_spec_delete_missing_is_404(dash):
    assert dash.post("/api/spec/delete", {"path": "gone.md"}) == (
        404, {"ok": False, "error": "File or folder no longer exists."})


def test_spec_rename_moves_the_file(dash):
    (dash.prd_dir / "old.md").write_text("x", encoding="utf-8")
    assert dash.post("/api/spec/rename", {"path": "old.md", "new_name": "new.md"}) == (
        200, {"ok": True, "path": "new.md"})
    assert (dash.prd_dir / "new.md").exists()


@pytest.mark.parametrize("new_name", ["", "a/b.md", "a\\b.md", ".", ".."])
def test_spec_rename_rejects_invalid_names(dash, new_name):
    (dash.prd_dir / "old.md").write_text("x", encoding="utf-8")
    assert dash.post("/api/spec/rename", {"path": "old.md", "new_name": new_name}) == (
        400, {"ok": False, "error": "Invalid new name."})


def test_spec_rename_onto_an_existing_name_is_409(dash):
    (dash.prd_dir / "old.md").write_text("x", encoding="utf-8")
    (dash.prd_dir / "taken.md").write_text("y", encoding="utf-8")
    assert dash.post("/api/spec/rename", {"path": "old.md", "new_name": "taken.md"}) == (
        409, {"ok": False, "error": '"taken.md" already exists.'})


# ---------------------------------------------------------------------------
# POST /api/clarify/*
# ---------------------------------------------------------------------------
def test_clarify_save_writes_answers_and_reports_counts(dash):
    path = dash.clar_dir / "clarification-20260101-000000.md"
    path.write_text(_item("1", "critical", "") + _item("2", "minor", ""), encoding="utf-8")
    # Item ids are the parser's composite keys ("f<file-index>-<raw id>"), not the raw
    # id from the marker — the handler parses with file_index 0 (see parse_file).
    status, body = dash.post("/api/clarify/save", {
        "path": path.name,
        "items": [{"id": "f0-1", "mode": "own", "answer": "my answer"}],
    })
    assert (status, body) == (200, {"ok": True, "path": path.name, "answered": 1, "total": 2})
    assert "my answer" in path.read_text(encoding="utf-8")
    assert dash.server.any_saved is True


def test_clarify_save_is_locked_while_finalize_runs(dash):
    dash.server.clarify_run["running"] = True
    dash.server.clarify_run["mode"] = "finalize"
    status, body = dash.post("/api/clarify/save", {"path": "x.md", "items": []})
    assert status == 409
    assert body["error"].startswith("Finalized Clarification is running")


def test_clarify_save_missing_file_is_404(dash):
    assert dash.post("/api/clarify/save", {"path": "gone.md", "items": []}) == (
        404, {"ok": False, "error": "File no longer exists."})


def test_clarify_run_rejects_an_unknown_mode(dash):
    assert dash.post("/api/clarify/run", {"mode": "bogus"}) == (
        400, {"ok": False, "error": "Invalid mode."})


def test_clarify_run_starts_a_run(dash, monkeypatch):
    seen = []
    monkeypatch.setattr(dashboard_server, "_start_clarify_run",
                        lambda server, mode: seen.append(mode) or True)
    assert dash.post("/api/clarify/run", {"mode": "run"}) == (200, {"ok": True})
    assert seen == ["run"]


def test_clarify_run_conflicts_when_one_is_already_going(dash, monkeypatch):
    monkeypatch.setattr(dashboard_server, "_start_clarify_run", lambda server, mode: False)
    assert dash.post("/api/clarify/run", {"mode": "run"}) == (
        409, {"ok": False, "error": "A clarification run is already in progress."})


def test_clarify_finalize_is_gated_on_a_clean_evaluation(dash):
    status, body = dash.post("/api/clarify/run", {"mode": "finalize"})
    assert status == 409
    assert body["error"].startswith("Cannot finalize yet")


@pytest.mark.parametrize("route", [
    "/api/clarify/stop", "/api/clarify/stop-graceful", "/api/clarify/stop-graceful/cancel",
])
def test_clarify_stop_routes_conflict_when_nothing_runs(dash, route):
    assert dash.post(route, {}) == (
        409, {"ok": False, "error": "No clarification run is currently in progress."})


def test_clarify_skip_minor_persists_the_toggle(dash):
    assert dash.post("/api/clarify/skip-minor", {"skip_minor_findings": False}) == (
        200, {"ok": True, "skipMinorFindings": False})
    assert tempa_config.load_config()["skip_minor_findings"] is False


def test_clarify_skip_minor_requires_a_boolean(dash):
    assert dash.post("/api/clarify/skip-minor", {"skip_minor_findings": "no"}) == (
        400, {"ok": False, "error": "Malformed request."})


# ---------------------------------------------------------------------------
# POST /api/implement/*
# ---------------------------------------------------------------------------
def test_implement_run_is_blocked_before_any_clarification(dash):
    status, body = dash.post("/api/implement/run", {})
    assert status == 409
    assert body["error"] == (
        "Cannot start implementation before clarification has been run at least once.")


def test_implement_run_starts_once_clarification_is_clean(dash, monkeypatch):
    config = tempa_config.load_config()
    config["last_clarification_action"] = "evaluate"
    tempa_config.save_config(config)
    monkeypatch.setattr(dashboard_server, "_start_implement_run", lambda server: True)
    assert dash.post("/api/implement/run", {}) == (200, {"ok": True})


def test_implement_run_conflicts_when_already_running(dash, monkeypatch):
    config = tempa_config.load_config()
    config["last_clarification_action"] = "evaluate"
    tempa_config.save_config(config)
    monkeypatch.setattr(dashboard_server, "_start_implement_run", lambda server: False)
    assert dash.post("/api/implement/run", {}) == (
        409, {"ok": False, "error": "Implementation is already running."})


@pytest.mark.parametrize("route", [
    "/api/implement/stop", "/api/implement/stop-graceful", "/api/implement/stop-graceful/cancel",
])
def test_implement_stop_routes_conflict_when_nothing_runs(dash, route):
    assert dash.post(route, {}) == (409, {"ok": False, "error": "Implementation is not running."})


# ---------------------------------------------------------------------------
# POST /api/verify/*
# ---------------------------------------------------------------------------
def test_verify_run_requires_an_epic(dash):
    assert dash.post("/api/verify/run", {"epic": "  "}) == (
        400, {"ok": False, "error": "Missing epic."})


def test_verify_run_starts(dash, monkeypatch):
    monkeypatch.setattr(dashboard_server, "_start_verify_run", lambda server, epic: True)
    assert dash.post("/api/verify/run", {"epic": "EPIC-01"}) == (200, {"ok": True})


def test_verify_run_conflicts_when_already_running(dash, monkeypatch):
    monkeypatch.setattr(dashboard_server, "_start_verify_run", lambda server, epic: False)
    assert dash.post("/api/verify/run", {"epic": "EPIC-01"}) == (
        409, {"ok": False, "error": 'A verification run for "EPIC-01" is already in progress.'})


def test_verify_stop_without_a_run_is_409(dash):
    assert dash.post("/api/verify/stop", {"epic": "EPIC-01"}) == (
        409, {"ok": False, "error": "No verification run is currently in progress for that epic."})


def test_verify_delete_missing_is_404(dash):
    assert dash.post("/api/verify/delete", {"id": "nope"}) == (
        404, {"ok": False, "error": "Verification run not found."})


def test_verify_delete_removes_a_report(dash, monkeypatch):
    monkeypatch.setattr(dashboard_server, "_delete_verify_run", lambda run_id: True)
    assert dash.post("/api/verify/delete", {"id": "some-id"}) == (200, {"ok": True})
    assert dashboard_verify is not None  # imported for the module-level seam above


# ---------------------------------------------------------------------------
# POST /api/config/save
# ---------------------------------------------------------------------------
def _config_payload(**overrides) -> dict:
    payload = {
        "models": {stage: "sonnet-5" for stage in STAGES},
        "backends": {stage: "claude" for stage in STAGES},
        "reasoning_efforts": {stage: "" for stage in STAGES},
        "features_per_session": 3,
        "max_session_run": 30,
        "max_clarification_run": 20,
        "finalize_no_progress_rounds": 5,
        "usage_limit_retry_wait_sec": 1800,
        "usage_limit_heartbeat_sec": 300,
        "server_overloaded_retry_wait_sec": 300,
        "poll_interval_sec": 60,
        "allow_finalize_with_critical": False,
        "commit_after_qa_pass": True,
        "implementation_start_requirement": "no_critical_or_major",
        "notifications": {"email": {"enabled": False}},
    }
    payload.update(overrides)
    return payload


def test_config_save_persists_and_echoes_the_settings(dash):
    status, body = dash.post("/api/config/save", _config_payload())
    assert status == 200
    assert body["ok"] is True and body["warning"] is None
    # Friendly aliases are resolved to full Claude model ids on the way in.
    assert body["config"]["models"] == {stage: "claude-sonnet-5" for stage in STAGES}
    assert body["config"]["backends_status"] == FAKE_BACKEND_STATUS
    saved = tempa_config.load_config()
    assert saved["models"] == {stage: "claude-sonnet-5" for stage in STAGES}
    assert saved["poll_interval_sec"] == 60


def test_config_save_keeps_non_claude_model_strings_verbatim(dash):
    payload = _config_payload(
        backends={stage: "codex" for stage in STAGES},
        models={stage: "gpt-5-codex" for stage in STAGES},
    )
    status, body = dash.post("/api/config/save", payload)
    assert status == 200
    assert body["config"]["models"] == {stage: "gpt-5-codex" for stage in STAGES}


def test_config_save_rejects_a_malformed_body(dash):
    assert dash.post("/api/config/save", raw=b"[]") == (
        400, {"ok": False, "error": "Malformed request."})


def test_config_save_rejects_a_missing_models_block(dash):
    payload = _config_payload()
    del payload["models"]
    assert dash.post("/api/config/save", payload) == (
        400, {"ok": False, "error": "Malformed request."})


def test_config_save_rejects_an_unknown_backend(dash):
    payload = _config_payload(backends={**{s: "claude" for s in STAGES}, "plan": "bogus"})
    status, body = dash.post("/api/config/save", payload)
    assert status == 400
    assert body["error"].startswith("The plan backend must be one of: ")


def test_config_save_rejects_an_empty_model(dash):
    payload = _config_payload(models={**{s: "sonnet-5" for s in STAGES}, "implement": "  "})
    assert dash.post("/api/config/save", payload) == (
        400, {"ok": False, "error": "The implement model cannot be empty."})


def test_config_save_rejects_an_invalid_reasoning_effort(dash):
    payload = _config_payload(reasoning_efforts={**{s: "" for s in STAGES}, "clarify": "turbo"})
    status, body = dash.post("/api/config/save", payload)
    assert status == 400
    assert body["error"].startswith("The clarify reasoning effort must be empty or one of: ")


@pytest.mark.parametrize("field,message", [
    ("features_per_session", "Features per Session must be empty or a positive whole number."),
    ("max_session_run", "Max Session Runs must be empty or a positive whole number."),
    ("max_clarification_run", "Max Finalize Clarification Round must be a positive whole number."),
    ("finalize_no_progress_rounds", "Max Finalize No-Progress Round must be a positive whole number."),
    ("usage_limit_retry_wait_sec", "Usage Limit Retry Wait must be a positive whole number."),
    ("usage_limit_heartbeat_sec", "Usage Limit Heartbeat Interval must be a positive whole number."),
    ("server_overloaded_retry_wait_sec", "Server Overload Retry Wait must be a positive whole number."),
    ("poll_interval_sec", "Implementation Poll Interval must be a positive whole number."),
])
def test_config_save_rejects_non_positive_limits(dash, field, message):
    assert dash.post("/api/config/save", _config_payload(**{field: 0})) == (
        400, {"ok": False, "error": message})


@pytest.mark.parametrize("field", ["max_clarification_run", "finalize_no_progress_rounds",
                                    "usage_limit_retry_wait_sec", "poll_interval_sec"])
def test_config_save_rejects_blank_required_limits(dash, field):
    status, body = dash.post("/api/config/save", _config_payload(**{field: ""}))
    assert status == 400 and body["ok"] is False


def test_config_save_allows_blank_optional_limits(dash):
    status, body = dash.post(
        "/api/config/save", _config_payload(features_per_session="", max_session_run=""))
    assert status == 200
    assert body["config"]["features_per_session"] is None
    assert body["config"]["max_session_run"] is None


def test_config_save_rejects_an_unknown_start_requirement(dash):
    status, body = dash.post(
        "/api/config/save", _config_payload(implementation_start_requirement="whenever"))
    assert status == 400
    assert body["error"].startswith("Start Implementation requirement must be one of: ")


def test_config_save_rejects_bad_smtp_numbers(dash):
    payload = _config_payload(notifications={"email": {"enabled": False, "smtp_port": "many"}})
    assert dash.post("/api/config/save", payload) == (
        400, {"ok": False, "error": "SMTP port and timeout must be whole numbers."})


def test_config_save_rejects_an_out_of_range_smtp_port(dash):
    payload = _config_payload(notifications={"email": {"enabled": False, "smtp_port": 70000}})
    assert dash.post("/api/config/save", payload) == (
        400, {"ok": False, "error": "Invalid SMTP security, port, or timeout."})


def test_config_save_rejects_non_list_recipients(dash):
    payload = _config_payload(
        notifications={"email": {"enabled": False, "recipients": "a@b.c"}})
    assert dash.post("/api/config/save", payload) == (
        400, {"ok": False, "error": "Email recipients and events must be lists."})


def test_config_save_requires_smtp_details_when_email_is_enabled(dash):
    payload = _config_payload(notifications={"email": {"enabled": True}})
    assert dash.post("/api/config/save", payload) == (
        400, {"ok": False,
              "error": "Enabled email needs SMTP host, sender, and at least one recipient."})


def test_config_save_normalizes_enabled_email(dash):
    payload = _config_payload(notifications={"email": {
        "enabled": True, "smtp_host": "smtp.example.com", "from": "bot@example.com",
        "recipients": ["  a@example.com  ", "", 5], "events": ["plan_failed", "not_an_event"],
    }})
    status, body = dash.post("/api/config/save", payload)
    assert status == 200
    email = body["config"]["notifications"]["email"]
    assert email["recipients"] == ["a@example.com"]
    assert email["events"] == ["plan_failed"]


# ---------------------------------------------------------------------------
# POST principles / test-email / clear / update / restart
# ---------------------------------------------------------------------------
def test_principles_save_writes_the_document(dash):
    status, body = dash.post("/api/principles/save", {"content": "Rule 1\r\nRule 2\r\n"})
    assert (status, body) == (200, {"ok": True, "content": "Rule 1\nRule 2"})
    assert tempa_config.get_principles_path().read_bytes() == b"Rule 1\nRule 2\n"


def test_principles_save_with_blank_content_clears_the_file(dash):
    dash.post("/api/principles/save", {"content": "Rule 1"})
    assert dash.post("/api/principles/save", {"content": "   "}) == (
        200, {"ok": True, "content": ""})
    assert not tempa_config.get_principles_path().exists()


def test_principles_save_rejects_non_text(dash):
    assert dash.post("/api/principles/save", {"content": 5}) == (
        400, {"ok": False, "error": "Content must be text."})


def test_test_email_reports_the_notification_result(dash, monkeypatch):
    monkeypatch.setattr(dashboard_api_settings, "send_test_email", lambda: (False, "SMTP is off."))
    assert dash.post("/api/notifications/test-email", {}) == (
        400, {"ok": False, "message": "SMTP is off."})


def test_clear_is_blocked_while_a_run_is_in_progress(dash):
    dash.server.implement_run["running"] = True
    status, body = dash.post("/api/clear", {})
    assert status == 409
    assert body["error"] == "Cannot clear while a clarify or implementation run is in progress."


def test_update_run_is_blocked_while_a_run_is_in_progress(dash):
    dash.server.clarify_run["running"] = True
    status, body = dash.post("/api/update/run", {})
    assert status == 409
    assert body["error"] == "Cannot update while a clarify or implementation run is in progress."


def test_server_restart_is_blocked_while_a_run_is_in_progress(dash):
    dash.server.clarify_run["running"] = True
    status, body = dash.post("/api/server/restart", {})
    assert status == 409
    assert body["error"] == "Cannot restart while a clarify or implementation run is in progress."


def test_workspace_open_without_a_workspace_folder_is_404(dash, monkeypatch):
    tempa_config.clear_active_workspace_root()
    status, body = dash.post("/api/workspace/open", {})
    assert status == 404
    assert body["error"] == "Working folder not found on disk."
