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
import dashboard_assets
import dashboard_runs
import dashboard_server
import dashboard_verify
import tempa_backend
import tempa_config
import tempa_decisions
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

    def get_raw(self, path: str) -> tuple[int, dict, bytes]:
        """Status, headers and undecoded body — for the one route whose bytes and whose
        caching headers are the point (the vendored mermaid bundle)."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

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
    epics_dir = workspace / "specs" / "pbi" / "epics"
    prd_dir.mkdir(parents=True)
    clar_dir.mkdir(parents=True)
    epics_dir.mkdir(parents=True)
    tempa_config.set_active_workspace_root(workspace)

    # Backend readiness shells out to `which`/`--version` probes; pin it so responses
    # don't depend on what happens to be installed on the machine running the suite.
    monkeypatch.setattr(tempa_backend, "get_backend_status", lambda writable: FAKE_BACKEND_STATUS)

    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server._DashboardHandler)
    server.prd_dir = prd_dir
    server.clar_dir = clar_dir
    server.epics_dir = epics_dir
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
    client.epics_dir = epics_dir
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


def test_the_mermaid_bundle_is_served_from_this_server(dash):
    """The dashboard renders ```mermaid blocks as diagrams by fetching this at runtime, so
    the bundle has to come off Tempa's own port — that is what keeps the page offline-safe."""
    status, headers, body = dash.get_raw(
        dashboard_assets.MERMAID_ROUTE + "?v=" + dashboard_assets.MERMAID_VERSION)
    assert status == 200
    assert headers["Content-Type"].startswith("text/javascript")
    assert int(headers["Content-Length"]) == len(body)
    assert body == dashboard_assets.mermaid_bundle()


def test_the_mermaid_bundle_is_the_one_cacheable_response(dash):
    """3.5 MB of bytes that never change for a given ?v=, unlike every other route here,
    which answers with live state and must not be cached at all."""
    _, headers, _ = dash.get_raw(dashboard_assets.MERMAID_ROUTE)
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"
    _, index_headers, _ = dash.get_raw("/")
    assert index_headers["Cache-Control"] == "no-store"


def test_the_mermaid_route_is_not_a_static_file_server(dash):
    """It is one fixed route, not a directory: nothing in the request names a file, so there
    is nothing to traverse out of."""
    assert dash.get("/assets/dashboard.css") == (404, "Not found")
    assert dash.get("/assets/../dashboard_server.py") == (404, "Not found")
    assert (dashboard_server._DashboardHandler.GET_ROUTES[dashboard_assets.MERMAID_ROUTE]
            == "_serve_mermaid")


def test_a_missing_mermaid_bundle_degrades_to_404(dash, monkeypatch):
    """An install stripped of the vendored file shows mermaid blocks as the plain code they
    are today — the route must not 500 the page's lazy fetch."""
    monkeypatch.setattr(dashboard_assets, "mermaid_bundle", lambda: None)
    monkeypatch.setattr(dashboard_server, "mermaid_bundle", lambda: None)
    assert dash.get(dashboard_assets.MERMAID_ROUTE) == (404, "Not found")


# ---------------------------------------------------------------------------
# GET /api/tree
# ---------------------------------------------------------------------------
def test_tree_returns_the_full_first_paint_payload(dash):
    (dash.prd_dir / "PRD.md").write_text("# PRD\n", encoding="utf-8")
    status, body = dash.get("/api/tree")
    assert status == 200
    assert body["ok"] is True
    assert set(body) == {"ok", "workspace", "spec", "clarify", "principles", "backends"}
    assert set(body["workspace"]) == {"initialized", "root", "canClose", "recent"}
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


# ---------------------------------------------------------------------------
# GET /api/epic/spec + POST /api/epic/spec/save
#
# The epic spec folder is a SIBLING of the PRD folder the Specification tree is rooted at,
# so none of it is reachable through /api/spec/file — yet it is the file QA grades an epic
# against, and the one to correct when QA rounds keep contradicting each other.
# ---------------------------------------------------------------------------
def test_epic_spec_is_found_by_label_alone(dash):
    """The epic's card only knows it as "EPIC-13"; the file carries a slug too."""
    (dash.epics_dir / "EPIC-13-nav-and-unitisation.md").write_text("# E13\n", encoding="utf-8")
    status, body = dash.get("/api/epic/spec?epic=EPIC-13")
    assert status == 200
    assert body["path"] == "EPIC-13-nav-and-unitisation.md"
    assert body["content"] == "# E13\n"
    assert body["epic"] == "EPIC-13" and body["markdown"] is True


def test_epic_spec_prefers_an_exact_name(dash):
    (dash.epics_dir / "EPIC-13.md").write_text("exact\n", encoding="utf-8")
    (dash.epics_dir / "EPIC-13-with-a-slug.md").write_text("slug\n", encoding="utf-8")
    assert dash.get("/api/epic/spec?epic=EPIC-13")[1]["content"] == "exact\n"


def test_epic_spec_does_not_match_a_longer_epic_number(dash):
    """Without requiring the separator, EPIC-1 would happily open EPIC-13's spec."""
    (dash.epics_dir / "EPIC-13-nav.md").write_text("# E13\n", encoding="utf-8")
    status, body = dash.get("/api/epic/spec?epic=EPIC-1")
    assert status == 404
    assert "No spec file for EPIC-1" in body["error"]


def test_epic_spec_refuses_to_guess_between_two_candidates(dash):
    (dash.epics_dir / "EPIC-13-first.md").write_text("one\n", encoding="utf-8")
    (dash.epics_dir / "EPIC-13-second.md").write_text("two\n", encoding="utf-8")
    assert dash.get("/api/epic/spec?epic=EPIC-13")[0] == 404


def test_epic_spec_missing_says_where_specs_come_from(dash):
    status, body = dash.get("/api/epic/spec?epic=EPIC-99")
    assert status == 404
    assert "tempa plan-epics" in body["error"]


@pytest.mark.parametrize("label", ["../secret", "a/b", ""])
def test_epic_spec_rejects_a_label_that_is_not_an_epic_id(dash, label):
    assert dash.get("/api/epic/spec?epic=" + label)[0] == 404


def test_epic_spec_save_writes_the_resolved_file(dash):
    target = dash.epics_dir / "EPIC-13-nav.md"
    target.write_text("# old\n", encoding="utf-8")

    status, body = dash.post("/api/epic/spec/save", {"epic": "EPIC-13", "content": "# new\n"})

    assert status == 200 and body["epic"] == "EPIC-13"
    assert target.read_text(encoding="utf-8") == "# new\n"


def test_epic_spec_save_takes_a_label_not_a_path(dash):
    """Save re-resolves the file from the epic id, so a path from the client addresses
    nothing — the only writable target is a spec that already exists for a real epic."""
    outside = dash.epics_dir.parent / "outside.md"
    outside.write_text("untouched\n", encoding="utf-8")

    status, _ = dash.post(
        "/api/epic/spec/save", {"epic": "../outside", "path": "../outside.md", "content": "hacked"})

    assert status == 404
    assert outside.read_text(encoding="utf-8") == "untouched\n"


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
        "finalize_checkpoint_rounds", "finalize_checkpoint_commit",
        "commit_after_qa_pass", "terminate_leftover_processes",
        "implementation_start_requirement", "notifications",
        "usage_limit_retry_wait_sec", "usage_limit_heartbeat_sec",
        "server_overloaded_retry_wait_sec", "poll_interval_sec", "backends_status",
        "qa_loop_strikes", "max_qa_fail_rounds",
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


def test_update_changelog_returns_content_for_a_valid_version(dash, monkeypatch):
    monkeypatch.setattr(tempa_update, "get_local_version", lambda: "0.1.0")
    monkeypatch.setattr(tempa_update, "get_changelog_since", lambda current, latest: "## [0.2.0] - 2026-01-02\n\n- did a thing")
    assert dash.get("/api/update/changelog?latest=0.2.0") == (
        200, {"ok": True, "content": "## [0.2.0] - 2026-01-02\n\n- did a thing", "truncated": False})


def test_update_changelog_rejects_malformed_version(dash):
    status, body = dash.get("/api/update/changelog?latest=not-a-version")
    assert status == 400
    assert body["ok"] is False


def test_update_changelog_when_github_is_unreachable(dash, monkeypatch):
    monkeypatch.setattr(tempa_update, "get_local_version", lambda: "0.1.0")
    monkeypatch.setattr(tempa_update, "get_changelog_since", lambda current, latest: None)
    status, body = dash.get("/api/update/changelog?latest=0.2.0")
    assert status == 200
    assert body["ok"] is False
    assert body["error"] == "Could not reach GitHub to fetch changelog details."


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
        "finalize_checkpoint_rounds": 5,
        "finalize_checkpoint_commit": True,
        "qa_loop_strikes": 2,
        "max_qa_fail_rounds": 6,
        "usage_limit_retry_wait_sec": 1800,
        "usage_limit_heartbeat_sec": 300,
        "server_overloaded_retry_wait_sec": 300,
        "poll_interval_sec": 60,
        "allow_finalize_with_critical": False,
        "commit_after_qa_pass": True,
        "terminate_leftover_processes": True,
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


def test_config_save_persists_leftover_process_termination_turned_off(dash):
    status, body = dash.post("/api/config/save", _config_payload(terminate_leftover_processes=False))
    assert status == 200
    assert body["config"]["terminate_leftover_processes"] is False
    assert tempa_config.load_config()["terminate_leftover_processes"] is False


def test_config_save_keeps_leftover_process_termination_on_when_the_field_is_absent(dash):
    """A client predating the toggle — a dashboard tab left open across an upgrade, a
    hand-built payload — must not silently turn containment off by omitting the field.
    Unlike every other toggle on this form, "off" has no observable symptom until orphaned
    processes have been piling up for hours, so an accidental flip is never traced back."""
    payload = _config_payload()
    del payload["terminate_leftover_processes"]
    status, body = dash.post("/api/config/save", payload)
    assert status == 200
    assert body["config"]["terminate_leftover_processes"] is True
    assert tempa_config.load_config()["terminate_leftover_processes"] is True


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


def test_config_save_persists_the_qa_loop_guard_limits(dash):
    """These used to be config.json-only, so giving an epic more rope before the QA loop guard
    halts the run meant hand-editing JSON."""
    status, body = dash.post(
        "/api/config/save", _config_payload(qa_loop_strikes=4, max_qa_fail_rounds=10))
    assert status == 200
    assert body["config"]["qa_loop_strikes"] == 4
    assert body["config"]["max_qa_fail_rounds"] == 10
    saved = tempa_config.load_config()
    assert (saved["qa_loop_strikes"], saved["max_qa_fail_rounds"]) == (4, 10)


@pytest.mark.parametrize("field", ["qa_loop_strikes", "max_qa_fail_rounds"])
def test_config_save_rejects_a_non_positive_qa_loop_limit(dash, field):
    status, body = dash.post("/api/config/save", _config_payload(**{field: 0}))
    assert status == 400
    assert "positive whole number" in body["error"]


def test_config_save_leaves_a_field_the_payload_never_mentions_alone(dash):
    """A dashboard tab left open from before an upgrade doesn't know about a field added
    since. Omitting it must not reset it to its default — nor, for a required field, cost the
    client the entire save over a control it isn't even rendering."""
    dash.post("/api/config/save", _config_payload(qa_loop_strikes=4, max_session_run=17))
    payload = _config_payload()
    del payload["qa_loop_strikes"]
    del payload["max_session_run"]

    status, _ = dash.post("/api/config/save", payload)

    assert status == 200
    # Checked on disk, not in the echoed response: an omitted field isn't part of what this
    # save validated, so it isn't echoed — the contract is that it survives untouched.
    saved = tempa_config.load_config()
    assert saved["qa_loop_strikes"] == 4
    assert saved["max_session_run"] == 17


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


# ---------------------------------------------------------------------------
# /api/implement/decision — answering a blocked feature from the epic card
# ---------------------------------------------------------------------------

def _seed_deferred_epic(status="blocked", answer=""):
    """A deferred epic parked on one blocked feature, alongside enough unrelated top-level
    config for a save to be able to prove it left the rest of the document alone."""
    tempa_config.save_config({
        "models": {"implement": "claude-sonnet-5"},
        "poll_interval_sec": 60,
        "epic": [{
            "epic_name": "EPIC-04", "status": "deferred",
            "completed_features": 1, "total_features": 2,
            "features": [
                {"id": "F1", "name": "one", "status": "done"},
                {"id": "F2", "name": "two", "status": status, "blocked_answer": answer,
                 "blocked_question": "Migrate, or descope?",
                 "blocked_recommendation": "Descope for now."},
            ],
        }],
    })


def _saved_feature():
    return tempa_config.load_config()["epic"][0]["features"][1]


def test_decision_follow_stores_the_session_own_recommendation(dash):
    """The answer text is read out of the feature server-side rather than taken from the
    request — the client should not get to decide what "I approve your recommendation" turns
    out to have meant."""
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision",
                             {"epic": "EPIC-04", "feature": "F2", "mode": "follow",
                              "answer": "something else entirely"})
    assert status == 200
    assert body["ok"] is True and body["dropped"] is False
    assert body["answer"] == "Descope for now."
    assert _saved_feature()["blocked_answer"] == "Descope for now."


def test_decision_is_recorded_in_its_own_file_as_well_as_config(dash):
    """Both writes matter: config.json so it takes effect now, the sidecar so it survives being
    overwritten by the runner or the agent."""
    _seed_deferred_epic()
    dash.post("/api/implement/decision",
              {"epic": "EPIC-04", "feature": "F2", "mode": "own", "answer": "Migrate it."})

    pending = tempa_decisions.pending_answers()
    assert [p[1]["answer"] for p in pending] == ["Migrate it."]
    assert _saved_feature()["blocked_answer"] == "Migrate it."


def test_decision_own_answer_is_stored_verbatim(dash):
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision",
                             {"epic": "EPIC-04", "feature": "F2", "mode": "own",
                              "answer": "  Migrate it, but behind a flag.  "})
    assert status == 200
    assert body["answer"] == "Migrate it, but behind a flag."
    assert _saved_feature()["blocked_answer"] == "Migrate it, but behind a flag."


def test_decision_drop_marks_the_feature_done(dash):
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision",
                             {"epic": "EPIC-04", "feature": "F2", "mode": "drop",
                              "answer": "Superseded by EPIC-09."})
    assert status == 200
    assert body["dropped"] is True
    feature = _saved_feature()
    assert feature["status"] == "done"
    assert feature["blocked_answer"] == "Superseded by EPIC-09."


def test_decision_leaves_the_rest_of_the_config_untouched(dash):
    """The write is surgical: one field of one feature, re-read inside the lock, so it can never
    clobber what another writer put in the same file."""
    _seed_deferred_epic()
    before = tempa_config.load_config()
    dash.post("/api/implement/decision",
              {"epic": "EPIC-04", "feature": "F2", "mode": "own", "answer": "Migrate it."})
    after = tempa_config.load_config()

    assert after["epic"][0]["features"][0] == before["epic"][0]["features"][0]
    assert after["models"] == before["models"]
    assert after["epic"][0]["total_features"] == before["epic"][0]["total_features"]


def test_decision_rejects_a_malformed_body(dash):
    status, body = dash.post("/api/implement/decision", raw=b"{not json")
    assert status == 400
    assert body == {"ok": False, "error": "Malformed request."}


def test_decision_rejects_a_missing_epic(dash):
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision", {"feature": "F2", "mode": "follow"})
    assert status == 400
    assert body["error"] == "Missing or invalid epic."


def test_decision_rejects_an_epic_label_that_is_not_a_label(dash):
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision",
                             {"epic": "../../etc", "feature": "F2", "mode": "follow"})
    assert status == 400
    assert body["error"] == "Missing or invalid epic."


def test_decision_rejects_a_missing_feature(dash):
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision", {"epic": "EPIC-04", "mode": "follow"})
    assert status == 400
    assert body["error"] == "Missing or invalid feature."


def test_decision_rejects_an_unknown_mode(dash):
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision",
                             {"epic": "EPIC-04", "feature": "F2", "mode": "maybe"})
    assert status == 400
    assert body["error"] == "Invalid answer mode."


def test_decision_rejects_an_own_answer_with_nothing_in_it(dash):
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision",
                             {"epic": "EPIC-04", "feature": "F2", "mode": "own", "answer": "   "})
    assert status == 400
    assert body["error"] == "Write an answer before saving."


def test_decision_on_an_unknown_epic_is_a_404(dash):
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision",
                             {"epic": "EPIC-99", "feature": "F2", "mode": "follow"})
    assert status == 404
    assert body["error"] == 'No epic named "EPIC-99" in the plan.'


def test_decision_on_an_unknown_feature_is_a_404(dash):
    _seed_deferred_epic()
    status, body = dash.post("/api/implement/decision",
                             {"epic": "EPIC-04", "feature": "F9", "mode": "follow"})
    assert status == 404
    assert body["error"] == 'No feature "F9" in EPIC-04.'


def test_decision_on_a_feature_that_moved_on_is_a_conflict(dash):
    """A stale tab answering a question the runner has already got past — say so rather than
    quietly writing a blocked_answer onto a feature nothing will read it from."""
    _seed_deferred_epic(status="require_fixing")
    status, body = dash.post("/api/implement/decision",
                             {"epic": "EPIC-04", "feature": "F2", "mode": "follow"})
    assert status == 409
    assert "no longer waiting on a decision" in body["error"]


def test_decision_follow_needs_a_recommendation_to_follow(dash):
    """Nothing guarantees the session wrote one — offering to follow an empty recommendation
    would store a blank answer, which reads as unanswered and parks the epic again."""
    tempa_config.save_config({"epic": [{
        "epic_name": "EPIC-04", "status": "deferred",
        "features": [{"id": "F2", "name": "two", "status": "blocked",
                      "blocked_question": "Migrate, or descope?"}],
    }]})
    status, body = dash.post("/api/implement/decision",
                             {"epic": "EPIC-04", "feature": "F2", "mode": "follow"})
    assert status == 409
    assert "no recommendation to follow" in body["error"]


def test_a_rejected_decision_records_nothing(dash):
    _seed_deferred_epic()
    dash.post("/api/implement/decision",
              {"epic": "EPIC-04", "feature": "F9", "mode": "follow"})
    assert tempa_decisions.pending_answers() == []


# ---------------------------------------------------------------------------
# Finalize checkpoint settings
# ---------------------------------------------------------------------------

def test_config_save_persists_the_checkpoint_settings(dash):
    status, body = dash.post("/api/config/save", _config_payload(
        finalize_checkpoint_rounds=3,
        finalize_checkpoint_commit=False,
    ))
    assert status == 200
    saved = tempa_config.load_config()
    assert saved["finalize_checkpoint_rounds"] == 3
    assert saved["finalize_checkpoint_commit"] is False
    assert body["config"]["finalize_checkpoint_rounds"] == 3


def test_config_save_blank_checkpoint_rounds_means_no_checkpoints(dash):
    status, body = dash.post("/api/config/save", _config_payload(finalize_checkpoint_rounds=""))
    assert status == 200
    assert body["config"]["finalize_checkpoint_rounds"] is None
    assert tempa_config.load_config()["finalize_checkpoint_rounds"] is None


@pytest.mark.parametrize("value", [0, -2, "x"])
def test_config_save_rejects_a_junk_checkpoint_interval(dash, value):
    status, body = dash.post("/api/config/save", _config_payload(finalize_checkpoint_rounds=value))
    assert status == 400
    assert body["error"] == "Checkpoint Every N Rounds must be empty or a positive whole number."


def test_config_save_leaves_the_checkpoint_settings_alone_when_the_fields_are_absent(dash):
    """A dashboard tab left open across the upgrade that added these fields omits them. It
    must not be able to turn the workspace's only clarification rollback points off with a save
    that reports "Saved."."""
    dash.post("/api/config/save", _config_payload(finalize_checkpoint_rounds=4))
    payload = _config_payload()
    for key in ("finalize_checkpoint_rounds", "finalize_checkpoint_commit"):
        payload.pop(key)

    status, _ = dash.post("/api/config/save", payload)

    assert status == 200
    saved = tempa_config.load_config()
    assert saved["finalize_checkpoint_rounds"] == 4
    assert saved["finalize_checkpoint_commit"] is True
