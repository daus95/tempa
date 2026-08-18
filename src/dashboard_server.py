"""The dashboard HTTP handler.

_DashboardHandler serves the single-page app and the /api/* GET/POST routes (spec browse/
edit/upload/delete/rename, clarify view/save/run/stop, implement run/stop/decision, verify
run/stop/delete, clear, workspace init/open/close, config get/save). All file access is confined to
prd_dir / clar_dir / the verify report folder via _resolve_within."""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import dashboard_api_clarify
import dashboard_api_decisions
import dashboard_api_settings
import dashboard_api_spec
import dashboard_api_status
import dashboard_api_workspace
import dashboard_zip
import tempa_config
from dashboard_assets import principles_guide_page, spec_guide_page
from dashboard_clarify_parse import (
    _clarify_files_overview,
    _implement_readiness_status,
    _latest_evaluation_findings,
    pending_overlay_stats,
)
from dashboard_config import (
    _load_clarify_applied_hashes,
    _load_clarify_file_timings,
    _load_dashboard_config,
)
from dashboard_runs import (
    _cancel_graceful_stop_clarify_run,
    _cancel_graceful_stop_implement_run,
    _finalize_limit_change_warning,
    _graceful_stop_clarify_run,
    _graceful_stop_implement_run,
    _start_clarify_run,
    _start_implement_run,
    _stop_clarify_run,
    _stop_implement_run,
)
from dashboard_verify import (
    _delete_verify_run,
    _list_verify_runs,
    _start_verify_run,
    _stop_verify_run,
    _verify_detail,
)

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

    def _send(self, status: int, content_type: str, body: bytes, *, filename: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionAbortedError):
            self.wfile.write(body)

    def _send_json(self, status: int, obj: dict) -> None:
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    # -- GET ----------------------------------------------------------------
    # Route tables rather than if/elif chains: every route the dashboard answers is listed
    # in one place, and dispatch is a lookup. `self.query` is the parsed query string,
    # set per request before the handler runs so handlers that need it can read it without
    # every other one carrying an unused parameter.
    GET_ROUTES = {
        "": "_serve_index",
        "/": "_serve_index",
        "/architecture-principles": "_serve_principles_guide",
        "/spec-guide": "_serve_spec_guide",
        "/api/tree": "_handle_tree",
        "/api/spec/file": "_handle_spec_file",
        "/api/spec/download-zip": "_handle_spec_download_zip",
        "/api/epic/spec": "_handle_epic_spec_file",
        "/api/epic/download-zip": "_handle_epic_download_zip",
        "/api/clarify/file": "_handle_clarify_file",
        "/api/clarify/run": "_handle_clarify_run_status",
        "/api/implement/run": "_handle_implement_run_status",
        "/api/config": "_handle_config_get",
        "/api/backends/status": "_handle_backends_status",
        "/api/principles": "_handle_principles_get",
        "/api/update/status": "_handle_update_status",
        "/api/update/changelog": "_handle_update_changelog",
        "/api/log-file": "_handle_log_file",
        "/api/qa-report": "_handle_qa_report",
        "/api/verify/runs": "_handle_verify_runs",
        "/api/verify/detail": "_handle_verify_detail",
    }

    def do_GET(self) -> None:
        self._dispatch(self.GET_ROUTES)

    def _dispatch(self, routes: dict) -> None:
        parsed = urlparse(self.path)
        handler = routes.get(parsed.path)
        if handler is None:
            self._send(404, "text/plain; charset=utf-8", b"Not found")
            return
        self.query = parse_qs(parsed.query)
        getattr(self, handler)()

    def _serve_index(self) -> None:
        self._send(200, "text/html; charset=utf-8", self.server.page_html.encode("utf-8"))

    def _serve_principles_guide(self) -> None:
        self._send(200, "text/html; charset=utf-8", principles_guide_page().encode("utf-8"))

    def _serve_spec_guide(self) -> None:
        self._send(200, "text/html; charset=utf-8", spec_guide_page().encode("utf-8"))

    def _handle_tree(self) -> None:
        self._send_json(*dashboard_api_status.tree_payload(
            self.server.prd_dir, self.server.clar_dir, dashboard_api_status.backend_status()))

    def _handle_backends_status(self) -> None:
        self._send_json(200, {"ok": True, "backends": dashboard_api_status.backend_status()})

    def _handle_principles_get(self) -> None:
        self._send_json(200, {"ok": True, "content": tempa_config.read_principles()})

    def _handle_verify_runs(self) -> None:
        self._send_json(200, {"ok": True, "runs": _list_verify_runs(self.server)})

    def _handle_spec_file(self) -> None:
        self._send_json(*dashboard_api_spec.read_file(
            self.server.prd_dir, self.query.get("path", [""])[0]))

    def _handle_epic_spec_file(self) -> None:
        self._send_json(*dashboard_api_spec.read_epic_spec(
            self.server.epics_dir, self.query.get("epic", [""])[0]))

    def _handle_spec_download_zip(self) -> None:
        self._send(200, "application/zip",
                   dashboard_zip.build_zip(self.server.prd_dir), filename="prd-specs.zip")

    def _handle_epic_download_zip(self) -> None:
        self._send(200, "application/zip",
                   dashboard_zip.build_zip(self.server.epics_dir), filename="pbi-epics.zip")

    def _handle_clarify_file(self) -> None:
        self._send_json(*dashboard_api_clarify.read_file(
            self.server.clar_dir, self.query.get("path", [""])[0]))

    def _handle_log_file(self) -> None:
        self._send_json(*dashboard_api_status.read_log_file(self.query.get("name", [""])[0]))

    def _handle_qa_report(self) -> None:
        self._send_json(*dashboard_api_status.read_qa_report(self.query.get("name", [""])[0]))

    def _handle_verify_detail(self) -> None:
        detail = _verify_detail(self.server, self.query.get("id", [""])[0])
        if detail is None:
            self._send_json(404, {"ok": False, "error": "Verification run not found."})
            return
        self._send_json(200, {"ok": True, **detail})

    def _handle_clarify_run_status(self) -> None:
        self._send_json(*dashboard_api_status.clarify_run_status(self.server, self.query))

    def _handle_implement_run_status(self) -> None:
        self._send_json(*dashboard_api_status.implement_run_status(self.server, self.query))

    def _handle_config_get(self) -> None:
        self._send_json(*dashboard_api_settings.read_config(dashboard_api_status.backend_status()))

    def _handle_update_status(self) -> None:
        self._send_json(*dashboard_api_status.update_status())

    def _handle_update_changelog(self) -> None:
        self._send_json(*dashboard_api_status.update_changelog(self.query.get("latest", [""])[0]))

    # -- POST ---------------------------------------------------------------
    POST_ROUTES = {
        "/api/spec/save": "_handle_spec_save",
        "/api/spec/upload": "_handle_spec_upload",
        "/api/spec/delete": "_handle_spec_delete",
        "/api/spec/rename": "_handle_spec_rename",
        "/api/clarify/save": "_handle_clarify_save",
        "/api/clarify/run": "_handle_clarify_run_start",
        "/api/clarify/stop": "_handle_clarify_run_stop",
        "/api/clarify/stop-graceful": "_handle_clarify_stop_graceful",
        "/api/clarify/stop-graceful/cancel": "_handle_clarify_stop_graceful_cancel",
        "/api/clarify/skip-minor": "_handle_clarify_skip_minor_save",
        "/api/implement/run": "_handle_implement_run_start",
        "/api/implement/stop": "_handle_implement_run_stop",
        "/api/implement/stop-graceful": "_handle_implement_stop_graceful",
        "/api/implement/stop-graceful/cancel": "_handle_implement_stop_graceful_cancel",
        "/api/implement/decision": "_handle_implement_decision",
        "/api/clear": "_handle_clear_all",
        "/api/workspace/init": "_handle_workspace_init",
        "/api/workspace/open": "_handle_workspace_open",
        "/api/workspace/close": "_handle_workspace_close",
        "/api/workspace/open-recent": "_handle_workspace_open_recent",
        "/api/workspace/pick-parent": "_handle_workspace_pick_parent",
        "/api/workspace/create": "_handle_workspace_create",
        "/api/workspace/recent/remove": "_handle_workspace_recent_remove",
        "/api/config/save": "_handle_config_save",
        "/api/notifications/test-email": "_handle_test_email",
        "/api/principles/save": "_handle_principles_save",
        "/api/epic/spec/save": "_handle_epic_spec_save",
        "/api/update/run": "_handle_update_run",
        "/api/server/restart": "_handle_server_restart",
        "/api/verify/run": "_handle_verify_run_start",
        "/api/verify/stop": "_handle_verify_run_stop",
        "/api/verify/delete": "_handle_verify_delete",
    }

    def do_POST(self) -> None:
        self._dispatch(self.POST_ROUTES)

    def _read_json_body(self) -> dict | list | None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _handle_spec_save(self) -> None:
        self._send_json(*dashboard_api_spec.save_file(self.server.prd_dir, self._read_json_body()))

    def _handle_spec_upload(self) -> None:
        """Raw file bytes in the body (not JSON), so the body is read here rather than
        through _read_json_body."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        data = self.rfile.read(length) if length else b""
        self._send_json(*dashboard_api_spec.upload_file(
            self.server.prd_dir, self.query.get("path", [""])[0], data))

    def _handle_spec_delete(self) -> None:
        self._send_json(*dashboard_api_spec.delete_path(self.server.prd_dir, self._read_json_body()))

    def _handle_spec_rename(self) -> None:
        self._send_json(*dashboard_api_spec.rename_path(self.server.prd_dir, self._read_json_body()))

    def _handle_clarify_save(self) -> None:
        payload = self._read_json_body()
        run = self.server.clarify_run
        with run["lock"]:
            finalize_running = run["running"] and run["mode"] == "finalize"
        status, body = dashboard_api_clarify.save_answers(
            self.server.clar_dir, payload, finalize_running)
        if status == 200:
            self.server.any_saved = True
        self._send_json(status, body)

    def _handle_clarify_run_start(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        mode = payload.get("mode")
        if mode not in ("run", "finalize", "apply"):
            self._send_json(400, {"ok": False, "error": "Invalid mode."})
            return
        # Server-side gate, not just a disabled button client-side (see the matching
        # comment on _handle_implement_run_start below) — clarification and
        # implementation both touch the spec/PRD and must never run concurrently.
        if self.server.implement_run["running"]:
            self._send_json(409, {"ok": False, "error": "Cannot start clarification while implementation is running."})
            return
        if mode == "finalize":
            error = dashboard_api_clarify.finalize_gate_error(self.server.clar_dir)
            if error:
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

    # Deliberately separate routes rather than a "mode" field on /api/clarify/stop above:
    # graceful and immediate differ in whether a live agent session gets killed, and an
    # empty or malformed body defaulting to one of them is not a coin worth flipping.
    def _handle_clarify_stop_graceful(self) -> None:
        if not _graceful_stop_clarify_run(self.server):
            self._send_json(409, {"ok": False, "error": "No clarification run is currently in progress."})
            return
        self._send_json(200, {"ok": True})

    def _handle_clarify_stop_graceful_cancel(self) -> None:
        if not _cancel_graceful_stop_clarify_run(self.server):
            self._send_json(409, {"ok": False, "error": "No clarification run is currently in progress."})
            return
        self._send_json(200, {"ok": True})

    def _handle_clarify_skip_minor_save(self) -> None:
        self._send_json(*dashboard_api_clarify.save_skip_minor(self._read_json_body()))

    def _handle_implement_run_start(self) -> None:
        # Server-side gate, not just a disabled button client-side — tempa.py's
        # `implement` itself has no awareness of clarification findings and will
        # happily start regardless, so this is the only thing actually enforcing it.
        if self.server.clarify_run["running"]:
            self._send_json(409, {
                "ok": False,
                "error": "Cannot start implementation while a clarification run is in progress.",
            })
            return
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

    # Separate from the immediate stop above for the same reason as the clarify pair.
    def _handle_implement_stop_graceful(self) -> None:
        if not _graceful_stop_implement_run(self.server):
            self._send_json(409, {"ok": False, "error": "Implementation is not running."})
            return
        self._send_json(200, {"ok": True})

    def _handle_implement_stop_graceful_cancel(self) -> None:
        if not _cancel_graceful_stop_implement_run(self.server):
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

    def _handle_implement_decision(self) -> None:
        """Answer a blocked feature's question from the Implementation page's card, instead of
        hand-editing config.json the way the callout text used to be the only way to. Safe to
        call while a run is in progress — see dashboard_api_decisions for how the write avoids
        both halves of the lost-update race against the runner and the agent."""
        self._send_json(*dashboard_api_decisions.save_answer(self._read_json_body()))

    def _handle_clear_all(self) -> None:
        self._send_json(*dashboard_api_workspace.clear_everything(self.server.clarify_run["running"], self.server.implement_run["running"]))

    def _handle_workspace_init(self) -> None:
        self._send_json(*dashboard_api_workspace.init_workspace(self.server, _pick_folder_dialog))

    def _handle_workspace_open(self) -> None:
        self._send_json(*dashboard_api_workspace.open_workspace_folder(_open_and_focus_folder))

    def _handle_workspace_close(self) -> None:
        self._send_json(*dashboard_api_workspace.close_workspace(self.server))

    def _handle_workspace_open_recent(self) -> None:
        self._send_json(*dashboard_api_workspace.open_recent_workspace(self.server, self._read_json_body()))

    def _handle_workspace_pick_parent(self) -> None:
        self._send_json(*dashboard_api_workspace.pick_parent_folder(_pick_folder_dialog))

    def _handle_workspace_create(self) -> None:
        self._send_json(*dashboard_api_workspace.create_workspace(self.server, self._read_json_body()))

    def _handle_workspace_recent_remove(self) -> None:
        self._send_json(*dashboard_api_workspace.remove_recent_workspace(self._read_json_body()))

    def _handle_config_save(self) -> None:
        self._send_json(*dashboard_api_settings.save_settings(
            self._read_json_body(),
            dashboard_api_status.backend_status(),
            lambda previous, new: _finalize_limit_change_warning(self.server, previous, new),
        ))

    def _handle_test_email(self) -> None:
        self._send_json(*dashboard_api_settings.run_test_email())

    def _handle_principles_save(self) -> None:
        self._send_json(*dashboard_api_settings.save_principles(self._read_json_body()))

    def _handle_epic_spec_save(self) -> None:
        self._send_json(*dashboard_api_spec.save_epic_spec(
            self.server.epics_dir, self._read_json_body()))

    def _handle_update_run(self) -> None:
        self._send_json(*dashboard_api_workspace.run_update(self.server.clarify_run["running"], self.server.implement_run["running"]))

    def _handle_server_restart(self) -> None:
        """Relaunch a replacement server on this port, then stop this one — after the
        response is on the wire, so the browser knows where to reconnect."""
        status, body = dashboard_api_workspace.relaunch_server(
            self.server.server_address[1], self.server.clarify_run["running"], self.server.implement_run["running"])
        self._send_json(status, body)
        if status != 200:
            return

        def _shutdown_after_response() -> None:
            time.sleep(0.3)
            self.server.shutdown()

        threading.Thread(target=_shutdown_after_response, daemon=True).start()
