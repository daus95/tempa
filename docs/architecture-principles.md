# Architecture Principles

Project-wide rules that Tempa applies to **every** stage it runs. Optional — skip it entirely and
Tempa behaves exactly as it did before.

## What it is

Tempa runs a project through several independent Claude sessions: clarification, applying answers,
planning epics, implementation, QA, and verification. None of them remembers the previous one.
Anything that should hold true across all of them — the database you use, whether an ORM is
allowed, how API errors are shaped, what has to be true before code counts as done — has nowhere
to live except repeated in every specification.

Architecture Principles is that missing place. You write the rules once; Tempa prepends them to
every prompt it builds.

## Where it lives

```
<workspace_root>/.tempa/architecture-principles.md
```

One file per workspace, alongside `config.json` / `logs/` / `qa/` / `verify/` / `specs/` (see
[folders-and-paths.md](folders-and-paths.md)). Deliberately **not** a `config.json` key — it's a
multi-paragraph document, and prompt content already lives as `.md` files on disk in this
codebase.

- **Absent or blank file = unset.** Nothing is injected and prompts are byte-identical to a Tempa
  without this feature.
- **Survives `tempa clear`.** Clearing removes plans, QA reports, logs, and clarification results
  — never the principles.
- **Survives `tempa close-folder`.** Like everything else under `.tempa/`, it stays put and is
  picked back up when the workspace is reopened.

## How to set it

Editing is done in the dashboard:

```bash
tempa dashboard
```

The Home page shows an **Architecture Principles** card above Step 1 (Upload Specification), and
there's a matching entry in the sidebar. Both open a page with a free-form Markdown textarea, a
**Learn more** link to an in-app guide with worked examples, and a Save button. Saving an empty box
deletes the file, which is how you unset the principles.

To read the current value from the terminal:

```bash
tempa show-principles
```

## How it reaches the prompts

`build_prompt()` in `src/tempa_prompts.py` is the single function every stage's prompt is built
through. It prepends the principles block before doing its `${...}` placeholder substitution, so
all ten prompts get it automatically:

| Stage | Template |
|---|---|
| Clarification | `clarification.md` |
| Apply answers | `apply_clarification.md` |
| Auto-answer | `auto_answer.md` |
| Plan epics | `plan_epics.md` |
| Review epics | `review_epics.md` |
| Implementation | `implementation.md` |
| Implementation (resume) | `continuation.md` |
| QA | `qa.md` |
| QA (resume) | `qa_continuation.md` |
| Verify | `verify.md` |

There is **no `${...}` placeholder** for the principles — injection is unconditional, so a
placeholder would duplicate the block in any template that used it. A new stage added later gets
the principles for free as long as it builds its prompt via `build_prompt()`.

The injected block frames the principles as non-negotiable: they outrank convention, convenience,
and the model's own defaults, and a conflict with the prompt or the specification must be reported
rather than silently resolved. The file is read fresh on every prompt build, so an edit takes
effect on the next session with no restart.

## Writing good principles

1. **Be specific enough to check.** "Write clean code" changes nothing — the model already thinks
   it is. "No function over 50 lines, no file over 400 lines" is enforceable.
2. **Say what to do, not only what to avoid.** "Don't use an ORM" leaves the alternative
   undefined. Add "all database access goes through hand-written SQL in a repository class".
3. **Include the reason when it isn't obvious.** A stated reason lets the model judge edge cases
   instead of applying the rule mechanically.
4. **Keep them stable.** If it changes next sprint, it's a task, not a principle.
5. **Keep the list short.** Ten to thirty sharp rules beat a hundred vague ones.
6. **Don't contradict yourself.** Every principle is enforced; two that can't both hold produce a
   stream of reported conflicts instead of progress.

What does **not** belong here: requirements for one specific feature (that's the PRD), decisions
you haven't made yet (leave those for clarification to surface), roadmaps, or business background.

## Example

```markdown
# Architecture Principles — Clinic Back-Office

## Stack
- Python 3.12 + FastAPI for all backend services. No second framework.
- PostgreSQL 16 is the only datastore. Flag any proposal to add another.

## Structure
- One service per folder under apps/. A service owns its own tables and never
  reads another service's tables directly.
- Database access only through repository classes. No SQL in route handlers.

## Data
- All timestamps are TIMESTAMPTZ stored in UTC. We operate across three time
  zones and have been burned by mixed offsets before.
- Money is stored as integer cents, never as a float.
- Schema changes ship as numbered forward-only migrations.

## Security
- Every endpoint is authenticated unless explicitly marked public with a
  justifying comment.
- Secrets come from environment variables. Never committed, never logged.
- Patient data must never appear in application logs. Regulatory requirement.

## Quality
- Every endpoint has an integration test against a real database.
  Mocked-database tests do not count.
- Every bug fix begins with a failing test that reproduces it.
```

The dashboard's **Learn more** page (`/architecture-principles` while the dashboard is running)
has a longer set of examples grouped by category, plus common mistakes to avoid.
