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
3. A suggested next step, based on what the round's **severity phase** still owes (see
   [Severity phases](#severity-phases)):
   - findings remain in this phase → recommends answering them, manually or with
     `--auto-answer`, then running `clarify` again.
   - the phase came back clean but unconfirmed → recommends one more round at the same scope.
   - the phase is settled → says which severity the next round moves on to.
   - the last phase is settled → clarification is considered done; recommends applying and
     moving on to `implement` (plan drafting will run automatically first).
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

## Severity phases

Clarification sweeps the severities **one phase at a time** — every critical first, then
major, then minor — rather than evaluating them all in one round. On by default;
`clarify_severity_phases: false` in config.json turns it off and restores the single-scope
behavior exactly.

### Why

A round can only *answer* what it found. Answering a major rewrites the PRD, and a rewritten
PRD grows new criticals — the overlay's own rule 3c ("a decision ADDS something without
wiring it in") exists precisely because that keeps happening. So a round looking for both at
once re-derives criticals from documents its own previous round changed: it is sweeping a
target that moves every time it makes progress.

Measured on a POS test workspace, five rounds of the mixed scope reported **4, 4, 2, 3 and 1**
critical findings and never converged, at ~26 minutes of evaluation. Most of them were the
same class of defect — a capability with no screen or endpoint behind it — and several were
derivable from the *original* PRD, meaning earlier rounds simply missed them.

Sweeping criticals alone until they are exhausted settles the expensive severity against a
specification that is holding still. Only then does the scope widen.

### How a phase ends

Each phase **widens** the scope by one severity rather than switching to it:

| phase | evaluates for | has to reach zero |
|---|---|---|
| critical | critical only | critical |
| major | critical + major | critical + major |
| minor (only when `--skip-minor` is off) | all three | all three |

Because a later phase still checks the earlier severities, a critical that turns up during
the major sweep **demotes** the run straight back to the critical phase, narrowing the next
round to criticals only. That is the case the widening exists to catch — and it is the
fallout of answering a major, arriving exactly where it can be seen.

A phase is settled when a round reports nothing left in it **and** that result is backed up:

- by a [coverage ledger](#the-coverage-ledger) that accounts for every finding the previous
  round raised, is complete (`unchecked="0"`), and is no smaller than the previous round's —
  neither its check table nor any inventory category — one clean round is then enough; or
- failing that, by a **second** clean round in a row.

One clean round on its own is never enough. The behavior this replaces had a round report
zero criticals while three of them sat in the specification.

When a phase settles, `--finalize` writes everything that phase decided into the PRD and
commits it, so each phase boundary is a readable diff and a restorable point. There is no
separate verification round there: the next phase's first round re-checks the settled
severities over those same freshly written documents anyway.

The phase is persisted (`last_severity_phase`), because a manual `clarify` is one round per
process — without it, every round would restart the sweep at the widest scope. A
`--finalize` run resumes whatever phase manual rounds left behind.

### What it changes elsewhere

- **`critical_phase_max_rounds`** (default `10`) bounds how many answering rounds a
  `--finalize` run may spend in the critical phase across the whole run. Past it the run stops
  and asks for a human: a critical is, by the rubric, the specification being unbuildable,
  which is worth a decision rather than another unattended answering round. Measured rather
  than guessed — eight rounds against a 256-line PRD found a critical derivable from the
  *original* spec in each of rounds 4 through 8, so a smaller budget cuts a sweep off while it
  is still productive. `10` leaves headroom past that and still binds before
  `max_clarification_run`.
- The **convergence guard** (`finalize_no_progress_rounds`) judges the phase's own count. In
  the critical phase, a round that changed nothing about criticals has made no progress,
  whatever happened to the majors it never looked at.
- **Start Implementation** stays closed while the run is still on the critical sweep, at the
  default `no_critical_or_major` requirement. That round reports zero majors because it never
  looked for any; one more round (the major phase's first) is what clears it. At
  `no_critical`, which says majors may remain open, it does not gate.

## The coverage ledger

The critical pass is not a reading the agent summarises; it is a table it fills in. Before
writing any findings, the evaluation writes a ledger to a `coverage/` subfolder of
`sources.clarifications`, named `coverage-<YYYYMMDD-HHMMSS>.md`:

- **Part 1 — inventory.** Every role, capability, screen, endpoint, entity, field, state,
  state transition, business rule and acceptance criterion, each with its location in the spec
  **and a short quote of the phrase that grants it** — including everything the pending overlay
  adds. Transcription, not judgement. Blanket sentences ("Admin sees everything", "full
  access", "no master data") are expanded into one entry per member of the set they quantify
  over, never one entry for the sentence. Closed by a
  `<!-- coverage:inventory roles="…" capabilities="…" … -->` marker giving each category's size.
- **Part 2 — the check table.** One row per (axis, subject) pair over the eight critical axes,
  each with a verdict of `OK`, `CRITICAL`, `N/A` — or **blank**, meaning the row could not be
  decided. A row that cannot be decided is unchecked, not OK.
- **Part 3 — the carry-over table.** One row per finding the *previous* round raised at a
  severity this round is scoped to, each with a verdict of `RESOLVED` (naming the decision or
  section that closes it), `STILL OPEN` (naming the id it was re-raised as this round), or
  `WITHDRAWN` (saying what the previous round misread). Wrapped in
  `<!-- coverage:carried -->` / `<!-- coverage:endcarried -->`, since it is read mechanically.
- A closing `<!-- coverage:summary checks="…" ok="…" critical="…" na="…" unchecked="…" -->`
  marker.

The findings file is then a projection of the `CRITICAL` rows.

The point is that the failure mode being fixed is *writing a report of a plausible size*. A
table has a known number of rows, and a row nobody filled in is visible — which is also what
makes a "no criticals" result checkable rather than a claim.

`unchecked="0"` alone is not quite enough to settle a phase, though, and it is worth being
precise about why. The marker attests that every row the agent **listed** got a verdict, never
that it listed every row there is — the table's construction is still its judgement. Two runs
of the same round, over the same PRD, on the same prompt and the same model, produced tables
of **113 rows and 64**, both reporting zero unchecked.

Size is the check available for that. Within a phase the spec only gains surface — answering
adds screens, fields and rules, and a re-derived inventory has to cover them — so a table
coming back materially smaller than the previous round's has lost rows rather than the spec
having lost surface. A ledger under 85% of the previous round's row count is therefore treated
exactly like no ledger at all: the phase falls back to needing a second clean round. The
session log says so when it happens.

### Nothing drops out silently

Re-deriving the inventory every round is what keeps a round honest about the spec, but it is
also how a finding disappears: round N raises it, round N+1 simply doesn't list that row, and
nobody notices. This is not hypothetical — four runs of the same round over the same PRD
produced overlapping but *different* critical sets, and their union was larger than any one of
them.

Part 3 is the answer to that, and it is the one part of the ledger that may **not** be
re-derived. Tempa reads the previous round's findings itself, passes their ids and titles into
the prompt, and afterwards checks that the carry-over table accounts for every one of them.
Only the text between the `coverage:carried` markers is searched, because finding ids restart
at `C1` each round and a bare `C1` elsewhere in the ledger is this round's own finding.

An id the table never mentions counts exactly like an unchecked row: the phase does not settle
on that round, and the session log names the ids that went missing. An inventory may
legitimately be grouped a new way from one round to the next; a finding somebody already
raised may not quietly stop existing.

Minor findings are never carried, even when they are being looked for — they block nothing, so
an accountability table over them would be cost with no decision behind it.

### Existence is not agreement

Six of the seven critical axes ask whether a thing exists — does this capability have a screen,
does this screen have its data, does this field have something that creates it. A ledger row
once read `Product.stock_qty — entered on the Products form; maintained by Stock In (+),
checkout (−), void (+)` and was verdicted OK. Everything in it is true, and axis 4 is satisfied:
something creates the field, something maintains it. What nobody asked is whether the form's
direct write obeys the movement-log and negative-stock rules the other three paths obey. It
does not, and that took three rounds to surface as a critical.

Axis 7 asks that question. For every field, entity, state or guarded surface that more than one
path reaches, it lists the paths and the rule each is subject to, and raises a finding for a
path that escapes a rule the others obey — or one subject to no stated rule at all. Its
population costs no new enumeration: it is read off the axis-3 and axis-4 rows already written,
so a cell naming four writers is four paths and gets an axis-7 row.

Two other findings had the same shape: a non-nullable `invoice_no` whose one creation path had
no format or uniqueness rule, and a read carve-out for a role whose write on the same field
stayed banned. In all three, existence was never what was missing.

### One defect, not one per field

Axis 7 compares a subject's paths to each other, so it can only see a member that is out of
step with its siblings. When every sibling is equally unconstrained there is nothing for it to
see — and that turned one defect into one finding per round for three rounds running.

The measurement: a ledger row read `Product.purchase_price — 2 paths: the form (direct entry)
and stock-in (BR11 overwrite); both stated, neither carries a further guard` and was verdicted
**OK**. Three rounds later, after other rounds had put bounds on quantities and on the discount
value, the same two paths with the same missing bound became **CRITICAL** — *"neither carries a
lower bound, while every other entered numeric input now carries one"*. The spec fact never
changed. Only the comparison class did.

Axis 8 asks the question class-wise instead: take each kind of rule the spec could state — a
bound on a numeric input, uniqueness on an identifier, case-sensitivity on a comparison, what
supplies a required column, a guard on an access path — list every member it could cover, and
report the members that do not carry it as **one** finding. Its rows are per class, not per
member, and it runs even when the spec states that rule nowhere at all, which is the case that
defeats axis 7 entirely.

### An inventory may not go short either

Part 3 stops a finding that was *raised* from being lost. It cannot help with one that was
never raised, and the way that happens is Part 1: an item the inventory never lists never
becomes a row, so it is not reported as unchecked either — it is simply absent, and every check
that would have quantified over it silently disappears with it. One round's inventory had no
entry at all for a capability the spec grants, and the contradiction that capability was part
of went unreported for two rounds while every ledger involved reported `unchecked="0"`.

Two things address it. In the prompt, every entry has to quote the phrase that grants it — an
entry you cannot quote is one you inferred — and any sentence that quantifies over a set has to
be expanded into its members, which is exactly the case that went missing. In the code, the
`coverage:inventory` marker's per-category counts are compared against the previous round's
under the same 85% rule as the check table, **category by category** rather than as one total,
since a total can hold still while capabilities halve and fields double. A category that
shrinks — or disappears — is named in the log and stops the round settling its phase.

What this does not do is make a *first* round provably complete. There is no previous inventory
to compare it against, and nothing but the agent reads the PRD. The guard bounds how far the
inventory can drift once a baseline exists; the prompt rules are what aim at the first round.

### The same comparison checks an apply

Applying is an agent rewriting prose, and nothing else compares the PRD before it against the
PRD after. The inventory counts do, for free and without being designed to: the round before an
apply derives them from the overlay plus the PRD, and the round after derives them from the
rewritten PRD alone. Applying moves surface into the document — it never removes any — so those
two counts should match category by category, and a category that comes back short means the
apply dropped something rather than the agent having listed less.

Measured twice on the same workspace, over an apply that took the PRD from 256 lines to 369 and
then to 400 while writing in 28 decisions:

| | before → after apply 1 | before → after apply 2 |
|---|---|---|
| capabilities, screens, endpoints, entities, fields, roles, criteria, transitions | identical | identical |
| rules | 41 → 44 | 50 → 51 |

Eight of nine categories unchanged both times, the ninth up. Nothing was lost. A shrunken
category here is a real defect report about the apply, not a false alarm to tune away.

Each round carries the previous ledger and must re-derive the inventory rather than trust it:
a ledger says what was checked, not what is true, so a screen missing from it *is* the miss
being hunted. Everything the overlay has added since gets new rows, and any row left blank
last round is the next round's first stop.

The ledger lives in a subfolder deliberately. Everything that reads `sources.clarifications`
globs `*.md` non-recursively and treats every hit as a round's findings, so a ledger written
beside them would be tabbed into the answer UI and swept into the apply backlog.

Nothing fails if the agent doesn't write one — a phase then falls back to needing two clean
rounds. A phase that could never advance because a marker kept being omitted would be a worse
failure than the miss the ledger guards against.

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
`clarify` prompt) and renders the file as a formatted page. Every finding carries a
`**Your answer:**` label above its answer block; the parser also tolerates a round that
omits the label by falling back to the `clarify:answer-start` marker, so such findings
still appear in the review UI instead of being silently dropped. For every finding you can pick:

- **Follow the recommendation** — keeps the agent's suggested resolution as your answer.
- **I'll write my own answer** — enables a text area (5+ rows) to type your own answer,
  overriding the recommendation.

A finding that names something an **earlier** round already named — a field or entity in
backticks or bold, or a UI string in quotes — also carries a **Decided elsewhere** note between
its recommendation and those two choices, listing the shared surfaces and which finding in which
round named them. It is a prompt to read the two side by side, nothing more: it blocks no answer
and changes no count. Since a recommendation accepted with one click becomes the PRD's text
verbatim, this is the cheapest moment to notice that accepting it would reword a decision
somebody already made — the alternative is a later round raising the contradiction as a fresh
critical finding.

**Every** earlier round is checked, whether or not its answers have been written into the PRD
yet, and each line says which state its round is in:

- *decided, not yet in the PRD* — the decision exists only in that clarification file. The PRD
  still reads the old way, so a recommendation that rewords it contradicts something no document
  shows. This is the one that costs a round to find later.
- *already in the PRD* — its round has been applied, so the wording is in the spec this round was
  evaluated against. The note is provenance: it says which round chose that wording, and where to
  check that this answer preserves it.
- *not yet answered* — that finding is still open, so there is no decision yet, only another
  finding reaching for the same thing. Answer the two together.

Clicking the finding id opens it in the same right-hand drawer that spec references use, showing
it read-only with the answer that was recorded for it, so the comparison never costs you the
unsaved answers on the page you are on. **⧉** in the drawer's header opens that whole
clarification file instead (with the usual unsaved-answers prompt).

The match is textual and deterministic — no model runs, and the PRD is never read for it — so
it is deliberately imperfect in both directions: a surface named in two unrelated senses gets a
note nobody needs, and one named two different ways in two rounds gets none. Surfaces that most
of the folder mentions are treated as the workspace's vocabulary and dropped, so the note stays
rare enough to be worth reading.

### Reading the spec a finding cites

Requirement/rule ids, `§`-section references and file paths mentioned anywhere in a finding
are rendered as links. Clicking one opens a drawer from the right showing that specification
file, scrolled to and briefly highlighting the exact table row, bullet or heading that
defines the id. The drawer is modal — the page behind it dims and stops responding while it
is open — and closing it (**✕**, **Escape**, or a click outside it) leaves the Clarification
page exactly as it was, unsaved answers included. It is read-only; **Open in Specification**
switches to the full editor. Drag its left edge to resize it.

References are resolved against the PRD **as it stands right now**, not as it stood when the
finding was written, so links keep working on clarification files from earlier rounds and the
line they point at is never stale. Two things deliberately don't become links:

- an id the PRD never *defines* — one only mentioned in passing, and, importantly, a
  finding's reference to an **earlier round's finding** (`R8 M2`), which shares the shape of
  a requirement id but is not one;
- an id defined in two different files, where guessing which one was meant would be worse
  than plain text.

Since resolution is by definition site, an id has to be written the way the PRD writes it —
which is what `clarification.md` instructs, along with naming the spec file at the start of
each **Where:** line so a finding that cites no id still links somewhere.

The left navigation collapses to an icon rail (the button beside **Refresh**, or
**Ctrl/Cmd+B**) when you want the extra width for reading.

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

From the dashboard, one **Apply Answers** click keeps re-running this until every ready file is
applied, so **Stop After Current Session** on that button lets the apply session in flight finish
and stamp what it wrote, then skips the remaining files rather than killing the agent mid-rewrite.
**Stop Now** kills it immediately, discarding that session's unwritten work.

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
2. **Loop: evaluate → auto-answer, one severity phase at a time.** Each round evaluates the
   PRD at its phase's scope, carrying the whole overlay, then auto-answers whatever it found.
   **No apply runs per round** — the PRD is untouched between checkpoints (see below) and
   phase boundaries. The critical sweep runs to exhaustion first; only then does the scope
   widen to majors. See [Severity phases](#severity-phases) for how a phase ends and what
   sends the run back to an earlier one. With phases off, this is one scope
   (`critical`+`major`) repeated until both reach zero, exactly as before.
3. **Compaction.** One apply pass writes the entire accumulated overlay into the PRD/spec.
   This replaces what used to be an apply after every single round.
4. **Verification.** One more evaluation, this time over the compacted PRD with an empty
   overlay — because applying is an agent rewriting prose, and the only way to know the
   documents themselves are clean is to evaluate them. A clean verification ends the run
   successfully (and leaves `last_clarification_action` at `evaluate`, which is what the
   dashboard's finalize readiness looks for), after one last commit — the version that is
   ready to be implemented. That closing commit runs on every successful finish, including a
   short run that never checkpointed and a run with checkpoints switched off; only the commit
   toggle below can suppress it.

### Checkpoints

Everything above keeps the PRD untouched until the compaction, which is cheap — but it means
a long unattended run holds hours of agent work in an overlay the documents have never seen,
with no restorable point until the very last step.

`finalize_checkpoint_rounds` (default `3`, dashboard Settings → Runs tab → "Checkpoint Every
N Rounds") buys some of that back. Every N answering rounds the loop stops and does two things:

1. **Apply** — one apply pass writes everything answered so far into the PRD/spec.
2. **Commit** — `git add -A` + `git commit` in the working folder
   (`finalize_checkpoint_commit`), so each checkpoint is a readable diff of what that stretch
   of rounds actually changed in the document.

That second step only means anything because the PRD is kept in the repo on purpose. It lives
under `.tempa/`, which `tempa init` git-ignores wholesale — so Tempa writes ignore rules that
carve out an exception for `.tempa/specs/prd/` while leaving logs, QA/verify reports,
`config.json` and the generated epic/clarification specs ignored. `init` writes them when a
workspace is created or reopened, and each checkpoint re-checks them before committing. See
[folders-and-paths.md](folders-and-paths.md).

Set the interval to blank for no checkpoints at all — the pure one-apply-at-the-end behavior
finalize had before. Each checkpoint costs one extra agent session, so `1` (apply after every
round) is the most expensive setting; the trade being made is recoverability, not evaluation
quality, since the overlay already made per-round applies unnecessary for correctness.

Committing is **best-effort**: whatever it reports is logged and the run carries on either
way, because losing an unattended run's whole clarification effort over a missing `user.email`
would cost far more than the commit was protecting. A checkpoint whose *apply* fails does stop
the run — that is a systemic problem (auth, backend, prompt) which would recur at the
compaction anyway.

Two interactions worth knowing:

- A checkpoint does **not** consume the two-rewrite budget below. That bound exists for the
  "verification came back dirty, rewrite again" loop; a checkpoint is scheduled by a round
  counter instead, and charging it there would stop a run checkpointing every 3 rounds long
  before its 20th round.
- The counter is per *answering* round and resets after any write to the PRD, so a compaction
  also clears it — a checkpoint never fires just to apply a single round's answers right
  after a compaction emptied the overlay.

See [config-json.md](config-json.md) for both settings.

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

Stopping a `--finalize` run mid-way leaves the answers recorded since the last checkpoint
**unapplied** (in the overlay) rather than a partially-updated PRD. With checkpoints off,
that means nothing has been written at all until the compaction step; with them on, the PRD
is whatever the most recent checkpoint made it — a complete, applied, committed state, not a
half-finished one, since a checkpoint's apply either finishes or stops the run.

### Stopping a finalize run

**Stop Now** (the button, or Ctrl+C) kills the run immediately, discarding whatever the session
in flight had worked out but not yet written.

**Stop After Current Round** — the chevron next to that button, or `tempa clarify
--stop-graceful` — leaves a request instead. The run checks for it at the three points where it
is about to spend another agent session: before the next round, before the compaction apply,
before the auto-answer step, and before a checkpoint's apply. Whichever session is already running finishes and records its work;
the run then exits cleanly (code 0). Because `last_finalize_round` / `last_finalize_phase` and
the round's findings are saved before each of those checks, the run picks up from exactly where
it stopped the next time you click **Finalized Clarification**.

Cancel a pending request with the dropdown's **Cancel Graceful Stop**, or
`tempa clarify --stop-graceful-cancel`. Requests made in a terminal and from the dashboard are
the same request — either surface can make it, cancel it, or show it. A **Start / Continue
Clarification** run has no loop to stop between (it is one evaluate session), so the graceful
option there simply means "let this session finish and record its findings" instead of killing it.

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
