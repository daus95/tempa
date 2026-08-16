# Logs & Output

See [README.md](../README.md) for a summary. All paths below are relative to the active
workspace's `.tempa/` folder (i.e. `<workspace_root>/.tempa/...`) — see
[folders-and-paths.md](folders-and-paths.md).

| Location | Contents |
|--------|-----|
| `.tempa/logs/process_*.txt` | Runner process log (all `log()` output) |
| `.tempa/logs/session_*.txt` | Log per implementation session |
| `.tempa/logs/qa_*.txt` | Log per QA session |
| `.tempa/logs/clarification_*` · `.tempa/logs/apply_clarification_*` | Clarification loop logs |
| `.tempa/logs/plan_epics_*` | Plan-drafting log (automatic via `implement`) |
| `.tempa/logs/*.prompt.md` | Sidecar prompt file, one per session, for backends that can't take a multi-line prompt as a CLI argument (currently GitHub Copilot CLI) — see [ai-models.md](ai-models.md) |
| `.tempa/qa/EPIC-NN-qa-*.md` | QA findings report per epic |
| `.tempa/verify/EPIC-NN-verify-*.md` | Manual verification report |

To wipe everything in `.tempa/qa/` and `.tempa/logs/`: `tempa implement --clear` (asks for
confirmation; add `--yes` to skip). See [docs/command-reference.md](command-reference.md).

## Viewing a log file in the dashboard

Every "log: `<filename>`" reference the Clarify/Implement Log tabs print (from a session's
own startup banner line) is a clickable link — clicking it opens the file's content in a
viewer modal instead of needing to go find it on disk. The link detection
(`LOG_FILENAME_RE` in `assets/js/64-clarify-run.js`) keys off the `_<YYYYMMDD>_<HHMMSS>.txt` suffix every
log filename above ends with, not a list of known prefixes, so a new `log_prefix` added to
`_run_backend_session` becomes clickable with no dashboard change. The modal is large by default
(`min(92vw, 1200px)` × `min(88vh, 900px)`) with a header button to toggle fullscreen, since a
session log can legitimately run past 400KB.

Served by `GET /api/log-file?name=<filename>` (`dashboard_server.py`), confined to
`.tempa/logs/` via the same path-traversal guard used for spec/clarification file reads,
restricted to `.txt` files, and capped at 5MB — keeping the **tail** (the most recently
written, most diagnostically relevant part) for the rare pathologically large file, with a
`truncated` flag in the response so the modal can say so.
