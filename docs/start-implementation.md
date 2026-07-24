# `implement` Details (Run Implementation)

See [README.md](../README.md) — Step 3 (Run Implementation) — for a short summary. This
document explains the details.

```bash
py tempa.py implement                 # poll every 60 seconds (drafts a plan first if there's no task yet)
py tempa.py implement --replan        # force a fresh plan first, then continue implementation
py tempa.py implement --features 4    # cap at 4 features per session (overrides config)
```

## Flow

When run, the harness first checks `config.json`: if there's no task yet (epic/feature) —
e.g. the first run, or `--replan` was given — **plan drafting** runs automatically first
(studying the PRD + `docs/` + the actual code, then creating new epics/features/tasks and
registering them in config.json). After that, the implementation loop runs per epic:

1. The system implements **one or more features** within the epic currently being worked on.
2. Once **all features** in that epic are done → **QA** runs automatically against every
   feature in that epic.
3. **QA finds an issue** → the affected feature is fixed (epic status `require_fixing` →
   `on_progress` → ...), then QA runs again.
4. **No issues found** → move on to the next epic, or **stop** if every epic is already done
   (exit 0).

Work-selection priority each time the runner polls (this implements the 1–4 sequence above):

1. **Resume QA** that was interrupted (`qa_status == ongoing`).
2. **Resume the session** of an epic that's `on_progress`.
3. **QA gate** — an epic that's `done` but hasn't passed QA (`qa_passed == false`) → run QA.
4. An epic marked **`require_fixing`** (already implemented but hit QA findings) → fix it.
5. An epic marked **`pending`** → implement from scratch.
6. Nothing left → **everything is done**, the runner stops (exit 0).

The runner stops automatically when: every epic is done, an epic is `failed`, the
`max_session_run` limit is reached, or Claude's **usage limit** is hit (exit 2; the epic is
left as-is so it can be resumed once the limit resets — just run `implement` again).

## Epic Status Lifecycle

```
pending ──► on_progress ──► done ──►[QA]──► qa_passed=true ✅ (move to next epic)
                                      │
                                      └─(QA finds a problem)─► require_fixing ──► on_progress ──► ...
```

- **Epic status** (`status`): `pending` · `on_progress` · `done` · `require_fixing` · `failed`.
- **QA status** (`qa_status`): `idle` · `ongoing` · `done`.
- **`qa_passed`**: `false` until QA passes, then `true`.

Epic status is changed to `done` / `require_fixing` by **Claude itself** by editing
config.json during the session; the harness only marks it `failed` when a session errors out
(not on a usage-limit stop).

## Monitor

```bash
py tempa.py status                  # summary of all epic + feature + QA status
py tempa.py show-folders            # active working folder
```

## Recovery (if something goes wrong)

```bash
py tempa.py implement --reset          # epic on_progress → pending (clears session_id)
py tempa.py implement --reset-failed   # all failed epics → pending
py tempa.py implement --reset-qa       # force re-QA for every done epic
py tempa.py implement --clear      # delete ALL files in qa/ and logs/ (QA reports + session logs)
py tempa.py implement --clear-plan # clear plan: wipes specs/pbi contents + empties the "epic" array
py tempa.py clarify --clear        # clear clarifications: deletes files in specs/clarifications (except claude.md)
py tempa.py clear                  # runs all three clear commands above, behind one confirmation
```

> `implement --clear`, `implement --clear-plan`, `clarify --clear` (and the combined
> `clear`) are **destructive**:
> - `implement --clear` deletes ALL files in `qa/` and `logs/` (config.json is left untouched).
> - `implement --clear-plan` deletes the ENTIRE contents of the `specs/pbi` folder (including
>   `README.md`/`findings.md`/`workplan.md`) and empties the `epic` array in config.json.
> - `clarify --clear` deletes all contents of the `specs/clarifications` folder EXCEPT
>   `claude.md` (config.json is left untouched).
>
> All of them ask for confirmation (`type "yes"`) before deleting; add `--yes` to skip the
> confirmation (e.g. for non-interactive use). `clear` asks once and then runs all three.

## Manual verification

Manually verify a single epic (produces a report in `verify/`):

```bash
py tempa.py verify EPIC-05
```
