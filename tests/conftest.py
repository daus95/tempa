"""Shared pytest fixtures for the Tempa test suite.

The autouse `isolate_tempa_paths` fixture is the load-bearing piece: it redirects every
module-level path constant Tempa computes at import time (SCRIPT_DIR, WORKING_DIR,
PROMPT_DIR, ACTIVE_WORKSPACE_POINTER, WORKSPACE_HISTORY_PATH) into a per-test tmp_path, so
no test ever reads or writes the real dev machine's actual install folder, its real
`.active-workspace` pointer, or its real recent-workspaces history (which, outside tests,
may point at/list real, unrelated workspaces)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_tempa_paths(tmp_path, monkeypatch):
    """Redirect tempa_config's module-level path constants into tmp_path.

    tempa_prompts.py does `from tempa_config import PROMPT_DIR`, a value import that binds
    its own local name at import time — patching tempa_config.PROMPT_DIR alone would NOT
    affect tempa_prompts.PROMPT_DIR, so both are patched here.
    """
    import tempa_config
    import tempa_prompts

    fake_script_dir = tmp_path / "install_root"
    fake_script_dir.mkdir(parents=True, exist_ok=True)
    fake_working_dir = fake_script_dir.parent
    fake_prompt_dir = tmp_path / "prompt"
    fake_prompt_dir.mkdir(parents=True, exist_ok=True)
    fake_pointer = fake_script_dir / ".active-workspace"
    fake_history_path = fake_script_dir / ".workspace-history.json"

    monkeypatch.setattr(tempa_config, "SCRIPT_DIR", fake_script_dir)
    monkeypatch.setattr(tempa_config, "WORKING_DIR", fake_working_dir)
    monkeypatch.setattr(tempa_config, "PROMPT_DIR", fake_prompt_dir)
    monkeypatch.setattr(tempa_config, "ACTIVE_WORKSPACE_POINTER", fake_pointer)
    monkeypatch.setattr(tempa_config, "WORKSPACE_HISTORY_PATH", fake_history_path)
    monkeypatch.setattr(tempa_prompts, "PROMPT_DIR", fake_prompt_dir)

    return {
        "script_dir": fake_script_dir,
        "working_dir": fake_working_dir,
        "prompt_dir": fake_prompt_dir,
        "pointer": fake_pointer,
        "history_path": fake_history_path,
    }


@pytest.fixture
def dashboard_server(isolate_tempa_paths, tmp_path):
    """Serve the real dashboard on an ephemeral loopback port and yield its URL.

    Stands up the production request handler with the production per-server state (via
    dashboard_ui.configure_server), so a browser test exercises the page the user actually
    gets rather than a stripped-down stand-in. Bound to 127.0.0.1 only; nothing leaves the
    machine. Used by the `browser` tests -- see tests/test_dashboard_ui_models.py.
    """
    import threading
    from http.server import ThreadingHTTPServer

    import dashboard_ui
    import tempa_config
    from dashboard_assets import render_page
    from dashboard_clarify_parse import _clarify_files_overview
    from dashboard_config import _load_clarify_applied_hashes, _load_clarify_file_timings
    from dashboard_server import _DashboardHandler
    from dashboard_spec import build_tree

    prd_dir = tmp_path / "specs" / "prd"
    clar_dir = tmp_path / "specs" / "clarifications"
    prd_dir.mkdir(parents=True, exist_ok=True)
    clar_dir.mkdir(parents=True, exist_ok=True)

    # workspace.root is the dashboard's only signal that `tempa init` has ever been run
    # (dashboard_config._workspace_initialized), and the sidebar disables Settings without
    # it -- so a browser test against an unset root can never reach the pane it is testing.
    config = tempa_config.load_config()
    config["workspace"] = {**config.get("workspace", {}), "root": str(tmp_path)}
    tempa_config.save_config(config)

    unanswered, answered = _clarify_files_overview(
        clar_dir, _load_clarify_applied_hashes(), _load_clarify_file_timings()
    )
    page_html = render_page(prd_dir, clar_dir, build_tree(prd_dir), unanswered, answered, "home")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _DashboardHandler)
    dashboard_ui.configure_server(server, prd_dir, clar_dir, page_html)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
