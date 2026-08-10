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
`${sources.<key>}`, `${config_path}`, `${output_file}`, `${qa_output_file}`,
`${clarification_files}`, `${finding_scope}`, `${pending_resolutions}`.

Nearly all of these substitute a **path** (or a list of them) and leave the reading to the
agent's own tools — the PRD is never inlined into a prompt. The one exception is
`${pending_resolutions}` in `clarification.md`: the question-and-answer text of every
clarification finding that's been answered but not yet written into the PRD, rendered inline
so the evaluation can treat those decisions as settled without an apply pass having run
first. See [clarify-modes.md](clarify-modes.md#pending-resolutions-overlay). It's built by
`_render_pending_overlay()` in `src/tempa_prompts.py` from data the CLI half computes
(`pending_resolutions()`), and it substitutes to an explicit "nothing pending" line rather
than an empty string when there's no overlay.

## Architecture principles are prepended automatically

If the workspace has an [Architecture Principles](architecture-principles.md) document
(`.tempa/architecture-principles.md`), its content is prepended to **every** template above —
framed as non-negotiable rules — before the placeholders are substituted. That happens in
`build_prompt()` in `src/tempa_prompts.py`, the one function all ten prompts are built through, so
the same rules apply to clarification, planning, implementation, QA, and verification alike.

There is no placeholder for it: injection is unconditional, so a `${...}` would duplicate the
block. An absent or blank file injects nothing, leaving the templates exactly as written. Read the
current value with `tempa show-principles`; edit it in the dashboard.
