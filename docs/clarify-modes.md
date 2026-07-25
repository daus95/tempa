# Clarification Mode Reference (`clarify`)

Full reference for every `clarify` mode: manual (default), `--auto-answer`, `--apply`, and
`--finalize`. See [README.md](../README.md) — Step 2 (Answer Clarifications) — for the
recommended iterative flow.

## Manual mode (default) — `py tempa.py clarify`

```bash
py tempa.py clarify
```

Runs **one evaluation pass**, then stops:

1. Claude examines the **entire PRD document** in `sources.prd` (the PRD can span multiple
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

```bash
py tempa.py clarify --noui   # skip opening the web UI; just write the file and print the summary
```

You can also open the result file in a text editor and **answer the clarifications
manually** (editing the PRD/spec per your decisions), then call `clarify` again for the next
iteration.

## Clarification-answer web UI

Opened automatically after a manual `clarify` pass (unless `--noui`), or on demand for any
existing clarification result file:

```bash
py tempa.py answer <clarification-file>
```

`<clarification-file>` can be an absolute/relative path, or just a filename inside
`sources.clarifications` (with or without the `.md` extension).

The UI parses each finding (delimited by `<!-- clarify:item ... -->` markers written by the
`clarify` prompt) and renders the file as a formatted page. For every finding you can pick:

- **Follow the recommendation** — keeps Claude's suggested resolution as your answer.
- **I'll write my own answer** — enables a text area (5+ rows) to type your own answer,
  overriding the recommendation.

Clicking **Save answers** shows a confirmation summarizing what will change, then writes
every answer back into the clarification file's `**Your answer:**` section (between
`<!-- clarify:answer-start -->` / `<!-- clarify:answer-end -->` markers) and exits the tool
so you can move on to the next step (typically `clarify --apply` or `clarify --finalize`).
**Cancel** closes the page without touching the file. The server only listens on
`127.0.0.1`, for the duration of that one answer session.

## Auto-answer mode — `py tempa.py clarify --auto-answer`

```bash
py tempa.py clarify --auto-answer
```

Automatically answers clarification findings that are **still unanswered** (one pass,
**without** re-evaluating or looking for new findings):

1. Claude reads the clarification result file in `sources.clarifications`, checking which
   findings don't have an answer yet.
2. For each unanswered finding → adds an answer/resolution directly in the clarification
   file (based on PRD analysis), without overwriting answers that already exist.
3. If **every** finding has already been answered → no changes are made; the console shows a
   message that there's nothing left to answer.

Useful as a middle ground: let Claude answer first, then you review/correct.

## Apply mode — `py tempa.py clarify --apply`

```bash
py tempa.py clarify --apply
```

Applies the answers/resolutions **already recorded** in the clarification file into the
PRD/spec document (`sources.prd`) — one session, **without** re-evaluating. This is the step
after answering (whether manually or via `--auto-answer`): Claude reads the resolutions in
`sources.clarifications`, then edits the relevant PRD/spec documents, and updates
`last_clarification_findings`. If there's no clarification result yet, the harness asks you
to run `clarify` first.

Difference from `--finalize`: `--apply` doesn't re-evaluate — it only applies existing
answers. `--finalize` combines evaluate + apply in a loop.

## Finalize mode (automatic clarification) — `py tempa.py clarify --finalize`

```bash
py tempa.py clarify --finalize
```

Performs **automatic evaluation and answering** in a loop (up to `max_clarification_run`
rounds) without pausing: evaluate → if `critical`/`major` findings remain, Claude applies its
own resolution to the PRD/spec document → repeat until `critical == 0` and `major == 0`
(remaining `minor` findings are considered acceptable). Here Claude decides the answers, not
you — run this once you're confident its recommended answers can be trusted.
