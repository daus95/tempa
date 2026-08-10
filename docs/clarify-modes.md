# Clarification Mode Reference (`clarify`)

Full reference for every `clarify` mode: manual (default), `--auto-answer`, `--apply`, and
`--finalize`. See [README.md](../README.md) — Step 2 (Answer Clarifications) — for the
recommended iterative flow.

## Manual mode (default) — `tempa clarify`

```bash
tempa clarify
```

Runs **one evaluation pass**, then stops:

1. The agent (whichever CLI backend is configured for `clarify` — see [ai-models.md](ai-models.md))
   examines the **entire PRD document** in `sources.prd` (the PRD can span multiple
   files) for conflicts/ambiguity, writes the findings **along with recommended answers** to
   `sources.clarifications`, and fills in `last_clarification_findings` (`critical`, `major`,
   `minor`) in config.json.
2. The console shows the **finding count per category** and the **path to the result file**
   (so it's easy to open).
3. A suggested next step, based on severity:
   - `critical == 0` **and** `major == 0` → clarification is considered done; recommends
     moving on to `implement` (plan drafting will run automatically first).
   - `critical == 0` (still some `major`) → recommends answering manually, **or** finishing
     automatically with `clarify --finalize`.
   - `critical > 0` → recommends reviewing & answering manually in the result file, then
     running `clarify` again.
4. Unless `--noui` is passed, opens the **clarification-answer web UI** (see below) on the
   freshly written result file, so you can answer right there instead of hand-editing the
   markdown. The command blocks until you save or cancel in the browser (or press Ctrl+C).
   Saving records your answers in the clarification file; it does **not** rewrite the PRD.
   Those answers are carried into the next clarification round automatically — see
   **Pending resolutions overlay** below — so you can keep clarifying without applying.

```bash
tempa clarify --noui   # skip opening the web UI; just write the file and print the summary
```

```bash
tempa clarify --skip-minor   # evaluate for critical/major only — minor findings aren't looked for at all
```

`--skip-minor` (combinable with `--noui`/`--finalize`) tells the evaluation pass to skip
minor findings entirely instead of finding and reporting them — since minor findings never
block `--finalize` or Start Implementation anyway, this just saves the evaluation effort of
producing them. When the flag isn't passed, config.json's `skip_minor_findings` (the
dashboard Clarification page's "Only evaluate critical & major findings" switch, default
`true`) is used instead.

You can also open the result file in a text editor and **answer the clarifications
manually** (editing the PRD/spec per your decisions), then run `clarify --apply` to apply
those answers into the PRD/spec, or call `clarify` again to re-evaluate for the next
iteration.

## Pending resolutions overlay

Answering a finding does not rewrite the PRD. The answer sits in the clarification file until
you run an apply step — and in the meantime it is **carried into every clarification
evaluation** as an already-decided resolution, alongside the PRD itself. The agent is told to
treat that block as authoritative: wherever a decision contradicts what the PRD currently
says, the decision wins and the PRD text is simply stale.

That's what makes it unnecessary to apply between rounds. Without it, every round of
questions would have to be followed by a full agent session rewriting the PRD just so the
next round wouldn't re-raise points you had already settled.

The rules the evaluation prompt states about the overlay:

- **Already decided.** Points the overlay settles are closed — not re-raised as findings, not
  re-asked, even though the PRD still reads the old way.
- **Last one wins.** Rounds are supplied oldest first; a later round's decision supersedes an
  earlier one covering the same point, completely.
- **Contradiction is a finding.** If two decisions in the *same* round contradict each other,
  or a decision would break a still-valid part of the spec it doesn't itself resolve, that is
  reported as a new finding. This is the one case where the overlay produces findings instead
  of suppressing them.
- **Evaluate as if applied.** The spec is judged as it will read once every decision has been
  written in — no findings that only exist because the overlay hasn't been applied yet.

What's pending is derived from `clarify_applied_hashes` (see [config-json.md](config-json.md)):
an answered finding is pending exactly when its file's current content doesn't match what was
last applied. So editing an answer after an apply puts it back into the overlay, and applying
empties it.

The dashboard's Clarification page shows a **Pending resolutions** card with the count and
approximate size. Past `clarify_overlay_warn_findings` (default `25`) it also suggests
applying — carrying a large overlay is legitimate, it just makes every evaluation prompt
bigger. Nothing is ever applied automatically.

Two things still require the overlay to be empty, because they read the PRD documents rather
than the clarification files:

- **Start Implementation** — a hard gate, enforced server-side, regardless of
  `implementation_start_requirement` (see [start-implementation.md](start-implementation.md)).
- The end of a `--finalize` run, which compacts the overlay into the PRD itself.

## Clarification-answer web UI

Opened automatically after a manual `clarify` pass (unless `--noui`), or on demand at any
time:

```bash
tempa answer
```

`answer` takes no arguments. It scans `sources.clarifications` for every clarification
result file (everything except `claude.md`):

- If every file already has an answer for every finding, it prints a message saying there's
  nothing left to answer and exits — no browser tab opens.
- Otherwise it opens the web UI on **all** of those files at once, one tab per file. Each
  tab's label is badged **✓ Complete** or **N/M answered**, so you can see at a glance which
  files still need attention while still being able to review/edit an already-complete file
  from the same page. (With only one clarification file, the tab bar is skipped and the page
  looks exactly like the single-file view always has.)

The UI parses each finding (delimited by `<!-- clarify:item ... -->` markers written by the
`clarify` prompt) and renders the file as a formatted page. For every finding you can pick:

- **Follow the recommendation** — keeps the agent's suggested resolution as your answer.
- **I'll write my own answer** — enables a text area (5+ rows) to type your own answer,
  overriding the recommendation.

Clicking **Save answers** offers three choices: **Save** (the default) writes every answer —
across every open tab/file, not just the one currently visible — back into each clarification
file's `**Your answer:**` section (between `<!-- clarify:answer-start -->` /
`<!-- clarify:answer-end -->` markers) and stops there; **Save & Clarify** does that same save
and then immediately starts the next **Continue Clarification** round (re-evaluate → report →
web UI), so you never have to make a separate trip back to the overview to kick it off;
**Cancel** closes the dialog without touching any file.

Saving alone is enough to keep working: saved answers are carried into every subsequent
clarification round as already-decided resolutions (see **Pending resolutions overlay**).
Writing them into the PRD/spec itself is a separate step — the **Apply Answers** button at the
top of the Clarification overview, or `clarify --apply` from the terminal — and is required
before starting implementation, not between every round. The server only listens on
`127.0.0.1`, for the duration of that one answer session.

## Auto-answer mode — `tempa clarify --auto-answer`

```bash
tempa clarify --auto-answer
```

Automatically answers clarification findings that are **still unanswered** (one pass,
**without** re-evaluating or looking for new findings):

1. Tempa first works out which clarification files still have at least one unanswered
   finding — files that are already fully answered are skipped entirely, so they're never
   read by the agent.
2. For each unanswered finding → adds an answer/resolution directly in the clarification
   file (based on PRD analysis), without overwriting answers that already exist.
3. If **every** finding has already been answered → no session is even started; the console
   shows a message that there's nothing left to answer.

Useful as a middle ground: let the agent answer first, then you review/correct.

## Apply mode — `tempa clarify --apply`

```bash
tempa clarify --apply
```

Writes the answers/resolutions **already recorded** in the clarification file(s) into the
PRD/spec document (`sources.prd`) — one session, **without** re-evaluating. This is the
compaction step: everything currently in the pending overlay stops being an overlay and
becomes part of the documents. The agent reads the resolutions, edits the relevant PRD/spec
documents, and updates `last_clarification_findings`. If there's no clarification result yet,
the harness asks you to run `clarify` first.

Applying is **not** required between clarification rounds — answers ride along in the overlay
until you choose to compact them. It **is** required before **Start Implementation**, which
reads the PRD documents and cannot see decisions that only exist in a clarification file.

Only the files that actually still need applying are sent to the agent — every file already
reflected in `clarify_applied_hashes` (see [config-json.md](config-json.md)) is skipped, so a
workspace with many past clarification rounds doesn't pay to re-read all of them on every
apply. They're sent oldest round first, and the prompt says so: where two rounds cover the
same point, the later one is applied and the earlier one dropped.

Difference from `--finalize`: `--apply` doesn't re-evaluate — it only applies existing
answers. `--finalize` runs the whole evaluate/answer loop and finishes with one apply.

Once the apply step succeeds, `clarify --apply` also shows the **Ask to continue** prompt
below. Started from the dashboard instead, it keeps applying — one backlog file per
subprocess — until no backlog is left and then stops — it does **not** chain into an
evaluation of its own. If a batch doesn't actually reduce the remaining count (a file that
can't resolve on its own), the loop stops itself early rather than spinning forever, says so
in the log, and leaves that file for a human to review by hand instead of retrying it forever.
Run **Continue Clarification** when you want fresh numbers for the updated PRD.

## Ask to continue — after any apply step

```
Run another clarification round now? [y/N]:
```

Shown right after answers are applied to the PRD/spec by `clarify --apply`. Answering
`y`/`yes` immediately starts a fresh `clarify` round (re-evaluate → report → web UI); anything
else (including just pressing Enter) exits so you can review the result yourself first.
Looping back from plain `clarify` respects whatever `--noui` you originally passed; looping
back from `answer` or `clarify --apply` always reopens the web UI.

Only asked in an interactive terminal — if stdin isn't a TTY (e.g. scripted/CI use), the
prompt is skipped entirely and the command just exits, same as before this behavior existed.

Not shown by `--finalize`: that mode runs its own loop by rule, so there's nothing to ask.

## Finalize mode (automatic clarification) — `tempa clarify --finalize`

```bash
tempa clarify --finalize
```

Performs **automatic evaluation and answering** in a loop (up to `max_clarification_run`
rounds) without pausing. Here the agent decides the answers, not you — run this once you're
confident its recommended answers can be trusted.

The sequence:

1. **Pre-flight.** Any finding already sitting in `sources.clarifications` without an answer
   is filled in mechanically with its own `Recommendation` text (no agent call). Everything
   answered enters the loop as the pending overlay. Nothing is applied here.
2. **Loop: evaluate → auto-answer.** Each round evaluates the PRD carrying the whole overlay,
   then auto-answers whatever it found. **No apply runs inside the loop** — the PRD is not
   touched. Repeat until a round reports `critical == 0` and `major == 0` (remaining `minor`
   findings are considered acceptable).
3. **Compaction.** One apply pass writes the entire accumulated overlay into the PRD/spec.
   This replaces what used to be an apply after every single round.
4. **Verification.** One more evaluation, this time over the compacted PRD with an empty
   overlay — because applying is an agent rewriting prose, and the only way to know the
   documents themselves are clean is to evaluate them. A clean verification ends the run
   successfully (and leaves `last_clarification_action` at `evaluate`, which is what the
   dashboard's finalize readiness looks for).

If that verification comes back **dirty**, the run doesn't fail: it re-enters the loop,
answers the new findings, and compacts a second time. That's bounded at two compactions per
run — beyond it the run stops and asks for a human (`tempa answer`), rather than rewriting
the PRD again and again unattended.

The compaction resumes the evaluate session that just ran (see `clarify_session_id` in
[config-json.md](config-json.md)) instead of starting cold: that session already read the
whole PRD *and* was handed the entire overlay, which is exactly what the apply has to write.
Evaluate rounds themselves never resume — a fresh read of the PRD every round is what makes
them trustworthy.

If `finalize_no_progress_rounds` (default `5`, dashboard Settings → Runs tab → "Max Finalize
No-Progress Round") rounds in a row fail to reduce the critical+major count, the loop stops
on its own instead of running to `max_clarification_run` — those findings likely need a
human decision (`tempa answer`) rather than more automated answering. Raise that setting if
you'd rather the automation kept trying for longer. The counter resets after a compaction: the PRD has just
been rewritten, so a count from before it says nothing about progress.

The verification round is a full evaluation and counts as a normal round — it increments
`last_clarification_round` / `last_finalize_round` and consumes the `max_clarification_run`
budget like any other.

Stopping a `--finalize` run mid-way now leaves the answers **unapplied** (in the overlay)
rather than a partially-updated PRD — nothing has been written until the compaction step.

`--skip-minor` also applies here (`tempa clarify --finalize --skip-minor`), skipping minor
findings on every evaluate pass in the loop.

## Clarification history and round tracking

Every evaluation pass (manual `clarify` or one iteration of `clarify --finalize`) writes its
findings to a **new** file in `sources.clarifications` instead of overwriting or deleting a
previous one — so every past round's findings and answers stay visible in the dashboard's
Clarification Overview ("Unanswered" / "Fully answered" sections), even after later rounds run
or answers are applied. Applying answers (`clarify --apply`, or saving in the web UI) never
touches the clarification files themselves — only the PRD/spec documents and `config.json`.

The dashboard's "Finalize readiness" panel shows **Round N**, where N is
`last_clarification_round` — a running total across *every* evaluate pass ever, manual
`clarify` and `clarify --finalize` alike — shown alone, with no total, since manual `clarify`
isn't bounded by `max_clarification_run`.

Progress against that limit is a separate counter, `last_finalize_round`: it resets to `0` at
the start of every `--finalize` run and counts up to `max_clarification_run` (M) within that
one run only, independent of the all-time total above. It's shown as **N / M** next to the
"Finalized Clarification" button, ticking up live once a second while the run is in progress.

While a finalize run is in progress, "Finalized Clarification" is itself replaced by a **Stop
Finalize** button — clicking it (after a confirmation prompt) kills the running process, the
same way "Stop Implementation" does for `tempa implement`. "Start Clarification"/"Continue
Clarification" and "Apply Answers" get the same treatment while *they're* running — **Stop
Clarification** and **Stop Apply Answers** respectively, same confirmation-then-kill. Apply's
auto-chain loop (see **Ask to continue** above) additionally checks for a pending stop between
batches: a Stop clicked in that gap skips the next queued file instead of waiting for it to
start and then killing it mid-way.

The Clarification page itself splits into an **Overview** tab (the Unanswered/Fully answered
tables above) and a **Log** tab (raw streamed console output for whichever run is in
progress), the same Status/Log split the Implementation page uses. The run buttons and
readiness panels (Evaluation scope, Pending resolutions, Finalize readiness) stay pinned above
both tabs, with a spinning status badge next to the run buttons reflecting live progress
regardless of which tab is open.
