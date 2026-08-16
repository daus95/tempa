# Architecture

How Tempa's own codebase is put together — for anyone contributing to `tempa_*.py` /
`dashboard_*.py`, not for anyone just using the tool. See [CONTRIBUTING.md](../CONTRIBUTING.md)
for the contribution workflow itself.

## Overview

Tempa is one Python package split into two halves that share a config layer:

- **The CLI** (`tempa_*.py`) — `tempa init/clarify/implement/...`, the thing that actually spawns
  `claude` and drives the clarify → plan → implement → QA loop.
- **The dashboard** (`dashboard_*.py`) — `tempa dashboard`, a local web UI that drives the same
  workflow through buttons instead of commands.

Both halves are plain top-level modules in `src/`, not a package with relative imports — that's
what lets `tempa.py` run as a standalone script from any working directory (see
[Entry points](#entry-points)). There is no framework: the CLI is `argparse`, the dashboard is
`http.server.BaseHTTPRequestHandler`, and the whole thing (asset bundling, session management,
config I/O) is standard library only.

## Entry points

```
tempa.py  (repo root)
  → puts src/ on sys.path
  → tempa_cli.run()
      → argparse dispatch to a tempa_* handler, e.g.:
          "dashboard" → tempa_commands.run_dashboard_command() → dashboard_ui.run_dashboard()
          "clarify"   → tempa_clarify.run_clarify_once() / run_clarify_finalize() / ...
          "implement" → tempa_implement.main()
```

`tempa.cmd` / `tempa` (the shell launchers) just invoke `tempa.py` with the same argv — see
[folders-and-paths.md](folders-and-paths.md) for the install-folder layout. The dashboard itself
re-invokes `tempa.py clarify` / `tempa.py implement` as a **subprocess** for background runs
(see [The CLI/dashboard boundary](#the-clidashboard-boundary) below) — it never calls those
functions directly in-process.

## Module map

### CLI side (`tempa_*.py`)

| Module | Responsibility |
|---|---|
| `tempa_cli.py` | Argument parsing and dispatch only. No workflow logic lives here. |
| `tempa_cli_help.py` | The hand-written `tempa --help` page (`print_help()`), separated from `tempa_cli.py` purely because it is 170 lines of formatted text. `-h`/`--help` is deliberately never registered with argparse — see `_build_arg_parser`. |
| `tempa_config.py` | Config.json I/O, `workspace`/`sources`/`models`/`backends`/`reasoning_efforts` resolution, plus `workspace_is_writable()` (see [cli-availability.md](cli-availability.md)). The one module every other module can depend on — stdlib-only, imports nothing local. |
| `tempa_logging.py` | The shared `_state` (`_RunnerState`) and process-log file. Everything that runs a session imports `_state`/`log` from here. |
| `tempa_prompts.py` | Loads `src/prompt/*.md` templates and builds the final prompt string per stage (`${...}` substitution + Architecture Principles injection). |
| `tempa_session.py` | The agent-runner session engine: spawns whichever CLI backend a stage is configured for (see `tempa_backend.py`), streams/parses its output, detects usage-limit/auth-error stop conditions. The concrete session runners (implementation, QA, clarification, apply, one-shot) live here too, along with the usage-limit pause/retry helpers (`wait_out_usage_limit`, `run_with_usage_limit_retry`) every caller in `tempa_clarify.py`/`tempa_implement.py` uses to wait a usage-limit stop out and retry the interrupted step instead of failing. |
| `tempa_session_outcome.py` | What a finished implementation session MEANS for its epic — `apply_session_outcome()`, called by `run_session` under `_state.lock`. Exit code 0 is not the same question as "did this epic make progress?": a usage-limit/auth/overload stop is a pause, a session the backend cut short is not a stalled epic, and an exit-0 session that completed no feature for `implement_no_progress_rounds` rounds gets triaged into reorder-the-plan / repair-a-QA-state-desync / fail-and-stop. |
| `tempa_backend.py` | Per-CLI backend adapters (Claude Code, GitHub Copilot CLI, OpenAI Codex CLI): argv building (including the `--effort`/`--reasoning-effort`/`-c model_reasoning_effort=...` flag per backend), prompt delivery (stdin vs. a sidecar file for CLIs whose `-p`-style flag can't take a multi-line argument on Windows), output parsing, session-id extraction, usage-limit/auth-error markers, each backend's valid reasoning-effort levels (per-model for Codex, uniform for Claude/Copilot — see `is_valid_reasoning_effort`), and per-backend readiness (`get_backend_status()` — see [cli-availability.md](cli-availability.md)). |
| `tempa_clarify.py` | The clarify workflow: evaluate, answer, apply, and the evaluate+apply finalize loop. |
| `tempa_implement.py` | The implement poll loop and scheduler: `check_and_run` walks four steps in priority order — `_resume_interrupted_qa`, `_resume_in_progress_epic`, `_run_qa_gate`, `_start_next_epic` — and the first one that takes the poll wins. That order IS the policy: resuming interrupted work beats starting new work, and no epic is implemented past one still waiting on QA. |
| `tempa_maintenance.py` | `clear`/reset commands — destructive, gated behind confirmation + a workspace-root safety check. |
| `tempa_commands.py` | The remaining mostly-stateless commands: workspace/model/backend/reasoning-effort/status/spec/verify/test, plus opening the dashboard. |
| `tempa_update.py` | Tempa updating itself: `version`, `check-update`, `update`. The only module that talks to GitHub or writes to the install folder, and the one the dashboard's Settings page reads the version from — importing it there avoids pulling in `tempa_commands`, which drags the whole dashboard in via `dashboard_ui`. |

### Dashboard side (`dashboard_*.py`)

| Module | Responsibility |
|---|---|
| `dashboard_ui.py` | `run_dashboard()` — starts the `HTTPServer`, wires the handler and initial page render together. The dashboard's own entry point. |
| `dashboard_server.py` | `_DashboardHandler` — the HTTP layer and nothing else: two route tables (`GET_ROUTES`/`POST_ROUTES`), body/query reading, and one thin adapter per route that hands off to a `dashboard_api_*` module and sends back the `(status, payload)` it returns. |
| `dashboard_api_spec.py` | Specification pane: read/save/upload/delete/rename inside the PRD folder, all confined to it by `dashboard_spec._resolve_within`. |
| `dashboard_api_clarify.py` | Clarification pane: render one findings file, write answers back into it (`apply_answers_to_file`), the skip-minor toggle, and the server-side gate on starting a Finalized Clarification run. |
| `dashboard_api_status.py` | The read-only/polled endpoints: `/api/tree`'s first-paint payload, both live run-status payloads, the log and QA-report viewers, the update check, and `backend_status()` (see [cli-availability.md](cli-availability.md)). |
| `dashboard_api_settings.py` | Settings pane: reading config.json for the form, and validating + saving it back. The validation is plain functions over the payload, so every rule (and its user-facing message) is testable without HTTP. Architecture Principles and the SMTP test live here too. |
| `dashboard_api_workspace.py` | Home page's working-folder controls and the Settings maintenance actions — select/open/detach a workspace, Clear Everything, apply an update, restart the server. Each shells out to `tempa.py <command>` rather than doing the work in-process (see [The CLI/dashboard boundary](#the-clidashboard-boundary)). |
| `dashboard_assets.py` | Reads `assets/dashboard.{html,css}`, the `assets/js/*.js` parts (concatenated in `JS_PARTS` order), and the two guide `.html` files from disk once (`lru_cache`), and inlines CSS/JS into a self-contained document — no external requests from the page. |
| `dashboard_config.py` | Thin read-only wrappers over `tempa_config` for dashboard-specific checks (workspace initialized/closable, etc.) — other `dashboard_*` modules still import `tempa_config` directly for the rest. |
| `dashboard_spec.py` | Builds the Specification file tree and the path-traversal guard (`_resolve_within`) — ported from the former standalone `spec_ui.py`. |
| `dashboard_clarify_parse.py` | Parses a clarification result file into findings (via the `clarify:item`/`clarify:answer` HTML-comment markers), computes the finalize/implement readiness state, and derives the pending-resolution overlay (`pending_resolutions` / `pending_overlay_stats`) shared by the dashboard and the clarification prompt. Ported from the former standalone `clarify_ui.py`. |
| `dashboard_clarify_render.py` | Turns parsed findings into the HTML shown in the Clarification pane (a small hand-rolled markdown renderer, not a dependency). |
| `dashboard_runs.py` | Background clarify/implement runs: `_stream_tempa_command` spawns `tempa.py clarify`/`tempa.py implement` and streams its output into a run-state dict the dashboard polls; `_clarify_run_worker`/`_implement_run_worker` are what each Start button runs on its own thread (including apply's backlog auto-chain), plus the Stop-implementation kill. |
| `dashboard_winui.py` / `dashboard_macui.py` / `dashboard_linuxui.py` | OS-native folder picker and reveal-in-file-manager, split per platform since there's no cross-platform stdlib API for either. Linux is best-effort (`zenity`/`kdialog` for the picker, `xdg-open` for the file manager, no window-focus equivalent) since neither is guaranteed installed the way PowerShell/osascript are on their platforms; `tempa init <path>` is the fallback if neither tool is present. |

### Assets (`src/assets/`, `src/prompt/`)

`dashboard.html`/`dashboard.css` plus `assets/js/*.js` are the single-page app shell;
`principles-guide.html` and `spec-guide.html` are standalone static documents opened in their
own tab (same stylesheet inlined, so they inherit the dashboard's theming — see
`dashboard_assets.principles_guide_page`/`spec_guide_page` for the pattern to follow when
adding a third). `src/prompt/*.md` are the raw prompt templates — see
[prompt-templates.md](prompt-templates.md).

The front-end script is split across `assets/js/` only so no single file has to hold all
~3,500 lines of it. It is **not** a module system: `dashboard_assets.JS_PARTS` concatenates
those files, in that exact order, into one inline `<script>`, so every part shares a single
script scope exactly as one file would — no `import`/`export`, and a function defined in one
part is callable from any other. The order is explicit (not a sorted glob) because two parts
are positional: `00-initial-data.js` declares the `INITIAL_*` constants `render_page()`
substitutes into, so it must come first, and `99-events-init.js` is the only part that *runs*
anything at load (the first paint), so it must come last. Everything in between is grouped by
pane. Adding a part means adding it to `JS_PARTS` too.

## The CLI/dashboard boundary

`tempa_config.py` is the one module both halves depend on, and it deliberately imports nothing
local (stdlib only) — that keeps it a leaf, so nothing it does can create a cycle.

The trickier constraint is the **other** direction: `tempa_commands.py` and `tempa_clarify.py`
import `dashboard_ui` (to open the dashboard from the CLI, e.g. `tempa dashboard` / `tempa clarify`
falling through to the answer UI). If anything on the dashboard side imported back into
`tempa_cli`/`tempa_commands`/`tempa_clarify`/`tempa_implement`, that would be a circular import.

It doesn't, and the reason is deliberate: when the dashboard needs to run a clarify or implement
pass, `dashboard_runs.py` does **not** call `tempa_clarify.run_clarify_once()` or
`tempa_implement.main()` in-process. It shells out to `python tempa.py clarify`/`implement` as a
brand-new subprocess and streams its stdout back for the live log panel. That's a real process
boundary, not just an implementation detail:

- A background run has its own process log (see [logging.md](logging.md)), separate from the
  dashboard server's own output.
- **Stop Now** works by killing that child process tree — there's no in-process cancellation to
  build. **Stop After Current Session** (the chevron next to it) is the one thing that genuinely
  needs to reach *into* the child, and it does so through a sentinel file — see
  [Graceful stop](#graceful-stop-the-one-request-that-crosses-the-process-boundary) below.
- The dashboard staying up doesn't hold a `_RunnerState` for a run that's actually happening in a
  different process; live status is read back from `config.json` and the log tail instead
  (see `dashboard_runs.py`).
- The usage-limit pause/retry behavior (wait 30 minutes, then retry the interrupted step —
  see `tempa_session.wait_out_usage_limit`/`run_with_usage_limit_retry`) lives entirely on the
  CLI side and needs nothing dashboard-specific: the child process just blocks for the wait
  (logging a heartbeat) instead of exiting, so the dashboard's plain stdout-streaming shows
  that as ordinary log lines, and **Stop Now**'s kill-the-process-tree already interrupts a
  paused wait exactly like it interrupts a running session.

If you're adding a new heavy workflow reachable from both the CLI and the dashboard, follow this
pattern: put the logic in a `tempa_*.py` module reachable from `tempa_cli.py`, and if the
dashboard needs to trigger it as a background run, add it to `dashboard_runs.py` as another
`python tempa.py <command>` subprocess spawn — don't import the `tempa_*` workflow module
directly from `dashboard_*`.

### Graceful stop: the one request that crosses the process boundary

Killing the process tree throws away whatever the agent session in flight had done but not yet
written — tokens already spent, for nothing. **Stop After Current Session** exists to avoid that:
let the session finish and record its work, then don't start the next one. Because the runner is a
different process, that has to be a *request*, and the only channels available are stdout (wrong
direction) and the filesystem.

The channel is a sentinel file, `.tempa/graceful-stop-implement` or `.tempa/graceful-stop-clarify`
(see `tempa_config.request_graceful_stop` / `graceful_stop_requested` / `clear_graceful_stop`).
Presence is the whole message; the timestamp inside is only for a human who finds a stray one.

A key in `config.json` was the obvious alternative and is the wrong one: the runner's session
threads read-modify-write that file constantly, so a flag written from outside would be lost the
next time the runner saved. A separate file has one writer and one reader and races with nothing.

Who reads it, and where the stop lands:

| Run | Reader | Seam |
|---|---|---|
| `tempa implement` | `tempa_implement._graceful_stop_is_due`, checked in `main`'s poll loop | Only once no session thread is alive — so after the feature or QA session in flight has finished |
| `tempa clarify --finalize` | `tempa_clarify._exit_if_graceful_stop` | The three points where the loop is about to spend another session: next round, the compaction apply, the auto-answer step |
| Apply Answers | `dashboard_runs`' auto-chain loop | Between backlog batches — apply's batching lives in the dashboard, not the CLI |
| Clarification (evaluate) | nobody | A single session with nothing after it; *not killing it* is the entire effect |

Lifecycle rules that keep a stale file harmless: the sentinel is cleared at the start of every run
(both by the CLI entry points and by `dashboard_runs`), when it is honoured, when the user cancels,
and as a safety net when a run ends for any other reason. Every read fails open — an unreadable
sentinel means "no request", never a stalled run.

The same file is what makes `tempa implement --stop-graceful` in a terminal stop a run started from
the dashboard, and vice versa: both sides resolve it through the active workspace. The dashboard's
status endpoints OR the sentinel into the in-memory flag so a request made either way shows up in
the UI.

## Prompt construction

Every stage's prompt is built by `tempa_prompts.build_prompt()`, which prepends the workspace's
Architecture Principles (if set) and substitutes `${...}` placeholders (`${sources.prd}`,
`${config_path}`, etc.) into the matching template in `src/prompt/*.md`. See
[architecture-principles.md](architecture-principles.md#how-it-reaches-the-prompts) for the full
stage → template table, and [prompt-templates.md](prompt-templates.md) for how to edit a
template safely.

`tempa_session.py` never builds prompts itself — callers (`tempa_clarify`, `tempa_implement`,
`tempa_commands.run_verify`) build the string via `tempa_prompts` first and pass it in.

Prompts carry *paths*, not documents: the agent reads the PRD with its own tools. The one
piece of content injected inline is the clarification prompt's pending-resolution overlay
(`${pending_resolutions}` — see
[clarify-modes.md](clarify-modes.md#pending-resolutions-overlay)). Note where that work is
split: `tempa_clarify` computes the overlay (it already imports `dashboard_clarify_parse` for
marker parsing) and hands it to `tempa_prompts`, which only *formats* it. That keeps
`tempa_prompts` — a CLI-half module — free of any import from the dashboard half, matching the
boundary described above.

## Extending Tempa

**Adding a new CLI command:** add the subparser in `tempa_cli._build_arg_parser()`, dispatch it in
`run()`, and put the handler in whichever `tempa_*.py` module fits by responsibility (a new
workflow → its own module; a small stateless command → `tempa_commands.py`).

**Adding a new session/prompt stage:** add the template to `src/prompt/`, a `build_*_prompt()` in
`tempa_prompts.py`, and a runner in `tempa_session.py` if it needs its own stop-condition handling
— otherwise reuse `_run_oneshot_session`.

**Adding a new dashboard route:** add an entry to `GET_ROUTES`/`POST_ROUTES` in
`dashboard_server._DashboardHandler` plus the one-line adapter method it names, and put the actual
logic in the `dashboard_api_*` module for that pane (a function taking what it needs explicitly and
returning `(status, payload)` — no HTTP objects, so it can be tested directly).

**Adding a new standalone guide page** (a document opened in its own tab, not part of the
single-page app): follow the `principles_guide_page()`/`spec_guide_page()` pattern in
`dashboard_assets.py` — a static `.html` file in `src/assets/` with the dashboard's CSS inlined at
request time — and give it a route in `GET_ROUTES` like the two existing ones.

**Adding a new background run kicked off from the dashboard:** see
[The CLI/dashboard boundary](#the-clidashboard-boundary) above — it goes through
`dashboard_runs.py` as a subprocess, not a direct import.

## Tests

`tests/` (pytest) covers the pure-logic modules end to end — see
[CONTRIBUTING.md](../CONTRIBUTING.md#testing) for how to run it and which modules aren't covered
yet (anything that shells out to `claude` or serves the dashboard's HTTP handler directly).
