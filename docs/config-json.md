# `config.json` Structure

See [README.md](../README.md) for a summary. Full reference for every key in `config.json`.

`config.json` lives inside the **active workspace**, at `<workspace_root>/.tempa/config.json` —
not inside Tempa's own install folder. Each workspace keeps its own copy, so switching
workspaces (`tempa close-folder` + `tempa init <other_path>`) never overwrites another
workspace's settings/history. Before any workspace is active, Tempa falls back to a scratch
copy at `<tempa_install>/.tempa/config.json`. See
[folders-and-paths.md](folders-and-paths.md) for the full mechanism.

| Key | Purpose |
|-----|--------|
| `workspace` | Working folder (root + docs/adr/specs/apps/infra/archive) — see [folders-and-paths.md](folders-and-paths.md) |
| `sources` | Input/output paths for each command, derived from `workspace` by default (optional per-key override) — see [folders-and-paths.md](folders-and-paths.md) |
| `models` | AI model per stage (`clarify`/`plan`/`implement`) — see [ai-models.md](ai-models.md) |
| `backends` | CLI backend per stage (`claude`/`copilot`/`codex`) — see [ai-models.md](ai-models.md). Whether each backend is currently *usable* (installed + workspace writable) is checked live, not stored here — see [cli-availability.md](cli-availability.md) |
| `reasoning_efforts` | Reasoning effort per stage (`""` = no override); must be supported by that stage's backend+model — see [ai-models.md](ai-models.md) |
| `features_per_session` | Max features per session (`null` = no limit; default `3`) — dashboard Settings' "Features per Session" |
| `max_session_run` | Max sessions per epic (anti-loop safeguard; default `30`) — dashboard Settings' "Max Session Runs" |
| `max_clarification_run` | Max rounds for the `clarify --finalize` loop (default `20`) — dashboard Settings' "Max Finalize Clarification Round". Read **once**, when the finalize run starts (`run_clarify_finalize` in `tempa_clarify.py`): a running loop keeps counting toward the limit that was in effect when it began, even though it re-reads the rest of config.json every round. Changing the value therefore applies from the *next* finalize run onward — nothing needs restarting, since every run is a fresh process. Saving a new value from dashboard Settings while a finalize run is in progress returns a `warning` on `/api/config/save` saying exactly that (`_max_clarification_run_change_warning` in `dashboard_runs.py`) |
| `last_clarification_findings` | Summary of the last clarification findings (critical/major/minor) |
| `last_clarification_action` | Which kind of clarification pass ran most recently: `"evaluate"` (`clarify`, or one evaluate iteration of `clarify --finalize`) or `"apply"` (`clarify --apply`, saving in the answer UI, or one apply iteration of `--finalize`). Absent until clarification has run at least once — stamped in `tempa_clarify.py`. Both readiness gates depend on it: its mere presence is the "clarification has been run" condition for Finalize *and* Start Implementation (see `implementation_start_requirement` below), and Finalize additionally wants it to be `"evaluate"`, since an apply pass edits the PRD without re-checking what's left. The combined `tempa clear` removes it (`_reset_clarify_config_state` in `tempa_maintenance.py`) so a wiped workspace reads as never-clarified instead of pointing at runs whose files are gone; plain `clarify --clear` deletes only the files and leaves config.json alone |
| `last_clarification_round` | Running total of every evaluate pass ever (`+= 1` on each one, manual `clarify` or one iteration of `clarify --finalize` alike) — shown on the dashboard as "Round N" next to "Finalize readiness". Unbounded: manual `clarify` isn't limited by `max_clarification_run`, so this is never compared against it |
| `last_finalize_round` | The `clarify --finalize` loop's own in-run counter (`run_number` in `run_clarify_finalize`) — reset to `0` at the start of every finalize run and counted up to `max_clarification_run` within that one run only, independent of `last_clarification_round` above. Shown on the dashboard as "N / M" next to the "Finalized Clarification" / "Stop Finalize" button, refreshed live every second while a run is in progress (`/api/clarify/run`'s poll response) |
| `last_auto_answer` | Number of findings answered by the last `clarify --auto-answer` |
| `last_clean_evaluation_at` | Epoch timestamp of the most recent fresh evaluate pass that found zero findings across every severity (`0` if none yet). Such a pass leaves no new clarification file behind (there's nothing to write), so the finalize/implement readiness gates fall back to this timestamp — see `_latest_evaluation_findings` in `dashboard_clarify_parse.py` — to recognize a genuinely clean round instead of misreading a stale, older finding-bearing file as still current |
| `allow_finalize_with_critical` | Dashboard Settings toggle (default `false`). When `true`, "Finalized Clarification" is allowed to start even while critical findings remain open, letting its automated evaluate/apply loop attempt to resolve them unsupervised. Independent of `implementation_start_requirement` below — this only ever affects Finalized Clarification |
| `skip_minor_findings` | Dashboard Clarification-page toggle + CLI `--skip-minor` (default `true`). When `true`, Start Clarification / Finalized Clarification evaluation passes only look for critical and major findings — minor findings are skipped entirely |
| `implementation_start_requirement` | Dashboard Settings "Start Implementation requires" control — one of `"no_critical_or_major"` (default: zero critical and zero major findings in the most recent evaluation), `"no_critical"` (zero critical findings only; major findings may remain open), or `"none"` (no clarification-findings condition; clarification must still have been run at least once). Enforced both client-side (Home/Clarification/Implementation pages) and server-side in `_handle_implement_run_start` |
| `clarify_applied_hashes` | Per clarification file, a hash of the exact content that was last applied to the PRD/spec (`{filename: content_hash}`). A fully-answered file whose current hash still matches is shown as "✅ Applied" in the dashboard's Clarification overview; a mismatch (or a missing entry) means its answers haven't reached the PRD yet, which is what enables the **Apply Answers** buttons and what the dashboard's apply auto-chain keeps re-running `clarify --apply` for until nothing is left. Cleared by `tempa clear` alongside `last_clarification_action` above |
| `clarify_file_timings` | Per clarification file, how long its evaluate pass took and how long its most recent apply pass took (`{filename: {clarify_seconds, apply_seconds}}`) — shown in the dashboard's clarification row detail dialog |
| `epic` | Array of state for every epic (status, features, QA, session_id, etc.). Each entry's resumable session ids are tagged with the backend that produced them (`session_id`/`session_backend` for implement, `qa_session_id`/`qa_session_backend` for QA) — a stage's backend switching mid-epic starts fresh instead of resuming a foreign CLI's session id. Configs from before multi-backend support (bare `claude_session_id`, no `*_backend` companion) are treated as backend `claude` |

Prompt templates are **no longer** in config.json — see [prompt-templates.md](prompt-templates.md).
Architecture principles aren't in config.json either: they live in
`.tempa/architecture-principles.md` — see
[architecture-principles.md](architecture-principles.md).
