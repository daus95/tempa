"""The dashboard HTTP handler.

_DashboardHandler serves the single-page app and the /api/* GET/POST routes (spec browse/
edit/upload/delete/rename, clarify view/save/run, implement run/stop, clear, workspace
init/open/close, config get/save). All file access is confined to prd_dir / clar_dir via
_resolve_within."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import tempa_config
from dashboard_config import (
    _load_clarify_applied_hashes, _load_dashboard_config, _resolve_source_dir,
    _workspace_can_close, _workspace_initialized, _workspace_root,
)
from dashboard_spec import MARKDOWN_EXTENSIONS, build_tree, _is_text_file, _resolve_within
from dashboard_clarify_parse import (
    file_answer_status, parse_file, _clarify_files_overview,
    _clarify_finalize_status, _live_clarification_findings,
)
from dashboard_assets import principles_guide_page
from dashboard_clarify_render import _render_blocks_html
from dashboard_runs import (
    _epic_sessions, _start_clarify_run, _start_implement_run, _stop_implement_run,
)
if sys.platform == "win32":
    from dashboard_winui import _open_and_focus_folder, _pick_folder_dialog
elif sys.platform == "darwin":
    from dashboard_macui import _open_and_focus_folder, _pick_folder_dialog
else:
    _pick_folder_dialog = _open_and_focus_folder = None


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
            new_text = item.recommendation
        else:
            new_text = (entry.get("answer") or "").strip()
        if item.has_markers:
            replacement = f"<!-- clarify:answer-start -->\n{new_text}\n<!-- clarify:answer-end -->"
        else:
            replacement = f"\n<!-- clarify:answer-start -->\n{new_text}\n<!-- clarify:answer-end -->\n"
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
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

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
        elif route == "/api/tree":
            unanswered, answered = _clarify_files_overview(
                self.server.clar_dir, _load_clarify_applied_hashes()
            )
            findings = _live_clarification_findings(unanswered + answered)
            last_action = _load_dashboard_config().get("last_clarification_action")
            self._send_json(200, {
                "ok": True,
                "workspace": {"initialized": _workspace_initialized(), "root": _workspace_root(),
                               "canClose": _workspace_can_close()},
                "spec": {"tree": build_tree(self.server.prd_dir)},
                "clarify": {"unanswered": unanswered, "answered": answered,
                            "findings": findings,
                            "finalize": _clarify_finalize_status(findings, last_action)},
                "principles": {"set": bool(tempa_config.read_principles())},
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
        elif route == "/api/principles":
            self._send_json(200, {"ok": True, "content": tempa_config.read_principles()})
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
        answered = sum(1 for it in items if it.existing_answer)
        summary = (
            f"{len(items)} finding(s) — {counts['critical']} critical · "
            f"{counts['major']} major · {counts['minor']} minor"
        )
        self._send_json(200, {
            "ok": True, "path": rel, "name": target.name,
            "summary": summary, "html": _render_blocks_html(blocks),
            "answered": answered, "total": len(items),
        })

    def _handle_clarify_run_status(self, query: dict) -> None:
        try:
            since = int((query.get("since", ["0"])[0]))
        except ValueError:
            since = 0
        run = self.server.clarify_run
        with run["lock"]:
            lines = list(run["lines"][max(since, 0):])
            total = len(run["lines"])
            self._send_json(200, {
                "ok": True, "running": run["running"], "mode": run["mode"],
                "returncode": run["returncode"], "lines": lines, "next": total,
                "progress": run["progress"],
            })

    def _handle_implement_run_status(self, query: dict) -> None:
        try:
            since = int((query.get("since", ["0"])[0]))
        except ValueError:
            since = 0
        run = self.server.implement_run
        with run["lock"]:
            lines = list(run["lines"][max(since, 0):])
            total = len(run["lines"])
            self._send_json(200, {
                "ok": True, "running": run["running"],
                "returncode": run["returncode"], "lines": lines, "next": total,
                "progress": run["progress"],
                # Epics are read fresh from config.json on every poll (not cached) —
                # the Status tab shows the same data the "Log" tab's run is actively
                # writing into config.json, so it needs to reflect live progress too.
                "epics": _epic_sessions(),
            })

    def _handle_config_get(self) -> None:
        config = tempa_config.read_config_safe()
        self._send_json(200, {
            "ok": True,
            "config": {
                "models": tempa_config.get_models(config),
                "features_per_session": config.get("features_per_session"),
                "max_session_run": config.get("max_session_run"),
                "max_clarification_run": config.get("max_clarification_run"),
            },
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
        elif parsed.path == "/api/principles/save":
            self._handle_principles_save()
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
        payload = self._read_json_body()
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
        unanswered, answered = _clarify_files_overview(
            self.server.clar_dir, _load_clarify_applied_hashes()
        )
        findings = _live_clarification_findings(unanswered + answered)
        last_action = _load_dashboard_config().get("last_clarification_action")
        if mode == "finalize" and not _clarify_finalize_status(findings, last_action)["ready"]:
            # Server-side gate, not just a disabled button client-side — mirrors the
            # implement gate below. `tempa clarify --finalize` itself has no awareness
            # of this precondition and would happily run regardless.
            self._send_json(409, {
                "ok": False,
                "error": "Cannot finalize yet — run Start Clarification once more and confirm "
                         "it shows zero critical findings first.",
            })
            return
        if not _start_clarify_run(self.server, mode):
            self._send_json(409, {"ok": False, "error": "A clarification run is already in progress."})
            return
        self._send_json(200, {"ok": True})

    def _handle_implement_run_start(self) -> None:
        # Server-side gate, not just a disabled button client-side — tempa.py's
        # `implement` itself has no awareness of clarification findings and will
        # happily start regardless, so this is the only thing actually enforcing it.
        unanswered, answered = _clarify_files_overview(
            self.server.clar_dir, _load_clarify_applied_hashes()
        )
        findings = _live_clarification_findings(unanswered + answered)
        if findings["critical"] or findings["major"]:
            self._send_json(409, {
                "ok": False,
                "error": "Cannot start implementation while critical/major clarification findings remain.",
            })
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
        """Open workspace.root in the OS file manager (Explorer/Finder) and bring it to
        the front — used by the path label on the Home page's working-folder panel.
        Windows and macOS only; Linux has no single native equivalent, so the button is
        disabled there (see _open_and_focus_folder)."""
        root = _workspace_root()
        if not root or not Path(root).is_dir():
            self._send_json(404, {"ok": False, "error": "Working folder not found on disk."})
            return
        if _open_and_focus_folder is None:
            self._send_json(200, {
                "ok": False,
                "error": "Opening the working folder in a file manager is only supported on Windows and macOS.",
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
        if not isinstance(models_in, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        models = {}
        for stage in ("clarify", "plan", "implement"):
            value = (models_in.get(stage) or "").strip()
            if not value:
                self._send_json(400, {"ok": False, "error": f"The {stage} model cannot be empty."})
                return
            models[stage] = tempa_config._resolve_model_alias(value)

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
            self._send_json(400, {"ok": False, "error": "Max Clarification Runs must be a positive whole number."})
            return

        config = tempa_config.load_config()
        config["models"] = models
        config["features_per_session"] = features_per_session
        config["max_session_run"] = max_session_run
        config["max_clarification_run"] = max_clarification_run
        tempa_config.save_config(config)
        print("[settings] configuration saved")
        self._send_json(200, {
            "ok": True,
            "config": {
                "models": models,
                "features_per_session": features_per_session,
                "max_session_run": max_session_run,
                "max_clarification_run": max_clarification_run,
            },
        })

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
