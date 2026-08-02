# CLI Backend Availability

See [README.md](../README.md) for a summary. This document explains the dashboard's **CLI
backend availability** check — the ✅/⬜ checklist shown on the Home page and the Settings
page's AI Backend & Model section — what "ready" actually means, where it's shown, and how
it's computed.

## What "ready" means

For each of the three supported backends (Claude Code, GitHub Copilot CLI, OpenAI Codex
CLI — see [ai-models.md](ai-models.md)), Tempa checks two things:

| Check | What it verifies |
|---|---|
| **Installed** | The backend's CLI executable resolves on `PATH` (the same lookup `tempa_session.py` uses right before spawning a session — see [architecture.md](architecture.md)) |
| **Writable** | The current OS user can write files into the active workspace's folder |

A backend is **ready** only when both are true. The writability check is the same result
for all three backends — they all run as normal subprocesses under your OS user account, so
whichever account is running Tempa either can or can't write to the workspace regardless of
which CLI is invoked. Installed status, on the other hand, is per backend.

Neither check actually invokes the CLI (no `--version`, no test session) — both are cheap,
synchronous, local checks, safe to run on every dashboard page load. That also means **this
is not an authentication check**: a backend can show "ready" and still fail at runtime if
you aren't logged in to it (see [Choosing a CLI Backend](../README.md#choosing-a-cli-backend)
in the README for each backend's login command). For a check that *does* exercise the
configured `implement`-stage backend end-to-end (a real Write/Read/Delete test), see
`tempa test` in [command-reference.md](command-reference.md).

## Where it's shown

- **Home page** — a "CLI backends ready for this workspace" checklist sits right under the
  working-folder path, so availability is visible as soon as a workspace is open. Before a
  workspace is open (or right after `tempa close-folder`), all three show "no workspace open
  yet" instead of a writability verdict.
- **Settings page** — the same checklist appears at the top of **AI Backend & Model**, above
  the three per-stage backend dropdowns (Clarification / Planning / Implementation). Any
  backend that isn't ready is also flagged inline in each dropdown's option label (e.g.
  `GitHub Copilot CLI (not ready)`), so it's visible right where you're choosing.
- **Detect CLI Backends button** — next to the Settings checklist, this re-runs both checks
  on demand and updates the checklist and dropdown labels immediately, without reloading the
  rest of the Settings form (so any unsaved model/effort edits aren't lost). Use it right
  after installing a CLI, logging into one, or fixing the workspace folder's permissions.

Picking a backend that isn't ready is still allowed — the checklist is informational, not a
save-time restriction, since you might be about to install/authenticate it.

## Under the hood

Reference for anyone extending this — see [architecture.md](architecture.md) for the module
map these live in:

- `tempa_config.workspace_is_writable(root)` — the filesystem-write probe (creates and
  immediately removes a temp file inside `root`).
- `tempa_backend.get_backend_status(workspace_writable)` — combines that with
  `tempa_backend.resolve_exe()` per backend into `{name: {label, installed, writable,
  ready}}`.
- `GET /api/tree` and `GET /api/config` (`dashboard_server.py`) both include this under a
  `backends`/`backends_status` key, so the Home and Settings pages already have it on first
  load without an extra round trip.
- `GET /api/backends/status` — a small dedicated endpoint the **Detect CLI Backends** button
  calls for an on-demand re-check.
