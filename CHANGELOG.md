# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once the first tagged release is cut.

## [Unreleased]

### Added

- **Settings → Run Limits now has a "Max Finalize No-Progress Round" field**, right below
  "Max Finalize Clarification Round". It configures `finalize_no_progress_rounds` — how many
  Finalized Clarification rounds in a row may fail to reduce the critical+major finding count
  before the loop gives up early and asks for human answers. Previously this was a
  config.json-only setting hardcoded to default `2`, which stopped finalization far too soon.

### Changed

- **`finalize_no_progress_rounds` now defaults to `5`** (was `2`), giving Finalized
  Clarification more attempts before it concludes the remaining findings need a human
  decision. Existing config.json files keep whatever value they already have.
- **The "Finalized Clarification Is Already Running" save warning now covers both finalize
  run limits.** Like "Max Finalize Clarification Round", the new no-progress limit is read
  once when the finalize run starts, so changing either one mid-run now says so and names
  each changed setting.

### Fixed

- **The Clarification page's log filenames are now clickable**, opening the same large log
  viewer modal the Implementation page's Log tab already had. The linkifier only recognized
  `session_`/`qa_`/`process_` filenames, so the clarify loop's own
  `clarification_*`/`apply_clarification_*` logs (and `verify_*`/`plan_epics_*`) rendered as
  plain text. It now keys off the `_<YYYYMMDD>_<HHMMSS>.txt` suffix every Tempa log filename
  ends with, so any future log prefix linkifies too.

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

[Unreleased]: https://github.com/daus95/tempa/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/daus95/tempa/compare/v0.5.0...v0.5.1
[0.4.4]: https://github.com/daus95/tempa/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/daus95/tempa/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/daus95/tempa/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/daus95/tempa/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/daus95/tempa/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/daus95/tempa/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/daus95/tempa/compare/v.0.1.0...v0.2.0
[0.1.0]: https://github.com/daus95/tempa/releases/tag/v.0.1.0
