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
