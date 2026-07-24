# `config.json` Structure

See [README.md](../README.md) for a summary. Full reference for every key in `config.json`.

| Key | Purpose |
|-----|--------|
| `workspace` | Working folder (root + docs/adr/specs/apps/infra/archive) — see [folders-and-paths.md](folders-and-paths.md) |
| `sources` | Input/output paths for each command (relative to `workspace.root`) — see [folders-and-paths.md](folders-and-paths.md) |
| `models` | AI model per stage (`clarify`/`plan`/`implement`) — see [ai-models.md](ai-models.md) |
| `features_per_session` | Max features per session (`null` = no limit) |
| `max_session_run` | Max sessions per epic (anti-loop safeguard) |
| `max_clarification_run` | Max rounds for the `clarify --finalize` loop |
| `last_clarification_findings` | Summary of the last clarification findings (critical/major/minor) |
| `last_auto_answer` | Number of findings answered by the last `clarify --auto-answer` |
| `epic` | Array of state for every epic (status, features, QA, session_id, etc.) |

Prompt templates are **no longer** in config.json — see [prompt-templates.md](prompt-templates.md).
