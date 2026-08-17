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

import time
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

from dashboard_assets import render_page as _render_page
from dashboard_clarify_parse import _clarify_files_overview
from dashboard_config import (
    _load_clarify_applied_hashes,
    _load_clarify_file_timings,
    _resolve_source_dir,
)
from dashboard_runs import _new_clarify_run_state, _new_implement_run_state
from dashboard_server import _DashboardHandler
from dashboard_spec import build_tree

# How many times to retry binding to a specific `port` (e.g. when restarting on the same
# port a just-exited process held) before giving up and falling back to an ephemeral one.
_REBIND_ATTEMPTS = 5
_REBIND_DELAY_SEC = 0.5


def run_dashboard(prd_dir: Path, clar_dir: Path, initial_view: str = "home",
                   port: int = 0, open_browser: bool = True) -> bool:
    """Serve the dashboard and block until interrupted with Ctrl+C. `initial_view` is one
    of "home" | "specification" | "clarification" and controls which sidebar section is
    expanded/shown on first paint. `port` defaults to 0 (a fresh OS-assigned ephemeral
    port); passing a specific port retries binding it a few times, falling back to
    ephemeral if it can't be reclaimed (e.g. the previous holder hasn't fully released it
    yet) -- used when self-relaunching via "Restart Server" to keep the same port/URL.
    `open_browser` controls whether a browser tab is opened automatically; a relaunch
    started via "Restart Server" should not pop a second one. Returns True iff at least
    one clarification answer was saved during the session."""
    prd_dir = prd_dir.resolve() if prd_dir.exists() else prd_dir
    clar_dir = clar_dir.resolve() if clar_dir.exists() else clar_dir

    spec_tree = build_tree(prd_dir)
    clarify_unanswered, clarify_answered = _clarify_files_overview(
        clar_dir, _load_clarify_applied_hashes(), _load_clarify_file_timings()
    )
    page_html = _render_page(prd_dir, clar_dir, spec_tree, clarify_unanswered, clarify_answered,
                             initial_view)

    server = None
    attempts = _REBIND_ATTEMPTS if port else 1
    for attempt in range(attempts):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _DashboardHandler)
            break
        except OSError:
            if attempt < attempts - 1:
                time.sleep(_REBIND_DELAY_SEC)
    if server is None:
        print(f"Could not rebind port {port}; falling back to a new port.")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _DashboardHandler)
    server.prd_dir = prd_dir
    server.clar_dir = clar_dir
    # Not a parameter like the two above: the epic-spec folder has no CLI entry point that
    # opens the dashboard "at" it, so it is only ever derived from the active workspace —
    # and re-derived, like prd_dir/clar_dir, whenever that workspace changes.
    server.epics_dir = _resolve_source_dir("epics", "pbi/epics")
    server.page_html = page_html
    server.any_saved = False
    server.clarify_run = _new_clarify_run_state()
    server.implement_run = _new_implement_run_state()
    # Keyed per-epic (unlike clarify_run/implement_run's single global slot) since more
    # than one epic's verification may run at once — see dashboard_verify.py.
    server.verify_runs = {}

    bound_port = server.server_address[1]
    url = f"http://127.0.0.1:{bound_port}/"
    print(f"Dashboard: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser and not webbrowser.open(url):
        print("Could not open a browser automatically -- open the URL above manually.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()

    return server.any_saved
