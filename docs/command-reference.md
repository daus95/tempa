# Command Reference

See [README.md](../README.md) for a workflow summary. Full list of every command.

| Command | Description |
|---------|-----------|
| `init <abs>` | Init project: sets `workspace.root` + creates working folders on disk (safe to re-run) |
| `set-folders --root <abs> [...]` | Sets the working folder in config only (root absolute; the rest relative) |
| `show-folders` | Shows the active working folder + resolved absolute paths |
| `set-model [--clarify m] [--plan m] [--implement m]` | Sets the AI model per stage (alias/id) |
| `show-models` | Shows the AI model per stage |
| `dashboard` | Opens the web dashboard (Home / Specification / Clarification / Implementation) |
| `spec --show` | Opens the dashboard directly on the Specification section |
| `clarify` | Clarify the PRD — one manual evaluation pass (human-in-the-loop), then opens the clarification-answer web UI on the result (unless `--noui`); saving in the UI auto-applies the answers, then asks whether to run another round |
| `clarify --noui` | Same as `clarify`, but skip opening the answer web UI |
| `answer` | Scans `sources.clarifications` for clarification files and, if any finding is still unanswered, reopens the clarification-answer web UI on ALL of them (one tab per file, badged complete/incomplete); saving auto-applies the answers, then asks whether to run another round |
| `clarify --auto-answer` | Automatically answers findings that are still unanswered (no re-evaluation) |
| `clarify --apply` | Applies answers from the clarification file into the PRD/spec document (no re-evaluation), then asks whether to run another round |
| `clarify --finalize` | Evaluate + auto-answer, looping until there's no critical/major left |
| `clarify --clear [--yes]` | Deletes all files in `specs/clarifications` except `claude.md` (asks for confirmation) |
| `implement --clear-plan [--yes]` | Clears the plan: wipes all of `specs/pbi` + empties the `epic` array (asks for confirmation) |
| `implement [--features N]` | Runs the implementation loop (polls every 60s). Drafts a plan automatically first if there's no task yet |
| `implement --replan` | Forces a fresh plan first, then continues/starts implementation |
| `verify <epic>` | Manually verifies a single epic → report in `verify/` |
| `status` | Summary of progress for every epic |
| `implement --reset` | `on_progress` → `pending` |
| `implement --reset-failed` | `failed` → `pending` |
| `implement --reset-qa` | Forces re-QA for `done` epics |
| `implement --clear [--yes]` | Deletes ALL files in `qa/` and `logs/` (asks for confirmation) |
| `clear [--yes]` | Runs `implement --clear` + `implement --clear-plan` + `clarify --clear` together, behind a single confirmation |
| `test` | Permission test (verifies the Claude CLI works) |
| `--help` | Full help text |

**Global flag** — `--show-prompt`: shows the prompt sent to Claude in the console. By
default the prompt is **not** shown (it's still logged to `logs/`). Applies to every command
that runs a Claude session — pass it after the subcommand, e.g. `tempa implement --show-prompt`.
