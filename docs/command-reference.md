# Command Reference

See [README.md](../README.md) for a workflow summary. Full list of every command.

| Command | Description |
|---------|-----------|
| `init <abs>` | Makes `<abs>` the active workspace + creates working folders on disk (safe to re-run — an existing `.tempa/config.json` is loaded as-is, not reset) |
| `set-folders --root <abs> [...]` | Sets the working folder in config only (root absolute; the rest relative) |
| `show-folders` | Shows the active working folder + resolved absolute paths |
| `close-folder` | Detaches the active workspace (drops the active-workspace pointer only — the workspace's own `.tempa/` folder is left untouched, ready to reopen) |
| `set-model [--clarify m] [--clarify-apply m] [--plan m] [--implement m]` | Sets the AI model per stage (alias/id — aliases are Claude-only). `--clarify-apply` covers `clarify --apply`/`--auto-answer`/the apply half of `--finalize` — mechanical work, defaults to a cheaper model than evaluate |
| `show-models` | Shows the AI model per stage |
| `set-backend [--clarify b] [--clarify-apply b] [--plan b] [--implement b]` | Sets the CLI backend per stage: `claude` \| `copilot` \| `codex` |
| `show-backends` | Shows the CLI backend per stage |
| `set-effort [--clarify e] [--clarify-apply e] [--plan e] [--implement e]` | Sets the reasoning effort per stage — must be supported by that stage's backend+model, `""` clears it back to the default |
| `show-efforts` | Shows the reasoning effort per stage |
| `show-principles` | Shows the [architecture principles](architecture-principles.md) prepended to every stage's prompt (optional; edited in the dashboard) |
| `dashboard` | Opens the web dashboard (Home / Specification / Clarification / Implementation) |
| `spec --show` | Opens the dashboard directly on the Specification section |
| `clarify` | Clarify the PRD — one manual evaluation pass (human-in-the-loop), then opens the clarification-answer web UI on the result (unless `--noui`); saving in the UI auto-applies the answers, then asks whether to run another round |
| `clarify --noui` | Same as `clarify`, but skip opening the answer web UI |
| `clarify --skip-minor` | Evaluate for critical/major findings only — minor findings are skipped entirely. Combinable with `--noui`/`--finalize`. Config.json's `skip_minor_findings` (dashboard toggle, default `true`) is used whenever this flag is omitted |
| `answer` | Scans `sources.clarifications` for clarification files and, if any finding is still unanswered, reopens the clarification-answer web UI on ALL of them (one tab per file, badged complete/incomplete); saving auto-applies the answers, then asks whether to run another round |
| `clarify --auto-answer` | Automatically answers findings that are still unanswered (no re-evaluation) |
| `clarify --apply` | Applies answers from the clarification file into the PRD/spec document (no re-evaluation), then asks whether to run another round |
| `clarify --finalize` | Evaluate + auto-answer, looping until there's no critical/major left |
| `clarify --clear [--yes]` | Deletes all files in `.tempa/specs/clarifications` except `claude.md` (asks for confirmation) |
| `implement --clear-plan [--yes]` | Clears the plan: wipes all of `.tempa/specs/pbi` + empties the `epic` array (asks for confirmation) |
| `implement [--features N]` | Runs the implementation loop (polls every 60s). Drafts a plan automatically first if there's no task yet |
| `implement --replan` | Forces a fresh plan first, then continues/starts implementation |
| `verify <epic>` | Manually verifies a single epic → report in `.tempa/verify/` |
| `status` | Summary of progress for every epic |
| `implement --reset` | `on_progress` → `pending` |
| `implement --reset-failed` | `failed` → `pending` |
| `implement --reset-qa [EPIC]` | Forces re-QA for all `done` epics, or just `EPIC` if given — also routes it back to `require_fixing` first if its own features weren't actually all done |
| `implement --clear [--yes]` | Deletes ALL files in `.tempa/qa/` and `.tempa/logs/` (asks for confirmation) |
| `clear [--yes]` | Runs `implement --clear` + `implement --clear-plan` + `clarify --clear` together, behind a single confirmation |
| `test` | Permission test (verifies the `implement` stage's configured backend CLI works). For a lighter, non-invoking check across all three backends, see the dashboard's readiness checklist: [cli-availability.md](cli-availability.md) |
| `version` | Shows the locally installed Tempa version (read from the `VERSION` file at the install root) |
| `check-update` | Checks GitHub for the latest published release and compares it against the installed version (works offline-gracefully — prints a message instead of failing if GitHub can't be reached). Same check is available from the dashboard's Settings → Maintenance tab — see [updates.md](updates.md) |
| `update [--yes]` | If a newer release exists, downloads it and overwrites this install's files with it (asks for confirmation showing current → latest version; only files actually shipped in the release archive are touched — `.tempa/`, `.active-workspace`, and similar local-only files/folders are left alone). Same action is available from the dashboard's Settings → Maintenance tab (**Update Now**) — see [updates.md](updates.md) |
| `--help` | Full help text |

**Global flag** — `--show-prompt`: shows the prompt sent to the backend CLI in the console. By
default the prompt is **not** shown (it's still logged to `.tempa/logs/`). Applies to every
command that runs an agent session — pass it after the subcommand, e.g. `tempa implement --show-prompt`.
