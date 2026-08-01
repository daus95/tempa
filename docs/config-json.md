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
| `features_per_session` | Max features per session (`null` = no limit) |
| `max_session_run` | Max sessions per epic (anti-loop safeguard) |
| `max_clarification_run` | Max rounds for the `clarify --finalize` loop |
| `last_clarification_findings` | Summary of the last clarification findings (critical/major/minor) |
| `last_clarification_round` | Round number of the most recent evaluate pass (manual `clarify` or one iteration of `clarify --finalize`) — shown on the dashboard as "Round N of `max_clarification_run`" |
| `last_auto_answer` | Number of findings answered by the last `clarify --auto-answer` |
| `allow_finalize_with_critical` | Dashboard Settings toggle (default `false`). When `true`, "Finalized Clarification" is allowed to start even while critical findings remain open, letting its automated evaluate/apply loop attempt to resolve them unsupervised. Never relaxes the separate Start Implementation gate, which always requires zero critical and zero major findings |
| `epic` | Array of state for every epic (status, features, QA, session_id, etc.) |

Prompt templates are **no longer** in config.json — see [prompt-templates.md](prompt-templates.md).
Architecture principles aren't in config.json either: they live in
`.tempa/architecture-principles.md` — see
[architecture-principles.md](architecture-principles.md).
