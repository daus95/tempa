# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once the first tagged release is cut.

## [Unreleased]

## [0.6.12] - 2026-08-17

### Fixed

- **The QA convergence guard no longer halts an epic on regression evidence it already
  counted.** A strike is meant to be one round showing the pattern, but the rules scanned the
  whole history for it — so once a feature had regressed once, every later round it stayed
  failing re-found that same old gap and scored another strike. An epic then tripped on "2
  rounds in a row showing it" when only the first round showed anything, turning
  `qa_loop_strikes` into "one regression plus N-1 ordinary consecutive failures" and spending
  exactly the tolerance that limit exists to provide. Seen on an epic whose feature failed round
  1, passed rounds 2-3, regressed in round 4, and was struck a second time in round 5 for
  round 4's gap all over again — while round 5's actual finding was a new defect that re-broke
  nothing. A regression now counts only when the re-verification is the round immediately
  before, and a round identical to the one before it is no longer read as the epic "coming back
  around" to an earlier state. Genuine oscillation still trips on the second strike as before,
  and standing still is still bounded by `max_qa_fail_rounds`.

### Changed

- **Fix sessions now see the epic's earlier QA reports, not just the newest one.** A session
  told only what the latest round found can reroute control flow around a code path an earlier
  round already had to fix, bringing that finding straight back — the single most common way an
  epic starts cycling through QA. The older reports are now listed (oldest first) as settled
  findings to check the session's own changes against and to leave a guarding test behind for,
  explicitly not as work to redo.
- **The QA halt's diagnosis hint now names a missing spec, not only a contradictory one.** Its
  first branch asked whether the two reports "ask for opposite things", which doesn't fit the
  common case where each round is internally consistent and simply turns on a question the spec
  never settles. It now covers a hole in the spec as well as a contradiction in it, since both
  need the same response: a ruling written into the epic's spec or Architecture Principles.

## [0.6.11] - 2026-08-17

### Added

- **Each epic's card now has a "Spec" button** that opens that epic's own specification — the
  file QA grades it against — in the same editor the Specification section uses. It was
  previously unreachable from the dashboard entirely: `sources.epics` is a *sibling* of the PRD
  folder that section's tree is rooted at, so nothing under it could be browsed to. That made
  the one file you need when QA rounds contradict each other the one file you had to leave the
  dashboard to read.

### Changed

- **The QA convergence guard no longer tells you to "fix the underlying conflict".** That
  wording named one cause as though it were the only one, sending a reader hunting for a
  contradiction that often isn't there — the guard trips on a *pattern* (a feature fixed,
  re-verified, then failing again), and that pattern has several causes needing opposite
  responses. The halt now describes how to tell them apart: compare the last two QA reports for
  the features named, and treat opposite demands as a spec contradiction needing your ruling,
  something genuinely broken as an ordinary regression worth retrying, and shifting minor items
  as churn to be handled by raising Features per Session / QA Loop Strikes. It points at the
  epic's spec and Architecture Principles as the two places a ruling can be written down.

## [0.6.10] - 2026-08-17

### Added

- **`qa_loop_strikes` and `max_qa_fail_rounds` now have Settings controls.** Giving an epic
  more rope before the QA convergence guard halts the run used to mean hand-editing
  `config.json` — the two knobs that decide when Tempa gives up on an epic were the ones a
  user was most likely to want to change after being stopped by them. Both are now on
  Settings → Runs → Run Limits, as "QA Loop Strikes" and "Max QA Fail Rounds".

### Fixed

- **Saving Settings from a dashboard tab left open across an upgrade no longer fails or
  silently resets a field.** A run-limit field the payload doesn't mention at all is now left
  exactly as it is on disk, instead of being reset to its default (optional fields) or
  rejecting the entire save with a message about a control the client isn't even rendering
  (required ones). A blank value is still a deliberate answer and behaves as before.
- **A halted epic never said how to get it moving again.** When a guard gives up on an epic,
  the dashboard's ⚠ Halted panel shows that epic's `blocked_reason` and nothing else — but
  the remediation was only ever written to the log line beside it, so the one place a user
  actually reads about the halt didn't mention that clicking **Continue Implementation**
  recovers it (that button has always run `--reset-failed` itself before every implementation
  pass). Every guard now appends the way out to `blocked_reason`, dashboard route first, so
  it appears both on the epic card and in `tempa status`.
- **Three of the five ways an epic can fail wrote no `blocked_reason` at all.** Hitting
  `max_session_run`, QA hitting the same limit without ever passing, and an implementation
  session exiting non-zero all marked the epic `failed` and left it at that — on the Status
  tab it showed up as a bare red ✗ with no cause and no next step anywhere, the explanation
  being buried in the process log. All three now record what happened (including, for the QA
  limit, the fact that the epic was marked failed rather than passed *because it has never
  been verified*) alongside how to retry.

## [0.6.9] - 2026-08-17

### Fixed

- **An epic could never pass QA because every round failed it on a different set of
  nitpicks.** The QA prompt treated ⚠️ exactly like ❌, so anything a reviewer would like
  improved marked its feature `require_fixing` — and against a spec with dozens of literal
  "How to test" bullets, an LLM reviewer will always find one whose exact phrasing no test
  is named after. Each round therefore failed a different subset of features while the code
  itself was fine, and because the loop guard reads a feature that's absent from one round
  and back in the next as work being undone, that shifting subset eventually halted the run
  as "cycling through QA". Observed on a 7-feature epic that spent 5 rounds and ~2.5 hours
  never converging, its final report carrying zero ❌ items. QA now grades at three levels
  and only two of them block: ❌ (not implemented, or fails when run), ⚠️ (implemented, but
  its observable behaviour or contract differs from the spec — the agent must be able to
  state what goes wrong at run time and for whom), and 📝 advisory (correct and verified,
  but more coverage or a better-named test would be nice). Advisory notes get their own
  report section and never mark a feature `require_fixing`.
- **Every QA round re-derived its own opinion of the epic from scratch.** Nothing carried
  the previous round's findings forward, so consecutive rounds examined different things and
  flagged different features — the shifting subset above. From round 2 on, the QA prompt now
  carries the previous round's report and requires the agent to re-verify its ❌/⚠️ items
  first (stating which findings are repeats and whether an earlier fix was undone), treat
  its 📝 notes as settled, and only then look for genuinely new defects. A report is now
  written on every round, a passing one included, so its advisory notes survive for the
  next round.
- **A QA agent could halt the run by editing the runner's own bookkeeping.** config.json is
  shared with the spawned agent, and one QA session appended its own `qa_history` entry —
  invented timestamp and all — for the round it was still working on. The runner then
  recorded that same round properly, leaving two identical rounds pointing at one report
  file, which is exactly the fingerprint the loop guard reads as an epic going in circles.
  `qa_history`, `qa_loop_strikes`, `blocked_reason`, `total_run` and `qa_total_run` are now
  snapshotted before every implementation and QA session and restored afterwards (logged
  when it happens), before that session's own outcome is recorded; the QA prompts also name
  those fields as off-limits. Independently, `record_qa_round` now treats one report file as
  one round, so a round written down twice overwrites rather than duplicates.
- **A QA round that flagged no features at all was read as proof that every feature had been
  re-verified.** Both loop-guard pattern rules key off a feature being *absent* from a round
  in between, taken to mean QA looked and was satisfied. A round whose per-feature
  bookkeeping was skipped fingerprints as an empty set, which made every feature look
  absent — so a single round of missing bookkeeping could read as a wholesale regression, or
  supply the "different set in between" that turns a repeat into a false cycle. Empty rounds
  are no longer accepted as that evidence; the `max_qa_fail_rounds` backstop still bounds
  them, which is what it exists for.

### Changed

- **Settings field descriptions: "More…" now sits inline instead of on its own line.**
  Long field descriptions on Settings previously parked their extra detail behind a
  `<details>`/`<summary>` disclosure that rendered as a separate line below the first
  sentence. It's now an inline "More…" toggle right at the end of that sentence, and
  expanding it reveals the rest of the text with a "Hide…" toggle at its end — the same
  "See more"/"See less" convention as Facebook — so collapsing stays near wherever the
  reader's eye already is instead of requiring a scroll back to the top.
- **Simplified the "Terminate leftover processes" description on Settings → Runs.** The
  explanation leaned on implementation detail (Job Objects, process groups, process trees)
  and a lengthy anecdote that made the setting harder to parse than it needed to be. It now
  states plainly what gets shut down, what's left alone, and when a change takes effect,
  without changing the setting's actual behavior.

### Added

- **The Maintenance tab now shows what's actually in an available update.** Previously
  "Update available: X" was all the information given — deciding whether to take an update
  meant leaving the dashboard to read the release on GitHub by hand, and there was no way to
  tell how many releases behind the installation was. A new "What's New" link opens the
  changelog entries for every version between the installed one and the latest, newest
  first, rendered the same way QA reports already are. The content is fetched from
  `CHANGELOG.md` pinned to the latest release's tag, so it always matches what actually
  shipped.
- **Sidebar now flags an available update.** Previously the only way to learn a newer Tempa
  release existed was to open Settings → Maintenance and check manually. The dashboard now
  checks once on load and, if a newer version is available, shows an "Update available"
  item above Architecture Principles in the sidebar; clicking it jumps straight to
  Settings → Maintenance.

### Fixed

- **Clarification and implementation can no longer run at the same time.** Start
  Clarification, Apply Answers, and Finalized Clarification stayed clickable on the Home
  and Clarification pages while an implementation run was in progress (and vice versa),
  risking both processes writing to the spec/PRD concurrently. All six buttons now
  disable for the duration of the other run, on both pages, and the dashboard's
  `/api/clarify/run` and `/api/implement/run` endpoints now reject the request with a
  409 if the other kind of run is already active.

## [0.6.8] - 2026-08-16

### Added

- **Processes a session leaves behind are now terminated with it.** An agent CLI starts
  long-running commands routinely — a dev server to check its own work, a file watcher, a
  build daemon, a test runner — and doesn't reliably stop them. Once the CLI exits they are
  orphaned, and nothing could reach them: Tempa's only tree-kill walks live parent-pid links,
  which an orphan by definition no longer has. In one workspace that left a `vite` dev server
  still holding its port 5.4 hours after the session that started it had finished, alongside
  15 idle build workers holding 1.9 GB between them. Each backend CLI is now spawned inside a
  container the operating system tears down along with the session — a Job Object on Windows,
  a process group on Linux and macOS — so the session's whole process tree goes when the
  session does, not just the CLI process. Because it asks only who started a process and
  never what it is, this works the same for any toolchain. On by default; toggle it off in
  dashboard Settings → Runs tab → "Process Cleanup" if you want a session's processes to
  outlive it. Anything genuinely detached — a Docker container, an installed service — is out
  of scope and unchanged, and containment that can't be established is logged and the session
  continues as before, since cleanup must never fail a run.

### Fixed

- **A backend session that raised mid-stream left its CLI process running.** The spawn in
  `_stream_backend_process` had no `try`/`finally`, so an exception in the output-parsing
  loop propagated out without ever reaching `process.wait()` or terminating anything — the
  one path where the CLI was leaked outright rather than merely outliving its usefulness.
- **A backend CLI that never exited would wedge the runner indefinitely.** The post-loop
  `process.wait()` had no timeout. It is now bounded whenever the session is contained (there
  is something to fall back on); with containment off it still waits exactly as before.
- **On Linux and macOS, the dashboard's Stop button orphaned the backend CLI.** It sent
  `terminate()` to the `tempa implement` process only — the Windows path had a `taskkill /T`
  tree-kill, the POSIX path had no equivalent. Tempa now installs SIGINT/SIGTERM handlers
  that reclaim the contained session, so Stop and Ctrl+C both reach the CLI's whole tree.

## [0.6.7] - 2026-08-16

### Changed

- **The session engine and the per-stage session runners are separate modules.**
  `tempa_session.py` (1,292 lines before this release's refactors) kept both "how do you run
  a backend CLI and read its output" and "what does the QA stage do with the result" in one
  place. The engine is 657 lines now; the five runners — implementation, QA, one-shot
  plan/review, clarification, apply-clarification — are `tempa_session_runners.py`.

- **The dashboard's background-run workers are no longer closures.** `_start_clarify_run`
  and `_start_implement_run` each wrapped a `worker()` around a near-identical `run_once()`,
  reachable only by actually spawning `tempa.py`. Both are now module-level functions over a
  single `_stream_tempa_command`, which means the apply auto-chain loop, its stalled/stopped
  message paths, and implement's `--reset-failed` pass are covered by tests for the first
  time (10 new ones) instead of being exercised only in production.

- **The dashboard's HTTP handler is now routing only, with each pane's logic in its own
  module.** `dashboard_server.py` was a 1,238-line class where routing, validation and
  business logic for seven feature areas sat in one 50-method block; it is 447 lines now —
  two route tables and a one-line adapter per route — with the work moved into
  `dashboard_api_spec`, `dashboard_api_clarify`, `dashboard_api_status`,
  `dashboard_api_settings` and `dashboard_api_workspace`. Those take what they need as plain
  arguments and return the `(status, payload)` to send, so the Settings form's 20-odd
  validation rules are now testable without an HTTP request. Every route answers exactly
  what it did before, held in place by the 101 characterization tests added first.

- **Tempa's self-update and its `--help` page moved out of the modules they were crowding.**
  `tempa version`/`check-update`/`update` are now `tempa_update.py` — the only module that
  talks to GitHub or writes to the install folder — and the dashboard reads the version from
  there directly instead of lazily importing `tempa_commands` (which drags in the whole
  dashboard via `dashboard_ui`). The 170-line hand-written help page is now
  `tempa_cli_help.py`, leaving `tempa_cli.py` a 320-line dispatch layer. Same commands, same
  output, verified identical string-for-string.

- **The three longest functions in the CLI half are now split into named steps.**
  `run_session`'s 150-line "what does this exit code mean for the epic?" tail moved into a
  new `tempa_session_outcome.py` (`apply_session_outcome`, plus one small function per
  outcome: reorder the plan, repair a QA-state desync, fail and stop); `check_and_run`'s
  four scheduling branches became `_resume_interrupted_qa` / `_resume_in_progress_epic` /
  `_run_qa_gate` / `_start_next_epic`, called in that priority order; and
  `run_clarify_finalize`'s loop body split into evaluate / compact / convergence-guard /
  auto-answer steps. Behavior is unchanged — every log line, notification, and exit code is
  byte-identical, verified by comparing the full set of runtime string literals before and
  after — and 12 new tests now cover the extracted implementation-outcome decision tree
  directly.

- **The dashboard's front-end script is now split across `src/assets/js/` instead of one
  3,558-line `dashboard.js`.** Twenty ordered parts, one per pane/concern (markdown renderer,
  DOM refs + state, modals, home, clarification, implementation, verification, settings,
  specification, shared events), concatenated by `dashboard_assets.JS_PARTS` into the same
  single inline `<script>` the page always had. No module system, no imports, no behavior
  change: the assembled script is byte-for-byte identical to the file it replaces.

### Added

- **Test coverage for the dashboard's HTTP surface, its page assembly, and the CLI's
  command surface.** `dashboard_server.py` (every `/api/*` route), `dashboard_assets.py`
  (CSS/JS inlining and the per-request data placeholders), and `tempa_cli.py` (which
  subcommands exist, what they accept, and whether the help text documents them) had no
  tests at all. 147 new characterization tests pin down the status codes, JSON shapes, and
  user-facing error strings these currently produce — groundwork for splitting the two
  largest modules without changing what they do.

## [0.6.6] - 2026-08-16

### Fixed

- **A session the backend CLI cut short no longer gets mistaken for a blocked epic.**
  Claude Code's print mode waits a fixed 10 minutes for the background work a turn left
  running — a delegated sub-agent, a `run_in_background`/`nohup ... &` command — then prints
  "Background tasks still running after 600s; terminating.", kills it, and **exits 0**. A single
  feature routinely takes longer than that, so a session that was working perfectly well got
  killed mid-implementation and reported as SUCCEEDED with nothing to show for it. Two of those
  in a row is exactly what `implement_no_progress_rounds` reads as "blocked on something outside
  this epic", so the epic was marked `failed` — with a `blocked_reason` quoting whatever
  happened to be the last line of the log — and the whole agent runner stopped, despite the epic
  being one Playwright run away from done. Tempa now raises that ceiling to its own
  `backend_background_wait_sec` (new; default 1 hour) on every spawn, recognizes the CLI's
  "terminating" message if it's hit anyway, reports the session honestly as cut short instead of
  SUCCEEDED, and leaves that round out of the no-progress count so the epic simply resumes.
  `max_session_run` remains the backstop against a genuine loop.

### Added

- `backend_background_wait_sec` in `config.json` (default `3600`): how long a backend CLI waits
  for the background work a session left running before killing it and exiting. Set as an
  environment default per backend — Claude Code's `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` today —
  and only when you haven't exported the variable yourself, so a hand-pinned value still wins.
  `0` is the CLI's "wait indefinitely" value; it is deliberately not the default, since a session
  that leaves a dev server running would then hang the runner with no upper bound.

### Changed

- The autonomous system prompt now tells the agent to do the implementation itself rather than
  handing a whole feature to a background sub-agent and ending its turn waiting on it, and to
  stop every process it started that doesn't exit on its own (dev servers, API hosts, watchers)
  before finishing — the two habits that make a backend CLI hit the ceiling above at all.

## [0.6.5] - 2026-08-16

### Added

- **Every Stop button can now stop *after* the work in flight instead of throwing it away.**
  Stopping used to mean one thing: `taskkill /T /F` on the whole process tree, backend CLI
  included. Press it twenty minutes into a feature session and those twenty minutes of tokens
  are gone — the agent never got to write its results to `config.json` or the spec, and the epic
  is left `on_progress` for the next run to redo from scratch. Each Stop button is now a split
  button: **Stop Now** is exactly the old behaviour, and the chevron beside it offers **Stop
  After Current Session** (**Stop After Current Round** for Finalized Clarification), which lets
  the session already running finish and record its work, then declines to start the next one.
  The runner exits cleanly and the run resumes from that boundary. While a request is pending the
  status reads "Stopping after current session…" and the chevron offers **Cancel Graceful Stop**;
  Stop Now keeps working throughout, so asking to stop politely never traps you into waiting.
  Covers Implementation, Clarification, Apply Answers and Finalized Clarification — each stopping
  at its own natural seam (between epic sessions, after the evaluate session, between apply
  batches, between finalize rounds).
- `tempa implement --stop-graceful` / `--stop-graceful-cancel` and the `clarify` equivalents do
  the same from a terminal, and it is the *same* request either way: a stop asked for in a
  terminal halts a run started from the dashboard, and the dashboard shows a request made in a
  terminal. `tempa status` reports a pending one. The channel is a sentinel file in `.tempa/`
  rather than a key in `config.json`, which the runner's own saves would have overwritten; a
  request left behind by a crash is cleared automatically the next time a run starts, so it can
  never stop a fresh run before it does any work.
- **The sidebar now shows when Clarification or Implementation is running, from any page.** An
  animated spinner appears next to each nav item while its background run (clarify/apply/finalize,
  implement) is active, and the Clarification page's "Continue Implementation" button disables
  while an implementation run is already in progress — matching the behavior its twin button on
  the Implementation page already had.

Nothing about how a run behaves otherwise changed: with no graceful stop requested, every new
check is a no-op and Stop Now, exit codes and scheduling are byte-for-byte what they were.

## [0.6.4] - 2026-08-15

### Added

- **The Implementation Status tab now shows each epic's QA round history.** Every epic card
  gets a collapsible "QA history (N rounds)" section — round number, verdict, which features
  were flagged, and a link to that round's report — built from the `qa_history` recorded by
  the QA loop guard. An epic with one or more strikes shows a `⚠ N strike(s)` badge next to
  the toggle even while it's still collapsed and the run is still going, so a cycling epic is
  visible before the guard gives up and stops it, not just after. A new `/api/qa-report`
  route serves the underlying `.tempa/qa/*.md` report a round's link points to, rendered as
  markdown in the same viewer modal the Log tab already uses for session logs.
- **Tempa now notices when an epic is cycling through QA instead of converging, and stops.**
  QA fails features 1–2 → the fix session repairs them and marks the epic done → QA fails
  features 3–4 → fixing those regresses 1–2 → repeat, forever. Every existing safeguard was
  blind to it: `implement_no_progress_rounds` is only evaluated while an epic is still
  `on_progress`/`require_fixing` after a session, and a successful fix round sets it `done`;
  `total_run` is reset by the forward progress each round genuinely makes. Since
  `require_fixing` outranks `pending` in the scheduler, one oscillating epic also blocked
  every later epic for as long as it ran. Each QA verdict is now recorded on the epic as
  `qa_history`, and an epic that shows a regression (a feature that failed, was fixed, was
  re-verified, and is failing again) or a repeated failure set for `qa_loop_strikes` rounds in
  a row (default 2) is marked `failed` with a round-by-round explanation, plus a new
  **QA loop detected** email alert. A `max_qa_fail_rounds` backstop (default 6) covers the case
  where the QA agent writes no per-feature statuses at all, leaving the pattern rules nothing to
  work with.
- `tempa implement --reset-failed` and `--reset-qa` restart that guard's window without
  discarding the history — they append a `reset` marker, so the record of what happened survives
  for whoever has to look at it.

### Changed

- **An epic that exhausts its QA run limit is now marked `failed` instead of passed.** Hitting
  `max_session_run` on QA used to set `qa_passed = true` — declaring the epic verified precisely
  because Tempa had run out of attempts to verify it, while the last real verdict on record was a
  failure. Nothing downstream could tell that apart from a genuine pass. It now stops the run and
  asks for review, recoverable with `tempa implement --reset-failed` like every other failure.
- A failed epic's `blocked_reason` is no longer labelled "no progress across resumed sessions" in
  `tempa status` and the dashboard — more than one guard writes it now, and each explains itself.

### Fixed

- **The runner no longer spins forever instead of halting on a failed epic.** The QA gate's
  "wait for the previous epic's re-implementation" deferral matches any earlier epic with
  `qa_status="done"` and `qa_passed=false` — which is exactly the state a `failed` epic is left in
  once QA found issues and something then gave up on it. That deferral never resolves on its own,
  so the runner logged `QA [x] deferred` on every poll and never reached the halt below it, turning
  an actionable stop into a silent spin. Both places that pick the next thing to do now share the
  same failed-epic check.

## [0.6.3] - 2026-08-15

### Fixed

- **A healthy session no longer gets force-terminated ~2 minutes in, then reported as a
  failure.** The stuck-after-done watchdog treated the backend's first `[Done]` line as "the
  turn is over", but Claude Code emits one of those per re-invocation inside a single run —
  and a resumed session can replay a wakeup its predecessor left pending as `[Done] turns=0`
  before it has done any work at all. The watchdog latched onto that and killed the process
  120s later, mid-test-suite. `[Done]` now only arms the watchdog; any further output disarms
  it again, so it can still catch a backend that genuinely hangs in post-turn cleanup, but
  can only ever fire on a process that has actually gone silent.
- **A force-terminated session no longer marks its epic `failed` and stops the runner.** The
  watchdog raises its "this is not a real failure, resume automatically" flag and the stop
  signal together, from a background thread. The poll loop woke on the stop signal and cleared
  the flag while the session thread was still ~3s from reading it (it has to drain stdout
  first), so the session thread saw a plain non-zero exit, marked the epic `failed`, and the
  runner exited 1 on what it had just decided wasn't a failure. The poll loop now waits for the
  session thread to finish its own bookkeeping before touching those flags. This makes the
  usage-limit, auth-error and overload recovery paths race-free for the same reason.
- **A force-terminate is now logged as what it is**, instead of a red
  `FAILED (exit code 1)` plus a log tail in the dashboard's Log tab.

### Changed

- **Autonomous system prompt now forbids scheduling wakeups and polling background tasks.**
  Sessions run as a one-shot headless CLI invocation, so nothing ever re-invokes the agent
  after it ends a turn to wait — those turns did no work and left a pending wakeup behind for
  the next resumed session to replay. Long builds and test suites should run in the foreground
  with an explicitly raised timeout.

## [0.6.2] - 2026-08-12

### Added

- **Commit after QA pass.** Right after an epic's QA verdict lands as a genuine pass, Tempa
  now runs `git commit` in the workspace, so a long unattended `implement` run leaves a
  checkpoint per verified epic instead of one giant uncommitted diff at the end. On by
  default; toggle it off in dashboard Settings → Runs tab → "Version Control" if you'd
  rather commit by hand. Skipped silently (logged, not an error) if the workspace isn't a
  git repository or there's nothing to commit.

### Fixed

- **Clarification page: "Evaluation scope" and "Finalize readiness" cards moved into the
  Overview tab.** They used to render in the pane header, above the Overview/Log tab bar,
  so they stayed visible even while viewing the Log tab. They now live inside Overview only,
  above the Unanswered/Fully answered tables.
- **"Stop Finalize" button rendering flush against the readiness checklist above it.** The
  spacing rule only targeted the checklist's immediate next sibling ("Finalized
  Clarification"), so it never applied once that button was hidden and replaced by "Stop
  Finalize" during a run. Both buttons now get consistent spacing.

## [0.6.1] - 2026-08-11

### Added

- **Verification in the dashboard.** Each epic on the Implementation page now has a Verify
  button (with a confirmation dialog) that runs `tempa verify <epic>` as a background
  session — unlike Implementation/Clarification, more than one epic can verify at once. A
  new "Verification" section lists every run (running/completed/failed, plus a Passed/
  Issues Found result once the report is in), and clicking a run opens its markdown report,
  with Stop (while running) and Delete actions.
- **"Restart Server" button in Settings**, next to Check for Updates. Stops the running
  dashboard process and relaunches it bound to the same port (retrying briefly to reclaim
  it), then automatically reloads the page once the new instance is back up — no more
  manually closing the dashboard and re-running `tempa dashboard` after an update (which
  used to land on a different, unpredictable port). Blocked with a 409 while a clarify or
  implementation run is in progress, same as Update Now; a confirmation dialog explains the
  impact before it runs.
- **Stop Clarification / Stop Apply Answers buttons.** Start Clarification and Apply
  Answers now swap to a Stop button while running, matching the existing Stop Finalize —
  a confirmation dialog, then the same process-tree kill. Apply Answers additionally
  respects Stop between its auto-chained batches (one backlog file at a time): a Stop
  clicked in that gap skips the next batch instead of waiting for it to start and then
  killing it.

### Changed

- **Settings page now splits into five tabs** — AI Models, Runs, Guardrails, Notifications
  and Maintenance — instead of one long scroll of eight cards. The page header and the
  **Save Settings** bar sit outside the tabs and stay pinned (the same flex layout the
  Implementation and Clarification pages already use), so Save is reachable from any tab and
  one Save still writes every group at once. The bar now also shows an **Unsaved changes**
  hint whenever a field differs from what was loaded, cleared on a successful save. The
  Maintenance tab (Updates, Restart Server) has nothing of its own to save, so the Save
  button is replaced there by a "Nothing to save on this tab." note — unless edits are
  pending on another tab, in which case it comes back.
- **Settings readability pass.** The eight longest field descriptions keep their first
  sentence inline and move the rest into a "More…" disclosure (no wording removed); the four
  backend/model/effort rows gained a Backend · Model · Reasoning effort column header so they
  read as an aligned table; the SMTP fields lay out in two columns on wide screens; and the
  SMTP/provider/alert-event fields are hidden entirely while **Enable email alerts** is off —
  their values are still saved and reloaded exactly as before.
- **README now shows Implementation page screenshots.** The Step 3 — Start Implementation
  section includes example screenshots of the Status and Log tabs
  (`docs/assets/implement-status.webp`, `docs/assets/implement-log.webp`).
- **Clarification page now splits into Overview/Log tabs**, same pattern as the
  Implementation page's Status/Log tabs — the Unanswered/Fully answered tables live under
  Overview, the run log lives under Log. The run buttons and readiness panels (Evaluation
  scope, Pending resolutions, Finalize readiness, Start Implementation) stay pinned above
  both tabs, and a spinning status badge next to the run buttons shows the live
  clarify/apply/finalize progress regardless of which tab is open, so switching to Overview
  during a run no longer hides the fact that something is happening.

### Fixed

- **Claude Code's "session limit" wording is now recognized as a usage-limit stop.** The
  live CLI text "You've hit your session limit · resets 1:30am (Asia/Jakarta)" didn't match
  any of Claude's `usage_limit_markers` (only "weekly limit"/"5-hour limit"/"usage limit"
  wordings were covered), so it fell through as a plain failure instead of triggering
  `wait_out_usage_limit`. QA/implementation resumption kept retrying on every raw poll tick
  (tens of seconds apart) instead of waiting out the configured retry delay.
- **README and docs/clarify-modes.md no longer describe a Finalize run as uncancelable, or
  the Clarification page's log as an expandable panel.** Both were stale against the Stop
  Clarification/Stop Apply Answers buttons and the Overview/Log tab split added above —
  README's Step 2 now documents all three Stop buttons and the tab split, and
  clarify-modes.md documents the auto-chain apply loop's self-stop-on-stall behavior.
- **Apply Answers' auto-chain loop no longer logs "Apply finished" after stalling.**
  When the loop stopped early because a batch wasn't clearing the remaining backlog (not
  because everything was applied), it still printed "Apply finished. Run Continue
  Clarification..." right after telling you it was stopping to let you review by hand —
  contradicting itself. It now only prints "Apply finished" when the backlog is actually
  cleared.

## [0.6.0] - 2026-08-10

### Added

- **Answers are now carried into the next clarification round without applying them first**
  ("pending resolutions overlay"). Every answered finding whose answer isn't in the PRD yet
  is embedded in the evaluation prompt as an already-decided resolution, with rules the
  agent must follow: don't re-raise settled points, a later round supersedes an earlier one,
  and report contradictions between decisions as new findings. This removes the full
  PRD-rewriting agent session that used to be mandatory between every pair of clarification
  rounds.
- **"Pending resolutions" card on the Clarification page**, showing how many answered
  findings (across how many rounds, and roughly how much text) are riding along unapplied.
  Past `clarify_overlay_warn_findings` (new config.json key, default `25`) it also suggests
  applying. Nothing is ever applied automatically.
- **Start Implementation now requires every recorded answer to have been applied to the
  PRD.** Implementation reads the PRD documents, so a decision that only exists in a
  clarification file would be invisible to it. Enforced server-side, and at every
  `implementation_start_requirement` level including `"none"` — with a message naming the
  pending count when it's the only thing blocking.
- **Unanswered/Fully answered panels now update live during a Finalized Clarification
  run**, right after each round's evaluate or apply step succeeds, instead of only once
  the whole (possibly multi-round) run finishes.
- **Clarification answers are locked (read-only) while Finalized Clarification is
  running**, since that run auto-answers findings itself — hand-editing a file mid-run
  could race with its own reads/writes to the same file. Enforced both in the UI and on
  the save endpoint (409 if attempted anyway).
- **Settings → Run Limits now has a "Max Finalize No-Progress Round" field**, right below
  "Max Finalize Clarification Round". It configures `finalize_no_progress_rounds` — how many
  Finalized Clarification rounds in a row may fail to reduce the critical+major finding count
  before the loop gives up early and asks for human answers. Previously this was a
  config.json-only setting hardcoded to default `2`, which stopped finalization far too soon.

### Changed

- **The `01-simple-web-app` example PRD is now the tighter rewrite**, and its Down Payment
  percentage is specified as whole-number entry only (`12`, not `12,3`).
- **Clarification findings are now written short and in paragraphs instead of as one wall
  of text.** The evaluation prompt now caps every paragraph at ~3 sentences, requires a
  blank line between paragraphs (the renderer joins un-separated lines into a single
  block, which is what produced the unreadable slabs), splits Where into "what's wrong"
  and "what it breaks", limits Question to one sentence with the options as bullets, and
  makes Recommendation lead with the resolution itself. Findings cards also got more
  generous line spacing and paragraph margins to match.
- **Continue Clarification is no longer blocked by unapplied answers.** Only findings you
  haven't answered yet hold up the next round. Applying is now something you do when you
  want the PRD documents themselves brought up to date — and once, before starting
  implementation.
- **Save is the primary action in the answer dialog**, with Save & Clarify as the secondary
  choice (they were the other way round, and the secondary choice used to apply to the PRD —
  it now starts the next Continue Clarification round instead). Saving alone is enough to
  keep clarifying.
- **Critical/major/minor finding colors are now clearly distinct** (red/yellow/gray) instead
  of three similarly-dark red-orange-brown shades that were hard to tell apart at a glance,
  especially at icon size.
- **The Fully Answered table no longer has a per-file Apply column/button.** Applying is done
  from the **Apply Answers** button at the top of the page, which already applies every ready
  file in one click — the per-row button was a redundant, easy-to-miss way to do the same
  thing one file at a time. A file's Status cell now shows an **Applied** badge under
  **Complete** once it's been applied.
- **`clarify --finalize` no longer applies after every round.** It now loops
  evaluate → auto-answer until an evaluation reports no critical/major findings, then writes
  the whole accumulated set of answers into the PRD in a single apply ("compaction"), then
  runs one verification evaluation over the result. A dirty verification re-enters the loop
  and compacts a second time; beyond that the run stops and asks for a human. The
  no-progress convergence counter resets after each compaction, since the PRD it was
  measuring has just been rewritten. **Stopping a finalize run mid-way now leaves the
  answers unapplied rather than a partially-updated PRD.**
- **The dashboard no longer auto-runs an evaluation after Apply Answers.** That chain existed
  because continuing was blocked until everything was applied; it isn't anymore, so the extra
  evaluate session is pure cost. Apply still drains its full backlog in one click.
- **`finalize_no_progress_rounds` now defaults to `5`** (was `2`), giving Finalized
  Clarification more attempts before it concludes the remaining findings need a human
  decision. Existing config.json files keep whatever value they already have.
- **The "Finalized Clarification Is Already Running" save warning now covers both finalize
  run limits.** Like "Max Finalize Clarification Round", the new no-progress limit is read
  once when the finalize run starts, so changing either one mid-run now says so and names
  each changed setting.
- **"Finalized Clarification" is gated again on a fresh, zero-critical evaluation**
  — reversing part of `0.4.5`. The button (Home and Clarification Overview) and
  `POST /api/clarify/run` (mode=`finalize`) now require: clarification has run at least
  once, the most recent result came from Start/Continue Clarification (not just Apply
  Answers), and that result shows 0 critical findings. `0.4.5`'s backlog auto-resolution
  (unanswered findings filled in with their own recommendation before Finalize's loop
  starts) is unchanged — only the button/endpoint gate that `0.4.5` turned informational
  is being restored.
- **Settings' "Allow finalizing with critical findings" now waives every readiness
  requirement above, not just the critical-findings one.** With it on, Finalized
  Clarification is clickable even before clarification has ever been run — its own
  evaluate/answer/apply loop establishes and then resolves the finding set unsupervised,
  so there's nothing left to check up front.
- **Removed the "Answer Findings" button from the Home and Clarification Overview
  pages.** It only ever jumped into the first unanswered file; both pages already let
  you click the specific file you want to answer (Home's per-file list in step 2, and
  the Unanswered/Fully answered table rows on the Clarification Overview page).

### Fixed

- **The Clarification page's log filenames are now clickable**, opening the same large log
  viewer modal the Implementation page's Log tab already had. The linkifier only recognized
  `session_`/`qa_`/`process_` filenames, so the clarify loop's own
  `clarification_*`/`apply_clarification_*` logs (and `verify_*`/`plan_epics_*`) rendered as
  plain text. It now keys off the `_<YYYYMMDD>_<HHMMSS>.txt` suffix every Tempa log filename
  ends with, so any future log prefix linkifies too.
- **"Follow the recommendation" no longer duplicates the recommendation text into the saved
  answer**, and reopening a clarification file now shows it as "Follow the recommendation"
  selected again instead of falling back to "own answer" with the (duplicated) text
  prefilled. The choice is now recorded via a `mode="recommendation"` marker with an empty
  body instead of a copy of the recommendation text — cutting the on-disk/prompt token cost
  of every followed recommendation roughly in half. Applies to both the dashboard's per-item
  save and the CLI/finalize backlog's mechanical auto-fill. Forward-only: files saved before
  this change keep rendering as "own answer", same as before.

## [0.5.8] - 2026-08-09

### Added

- **The Implementation Status tab now shows a spinning "QA running" indicator** on an
  epic's card while its QA session is actively in progress (`qa_status === "ongoing"`),
  instead of showing nothing (or a stale "QA --") until the session finishes. The
  dashboard also keeps polling for updates while any epic is running or being QA'd, even
  if that session wasn't started from this dashboard instance.

### Changed

- **Settings → Email alerts' "From" field now defaults to `tempa-noreply@tempa-ai.com`**
  for freshly created workspaces, instead of an empty field.

### Fixed

- **Epics showed as "QA ok" while their features were still stuck on `require_fixing`** —
  and with a `completed_features` count (often `0/N`) that contradicted the QA verdict, in
  `tempa status` and on the dashboard alike. A *failing* QA round rewrites every affected
  feature to `require_fixing` and recalculates `completed_features`; the QA prompt's *pass*
  branch only ever wrote `qa_passed`/`qa_status`, so cleaning that up again was left entirely
  to the following fix round's per-feature bookkeeping — and when that round marked only the
  epic itself `done` (a routine agent slip), nothing ever corrected the feature statuses,
  even after QA re-ran and passed. The pass branch of both QA prompts now marks every feature
  `done` and sets `completed_features` to the total, and `check_and_run` reconciles it
  deterministically on every poll and right after each QA session — which also repairs
  configs already left in that state by earlier versions. Guarded to `done` +
  `qa_passed: true` + `qa_status: done` epics only, so a failed or in-flight QA round's
  `require_fixing` statuses are never overwritten.
- **Scrolling the Implementation page's Status tab dragged the whole page with it** — the
  Start/Continue and Stop Implementation buttons, the "Implementation readiness" card and the
  Status/Log tab bar all scrolled out of view, and the epic cards ended up visually outside the
  tab they belong to. The page is now a fixed-height column whose header stays pinned, with the
  active tab panel as the scroll container (the same recipe the log-file viewer modal and the
  sidebar already use); the Log tab, which already scrolled internally, is unchanged.
- **The Implementation Status tab could not be scrolled at all while a run was active** — the
  1-second poll rebuilt every epic card from scratch, and emptying the container collapsed its
  height, so the browser clamped the scroll position back to the top on every tick. The panel's
  scroll position is now preserved across a re-render, the same way both Log panels already
  keep their position.
- **Two v0.5.6 changes shipped with no documentation of their own** — only CHANGELOG entries.
  `docs/logging.md` gains a "Viewing a log file in the dashboard" section explaining the
  Clarify/Implement Log tabs' clickable filename links, the viewer modal, and the
  `GET /api/log-file` endpoint behind it (path confinement, `.txt`-only, 5MB tail cap).
  `docs/start-implementation.md` gains a paragraph on the stuck-backend-CLI watchdog (120s
  grace period after a `[Done]` signal, force-terminate, and why it isn't treated as a real
  failure for implement/QA sessions but is for `clarify`/`verify`). README gets a short
  pointer to each from the relevant step.
- **Scrolling up in the Clarify/Implement Log panel to read earlier output got yanked back
  to the bottom** on the next 1-second poll tick, because both log panels were rebuilt from
  scratch on every render and unconditionally forced `scrollTop` to the bottom. Both panels
  now only follow new content to the bottom if the user was already there (or hadn't
  scrolled yet); scrolling up now stays put while the log keeps updating underneath, and
  scrolling back to the bottom resumes auto-follow.
- **Settings → Email alerts' "Alert events" checkboxes could visually break onto two
  lines** for longer event names (e.g. "Implementation auto-reordered", "Implementation
  run limit reached") — the checkbox row's `display: flex` was being overridden by the
  higher-specificity `.settings-field label` rule, since (unlike the neighboring
  switch/radio rows) it wasn't scoped under its own group selector. Every alert-event row
  now stays a single tidy line, truncating with an ellipsis instead of wrapping if it ever
  runs out of room.
- **An implementation session that made no progress across `implement_no_progress_rounds`
  resumed sessions, but whose own feature bookkeeping showed every feature already
  `done`, was being marked `failed`** with a misleading "likely blocked on something
  outside this epic" message — even though the epic's code was genuinely complete and the
  agent correctly had nothing left to do. This happens when an epic's `done`/`qa_passed`
  state gets reverted or lost after a real QA pass (a QA-state bookkeeping desync).
  `run_session` now recognizes this case (`_epic_genuinely_complete`) and repairs the
  epic's state instead of failing it, routing it back through the normal QA gate to be
  re-QA'd on the next poll (`_repair_qa_state_desync`), with a new
  `implementation_qa_state_repaired` notification event for visibility.
- **`config.json` writes were not atomic** (`save_config` did a plain in-place
  `open(..., "w")`) — a process interruption mid-write could leave a torn/partial file, and
  contributed to the state-desync failure mode above. Writes now go to a temp file in the
  same directory followed by `os.replace()`, matching the pattern already used for the
  notification outbox.

## [0.5.7] - 2026-08-09

### Added

- **`tempa implement --reset-qa` can now target a single epic** (`--reset-qa EPIC-04`)
  instead of always forcing every `done` epic to be re-QA'd.

### Fixed

- **An epic could be marked `done` and pass QA even though its own features were still
  showing `require_fixing` and `completed_features` was still `0`** — a re-implementation
  round had set the epic-level status to `done` without going through the mandatory
  per-feature bookkeeping (mark each feature `done`, increment `completed_features`) first,
  and QA's own "everything passes" path never double-checks that bookkeeping, since it
  reasonably assumes the epic wouldn't have reached `done` otherwise. `check_and_run`'s QA
  gate now verifies a `done` epic's features actually back up that status before running QA
  on it — if they don't, the epic is routed back to `require_fixing` to genuinely finish the
  remaining work instead. `--reset-qa` runs the same check, so forcing a re-check on an
  epic with this problem doesn't just immediately re-trigger QA against the same incomplete
  state.

## [0.5.6] - 2026-08-09

### Added

- **Session/QA log filenames in the Log tab are now clickable.** Every "log: `<filename>`"
  reference the Clarify/Implement Log tabs already print (from each session's own startup
  banner line) renders as a link; clicking it opens the file's content in a modal — large by
  default and toggleable to fullscreen, since a session log can run past 400KB — instead of
  needing to go find the file on disk. Served by a new `GET /api/log-file?name=<filename>`
  endpoint, confined to `.tempa/logs/` (the same path-traversal guard already used for spec/
  clarification files) and capped at 5MB (keeping the tail, the most recently written and
  most diagnostically relevant part) for the rare pathologically large file.

### Fixed

- **A session could sit "Running..." forever even after the backend CLI had fully finished its
  work**, this time because the CLI process itself never exited — not a pipe/handle issue like
  the v0.5.2/v0.5.3 hangs. Seen live: a QA session completed (report written, config.json
  already updated) and then, as its very last action, tried to stop a background test process
  it had spawned; that cleanup command was rejected by the CLI's own sandbox policy, and the
  process itself never returned, leaving an otherwise fully finished session stuck for 17+
  minutes. Tempa now runs a watchdog once a backend signals its turn is complete: if the
  process hasn't exited 2 minutes later, it's force-terminated instead of waited on
  indefinitely. This isn't treated as a real failure — the epic/QA state already reflects
  everything that session actually did — so `tempa implement` logs specifically what happened
  and resumes automatically, the same way it already does for a transient backend overload.

- **The cross-epic no-forward-progress guard and automatic reorder added in v0.5.4/v0.5.5 had
  no documentation of its own** — only CHANGELOG entries and a passing mention in the Recovery
  section. `docs/start-implementation.md` gains a "Cross-Epic Dependencies (No-Forward-Progress
  Guard)" section explaining stall detection, the `blocked_by_epic` reporting convention, the
  automatic reorder and the safety conditions that refuse it (unknown epic, already done,
  self-reference, circular reversal), and how the circular case is deliberately left for a
  human decision instead of an automatic restructuring attempt. README's implementation step
  gets a short summary pointing there.

## [0.5.5] - 2026-08-09

### Fixed

- **An epic that hit `max_session_run` was left stuck in `on_progress` forever with no
  self-service way out** — `--reset-failed` only resets epics already marked `failed`, so
  neither it nor another click of Continue Implementation could do anything once the limit
  was reached. It's now marked `failed` (like any other implementation stop), so the existing,
  already-familiar reset flow actually works. `--reset-failed` also now clears the epic's
  run/stall counters (`total_run`, `qa_total_run`, `no_progress_rounds`, `blocked_reason`,
  `blocked_by_epic`), not just its status — without that, a "reset" epic would immediately
  re-trip the exact same limit on its very next attempt, making the reset look like it worked
  while actually being a dead end.
- A long-running epic that legitimately needs many resumes across its natural lifecycle (many
  `features_per_session` batches, several QA fix-rounds) could accumulate toward
  `max_session_run` for reasons that have nothing to do with being stuck, and eventually hit it
  despite never having actually stalled. `total_run` now resets to 0 on every round that
  completes another feature, mirroring `no_progress_rounds` — so the (much lower,
  `implement_no_progress_rounds`) no-forward-progress guard from `v0.5.4` always gets the first
  chance to catch a genuine stall, instead of the blunt lifetime cap occasionally beating it to
  the punch and leaving no automatic recovery to fall back on.

## [0.5.4] - 2026-08-09

### Added

- **`tempa implement` now detects and auto-recovers from an epic blocked on a not-yet-implemented
  dependency owned by a different epic**, instead of silently re-resuming it every poll interval
  until it burns through the full `max_session_run` limit (each wasted resume can cost tens of
  millions of tokens). The backend CLI is now asked to record `"blocked_by_epic"` on the epic
  when it determines a feature genuinely can't proceed without another epic's work. After
  `implement_no_progress_rounds` (default `2`) resumed sessions in a row make no forward
  progress, Tempa automatically moves the named dependency ahead of the stuck epic in the plan
  so the scheduler works on it next — with guardrails against nonsense targets (unknown epic,
  already done, an epic naming itself) and circular reversals (each epic reporting it's blocked
  on the other). If it can't safely auto-fix it, the epic is marked `failed` with the session's
  own explanation captured as `blocked_reason` — folding in the other epic's own last-reported
  reason too when a circular reversal is what blocked the auto-fix, so a human deciding what to
  do sees both sides in one place — shown right on the dashboard's Status tab and in
  `tempa status` (not just buried in a log file), plus a new `implementation_auto_reordered`
  email alert event for the auto-fixed case.

## [0.5.3] - 2026-08-09

### Fixed

- The dashboard's own "Start Implementation"/"Start Clarification" runs could still get
  stuck reporting "Running..." forever, even after v0.5.2 fixed the same hang one layer
  down. The dashboard spawns `tempa.py implement`/`tempa.py clarify` as a child process and
  reads its console output line-by-line to drive the live log/progress panel — that read
  had the identical unbounded-wait-on-a-closed-pipe bug v0.5.2 fixed for the backend CLI
  itself, just one process layer further out, so a lingering grandchild process (e.g. a
  leftover `dotnet run` API host) could still hang it. Both dashboard-side reads now reuse
  the same bounded pipe reader.

## [0.5.2] - 2026-08-08

### Fixed

- A session could get stuck reporting "Running..." forever with a frozen row count, even
  though the backend CLI had already finished and its log already ended with `[Done]`.
  On Windows, a grandchild process the CLI spawns (a build tool, a leftover `dotnet run`
  server left listening for the rest of the session, etc.) can inherit and hold open the
  CLI's stdout pipe, so waiting on that pipe to reach EOF never completed. Tempa now stops
  waiting on the pipe shortly after the backend process itself has exited if nothing more
  is buffered, instead of blocking forever.

## [0.5.1] - 2026-08-08

### Added

- **Optional email alerts for human-attention states.** Configure Gmail, Microsoft 365, or
  custom SMTP in Dashboard Settings, with credentials saved locally in the workspace or
  supplied through environment variables. Select which events should notify you and verify
  delivery from the dashboard or with `tempa notifications test`. Alerts cover authentication
  failures, permanent workflow failures, safety limits, unanswered critical/major clarification
  findings, verification failures, and confirmation waits, while automatic recovery paths stay
  silent. Events are deduplicated in a durable per-workspace outbox and failed deliveries are
  retried by a later Tempa run.

### Fixed

- Codex CLI sessions no longer report a false authentication failure when successful
  command output contains authentication-related application text such as an OpenAPI
  `#/components/responses/Unauthorized` reference. Failure markers are now evaluated only
  for plain diagnostic output and structured backend failure events.

- **A finding resolved during `clarify --finalize` stayed permanently "Unanswered" in the
  dashboard, even after finalize succeeded.** When apply had no recorded "Your answer" for a
  finding, it fell back to that finding's own Recommendation to resolve the PRD/spec — but
  never wrote that fallback back into the clarification file itself, so the file kept
  showing 0 answered forever, regardless of the PRD already being fixed and finalize having
  reached 0 critical/0 major. `_run_apply_step` now mechanically fills any still-empty
  "Your answer" with its own Recommendation (no agent call — same as the pre-loop backlog
  resolution step) right before applying, so the clarification file ends up recording
  exactly what was applied.

## [0.5.0] - 2026-08-07

### Added

- **A separate `clarify_apply` stage for clarify's apply/auto-answer work**, configurable
  the same way as `clarify`/`plan`/`implement` — its own AI model (`tempa set-model
  --clarify-apply <model>`, default `claude-sonnet-5`), CLI backend (`tempa set-backend
  --clarify-apply <backend>`), and reasoning effort (`tempa set-effort --clarify-apply
  <level>`); new "Clarify — Apply / Auto-Answer" field in dashboard Settings with its own
  backend/model/effort controls. Drives `clarify --apply`, `clarify --auto-answer`, and the
  apply half of `--finalize` — mechanical work compared to evaluating the PRD (`clarify`,
  still `claude-opus-5` by default), so it no longer has to run on the same backend/model/
  effort as evaluate.
- **`resume_implementation_sessions` and `finalize_no_progress_rounds` config options.** See
  "Changed" below for what they control; both are on by default and don't require any action.
- **Configurable retry/poll timing in Settings.** The wait before automatically retrying
  after a usage limit or server overload, the usage-limit heartbeat log interval, and the
  `tempa implement` scheduler's poll interval — previously hardcoded (30 min / 5 min / 5 min /
  60s) — are now stored in `config.json` (`usage_limit_retry_wait_sec`,
  `usage_limit_heartbeat_sec`, `server_overloaded_retry_wait_sec`, `poll_interval_sec`) and
  editable from a new "Retries & Timing" card in dashboard Settings, above "Updates". Changes
  take effect on an already-running session or agent runner on its next wait/poll check — no
  restart needed.

### Changed

- **Clarify and implement sessions no longer re-pay to re-read context they already have.**
  Several changes that together cut token usage substantially on longer-running
  clarification and implementation, with no reduction in what gets evaluated or checked:
  - `clarify --apply` (and the apply half of `--finalize`) now reads only the clarification
    files that actually still need applying (the "apply backlog", tracked via
    `clarify_applied_hashes`) instead of every clarification file ever written for the
    workspace — previously O(N²) in the number of past rounds. `--auto-answer` likewise only
    reads files that still have an unanswered finding.
  - `clarify --finalize`'s apply step now resumes (`--resume`) the evaluate session that just
    wrote the findings it's applying, instead of starting a fresh session that re-reads the
    whole PRD from scratch. A usage-limit/overload retry mid-apply resumes that same partial
    apply attempt rather than losing it and starting over.
  - `tempa implement` now actually uses `--resume` for continuation/require_fixing epic
    sessions (the session id was already being captured and stored, but never passed back
    in) — new sessions no longer re-read the epic's specification file from scratch every
    `features_per_session` batch. New `resume_implementation_sessions` config option (default
    `true`) to disable if resuming ever misbehaves for a given backend/workspace.
  - `clarify --finalize` now stops on its own (`finalize_no_progress_rounds`, default `2`) if
    apply fails to reduce the critical+major finding count for that many rounds in a row,
    instead of continuing to burn full-PRD re-evaluation rounds up to `max_clarification_run`
    — those findings likely need a human decision instead.
- **Dashboard icons are now inline Lucide SVGs instead of emoji.** Every button, sidebar entry,
  status marker, severity dot, and log-line icon in the dashboard (`dashboard.html`/`.js`/`.css`)
  previously used raw emoji characters, which render inconsistently across platforms/fonts. They're
  now a small inline `<symbol>` sprite (added once in `dashboard.html`) referenced via
  `<svg><use></svg>`, with color applied through CSS (`--critical`/`--major`/`--minor`/`--ok`/`--danger`)
  instead of baked into the glyph. A new `iconSvg()` helper in `dashboard.js` renders icons built
  dynamically (severity dots, epic/feature status, clarification log lines, checklists). Out of
  scope: CLI terminal output (`tempa_*.py`) and one `<option>` element's text, since neither can
  render SVG.

## [0.4.10] - 2026-08-06

### Added

- **A "Stop Finalize" button.** "Finalized Clarification" now swaps to "Stop Finalize" for as
  long as that run is in progress (the same Start/Stop toggle "Start Implementation" already
  has), and clicking it — after a confirm — kills the running `clarify --finalize` subprocess
  (`taskkill /T /F` on Windows, so its backend CLI child dies with it, same as Stop
  Implementation). New `_stop_clarify_run` in `dashboard_runs.py` and `POST /api/clarify/stop`
  in `dashboard_server.py`; only mode `"finalize"` can be stopped this way.

- **Saving a new "Max Finalize Clarification Round" while a Finalized Clarification run is in progress
  now warns that the running loop won't pick it up.** `clarify --finalize` reads that setting
  once, when its process starts, and keeps counting toward it for the whole evaluate/apply
  loop — so lowering the limit mid-run leaves the log counting `ROUND 17/25` while Settings
  reads `10`, which looks like the limit isn't enforced at all. The limit is enforced; it
  just applies from the next finalize run onward (no restart needed — each run is a fresh
  process). Settings now says so in a modal right after saving, driven by a new `warning`
  field on `/api/config/save` computed server-side from the actually-running run.

### Changed

- **Settings' "Max Clarification Runs" is now "Max Finalize Clarification Round"** — the old
  name read as a cap on clarification in general, but it only ever bounds the automated
  `clarify --finalize` loop; manual `clarify` runs are unlimited. For the same reason, the
  Clarification page's "Finalize readiness" panel (and the Home page's Clarification step)
  now show just **Round N** (rounds run so far, uncapped) instead of **Round N of M**, which
  wrongly implied manual rounds counted toward that max too.

- **Finalize's round progress is now tracked separately from the all-time round count.**
  `last_clarification_round` (the "Round N" above) used to be overwritten by `--finalize`
  with its own in-run counter, so it could visibly go *backwards* the moment a finalize run
  finished its first round (e.g. drop from `5`, after 5 manual rounds, to `1`) — and then kept
  climbing with every later manual round, misrepresenting itself as finalize progress. It's
  now a true running total (`+= 1` on every evaluate pass, manual or finalize alike), while a
  new `last_clarification_round`-independent counter, `last_finalize_round`, resets to `0` at
  the start of every `--finalize` run and counts up to `max_clarification_run` within that one
  run only. That counter is what's now shown as **N / M** next to the "Finalized
  Clarification" button, live-updated every second while a run is in progress. `tempa clear`
  now resets the new `last_finalize_round` too, alongside every other tracked clarify field
  (`_reset_clarify_config_state` in `tempa_maintenance.py`).

- **The dashboard's "Start Implementation" button becomes "Continue Implementation" once
  implementation has actually started**, on all three surfaces that carry it (Home step 3,
  the Clarification ready banner, the Implementation header) — the same Start/Continue
  relabeling the clarification buttons already had. "Started" means at least one epic has
  moved off `pending` or carries a `last_run` stamp (a drafted-but-never-run plan is still a
  Start), and it's computed server-side and reported as the new `started` field of
  `/api/implement/run`, so the three buttons can't disagree.

### Fixed

- **A `failed` epic made the dashboard's Start/Continue Implementation button do nothing.**
  `implement` halts on any failed epic that precedes the next one to work on, so after a
  session failure every click just re-ran the halt: the log said to run
  `tempa implement --reset-failed`, but the dashboard had no way to do that — the only way
  forward was the CLI. The dashboard's implement run now performs that reset itself
  (`tempa implement --reset-failed`, streamed into the Log tab) before starting
  `tempa implement`. It's a no-op when nothing is failed, and the CLI's own behavior is
  unchanged — plain `tempa implement` still halts and still tells you to reset. **Stop
  Implementation** now also covers this first step: pressing it during the reset pass stops
  the run instead of letting implement start anyway, and it no longer reports "not running"
  in the brief gap between the run's two child processes.

- **A provider overload (529) could leave `implement` permanently halted on a `failed`
  epic.** When the AI provider's API reports itself overloaded, `implement` waits and retries
  — but the interrupted session can still have been marked `failed` in config.json (the
  overload is only skipped when its marker is actually recognized in the streamed output, so
  an overload in different wording, or one that kills the CLI again on the next attempt,
  looks like a plain non-zero exit). That status is sticky and blocking: `check_and_run`
  halts on any failed epic preceding the next one to work on, so both the retry and every
  later `tempa implement` run kept failing on it until it was reset by hand. The retry now
  performs that reset itself (`failed` → `pending`, the in-process equivalent of
  `tempa implement --reset-failed`) before resuming. A real session failure is unaffected —
  it still stops the runner and keeps its `failed` status. The halt message now also names
  the command to run (`tempa implement --reset-failed`).
- **Documentation that no longer matched the code.** README's Clarification step still
  described **Finalized Clarification** as staying disabled "until a Finalize readiness
  panel shows all 3 conditions met" — that gate was removed on both sides (the button is
  only disabled while a clarify run is in progress, and `_handle_clarify_run_start` has no
  server-side precondition for mode `finalize`), and the panel is informational with 4
  items. Rewritten as a readiness panel that tells you how much Finalize will do
  unsupervised, including the `allow_finalize_with_critical` override and the automatic
  backlog resolution. README's Start Implementation step likewise still asserted a fixed
  "no critical or major findings" requirement, predating
  `implementation_start_requirement`; it now describes the default plus the `no_critical` /
  `none` levels, the always-applies "clarification has run at least once" condition, and
  that the requirement is enforced server-side.
- **`docs/config-json.md` claimed to document every `config.json` key but was missing two**
  that the readiness gates and the Clarification overview depend on:
  `last_clarification_action` and `clarify_applied_hashes`. Both are now documented, along
  with the default values of `features_per_session` / `max_session_run` /
  `max_clarification_run` and the dashboard Settings field each maps to.
- **Stale internal docstrings** in `dashboard_clarify_parse._clarify_finalize_status` (still
  described itself as a gate, and claimed Start Implementation "always requires zero
  critical and zero major findings regardless of this setting") and in
  `dashboard_runs._start_clarify_run` (same removed-gate framing).

### Added

- README (CLI → Step 4) and `docs/start-implementation.md` now document the 529-overload
  pause/retry behavior, including the automatic `failed` → `pending` reset that happens
  before the retry resumes.

### Changed

- `CLAUDE.md`: documented the branch-naming convention (`feat/*`, `fix/*`, `docs/*`,
  `refactor/*`).

## [0.4.9] - 2026-08-05

### Fixed

- **A transient "529 Overloaded" (or similar server-side-overload) response from the
  configured backend's API made `clarify`/`implement`/`verify` mark the in-progress
  epic/session as failed and stop the agent runner entirely**, even though nothing was
  actually broken — the CLI's raw text (e.g. Claude Code's `API Error: 529 Overloaded.
  This is a server-side issue, usually temporary — try again in a moment.`) didn't match
  any known usage-limit or auth-error marker, so it fell through to a plain failure. Added
  a third failure category alongside usage-limit and auth-error detection
  (`overloaded_markers` on `Backend`, `_state.server_overloaded_hit`): an overload stop now
  pauses for 5 minutes (`SERVER_OVERLOADED_RETRY_WAIT_SEC`) and retries automatically,
  leaving the epic/session resumable exactly like a usage-limit stop does, instead of
  marking it failed.

## [0.4.8] - 2026-08-05

### Fixed

- **A clarification round that finds zero findings could leave "Finalized Clarification"
  and "Start Implementation" stuck blocked forever**, even though the PRD was genuinely
  clean. The finalize/implement readiness gates read the most recently *started*
  clarification file's own finding tags (deliberately, so a resolved critical finding can't
  keep counting forever — see `_latest_evaluation_findings`) — but a fresh evaluate pass
  that finds nothing leaves no new file behind to read (there's nothing to write per
  `prompt/clarification.md`), so the gate kept reading whichever older file still had
  findings in it. Added `config.json`'s `last_clean_evaluation_at`, stamped whenever a
  fresh evaluate pass reports zero critical/major/minor findings
  (`_stamp_clean_evaluation_if_zero` in `tempa_clarify.py`); the readiness gate now treats
  a clean stamp newer than the latest finding-bearing file as the current, authoritative
  state.
- **Claude Code's "weekly limit" message wasn't recognized as a usage-limit stop**, so
  hitting it made `clarify`/`implement`/`verify` exit with a plain error instead of waiting
  30 minutes and retrying (the behavior added in 0.4.6). The CLI's actual wording — "You've
  hit your weekly limit · resets ..." — didn't match any of `CLAUDE.usage_limit_markers`,
  which only covered the 5-hour/session limit phrasings. Added `"hit your weekly limit"`
  and `"weekly limit reached"` markers.

## [0.4.7] - 2026-08-05

### Added

- **`clarify`/`clarify --finalize` can now skip minor findings entirely** via a new
  `--skip-minor` CLI flag, or persistently through `config.json`'s `skip_minor_findings`
  (default `true`). A new "Evaluation scope" card on the dashboard's Clarification page —
  above "Finalize readiness" — exposes this as an "Only evaluate critical & major findings"
  switch that persists immediately (`POST /api/clarify/skip-minor`) and is honored by any
  dashboard-triggered clarify/finalize run automatically.

## [0.4.6] - 2026-08-04

### Added

- **A backend usage limit no longer stops `clarify`/`implement`/`verify` — they wait 30
  minutes and retry the interrupted step automatically** — previously, whenever the
  configured CLI backend's usage/session limit was hit, these commands exited immediately
  (exit code 2), leaving the user to notice and re-run the command by hand once the limit
  reset. Every entry point that can hit this (`clarify`, `clarify --auto-answer`,
  `clarify --apply`, `clarify --finalize`'s evaluate/apply loop, `implement`'s plan step and
  its per-epic/QA poll loop, and `verify`) now treats it as a pause instead: it logs that
  it's waiting (explicitly "this is not an error"), a periodic heartbeat during the wait, then
  automatically retries — repeating for as long as the limit is still in effect. Nothing is
  lost across the wait, since the retried step resumes from what's already recorded in the
  clarification files and `config.json` (epic/feature/QA progress left exactly as it was),
  instead of starting over. This is core CLI behavior (`tempa_session.run_with_usage_limit_retry`),
  so the dashboard's background clarify/implement runs get it for free too — they just spawn
  `tempa.py clarify`/`tempa.py implement` as a subprocess and stream its console output, and
  **Stop Implementation**'s existing kill-the-process-tree already cancels a paused wait the
  same way it cancels a running session. Authentication failures (exit 3) are unaffected —
  waiting can't fix a bad credential, so those still stop immediately.

## [0.4.5] - 2026-08-04

### Added

- **`clarify --finalize` now resolves any pre-existing clarification backlog before its
  loop starts** — previously, running Finalize while some clarification files still had
  unanswered findings or fully-answered-but-unapplied files left that backlog untouched
  (the dashboard's Finalize button was in fact gated closed in exactly this situation,
  requiring a fresh zero-critical evaluate on record first). Finalize now checks
  `sources.clarifications` up front: files with every finding answered but not yet
  applied get applied, and files with unanswered findings get each one filled in with
  its own Recommendation text (mechanically, no agent call — the same resolution the
  dashboard's "Follow the recommendation" button would save) and then applied too — all
  in a single apply pass, since apply always processes the whole clarifications folder
  regardless. Only once that's done does the usual evaluate/apply loop start. The
  dashboard's "Finalized Clarification" button (Home and Clarification Overview) is no
  longer disabled by the old precondition either — its readiness checklist is now
  informational rather than blocking.

## [0.4.4] - 2026-08-03

### Fixed

- **Fixed two stale tests in `test_dashboard_clarify_parse.py`** left over from the 0.4.3
  finalize/implement-readiness fix that renamed `_live_clarification_findings` (summed
  findings across every clarification file ever produced) to `_latest_evaluation_findings`
  (only the most-recently-started round) — the tests still called the old name and had
  been failing on every run since.
- **"Apply Answers" now applies every ready clarification file before evaluating again** —
  previously, whenever more than one fully-answered file was still waiting to be applied,
  the dashboard chained straight into a fresh Start Clarification (evaluate) run after
  the first successful apply, stranding the rest of the backlog until the user came back
  and clicked Apply again for each remaining file. It now keeps re-running `clarify
  --apply` while any ready file is still unapplied, and only starts a fresh evaluate once
  every one of them has been applied.

## [0.4.3] - 2026-08-03

### Fixed

- **Finalize/implement readiness now checks only the most recent evaluation round** —
  previously the "Most recent evaluation still shows N critical finding(s)" check (and the
  Start Implementation gate) summed critical/major/minor tags across every clarification
  file ever produced, including past rounds kept as historical record with their findings
  already resolved. Since those tags are never removed from old files, the count could
  never return to 0 once a single critical finding had ever appeared in any round. It now
  reads only the most-recently-started evaluation round's file.
- **Higher-contrast critical/major/minor severity colors** — the old major badge color
  (`#d97706`) failed WCAG AA contrast against its white badge text, and the old minor color
  was identical to the app's own accent blue, so a minor-severity badge/border was
  indistinguishable from an ordinary link or button. Now `#b91c1c` / `#c2410c` / `#a16207`.

### Added

- **"Started" column on the Clarification overview's Unanswered/Fully answered tables** —
  shows when each file's evaluation round began (`dd/MM HH:mm`, parsed from the
  `clarification-YYYYMMDD-HHMMSS.md` filename, falling back to file mtime), with both
  tables now sorted most-recent-first instead of alphabetically.
- **Merged Critical/Major/Minor columns into a single "Findings" column** with one icon per
  severity present (🔴/🟠/🟡), each carrying a native hover tooltip (e.g. "Critical: 2/2") —
  frees up table width for the new Started column.
- **Clicking a Clarification overview row now opens a detail dialog** (started time,
  per-severity findings, status, and — new — how long that file's evaluation and its most
  recent apply pass took) instead of jumping straight into the answer-editing view; an
  "Open File" button in the dialog still does that.
- **Configurable "Start Implementation requires" setting** — previously Start
  Implementation always required zero critical *and* zero major findings in the most
  recent clarification evaluation, with no way to relax it. A new Settings control lets
  you choose "No critical or major findings" (the previous, still-default behavior), "No
  critical findings" (major findings may remain open), or "No condition" (clarification
  must have run at least once, but its findings never block). The two relaxed options
  show an inline risk warning and a confirmation prompt before they can be selected. The
  gate is enforced both client-side (Home, Clarification, and Implementation pages) and
  server-side (`_handle_implement_run_start`), all driven by the same
  `implementation_start_requirement` config.json setting.

## [0.4.2] - 2026-08-02

### Changed

- **Documented the Settings page's Updates card** — new [docs/updates.md](docs/updates.md)
  reference page, a summary section in README.md's Dashboard walkthrough, and cross-references
  from README's Further Reference list and `docs/command-reference.md`'s `check-update`/`update`
  rows. Covers where the check/update UI lives, how it works, and why the Tempa process needs
  restarting afterward (a page reload alone doesn't pick up the new code).

## [0.4.1] - 2026-08-02

### Added

- **Updates card in the Settings page** — check the installed Tempa version against the
  latest GitHub release and apply the update directly from the dashboard (mirrors
  `tempa check-update` / `tempa update --yes`), without needing a terminal. After a
  successful update the dashboard tells the user to restart the Tempa application, since
  the running process keeps its old code loaded in memory until then — reloading the page
  alone does not pick up the new version.

## [0.4.0] - 2026-08-02

### Added

- **`tempa version`** — shows the locally installed Tempa version (read from a `VERSION`
  file at the install root, bumped as part of cutting each release).
- **`tempa check-update`** — checks GitHub for the latest published release and compares it
  against the installed version, printing a download link if an update is available (fails
  gracefully with a manual-check link if GitHub can't be reached).
- **`tempa update [--yes]`** — if a newer release exists, downloads it and applies it on top
  of the current install after an interactive confirmation (current → latest version shown
  first; `--yes` skips the prompt). Only overwrites files actually shipped in the release
  archive, so local-only files/folders (`.tempa/`, `.active-workspace`, etc.) are untouched.
- **CLI backend readiness** — the Home page's workspace info area and the Settings page's
  AI Backend & Model section now show, per CLI backend (Claude Code, GitHub Copilot CLI,
  OpenAI Codex CLI), whether it's ready to use for the active workspace: installed/on PATH
  and able to write to the workspace folder. Not-ready backends are also flagged inline in
  each stage's Backend dropdown. Settings has a **Detect CLI Backends** button to re-check
  on demand (e.g. right after installing or logging into a CLI) without reloading the form.

## [0.3.0] - 2026-08-02

### Added

- **Multi-CLI backend support** — each pipeline stage (Clarification, Planning,
  Implementation) can now be driven by GitHub Copilot CLI or OpenAI Codex CLI, not just
  Claude Code. Configurable per stage from the dashboard's Settings page (a Backend
  dropdown next to each stage's model field) or from the CLI (`tempa set-backend`/
  `show-backends`). Switching a stage's backend mid-epic starts its next session fresh
  instead of trying to resume a session under a different CLI.
- **Reasoning Effort** setting per stage, next to the model field, validated against the
  selected backend/model before it's ever sent to the CLI — per-model for OpenAI Codex CLI
  (whose supported levels vary by model), per-backend for Claude Code/GitHub Copilot CLI.
  Configurable via the dashboard or `tempa set-effort`/`show-efforts`.

## [0.2.0] - 2026-08-01

### Added

- Settings page toggle, **Allow finalizing with critical findings** (off by default).
  Finalized Clarification normally refuses to start while any critical finding is still
  open; enabling this lets its automated evaluate/apply loop attempt to resolve them
  unsupervised instead, with an explanation of the consequences shown when turning it on
  and a persistent warning while it's active. The Home and Clarification pages' Finalize
  buttons follow this setting; Start Implementation's separate zero-critical/zero-major
  gate is unaffected.
- Dashboard's Clarification page shows **Round N of M** (current clarification round vs.
  `max_clarification_run`) next to the Finalize readiness panel.

### Fixed

- Clarification result files are no longer overwritten or deleted by later evaluation/apply
  passes — each `clarify` evaluation now writes a new, uniquely-named file, and `clarify
  --apply` never touches the clarification files themselves. Previously answered rounds now
  stay visible in the dashboard's "Fully answered" list instead of disappearing after a
  later round or apply step.

## [0.1.0] - 2026-07-30

Initial tagged release.

### Added

- Initial Tempa harness: a Claude-driven CLI runner for PRD clarification, planning,
  and implementation.
- Cross-platform `tempa` / `tempa.cmd` launcher scripts, plus double-click
  `Open Dashboard` launchers for Windows/macOS/Linux.
- Unified web **dashboard** (`tempa dashboard`), replacing the earlier separate
  spec/clarify UIs — Home workflow page, live Implementation run controls and log,
  Clarification run controls (Start/Finalize/Apply) with live log, a Specification
  file browser (add/rename/delete, view/edit), and a Settings page for AI models and
  run limits.
- `spec --show`: an Explorer-style web UI to browse and edit spec files.
- Clarification answer web UI (`tempa answer`), including scanning for every
  unanswered clarification file at once (tabbed UI).
- Auto-apply of clarification answers back into the PRD right after saving in the UI,
  with a prompt to run another clarification round afterward.
- **Apply Answers** button on the dashboard Home page's Clarification step, and an
  **Answer Findings** shortcut that jumps straight to the first unanswered file.
- Auto-continuation of the clarify → answer → apply loop after Apply, with a clearer
  "not ready" state on Home while critical/major findings remain.
- Native macOS folder picker and Finder open/focus for workspace init.
- Per-workspace `.tempa/` folder holding config/logs/qa/verify/specs, so Tempa's own
  install folder stays clean across projects.
- Auto-creation of `config.json` with sane defaults if missing.
- **Architecture Principles** — project-wide rules injected into every Claude prompt
  (clarify, plan, implement, QA, verify), editable from the dashboard.
- Detection of `claude` CLI authentication failures, with a plain-language explanation
  and fix instructions instead of a raw error.
- `pytest` unit test suite (`tests/`) covering the pure-logic modules, with 100%
  coverage on those modules, and a GitHub Actions CI workflow running it on every
  push/PR to `main`.
- `ruff` linting, enforced as a separate CI job.
- CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, and GitHub issue/PR templates.

### Changed

- CLI moved to `argparse` subcommands; session runners deduplicated and consolidated
  under shared runner state.
- Config structure: `sources.*` paths are now derived from `workspace.*` instead of
  being duplicated in `config.json`.
- Codebase refactored into cohesive modules under `src/` (`tempa_*.py` for the CLI,
  `dashboard_*.py` for the dashboard).
- Default workspace app folder changed from `apps/` to `src/`.
- README rewritten with a problem/solution-framed introduction, a Quick Start section,
  and a table of contents.
- Home page's Clarification step now mirrors the dedicated Clarification page's
  Continue/Answer Findings behavior.
- Incomplete-answer warnings now use the dashboard's styled modal instead of a native
  `alert()`.
- **Start Implementation** now requires clarification to have run at least once,
  instead of being reachable before any clarification pass.

### Fixed

- `TypeError` when creating `.gitignore` during `init`.
- `TypeError` crashing the `/api/clarify/run` finalize gate.
- Top-level dashboard toolbar not hiding correctly; stray `+` icons on Add File/Folder
  buttons.
- `spec --show` opening the whole `specs` folder instead of `specs/prd`.
- Dashboard Implementation log tab not using the full panel height / live progress not
  visible.
- Crashes from non-UTF-8 Windows console codepages when printing Unicode log output.
- A path-traversal test (`_resolve_within`) that only reflected Windows path semantics,
  failing on non-Windows CI runners.

[Unreleased]: https://github.com/daus95/tempa/compare/v0.6.8...HEAD
[0.6.8]: https://github.com/daus95/tempa/compare/v0.6.7...v0.6.8
[0.6.7]: https://github.com/daus95/tempa/compare/v0.6.6...v0.6.7
[0.6.6]: https://github.com/daus95/tempa/compare/v0.6.5...v0.6.6
[0.6.5]: https://github.com/daus95/tempa/compare/v0.6.4...v0.6.5
[0.6.4]: https://github.com/daus95/tempa/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/daus95/tempa/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/daus95/tempa/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/daus95/tempa/compare/v0.6.0...v0.6.1
[0.5.1]: https://github.com/daus95/tempa/compare/v0.5.0...v0.5.1
[0.4.4]: https://github.com/daus95/tempa/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/daus95/tempa/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/daus95/tempa/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/daus95/tempa/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/daus95/tempa/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/daus95/tempa/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/daus95/tempa/compare/v.0.1.0...v0.2.0
[0.1.0]: https://github.com/daus95/tempa/releases/tag/v.0.1.0
