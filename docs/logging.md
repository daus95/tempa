# Logs & Output

See [README.md](../README.md) for a summary.

| Location | Contents |
|--------|-----|
| `logs/process_*.txt` | Runner process log (all `log()` output) |
| `logs/session_*.txt` | Log per implementation session |
| `logs/qa_*.txt` | Log per QA session |
| `logs/clarification_*` · `logs/apply_clarification_*` | Clarification loop logs |
| `logs/plan_epics_*` | Plan-drafting log (automatic via `implement`) |
| `qa/EPIC-NN-qa-*.md` | QA findings report per epic |
| `verify/EPIC-NN-verify-*.md` | Manual verification report |

To wipe everything in `qa/` and `logs/`: `tempa implement --clear` (asks for
confirmation; add `--yes` to skip). See [docs/command-reference.md](command-reference.md).
