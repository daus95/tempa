"""The dashboard HTTP handler.

_DashboardHandler serves the single-page app and the /api/* GET/POST routes (spec browse/
edit/upload/delete/rename, clarify view/save/run/stop, implement run/stop, verify run/stop/
delete, clear, workspace init/open/close, config get/save). All file access is confined to
prd_dir / clar_dir / the verify report folder via _resolve_within."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import tempa_backend
import tempa_config
from dashboard_assets import principles_guide_page, spec_guide_page
from dashboard_clarify_parse import (
    _clarify_files_overview,
    _clarify_finalize_status,
    _implement_readiness_status,
    _latest_evaluation_findings,
    file_answer_status,
    parse_file,
    pending_overlay_stats,
)
from dashboard_clarify_render import _render_blocks_html
from dashboard_config import (
    _load_clarify_applied_hashes,
    _load_clarify_file_timings,
    _load_dashboard_config,
    _resolve_source_dir,
    _workspace_can_close,
    _workspace_initialized,
    _workspace_root,
)
from dashboard_runs import (
    _epic_sessions,
    _finalize_limit_change_warning,
    _implementation_has_started,
    _start_clarify_run,
    _start_implement_run,
    _stop_clarify_run,
    _stop_implement_run,
)
from dashboard_spec import MARKDOWN_EXTENSIONS, _is_text_file, _resolve_within, build_tree
from dashboard_verify import (
    _delete_verify_run,
    _list_verify_runs,
    _start_verify_run,
    _stop_verify_run,
    _verify_detail,
)
from tempa_notifications import DEFAULT_ENABLED_EVENTS, send_test_email

if sys.platform == "win32":
    from dashboard_winui import _open_and_focus_folder, _pick_folder_dialog
elif sys.platform == "darwin":
    from dashboard_macui import _open_and_focus_folder, _pick_folder_dialog
elif sys.platform.startswith("linux"):
    from dashboard_linuxui import _open_and_focus_folder, _pick_folder_dialog
else:
    _pick_folder_dialog = _open_and_focus_folder = None


# A session/QA log can legitimately be large (some real ones run past 400KB), but there's
# no reason to ever ship more than this much text into the browser for one modal view — cap
# it and keep the tail (the most recently written, most diagnostically relevant part),
# matching the same tail-first philosophy as tempa_logging._print_log_tail.
LOG_FILE_MAX_CHARS = 5_000_000


def _backend_status() -> dict:
    """Per-CLI-backend readiness (installed + workspace-writable) for whichever workspace
    is currently active. Shared by /api/tree and /api/config so the writability probe
    (a real filesystem touch) only runs once per request, not once per handler."""
    root = _workspace_root()
    writable = tempa_config.workspace_is_writable(root) if root else False
    return tempa_backend.get_backend_status(writable)


def apply_answers_to_file(path: Path, payload: list[dict]) -> tuple[int, int]:
    """Write the given answers into `path` (one clarification result file) and return
    its updated (answered, total) counts."""
    text = path.read_text(encoding="utf-8")
    items, _ = parse_file(path, text, 0)
    items_by_key = {it.key: it for it in items}

    edits: list[tuple[int, int, str]] = []
    for entry in payload:
        item = items_by_key.get(entry.get("id"))
        if item is None:
            continue
        mode = entry.get("mode")
        if mode == "recommendation" and item.recommendation:
            # Don't duplicate the recommendation text into the file — it's already
            # shown once under "Recommendation:". Record the choice via the marker's
            # mode attribute instead; ClarificationItem.resolved_answer reconstructs
            # the full text for anything that needs it (answered counts, the pending
            # overlay carried into the next clarification round).
            new_text = ""
            start_marker = '<!-- clarify:answer-start mode="recommendation" -->'
        else:
            new_text = (entry.get("answer") or "").strip()
            start_marker = "<!-- clarify:answer-start -->"
        if item.has_markers:
            replacement = f"{start_marker}\n{new_text}\n<!-- clarify:answer-end -->"
        else:
            replacement = f"\n{start_marker}\n{new_text}\n<!-- clarify:answer-end -->\n"
        edits.append((item.answer_start, item.answer_end, replacement))

    for start, end, replacement in sorted(edits, key=lambda s: s[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    path.write_text(text, encoding="utf-8")
    return file_answer_status(path)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class _DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Injected on the server instance by run_dashboard:
    #   server.prd_dir    -> Path of the specs/PRD folder (Specification section)
    #   server.clar_dir   -> Path of the clarifications folder (Clarification section)
    #   server.page_html  -> str of the index page
    #   server.any_saved  -> bool, set True the first time a clarification answer is saved

    def log_message(self, fmt: str, *args) -> None:  # silence per-request logging
        pass

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionAbortedError):
            self.wfile.write(body)

    def _send_json(self, status: int, obj: dict) -> None:
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    # -- GET ----------------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("/", ""):
            self._send(200, "text/html; charset=utf-8",
                       self.server.page_html.encode("utf-8"))
        elif route == "/architecture-principles":
            self._send(200, "text/html; charset=utf-8",
                       principles_guide_page().encode("utf-8"))
        elif route == "/spec-guide":
            self._send(200, "text/html; charset=utf-8",
                       spec_guide_page().encode("utf-8"))
        elif route == "/api/tree":
            unanswered, answered = _clarify_files_overview(
                self.server.clar_dir, _load_clarify_applied_hashes(), _load_clarify_file_timings()
            )
            dashboard_config = _load_dashboard_config()
            findings = _latest_evaluation_findings(
                unanswered + answered, dashboard_config.get("last_clean_evaluation_at", 0)
            )
            last_action = dashboard_config.get("last_clarification_action")
            round_ = dashboard_config.get("last_clarification_round") or 0
            max_round = dashboard_config.get("max_clarification_run") or 0
            finalize_round = dashboard_config.get("last_finalize_round") or 0
            allow_finalize_with_critical = bool(dashboard_config.get("allow_finalize_with_critical"))
            implementation_requirement = tempa_config.get_implementation_start_requirement(dashboard_config)
            overlay = pending_overlay_stats(self.server.clar_dir, _load_clarify_applied_hashes())
            self._send_json(200, {
                "ok": True,
                "workspace": {"initialized": _workspace_initialized(), "root": _workspace_root(),
                               "canClose": _workspace_can_close()},
                "spec": {"tree": build_tree(self.server.prd_dir)},
                "clarify": {"unanswered": unanswered, "answered": answered,
                            "findings": findings,
                            "finalize": _clarify_finalize_status(
                                findings, last_action, round_, max_round, allow_finalize_with_critical,
                                finalize_round, overlay["findings"]),
                            "implementReadiness": _implement_readiness_status(
                                findings, last_action is not None, implementation_requirement,
                                overlay["findings"]),
                            "pendingOverlay": overlay,
                            "overlayWarnThreshold": tempa_config.get_clarify_overlay_warn_findings(
                                dashboard_config),
                            "skipMinorFindings": tempa_config.get_skip_minor_findings(dashboard_config)},
                "principles": {"set": bool(tempa_config.read_principles())},
                "backends": _backend_status(),
            })
        elif route == "/api/spec/file":
            self._handle_spec_file(parse_qs(parsed.query))
        elif route == "/api/clarify/file":
            self._handle_clarify_file(parse_qs(parsed.query))
        elif route == "/api/clarify/run":
            self._handle_clarify_run_status(parse_qs(parsed.query))
        elif route == "/api/implement/run":
            self._handle_implement_run_status(parse_qs(parsed.query))
        elif route == "/api/config":
            self._handle_config_get()
        elif route == "/api/backends/status":
            self._send_json(200, {"ok": True, "backends": _backend_status()})
        elif route == "/api/principles":
            self._send_json(200, {"ok": True, "content": tempa_config.read_principles()})
        elif route == "/api/update/status":
            self._handle_update_status()
        elif route == "/api/log-file":
            self._handle_log_file(parse_qs(parsed.query))
        elif route == "/api/verify/runs":
            self._send_json(200, {"ok": True, "runs": _list_verify_runs(self.server)})
        elif route == "/api/verify/detail":
            self._handle_verify_detail(parse_qs(parsed.query))
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _handle_spec_file(self, query: dict) -> None:
        rel = (query.get("path", [""])[0])
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None or not target.is_file():
            self._send_json(404, {"ok": False, "error": "File not found."})
            return
        if not _is_text_file(target):
            self._send_json(200, {
                "ok": True, "path": rel, "markdown": False, "text": False,
                "content": "", "reason": "This file type is not viewable as text.",
            })
            return
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self._send_json(200, {
                "ok": True, "path": rel, "markdown": False, "text": False,
                "content": "", "reason": "This file is not valid UTF-8 text.",
            })
            return
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not read file: {e}"})
            return
        self._send_json(200, {
            "ok": True, "path": rel,
            "markdown": target.suffix.lower() in MARKDOWN_EXTENSIONS,
            "text": True, "content": content,
        })

    def _handle_clarify_file(self, query: dict) -> None:
        rel = (query.get("path", [""])[0])
        target = _resolve_within(self.server.clar_dir, rel)
        if target is None or not target.is_file():
            self._send_json(404, {"ok": False, "error": "File not found."})
            return
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not read file: {e}"})
            return
        items, blocks = parse_file(target, text, 0)
        if not items:
            self._send_json(200, {
                "ok": True, "path": rel, "name": target.name,
                "summary": "No recognized clarification items in this file.",
                "html": "<p>No recognized clarification items in this file.</p>",
                "answered": 0, "total": 0,
            })
            return
        counts = {"critical": 0, "major": 0, "minor": 0}
        for it in items:
            counts[it.severity] += 1
        answered = sum(1 for it in items if it.resolved_answer)
        summary = (
            f"{len(items)} finding(s) — {counts['critical']} critical · "
            f"{counts['major']} major · {counts['minor']} minor"
        )
        self._send_json(200, {
            "ok": True, "path": rel, "name": target.name,
            "summary": summary, "html": _render_blocks_html(blocks),
            "answered": answered, "total": len(items),
        })

    def _handle_log_file(self, query: dict) -> None:
        """Serve one session/QA/process log file's content by bare filename (no
        subdirectories — these are all flat files directly under .tempa/logs/) for the Log
        tab's "log: <filename>" links to open in a viewer modal. `_resolve_within` confines
        this to get_logs_dir() the same way spec/clarify file reads are confined to their own
        roots, so a crafted name can't read anything outside the logs folder."""
        name = (query.get("name", [""])[0])
        target = _resolve_within(tempa_config.get_logs_dir(), name)
        if target is None or target.suffix.lower() != ".txt" or not target.is_file():
            self._send_json(404, {"ok": False, "error": "Log file not found."})
            return
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not read file: {e}"})
            return
        truncated = len(content) > LOG_FILE_MAX_CHARS
        if truncated:
            content = content[-LOG_FILE_MAX_CHARS:]
        self._send_json(200, {
            "ok": True, "name": target.name, "content": content, "truncated": truncated,
        })

    def _handle_verify_detail(self, query: dict) -> None:
        run_id = (query.get("id", [""])[0])
        detail = _verify_detail(self.server, run_id)
        if detail is None:
            self._send_json(404, {"ok": False, "error": "Verification run not found."})
            return
        self._send_json(200, {"ok": True, **detail})

    def _handle_clarify_run_status(self, query: dict) -> None:
        try:
            since = int(query.get("since", ["0"])[0])
        except ValueError:
            since = 0
        run = self.server.clarify_run
        # Read fresh from config.json on every poll (not cached) so the finalize
        # progress badge next to the "Finalized Clarification" button ticks up live,
        # round by round, the same way implement's epic snapshot does.
        dashboard_config = _load_dashboard_config()
        with run["lock"]:
            lines = list(run["lines"][max(since, 0):])
            total = len(run["lines"])
            self._send_json(200, {
                "ok": True, "running": run["running"], "mode": run["mode"],
                "returncode": run["returncode"], "lines": lines, "next": total,
                "progress": run["progress"],
                "finalizeRound": dashboard_config.get("last_finalize_round") or 0,
                "maxRound": dashboard_config.get("max_clarification_run") or 0,
                # "evaluate" | "verify" — which kind of round finalize is on (see
                # run_clarify_finalize). A finalize run ends with a verification round over
                # the freshly-compacted PRD, which looks identical in the badge otherwise.
                "finalizePhase": dashboard_config.get("last_finalize_phase") or "",
            })

    def _handle_implement_run_status(self, query: dict) -> None:
        try:
            since = int(query.get("since", ["0"])[0])
        except ValueError:
            since = 0
        run = self.server.implement_run
        # Epics are read fresh from config.json on every poll (not cached) — the Status
        # tab shows the same data the "Log" tab's run is actively writing into
        # config.json, so it needs to reflect live progress too.
        epics = _epic_sessions()
        with run["lock"]:
            lines = list(run["lines"][max(since, 0):])
            total = len(run["lines"])
            self._send_json(200, {
                "ok": True, "running": run["running"],
                "returncode": run["returncode"], "lines": lines, "next": total,
                "progress": run["progress"],
                "epics": epics,
                # Relabels the three Start Implementation buttons to "Continue
                # Implementation" once any epic has actually run — computed here so
                # every surface agrees (see _implementation_has_started).
                "started": _implementation_has_started(epics),
            })

    def _handle_config_get(self) -> None:
        config = tempa_config.read_config_safe()
        self._send_json(200, {
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
                "implementation_start_requirement": tempa_config.get_implementation_start_requirement(config),
                "notifications": {"email": tempa_config.get_email_notifications(config)},
                "usage_limit_retry_wait_sec": tempa_config.get_usage_limit_retry_wait_sec(config),
                "usage_limit_heartbeat_sec": tempa_config.get_usage_limit_heartbeat_sec(config),
                "server_overloaded_retry_wait_sec": tempa_config.get_server_overloaded_retry_wait_sec(config),
                "poll_interval_sec": tempa_config.get_poll_interval_sec(config),
                "backends_status": _backend_status(),
            },
        })

    def _handle_update_status(self) -> None:
        # Imported lazily: tempa_commands imports dashboard_ui (for run_dashboard), which
        # imports this module — a top-level import here would be circular.
        import tempa_commands
        current = tempa_commands.get_local_version()
        latest = tempa_commands.get_latest_release_version()
        if latest is None:
            self._send_json(200, {
                "ok": True, "current": current, "latest": None, "updateAvailable": False,
                "error": "Could not reach GitHub to check the latest release.",
            })
            return
        self._send_json(200, {
            "ok": True, "current": current, "latest": latest,
            "updateAvailable": current == "unknown" or current != latest,
        })

    # -- POST ---------------------------------------------------------------
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/spec/save":
            self._handle_spec_save()
        elif parsed.path == "/api/spec/upload":
            self._handle_spec_upload(parse_qs(parsed.query))
        elif parsed.path == "/api/spec/delete":
            self._handle_spec_delete()
        elif parsed.path == "/api/spec/rename":
            self._handle_spec_rename()
        elif parsed.path == "/api/clarify/save":
            self._handle_clarify_save()
        elif parsed.path == "/api/clarify/run":
            self._handle_clarify_run_start()
        elif parsed.path == "/api/clarify/stop":
            self._handle_clarify_run_stop()
        elif parsed.path == "/api/clarify/skip-minor":
            self._handle_clarify_skip_minor_save()
        elif parsed.path == "/api/implement/run":
            self._handle_implement_run_start()
        elif parsed.path == "/api/implement/stop":
            self._handle_implement_run_stop()
        elif parsed.path == "/api/clear":
            self._handle_clear_all()
        elif parsed.path == "/api/workspace/init":
            self._handle_workspace_init()
        elif parsed.path == "/api/workspace/open":
            self._handle_workspace_open()
        elif parsed.path == "/api/workspace/close":
            self._handle_workspace_close()
        elif parsed.path == "/api/config/save":
            self._handle_config_save()
        elif parsed.path == "/api/notifications/test-email":
            self._handle_test_email()
        elif parsed.path == "/api/principles/save":
            self._handle_principles_save()
        elif parsed.path == "/api/update/run":
            self._handle_update_run()
        elif parsed.path == "/api/server/restart":
            self._handle_server_restart()
        elif parsed.path == "/api/verify/run":
            self._handle_verify_run_start()
        elif parsed.path == "/api/verify/stop":
            self._handle_verify_run_stop()
        elif parsed.path == "/api/verify/delete":
            self._handle_verify_delete()
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _read_json_body(self) -> dict | list | None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _handle_spec_save(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        rel = payload.get("path", "")
        content = payload.get("content", "")
        if not isinstance(content, str):
            self._send_json(400, {"ok": False, "error": "Content must be text."})
            return
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None:
            self._send_json(400, {"ok": False, "error": "Invalid path."})
            return
        if not target.exists() or not target.is_file():
            self._send_json(404, {"ok": False, "error": "File no longer exists."})
            return
        if not _is_text_file(target):
            self._send_json(400, {"ok": False, "error": "This file type cannot be edited here."})
            return
        try:
            # Path.write_text()'s `newline` kwarg needs Python 3.10+; use open() directly
            # so this also works on 3.9 (otherwise Windows' text-mode translation would
            # silently re-insert \r\n and undo the normalization above).
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(content.replace("\r\n", "\n").replace("\r", "\n"))
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not save file: {e}"})
            return
        print(f"[saved] {rel}")
        self._send_json(200, {"ok": True, "path": rel})

    def _handle_spec_upload(self, query: dict) -> None:
        """Add a file to the Specification (PRD) folder — used by the "Add File" /
        "Add Folder" buttons. `path` is the destination relative to prd_dir (for a
        folder upload this includes the folder name and any subfolders); the request
        body is the raw file bytes. Overwrites an existing file at that path."""
        rel = (query.get("path", [""])[0])
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None:
            self._send_json(400, {"ok": False, "error": "Invalid path."})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        data = self.rfile.read(length) if length else b""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as f:
                f.write(data)
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not write file: {e}"})
            return
        print(f"[added] {rel}")
        self._send_json(200, {"ok": True, "path": rel})

    def _handle_spec_delete(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        rel = payload.get("path", "")
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None:
            self._send_json(400, {"ok": False, "error": "Invalid path."})
            return
        if not target.exists():
            self._send_json(404, {"ok": False, "error": "File or folder no longer exists."})
            return
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not delete: {e}"})
            return
        print(f"[deleted] {rel}")
        self._send_json(200, {"ok": True, "path": rel})

    def _handle_spec_rename(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        rel = payload.get("path", "")
        new_name = (payload.get("new_name") or "").strip()
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None:
            self._send_json(400, {"ok": False, "error": "Invalid path."})
            return
        if not target.exists():
            self._send_json(404, {"ok": False, "error": "File or folder no longer exists."})
            return
        if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
            self._send_json(400, {"ok": False, "error": "Invalid new name."})
            return
        new_target = target.parent / new_name
        if new_target.exists():
            self._send_json(409, {"ok": False, "error": f'"{new_name}" already exists.'})
            return
        try:
            target.rename(new_target)
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not rename: {e}"})
            return
        new_rel = str(new_target.relative_to(self.server.prd_dir)).replace("\\", "/")
        print(f"[renamed] {rel} -> {new_rel}")
        self._send_json(200, {"ok": True, "path": new_rel})

    def _handle_clarify_save(self) -> None:
        # Finalized Clarification auto-answers findings itself (mechanical recommendation
        # fill + agent-applied resolutions) — a hand save racing with that loop's own
        # reads/writes to the same file could corrupt or lose an auto-answer, so saves are
        # rejected for as long as a finalize run is in flight. This is the authoritative
        # guard (the client already disables editing — see isClarifyFinalizeLocked in
        # dashboard.js — but this also covers a stale tab that had the file open before
        # finalize started).
        payload = self._read_json_body()
        run = self.server.clarify_run
        with run["lock"]:
            finalize_running = run["running"] and run["mode"] == "finalize"
        if finalize_running:
            self._send_json(409, {
                "ok": False,
                "error": "Finalized Clarification is running and auto-answering these "
                         "findings — answers are locked until it stops.",
            })
            return
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        rel = payload.get("path", "")
        items = payload.get("items", [])
        if not isinstance(items, list):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        target = _resolve_within(self.server.clar_dir, rel)
        if target is None or not target.exists() or not target.is_file():
            self._send_json(404, {"ok": False, "error": "File no longer exists."})
            return
        try:
            answered, total = apply_answers_to_file(target, items)
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not save file: {e}"})
            return
        self.server.any_saved = True
        print(f"[saved] {rel} ({answered}/{total} answered)")
        self._send_json(200, {"ok": True, "path": rel, "answered": answered, "total": total})

    def _handle_clarify_run_start(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        mode = payload.get("mode")
        if mode not in ("run", "finalize", "apply"):
            self._send_json(400, {"ok": False, "error": "Invalid mode."})
            return
        if mode == "finalize":
            # Server-side gate, not just a disabled button client-side — mirrors the
            # implement gate below. `tempa clarify --finalize` itself has no awareness
            # of this precondition and would happily run regardless.
            unanswered, answered = _clarify_files_overview(
                self.server.clar_dir, _load_clarify_applied_hashes(), _load_clarify_file_timings()
            )
            dashboard_config = _load_dashboard_config()
            findings = _latest_evaluation_findings(
                unanswered + answered, dashboard_config.get("last_clean_evaluation_at", 0)
            )
            last_action = dashboard_config.get("last_clarification_action")
            allow_finalize_with_critical = bool(dashboard_config.get("allow_finalize_with_critical"))
            if not _clarify_finalize_status(
                findings, last_action, allow_finalize_with_critical=allow_finalize_with_critical
            )["ready"]:
                error = ("Cannot finalize yet — run Start Clarification once more and confirm "
                         "it shows zero critical findings first.")
                if findings["critical"] > 0 and not allow_finalize_with_critical:
                    error += (" Or enable \"Allow finalizing with critical findings\" in "
                              "Settings to skip this requirement.")
                self._send_json(409, {"ok": False, "error": error})
                return
        if not _start_clarify_run(self.server, mode):
            self._send_json(409, {"ok": False, "error": "A clarification run is already in progress."})
            return
        self._send_json(200, {"ok": True})

    def _handle_clarify_run_stop(self) -> None:
        if not _stop_clarify_run(self.server):
            self._send_json(409, {"ok": False, "error": "No clarification run is currently in progress."})
            return
        self._send_json(200, {"ok": True})

    def _handle_clarify_skip_minor_save(self) -> None:
        """Persist the Clarification page's "Only evaluate critical & major findings"
        switch (config.json's "skip_minor_findings") — a narrow, single-purpose save
        endpoint rather than routing through /api/config/save, since that handler
        requires the full Settings form payload (including a required
        max_clarification_run) which isn't loaded if the Settings pane was never
        opened this session."""
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict) or not isinstance(payload.get("skip_minor_findings"), bool):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        skip_minor_findings = payload["skip_minor_findings"]
        config = tempa_config.load_config()
        config["skip_minor_findings"] = skip_minor_findings
        tempa_config.save_config(config)
        self._send_json(200, {"ok": True, "skipMinorFindings": skip_minor_findings})

    def _handle_implement_run_start(self) -> None:
        # Server-side gate, not just a disabled button client-side — tempa.py's
        # `implement` itself has no awareness of clarification findings and will
        # happily start regardless, so this is the only thing actually enforcing it.
        dashboard_config = _load_dashboard_config()
        last_action = dashboard_config.get("last_clarification_action")
        if last_action is None:
            # Same "hasRun" check the finalize gate uses (_clarify_finalize_status) —
            # without it, a workspace where clarification was never run at all has zero
            # findings by simple absence of any clarification file, which would
            # otherwise trivially satisfy every requirement level below, including the
            # default.
            self._send_json(409, {
                "ok": False,
                "error": "Cannot start implementation before clarification has been run at least once.",
            })
            return
        unanswered, answered = _clarify_files_overview(
            self.server.clar_dir, _load_clarify_applied_hashes(), _load_clarify_file_timings()
        )
        findings = _latest_evaluation_findings(
            unanswered + answered, dashboard_config.get("last_clean_evaluation_at", 0)
        )
        requirement = tempa_config.get_implementation_start_requirement(dashboard_config)
        overlay = pending_overlay_stats(self.server.clar_dir, _load_clarify_applied_hashes())
        status = _implement_readiness_status(findings, True, requirement, overlay["findings"])
        if not status["ready"]:
            # Wording matches the requirement actually configured (dashboard Settings'
            # "Start Implementation requires") rather than always assuming the strictest
            # ("no_critical_or_major") level. The pending-overlay branch is checked FIRST
            # when it's the only thing blocking: reporting "critical/major findings remain"
            # to someone whose latest round found none is both wrong and unactionable.
            if overlay["findings"] and status["critical"] == 0 and status["major"] == 0:
                error = (f"Cannot start implementation: {overlay['findings']} answered clarification "
                         f"finding(s) across {overlay['files']} file(s) haven't been written into the "
                         "PRD yet. Click Apply Answers first (it's a no-op if the PRD already matches).")
            elif requirement == "no_critical":
                error = "Cannot start implementation while critical clarification findings remain."
            else:
                error = "Cannot start implementation while critical/major clarification findings remain."
            self._send_json(409, {"ok": False, "error": error})
            return
        if not _start_implement_run(self.server):
            self._send_json(409, {"ok": False, "error": "Implementation is already running."})
            return
        self._send_json(200, {"ok": True})

    def _handle_implement_run_stop(self) -> None:
        if not _stop_implement_run(self.server):
            self._send_json(409, {"ok": False, "error": "Implementation is not running."})
            return
        self._send_json(200, {"ok": True})

    def _handle_verify_run_start(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        epic = (payload.get("epic") or "").strip()
        if not epic:
            self._send_json(400, {"ok": False, "error": "Missing epic."})
            return
        if not _start_verify_run(self.server, epic):
            self._send_json(409, {"ok": False, "error": f'A verification run for "{epic}" is already in progress.'})
            return
        self._send_json(200, {"ok": True})

    def _handle_verify_run_stop(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        epic = (payload.get("epic") or "").strip()
        if not epic or not _stop_verify_run(self.server, epic):
            self._send_json(409, {"ok": False, "error": "No verification run is currently in progress for that epic."})
            return
        self._send_json(200, {"ok": True})

    def _handle_verify_delete(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        run_id = (payload.get("id") or "").strip()
        if not run_id or not _delete_verify_run(run_id):
            self._send_json(404, {"ok": False, "error": "Verification run not found."})
            return
        self._send_json(200, {"ok": True})

    def _handle_clear_all(self) -> None:
        if self.server.clarify_run["running"] or self.server.implement_run["running"]:
            self._send_json(409, {
                "ok": False,
                "error": "Cannot clear while a clarify or implementation run is in progress.",
            })
            return
        tempa_py = Path(__file__).resolve().parent.parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "clear", "--yes"]
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            self._send_json(500, {"ok": False, "error": f"Could not run clear: {e}"})
            return
        output = result.stdout or ""
        if result.returncode != 0:
            self._send_json(500, {"ok": False, "error": output.strip() or f"Clear failed (exit code {result.returncode})."})
            return
        self._send_json(200, {"ok": True, "output": output})

    def _handle_workspace_init(self) -> None:
        """"Select Working Folder" on the Home page: open a native folder picker, then
        run `tempa.py init <folder>` (same as the CLI) to make it the active workspace —
        pointing Tempa at `<folder>/.tempa/config.json` (created fresh, or loaded as-is if
        this folder was used before) — and scaffold the default working folders under it."""
        if _pick_folder_dialog is None:
            self._send_json(200, {
                "ok": False,
                "error": "The folder picker isn't available on this platform. Run "
                         "`tempa init <path>` from the CLI, then reload the dashboard.",
            })
            return
        try:
            root = _pick_folder_dialog()
        except RuntimeError as e:
            self._send_json(200, {"ok": False, "error": str(e)})
            return
        if root is None:
            self._send_json(200, {"ok": False, "cancelled": True})
            return
        tempa_py = Path(__file__).resolve().parent.parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "init", root]
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            self._send_json(500, {"ok": False, "error": f"Could not initialize: {e}"})
            return
        output = result.stdout or ""
        if result.returncode != 0:
            self._send_json(500, {"ok": False, "error": output.strip() or f"Init failed (exit code {result.returncode})."})
            return
        # Re-derive prd_dir/clar_dir now that workspace.root is set, so the dashboard
        # reflects the new location immediately instead of requiring a restart.
        self.server.prd_dir = _resolve_source_dir("prd", "prd")
        self.server.clar_dir = _resolve_source_dir("clarifications", "clarifications")
        print(f"[workspace] root set to {root}")
        self._send_json(200, {"ok": True, "root": root, "output": output})

    def _handle_workspace_open(self) -> None:
        """Open workspace.root in the OS file manager (Explorer/Finder/xdg-open) and, on
        Windows/macOS, bring it to the front — used by the path label on the Home page's
        working-folder panel. Unsupported only if the platform isn't recognized, or (on
        Linux) if none of the relied-upon command-line tools are installed (see
        _open_and_focus_folder)."""
        root = _workspace_root()
        if not root or not Path(root).is_dir():
            self._send_json(404, {"ok": False, "error": "Working folder not found on disk."})
            return
        if _open_and_focus_folder is None:
            self._send_json(200, {
                "ok": False,
                "error": "Opening the working folder in a file manager isn't supported on this platform.",
            })
            return
        try:
            _open_and_focus_folder(root)
        except (OSError, RuntimeError) as e:
            self._send_json(500, {"ok": False, "error": f"Could not open folder: {e}"})
            return
        self._send_json(200, {"ok": True})

    def _handle_workspace_close(self) -> None:
        """Detach the active workspace — the "✕" icon next to the working-folder path.
        Shells out to `tempa.py close-folder` (same subprocess pattern as init/clear) so
        the pointer-clearing logic stays in one place. Always available while a workspace
        is active — it only drops the active-workspace pointer, the workspace's own
        .tempa/ folder is never touched, so there's nothing to gate on."""
        tempa_py = Path(__file__).resolve().parent.parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "close-folder"]
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            self._send_json(500, {"ok": False, "error": f"Could not close working folder: {e}"})
            return
        output = result.stdout or ""
        if result.returncode != 0:
            self._send_json(500, {"ok": False, "error": output.strip() or f"Close failed (exit code {result.returncode})."})
            return
        self.server.prd_dir = _resolve_source_dir("prd", "prd")
        self.server.clar_dir = _resolve_source_dir("clarifications", "clarifications")
        print("[workspace] root cleared")
        self._send_json(200, {"ok": True})

    def _handle_config_save(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return

        models_in = payload.get("models")
        backends_in = payload.get("backends")
        reasoning_efforts_in = payload.get("reasoning_efforts")
        if not isinstance(models_in, dict) or not isinstance(backends_in, dict) or not isinstance(reasoning_efforts_in, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        backends = {}
        for stage in ("clarify", "clarify_apply", "plan", "implement"):
            value = (backends_in.get(stage) or "").strip()
            if value not in tempa_backend.BACKENDS:
                self._send_json(400, {"ok": False, "error": f"The {stage} backend must be one of: {', '.join(tempa_backend.BACKENDS)}."})
                return
            backends[stage] = value
        models = {}
        for stage in ("clarify", "clarify_apply", "plan", "implement"):
            value = (models_in.get(stage) or "").strip()
            if not value:
                self._send_json(400, {"ok": False, "error": f"The {stage} model cannot be empty."})
                return
            # Friendly aliases (opus-5, sonnet-5, ...) are Claude-only — for copilot/codex
            # the model string is stored as-is (see tempa_config._resolve_model_alias).
            models[stage] = tempa_config._resolve_model_alias(value) if backends[stage] == "claude" else value
        reasoning_efforts = {}
        for stage in ("clarify", "clarify_apply", "plan", "implement"):
            value = (reasoning_efforts_in.get(stage) or "").strip()
            backend_def = tempa_backend.get_backend_def(backends[stage])
            if not tempa_backend.is_valid_reasoning_effort(backend_def, models[stage], value):
                choices = ", ".join(backend_def.reasoning_effort_choices(models[stage]))
                self._send_json(400, {"ok": False, "error": f"The {stage} reasoning effort must be empty or one of: {choices}."})
                return
            reasoning_efforts[stage] = value

        def _parse_limit(name: str, required: bool) -> tuple[bool, int | None]:
            """Returns (ok, value). `value` is None for a blank/absent field (only
            valid when `required` is False, i.e. it means "no limit")."""
            raw = payload.get(name)
            if raw is None or raw == "":
                return (not required), None
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                return False, None
            return parsed >= 1, parsed

        ok, features_per_session = _parse_limit("features_per_session", required=False)
        if not ok:
            self._send_json(400, {"ok": False, "error": "Features per Session must be empty or a positive whole number."})
            return
        ok, max_session_run = _parse_limit("max_session_run", required=False)
        if not ok:
            self._send_json(400, {"ok": False, "error": "Max Session Runs must be empty or a positive whole number."})
            return
        ok, max_clarification_run = _parse_limit("max_clarification_run", required=True)
        if not ok:
            self._send_json(400, {"ok": False, "error": "Max Finalize Clarification Round must be a positive whole number."})
            return
        ok, finalize_no_progress_rounds = _parse_limit("finalize_no_progress_rounds", required=True)
        if not ok:
            self._send_json(400, {"ok": False, "error": "Max Finalize No-Progress Round must be a positive whole number."})
            return
        ok, usage_limit_retry_wait_sec = _parse_limit("usage_limit_retry_wait_sec", required=True)
        if not ok:
            self._send_json(400, {"ok": False, "error": "Usage Limit Retry Wait must be a positive whole number."})
            return
        ok, usage_limit_heartbeat_sec = _parse_limit("usage_limit_heartbeat_sec", required=True)
        if not ok:
            self._send_json(400, {"ok": False, "error": "Usage Limit Heartbeat Interval must be a positive whole number."})
            return
        ok, server_overloaded_retry_wait_sec = _parse_limit("server_overloaded_retry_wait_sec", required=True)
        if not ok:
            self._send_json(400, {"ok": False, "error": "Server Overload Retry Wait must be a positive whole number."})
            return
        ok, poll_interval_sec = _parse_limit("poll_interval_sec", required=True)
        if not ok:
            self._send_json(400, {"ok": False, "error": "Implementation Poll Interval must be a positive whole number."})
            return
        allow_finalize_with_critical = bool(payload.get("allow_finalize_with_critical"))
        implementation_start_requirement = payload.get("implementation_start_requirement")
        if implementation_start_requirement not in tempa_config.IMPLEMENTATION_START_REQUIREMENTS:
            self._send_json(400, {
                "ok": False,
                "error": "Start Implementation requirement must be one of: "
                         f"{', '.join(tempa_config.IMPLEMENTATION_START_REQUIREMENTS)}.",
            })
            return

        current_config = tempa_config.load_config()
        email_in = ((payload.get("notifications") or {}).get("email"))
        if not isinstance(email_in, dict):
            email_in = tempa_config.get_email_notifications(current_config)
        try:
            smtp_port = int(email_in.get("smtp_port", 587))
            timeout_seconds = int(email_in.get("timeout_seconds", 10))
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "SMTP port and timeout must be whole numbers."})
            return
        security = str(email_in.get("security", "starttls"))
        provider = str(email_in.get("provider", "custom"))
        recipients_in, events_in = email_in.get("recipients", []), email_in.get("events", [])
        if (security not in ("starttls", "ssl", "none") or provider not in ("gmail", "office365", "custom")
                or not 1 <= smtp_port <= 65535 or timeout_seconds < 1):
            self._send_json(400, {"ok": False, "error": "Invalid SMTP security, port, or timeout."})
            return
        if not isinstance(recipients_in, list) or not isinstance(events_in, list):
            self._send_json(400, {"ok": False, "error": "Email recipients and events must be lists."})
            return
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
            self._send_json(400, {"ok": False, "error": "Enabled email needs SMTP host, sender, and at least one recipient."})
            return

        config = current_config
        previous_finalize_limits = {
            "max_clarification_run": config.get("max_clarification_run"),
            "finalize_no_progress_rounds": config.get("finalize_no_progress_rounds"),
        }
        config["models"] = models
        config["backends"] = backends
        config["reasoning_efforts"] = reasoning_efforts
        config["features_per_session"] = features_per_session
        config["max_session_run"] = max_session_run
        config["max_clarification_run"] = max_clarification_run
        config["finalize_no_progress_rounds"] = finalize_no_progress_rounds
        config["allow_finalize_with_critical"] = allow_finalize_with_critical
        config["implementation_start_requirement"] = implementation_start_requirement
        config["notifications"] = {"email": email}
        config["usage_limit_retry_wait_sec"] = usage_limit_retry_wait_sec
        config["usage_limit_heartbeat_sec"] = usage_limit_heartbeat_sec
        config["server_overloaded_retry_wait_sec"] = server_overloaded_retry_wait_sec
        config["poll_interval_sec"] = poll_interval_sec
        tempa_config.save_config(config)
        print("[settings] configuration saved")
        # The save itself always succeeds; `warning` is an advisory note about a setting
        # a run already in flight can no longer pick up (see
        # _finalize_limit_change_warning). Computed server-side rather than in the
        # client, since only the server knows for certain what's running right now.
        warning = _finalize_limit_change_warning(self.server, previous_finalize_limits, {
            "max_clarification_run": max_clarification_run,
            "finalize_no_progress_rounds": finalize_no_progress_rounds,
        })
        self._send_json(200, {
            "ok": True,
            "warning": warning,
            "config": {
                "models": models,
                "backends": backends,
                "reasoning_efforts": reasoning_efforts,
                "features_per_session": features_per_session,
                "max_session_run": max_session_run,
                "max_clarification_run": max_clarification_run,
                "finalize_no_progress_rounds": finalize_no_progress_rounds,
                "allow_finalize_with_critical": allow_finalize_with_critical,
                "implementation_start_requirement": implementation_start_requirement,
                "notifications": {"email": email},
                "usage_limit_retry_wait_sec": usage_limit_retry_wait_sec,
                "usage_limit_heartbeat_sec": usage_limit_heartbeat_sec,
                "server_overloaded_retry_wait_sec": server_overloaded_retry_wait_sec,
                "poll_interval_sec": poll_interval_sec,
                "backends_status": _backend_status(),
            },
        })

    def _handle_test_email(self) -> None:
        ok, message = send_test_email()
        self._send_json(200 if ok else 400, {"ok": ok, "message": message})

    def _handle_principles_save(self) -> None:
        """Save the Architecture Principles document. Blank content deletes the file, which
        is how the principles are unset (an absent file means nothing is injected)."""
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        content = payload.get("content", "")
        if not isinstance(content, str):
            self._send_json(400, {"ok": False, "error": "Content must be text."})
            return
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
            self._send_json(500, {"ok": False, "error": f"Could not save the principles: {e}"})
            return
        print("[principles] " + ("cleared" if not content else "saved"))
        self._send_json(200, {"ok": True, "content": content})

    def _handle_update_run(self) -> None:
        """Apply the latest GitHub release on top of this install. Delegates to
        `tempa.py update --yes` in a subprocess (rather than calling run_update() in-process)
        because that function can sys.exit() on failure paths — isolating it in a child
        process keeps a failed update from taking the dashboard server down with it."""
        if self.server.clarify_run["running"] or self.server.implement_run["running"]:
            self._send_json(409, {
                "ok": False,
                "error": "Cannot update while a clarify or implementation run is in progress.",
            })
            return
        tempa_py = Path(__file__).resolve().parent.parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "update", "--yes"]
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            self._send_json(500, {"ok": False, "error": f"Could not run update: {e}"})
            return
        output = result.stdout or ""
        if result.returncode != 0:
            self._send_json(500, {"ok": False, "error": output.strip() or f"Update failed (exit code {result.returncode})."})
            return
        import tempa_commands
        self._send_json(200, {"ok": True, "output": output, "version": tempa_commands.get_local_version()})

    def _handle_server_restart(self) -> None:
        """Stop this dashboard process and relaunch a fresh one bound to the same port,
        so the browser can reload the same URL once it's back up. Spawns the replacement
        as a detached process (it must outlive this one exiting) *before* this server
        gives up its socket, so the new process's own bind-retry loop
        (run_dashboard()'s `port` handling in dashboard_ui.py) bridges the brief window
        where both are momentarily contending for the port."""
        if self.server.clarify_run["running"] or self.server.implement_run["running"]:
            self._send_json(409, {
                "ok": False,
                "error": "Cannot restart while a clarify or implementation run is in progress.",
            })
            return
        port = self.server.server_address[1]
        tempa_py = Path(__file__).resolve().parent.parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "dashboard", "--port", str(port), "--no-browser"]
        popen_kwargs = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, **popen_kwargs,
            )
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not start the new server process: {e}"})
            return
        self._send_json(200, {"ok": True, "port": port})

        def _shutdown_after_response() -> None:
            time.sleep(0.3)
            self.server.shutdown()

        threading.Thread(target=_shutdown_after_response, daemon=True).start()
