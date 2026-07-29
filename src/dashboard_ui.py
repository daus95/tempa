"""Local web UI: the `tempa dashboard` command.

Serves a single-page app on 127.0.0.1 with a Windows-Explorer-style selector on the
left (Home / Specification / Clarification / Implementation) and a content pane on
the right. Replaces the former standalone spec_ui.py and clarify_ui.py:

  - Home: watermark placeholder (nothing else built here yet).
  - Specification: the PRD folder (sources.prd) as a collapsible file/folder tree;
    clicking a file shows it as rendered/edit markdown, with save-to-disk. This is
    the same browsing behavior spec_ui.py used to provide standalone.
  - Clarification: the clarification files (sources.clarifications) that still have
    at least one unanswered finding; clicking one shows its findings with a
    recommendation-vs-own-answer textarea per finding, same mechanism clarify_ui.py
    used to provide as a one-shot form, but selected from the sidebar instead of a
    tab bar. Saving only writes the answer into the file — it does not re-run the
    apply-to-PRD step; that stays a separate, explicit `tempa clarify --apply`.
  - Implementation: placeholder — not built yet.

Unlike the old clarify_ui (which shut its server down as soon as the user saved or
cancelled), the dashboard stays up until the user stops it with Ctrl+C, matching
spec_ui's long-running browsing behavior — because the sidebar lets the user hop
between files/sections at will.

All file access is confined to the relevant root (prd_dir for Specification,
clar_dir for Clarification): every requested path is resolved and checked to be
inside its root before any read/write, so the browser cannot escape via `..` or
absolute paths.
"""

from __future__ import annotations

import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

from dashboard_assets import render_page as _render_page
from dashboard_clarify_parse import _clarify_files_overview
from dashboard_config import _load_clarify_applied_hashes
from dashboard_runs import _new_clarify_run_state, _new_implement_run_state
from dashboard_server import _DashboardHandler
from dashboard_spec import build_tree


def run_dashboard(prd_dir: Path, clar_dir: Path, initial_view: str = "home") -> bool:
    """Serve the dashboard on a random 127.0.0.1 port, open it in the default
    browser, and block until interrupted with Ctrl+C. `initial_view` is one of
    "home" | "specification" | "clarification" and controls which sidebar section
    is expanded/shown on first paint. Returns True iff at least one clarification
    answer was saved during the session."""
    prd_dir = prd_dir.resolve() if prd_dir.exists() else prd_dir
    clar_dir = clar_dir.resolve() if clar_dir.exists() else clar_dir

    spec_tree = build_tree(prd_dir)
    clarify_unanswered, clarify_answered = _clarify_files_overview(
        clar_dir, _load_clarify_applied_hashes()
    )
    page_html = _render_page(prd_dir, spec_tree, clarify_unanswered, clarify_answered, initial_view)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _DashboardHandler)
    server.prd_dir = prd_dir
    server.clar_dir = clar_dir
    server.page_html = page_html
    server.any_saved = False
    server.clarify_run = _new_clarify_run_state()
    server.implement_run = _new_implement_run_state()

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"Dashboard: {url}")
    print("Press Ctrl+C to stop.")
    if not webbrowser.open(url):
        print("Could not open a browser automatically -- open the URL above manually.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()

    return server.any_saved
