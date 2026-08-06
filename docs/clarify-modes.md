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
   Saving in the UI immediately runs an apply step (see **Apply mode** below) — no separate
   `clarify --apply` needed after using the UI.
5. After that apply step finishes, asks whether to run another clarification round right
   away (see **Ask to continue** below).

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

Clicking **Save answers** shows a confirmation summarizing what will change, then writes
every answer — across every open tab/file, not just the one currently visible — back into
each clarification file's `**Your answer:**` section (between `<!-- clarify:answer-start -->`
/ `<!-- clarify:answer-end -->` markers). The tool then immediately runs an apply step — the
same as `clarify --apply` — writing those answers into the PRD/spec for **all** the files
that were open, followed by the **Ask to continue** prompt below. **Cancel** closes the page
without touching any file (and without applying anything). The server only listens on
`127.0.0.1`, for the duration of that one answer session.

## Auto-answer mode — `tempa clarify --auto-answer`

```bash
tempa clarify --auto-answer
```

Automatically answers clarification findings that are **still unanswered** (one pass,
**without** re-evaluating or looking for new findings):

1. The agent reads the clarification result file in `sources.clarifications`, checking which
   findings don't have an answer yet.
2. For each unanswered finding → adds an answer/resolution directly in the clarification
   file (based on PRD analysis), without overwriting answers that already exist.
3. If **every** finding has already been answered → no changes are made; the console shows a
   message that there's nothing left to answer.

Useful as a middle ground: let the agent answer first, then you review/correct.

## Apply mode — `tempa clarify --apply`

```bash
tempa clarify --apply
```

Applies the answers/resolutions **already recorded** in the clarification file into the
PRD/spec document (`sources.prd`) — one session, **without** re-evaluating. This is the step
after answering (whether manually or via `--auto-answer`): the agent reads the resolutions in
`sources.clarifications`, then edits the relevant PRD/spec documents, and updates
`last_clarification_findings`. If there's no clarification result yet, the harness asks you
to run `clarify` first.

Difference from `--finalize`: `--apply` doesn't re-evaluate — it only applies existing
answers. `--finalize` combines evaluate + apply in a loop.

Once the apply step succeeds, `clarify --apply` also shows the **Ask to continue** prompt
below (same as the web UI path).

## Ask to continue — after any apply step

```
Run another clarification round now? [y/N]:
```

Shown right after answers are applied to the PRD/spec — whether that apply was triggered by
saving in the web UI (`clarify` / `answer`) or by running `clarify --apply` directly.
Answering `y`/`yes` immediately starts a fresh `clarify` round (re-evaluate → report → web
UI); anything else (including just pressing Enter) exits so you can review the result
yourself first. Looping back from plain `clarify` respects whatever `--noui` you originally
passed; looping back from `answer` or `clarify --apply` always reopens the web UI.

Only asked in an interactive terminal — if stdin isn't a TTY (e.g. scripted/CI use), the
prompt is skipped entirely and the command just exits, same as before this behavior existed.

Not shown by `--finalize`: that mode already loops evaluate → apply automatically by rule
until `critical`/`major` findings are gone, so there's nothing to ask.

## Finalize mode (automatic clarification) — `tempa clarify --finalize`

```bash
tempa clarify --finalize
```

Performs **automatic evaluation and answering** in a loop (up to `max_clarification_run`
rounds) without pausing: evaluate → if `critical`/`major` findings remain, the agent applies
its own resolution to the PRD/spec document → repeat until `critical == 0` and `major == 0`
(remaining `minor` findings are considered acceptable). Here the agent decides the answers,
not you — run this once you're confident its recommended answers can be trusted.

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
same way "Stop Implementation" does for `tempa implement`.
