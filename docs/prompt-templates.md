# Prompt Templates (`src/prompt/`)

See [README.md](../README.md) for a summary. One `.md` file per prompt; edit them directly
to customize harness behavior.

| File | Used when |
|------|--------------|
| `implementation.md` | Implementing a new feature (`implement`) |
| `continuation.md` | Resuming an implementation session (falls back to `implementation.md`) |
| `qa.md` | Automatic QA after an epic is `done` |
| `qa_continuation.md` | Resuming an interrupted QA session (falls back to `qa.md`) |
| `clarification.md` | Clarification evaluation (`clarify`) |
| `auto_answer.md` | Answering still-unanswered findings (`clarify --auto-answer`) |
| `apply_clarification.md` | Applying clarification resolutions to the PRD/spec (`clarify --apply` & `clarify --finalize`) |
| `plan_epics.md` | Generating epics/features/tasks (plan drafting, automatic via `implement`) |
| `review_epics.md` | Reviewing & fixing the result (plan drafting, automatic via `implement`) |
| `verify.md` | Manual verification (`verify`) |

**Placeholders** substituted at runtime (depending on the prompt): `${epic}`, `${sources}`,
`${sources.<key>}`, `${config_path}`, `${output_file}`, `${qa_output_file}`.
