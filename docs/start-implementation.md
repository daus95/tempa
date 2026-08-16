# `implement` Details (Run Implementation)

See [README.md](../README.md) — Step 3 (Run Implementation) — for a short summary. This
document explains the details.

```bash
tempa implement                 # poll every 60 seconds (drafts a plan first if there's no task yet)
tempa implement --replan        # force a fresh plan first, then continue implementation
tempa implement --features 4    # cap at 4 features per session (overrides config)
```

## Prerequisite: the PRD must be current

The dashboard's **Start Implementation** button is gated (client-side *and* server-side in
`_handle_implement_run_start`) on two things beyond the configured
`implementation_start_requirement`:

- Clarification has been run at least once.
- **Every recorded clarification answer has been applied to the PRD** — i.e. the pending
  resolution overlay is empty (see
  [clarify-modes.md](clarify-modes.md#pending-resolutions-overlay)).

That second condition holds at **every** requirement level, including `"none"`. The setting
expresses how strict to be about *open questions*; this is a different thing. Implementation
reads the PRD/spec documents, so a decision recorded in a clarification file but never
applied is invisible to it — the epic would be built from a specification the user has
already decided against. Since clarification no longer applies after every round, this gate
is the only thing left forcing the documents to be current before any code is written.

When it's the only thing blocking, the button and the 409 response say exactly that
("N answered clarification finding(s) … haven't been written into the PRD yet"), and
**Apply Answers** clears it — a no-op apply on an already-matching PRD is cheap and simply
re-stamps `clarify_applied_hashes`. There is deliberately no override.

Running `tempa implement` from the CLI bypasses this (the command itself has no awareness of
clarification state), so apply first when driving it by hand.

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

The runner stops automatically when: every epic is done (exit 0), an epic is `failed` (exit 1),
the `max_session_run` limit is reached, or the configured backend's **authentication fails**
(exit 3; expired login or an invalid API key — re-authenticate with whichever CLI is configured
for `implement` — `claude` + `/login`, `copilot login`, or `codex login` — then run `implement`
again). See [ai-models.md](ai-models.md) for how to check/change the `implement` stage's backend.

**A usage limit does not stop the runner.** If the configured backend's usage/session limit
is hit — during plan drafting or mid-epic — `implement` doesn't exit: it logs that it's
pausing (explicitly "not an error"), waits **30 minutes** for the limit to reset, then
retries automatically, repeating for as long as the limit is still in effect. The epic/QA
state it was working on is untouched while it waits (`on_progress`/`qa_status=ongoing`, same
as always), so the retry resumes exactly where it left off rather than starting the epic
over. This is core `implement` behavior — it applies the same way whether you run
`tempa implement` directly or via the dashboard's **Start Implementation** (which just runs
`tempa implement` as a subprocess and streams its output). Leave it running; there's nothing
to re-trigger by hand. `clarify`/`clarify --finalize`/`clarify --apply` behave the same way
for their own usage-limit stops.

**Neither does a provider-side overload (529).** When the AI provider fails to process a
request because its API is momentarily overloaded — Anthropic's transient `529 Overloaded`
— nothing on Tempa's or your app's side is broken, so `implement` doesn't exit either: it
logs the pause, waits **5 minutes** (much shorter than the usage-limit wait, since an
overload usually clears in minutes), then retries the interrupted epic/QA automatically,
repeating for as long as the overload lasts.

Before each of those retries resumes, `implement` clears any epic that the interrupted
session left behind as `failed` in config.json, flipping it back to `pending` — the
in-process equivalent of `tempa implement --reset-failed`. That step is what makes the retry
actually able to continue:

- `failed` is only skipped while the overload was *recognized in the streamed output*. An
  overload that surfaces in different wording, or one that kills the backend CLI again on
  the very next attempt, reaches Tempa as a plain non-zero exit and gets marked `failed`.
- `failed` is sticky and blocking: the runner halts on any failed epic that precedes the
  next one to work on (`Halted — session [x] at index i has failed`) — not just for the rest
  of that run, but for every `tempa implement` afterwards too.

So without the reset, one 529 could leave the project permanently stuck on a status that
never represented a real problem. Note the reset is deliberately scoped to this retry path:
a genuine session failure still stops the runner (exit 1) and still keeps its `failed`
status, so it stays visible — fix the cause, then run `tempa implement --reset-failed`
yourself (see [Recovery](#recovery-if-something-goes-wrong) below).

**On the dashboard, that reset is part of the button.** Once any epic has run, the
dashboard's **Start Implementation** button relabels itself to **Continue Implementation**,
and clicking it runs `tempa implement --reset-failed` first (streamed into the Log tab), then
`tempa implement`. Without it the button would be dead on arrival after any failed session —
every click would halt immediately on the same failed epic, with the CLI as the only way
forward. It's a no-op when nothing is failed, so it costs nothing on a clean continue. This
applies to all three copies of the button (Home step 3, the Clarification ready banner, the
Implementation header) — they all trigger the same run. The CLI is unchanged: plain
`tempa implement` still halts on a `failed` epic and tells you to reset it.

**A backend CLI that finishes but never exits doesn't stop the run either — and doesn't even
need the reset above.** A watchdog thread runs alongside every backend session (implement,
QA, `clarify`, `verify`) — once the CLI signals its turn is complete (a `[Done] ...` line,
per that backend's own output format), it's given **120 seconds** to actually exit on its own
before Tempa force-terminates the process instead of waiting on it indefinitely. Seen live: a
QA session's very last action tried to stop a background test process it had spawned; that
cleanup command was rejected by the CLI's own sandbox policy, and the process itself never
returned, leaving an otherwise fully finished session (report already written, config.json
already updated) stuck for 17+ minutes. For a session run from `implement`'s loop
(implementation or QA), this isn't treated as a real failure in the first place — the
epic/QA state on disk already reflects everything that session actually did, since it had
already reached `[Done]` before getting stuck in unrelated cleanup — so the epic is never
marked `failed` and there's nothing to reset; the loop just logs what happened and resumes
immediately (no wait, unlike the usage-limit/overload cases above). A `clarify`/`verify`
session hit by the same watchdog is still force-terminated (so it can't hang forever either),
but its non-zero exit is treated as an ordinary failure there rather than silently retried.

## Epic Status Lifecycle

```
pending ──► on_progress ──► done ──►[QA]──► qa_passed=true ✅ (move to next epic)
                                      │
                                      └─(QA finds a problem)─► require_fixing ──► on_progress ──► ...
```

- **Epic status** (`status`): `pending` · `on_progress` · `done` · `require_fixing` · `failed`.
- **QA status** (`qa_status`): `idle` · `ongoing` · `done`.
- **`qa_passed`**: `false` until QA passes, then `true`.

Epic status is changed to `done` / `require_fixing` by **the agent itself** by editing
config.json during the session; the harness only marks it `failed` when a session errors out
(not on a usage-limit stop, and not on a recognized provider overload — and a `failed` left
behind by an overload that *wasn't* recognized is reset back to `pending` before the
automatic retry, see the 529 note above).

**The QA gate doesn't trust a "done" epic blindly.** Marking each feature `done` and
incrementing `completed_features`, then only *after* every feature is done marking the epic
itself `done`, is all on the agent's own bookkeeping (see the MANDATORY RULE the
implementation prompt gives it) — a step it can skip. Before running QA on a `done` epic,
`check_and_run` checks that every one of its features is actually marked `done` and
`completed_features` matches `total_features`. If not, the epic is routed back to
`require_fixing` instead — genuinely finishing the remaining features — rather than running
QA (and possibly passing it) against work that was never actually completed. The same check
runs when manually forcing a re-check with `implement --reset-qa EPIC-ID` (see Recovery
below), so resetting an epic that has this problem doesn't just immediately re-trigger QA
against the same incomplete state.

**A QA pass also resyncs the feature bookkeeping.** When QA *fails*, it rewrites every
affected feature to `require_fixing` and recalculates `completed_features` — so the fix round
that follows has to mark each of those features `done` again for the epic's own bookkeeping to
end up consistent. When that round skips the per-feature step (marking only the epic itself
`done`), the epic can end up `done` + `qa_passed: true` while its features still read
`require_fixing` and `completed_features` still reads `0/N` — a self-contradictory state that
misreports progress in `tempa status` and on the dashboard, and that makes a later
`--reset-qa` bounce the epic into a pointless re-implementation round. The QA prompt's pass
branch now tells the agent to mark every feature `done` and set `completed_features` to the
total, and `check_and_run` reconciles it deterministically on every poll (and right after
each QA session) for the cases where the agent doesn't — including configs left in that state
by earlier versions. Only an epic that is `done` **and** `qa_passed: true` **and**
`qa_status: done` is reconciled: a failing QA verdict sets the epic-level status to
`require_fixing` before it touches any feature, so an in-flight or failed QA round is never
overwritten.

**A genuine QA pass also commits the workspace, by default.** Right after the reconciliation
above, `run_qa_session` (`tempa_session.py`) checks whether this epic just landed a real pass
(`status == "done"`, `qa_passed == true`, `qa_status == "done"`) and, if
`commit_after_qa_pass` is enabled (default `true` — see [config-json.md](config-json.md) and
dashboard Settings → Runs tab → "Version Control"), runs `git add -A` + `git commit` in
`workspace.root` via `tempa_git.commit_workspace_changes`. This is a one-shot hook tied to the
QA session that produced the pass — it doesn't also fire from `check_and_run`'s reconcile
catch-all, since a real pass is only ever freshly observed once, right when that QA session
ends. The outcome is always just logged, never fatal: a workspace that isn't a git
repository, or has nothing to commit, is logged as "skipped"; a real git failure (e.g. no
`user.name`/`user.email` configured) is logged as "failed" — neither stops the run or marks
the epic `failed`.

## Cross-Epic Dependencies (No-Forward-Progress Guard)

Epics are implemented in plan order — normally that's fine, since later epics build on
earlier ones. But if the plan schedules an epic before something it actually depends on (a
feature needs functionality only a *later* epic provides), the backend CLI correctly refuses
to work around the architecture violation, explains why, and exits cleanly (code 0) every
time it's resumed — which looks identical to genuine, if slow, progress unless something is
watching for it.

**Detecting a stall.** After every resumed session that exits 0, Tempa compares
`completed_features` before and after. If `implement_no_progress_rounds` (default `2`,
`config.json`) resumed sessions in a row complete zero additional features, the epic is
treated as stalled rather than still working.

**Reporting the blocker.** The implementation prompt asks the agent to record which specific
epic it's blocked on, by setting `"blocked_by_epic"` on its own `config.json` entry — e.g.
`"blocked_by_epic": "EPIC-17"` — whenever it determines a feature can't proceed without
another, not-yet-implemented epic's work. It's only set when the agent is confident which
epic owns the missing dependency.

**Automatic recovery.** Once the stall limit is reached, Tempa tries to fix the plan's
ordering itself: it moves the named epic to immediately before the stuck one, so the
scheduler works on the real dependency next instead of endlessly re-resuming the epic that
can't proceed. This is refused (falling through to the failure path below) when the move
isn't safe:

- the named epic doesn't exist in the plan,
- it's already marked `done` (so it's probably not the real blocker anymore),
- it's already scheduled before the stuck epic (the reorder already happened, but the block
  persists regardless),
- an epic names itself, or
- it would reverse an earlier move in the opposite direction — a likely **circular
  dependency** between the two epics, which reordering alone can never resolve.

A successful reorder resets the epic back to `pending` (dropping out of the "resume any
`on_progress` epic first" priority — see Epic Status Lifecycle above — so the promoted
dependency actually goes next) and logs what happened; a new `implementation_auto_reordered`
email alert event fires too (dashboard Settings → Notifications tab), purely informational.

This operates at the epic level only: the whole named epic gets fully implemented (every
feature in it) before the stuck epic resumes, even if only one of its features was actually
the dependency — Tempa doesn't interleave individual features across epics.

**When it can't fix itself.** If the reorder is refused, the epic is marked `failed` instead,
with the session's own explanation captured as `blocked_reason` — shown on the dashboard's
Status tab and in `tempa status`, not just left in a log file. For the circular case
specifically, `blocked_reason` folds in *both* epics' own last-reported explanations, since a
human deciding how to resolve it needs both sides in one place. A genuine circular dependency
between two epics is a plan design problem — no reordering scheme can satisfy "A before B"
and "B before A" at the same time — so it's deliberately left for a human decision (adjust the
epics/features in the plan) rather than an automatic restructuring attempt; see
[Recovery](#recovery-if-something-goes-wrong) below once it's resolved.

## Monitor

```bash
tempa status                  # summary of all epic + feature + QA status
tempa show-folders            # active working folder
```

The dashboard's Implementation → Status tab shows the same per-epic/feature state live, plus a
collapsible **QA history** on each epic card: every QA round it's had, which features were
flagged, and a link to open that round's report. An epic with one or more "strikes" toward the
QA loop guard (see `qa_loop_strikes` in [config-json.md](config-json.md)) shows a `⚠ N
strike(s)` badge next to the toggle even while collapsed — visible while the epic is still
`require_fixing` and the run is still going, not just after the guard gives up and marks it
`failed`.

## Stopping a run

There are two ways to stop, and the difference is what happens to the session already in flight.

**Stop Now** (the dashboard button, or Ctrl+C in a terminal) kills the runner and the backend CLI
it spawned, immediately. Whatever the current feature or QA session had worked out but not yet
written to `config.json` or the spec is lost, and the epic is left `on_progress` (or
`qa_status=ongoing`) for the next run to resume from. Use it when you need the machine back now.

**Stop After Current Session** — the chevron next to that button, or `tempa implement
--stop-graceful` — leaves a request instead. The session in progress runs to completion and
records its work as usual; the runner then exits cleanly (code 0) rather than picking up the next
epic or QA round. Nothing you have already paid tokens for is thrown away, and the run resumes from
a clean boundary with **Continue Implementation** (or `tempa implement`).

While the request is pending the header reads *Stopping after current session…*, and the dropdown
offers **Cancel Graceful Stop** if you change your mind. Stop Now still works during that window,
so asking to stop politely never traps you into waiting. Requests made in a terminal and from the
dashboard are the same request — either surface can make it, cancel it, or show it.

```bash
tempa implement --stop-graceful          # stop after the session in progress finishes
tempa implement --stop-graceful-cancel   # changed your mind
tempa status                             # shows "Graceful stop pending" while one is set
```

A pending request is cleared automatically when a run starts, so one left behind by a crash can
never stop the next run before it does any work.

## Recovery (if something goes wrong)

```bash
tempa implement --reset          # epic on_progress → pending (clears session_id)
tempa implement --reset-failed   # all failed epics → pending (run this after fixing a real failure;
                                 #   an overload-induced failure is reset automatically, and so is
                                 #   any failure when you click Continue Implementation on the
                                 #   dashboard — see above)
tempa implement --reset-qa       # force re-QA for every done epic
tempa implement --reset-qa EPIC-04   # same, but only for that one epic
```

`--reset-failed` also covers an epic that hit `max_session_run` or the no-forward-progress
guard (see [Cross-Epic Dependencies](#cross-epic-dependencies-no-forward-progress-guard)
above) — both mark the epic `failed` now instead of leaving it stuck in `on_progress` forever
with no self-service way out. It clears the epic's run/stall counters too (`total_run`,
`qa_total_run`, `no_progress_rounds`), not just its status, so the reset is a genuine clean
slate rather than one that immediately re-trips the same limit on the very next attempt.

`--reset-qa EPIC-ID` is the way to force a specific already-`done`-and-QA-passed epic to be
re-checked — e.g. if you notice its features don't actually look finished despite "QA ok"
(see the QA gate integrity check under [Epic Status Lifecycle](#epic-status-lifecycle) above).
It also runs that same check: if the epic's features genuinely aren't all `done`, it's routed
back to `require_fixing` instead of staying `done`, so it's actually finished before QA
re-runs on it — resetting `qa_passed` alone wouldn't have been enough, since `check_and_run`'s
QA gate acts on any `done` epic regardless of how it got there.

```bash
tempa implement --clear      # delete ALL files in .tempa/qa/ and .tempa/logs/ (QA reports + session logs)
tempa implement --clear-plan # clear plan: wipes .tempa/specs/pbi contents + empties the "epic" array
tempa clarify --clear        # clear clarifications: deletes files in .tempa/specs/clarifications (except claude.md)
tempa clear                  # runs all three clear commands above, behind one confirmation
```

> `implement --clear`, `implement --clear-plan`, `clarify --clear` (and the combined
> `clear`) are **destructive**:
> - `implement --clear` deletes ALL files in `.tempa/qa/` and `.tempa/logs/` (config.json is left untouched).
> - `implement --clear-plan` deletes the ENTIRE contents of the `.tempa/specs/pbi` folder (including
>   `README.md`/`findings.md`/`workplan.md`) and empties the `epic` array in config.json.
> - `clarify --clear` deletes all contents of the `.tempa/specs/clarifications` folder EXCEPT
>   `claude.md` (config.json is left untouched).
>
> All of them ask for confirmation (`type "yes"`) before deleting; add `--yes` to skip the
> confirmation (e.g. for non-interactive use). `clear` asks once and then runs all three.

## Manual verification

Manually verify a single epic (produces a report in `.tempa/verify/`):

```bash
tempa verify EPIC-05
```
