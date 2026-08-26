Re-evaluate all PRD documents at (${sources.prd}) to check whether any specifications are still conflicting or need clarification. The PRD may consist of multiple document files — check all of them.

=== ALREADY-DECIDED RESOLUTIONS NOT YET WRITTEN INTO THE PRD ===

The decisions below were raised in earlier clarification rounds and have already been answered by the user (or on the user's behalf). They have NOT yet been written into the PRD documents. Treat this block as an AUTHORITATIVE OVERLAY on top of the PRD: wherever a decision below contradicts what the PRD files currently say, THE DECISION BELOW IS THE TRUTH and the PRD text is simply stale.

${pending_resolutions}

Rules for using this overlay — follow all four:

1. ALREADY DECIDED. Do NOT raise a new finding for anything the overlay already settles, and do NOT re-ask a question it already answers, even if the PRD text still reads the old way. Those points are closed.
2. LAST ONE WINS. The rounds above are in chronological order, oldest first. If two decisions cover the same point, the one in the LATER round supersedes the earlier one completely. Do not merge them and do not average them.
3. A DECISION CAN ITSELF BE A FINDING. The overlay mostly suppresses findings, but three things about it produce new ones instead. RAISE ANY OF THEM as a new finding using the format below, quoting both sides:
   a. Two decisions in the SAME round contradict each other.
   b. A decision contradicts something in the PRD that it does not itself resolve — applying it would break a different, still-valid part of the spec.
   c. A decision ADDS something — a screen, a field, an endpoint, a rule, a state — without wiring it in. This is a hole rather than a contradiction, and it is the most common of the three: the added thing has no navigation entry, no lifecycle, no guard, nothing that enforces it, or it is missing from an enumeration an earlier decision wrote. Judge everything the overlay adds by exactly the same axes as the PRD's own content. Rule 1 closes the QUESTION a decision answered — never the surface that decision introduced.
4. EVALUATE AS IF APPLIED. Judge the specification as it will read once every decision above has been written in. Do not report an ambiguity that only exists because the overlay has not been applied yet.

Do NOT edit the PRD documents in this session, and do NOT edit any existing clarification file — this session only evaluates and writes a new findings file.

=== END ALREADY-DECIDED RESOLUTIONS ===

=== SEVERITY, AND WHAT TO LOOK FOR FIRST ===

Severity is not a matter of taste. Classify every finding by this rubric, the same way in every round:

- CRITICAL — the specification cannot be built as written. A capability that a role, an acceptance criterion or a business rule requires but which no screen, endpoint, entity or field in the spec can reach; two statements that directly contradict, so that no single implementation satisfies both; anything that loses or corrupts data; anything that leaves the system unreachable, or lets a role reach what it must not.
- MAJOR — buildable, but the implementer has to guess, and two reasonable guesses produce materially different systems.
- MINOR — wording, naming or consistency only; every reasonable reading yields the same system.

THIS ROUND IS SCOPED TO: ${finding_scope}. A severity outside that scope is not looked for, not evaluated, and not written down — not even as an aside inside another finding.

CRITICAL FINDINGS COME FIRST, AND THEY MUST BE COMPLETE. Before evaluating anything else, make one full pass over every spec document hunting ONLY for critical findings, and do not begin writing the findings file until that pass is finished. Report EVERY critical it turns up — there is no cap, and no "reasonable report size" to stay within. A round that reports fewer criticals than the spec actually contains costs the user one more full round for each one it held back, so holding one back is the most expensive thing you can do here. Only once that pass is exhausted, look for the other severities this round is scoped to.

Run the critical pass along these axes, checking each one ACROSS THE WHOLE SPEC rather than section by section — most criticals live between two sections that each read fine on their own:

1. Every capability a role is given has a screen (or endpoint) that role can actually reach, and that screen is listed in that role's navigation.
2. Every acceptance criterion has something in the spec that implements it — a screen, a rule, an entity, a field.
3. Every screen has the entities and fields it reads and writes, AND every role that can reach that screen is permitted to read and write them. Check the permission against the spec's own access rules, not against what the screen obviously needs — a screen whose role is banned from the data it reads is a contradiction no implementation satisfies, and it is invisible if you check the screen's data and the role's ban separately.
4. Every entity and field has something that creates it and something that maintains it.
5. Every state transition names who can trigger it, from which screen, and under what guard.
6. Every rule names what enforces it and what the user sees when it is violated.
7. Every field, entity, state and guarded surface that MORE THAN ONE path reaches is governed by the same rules on all of them. Axes 1-6 ask whether a thing exists; this one asks whether the things that exist agree. List every path that writes or reaches it and the rule each path is subject to, then raise a finding for either of these: a path that escapes a rule the other paths obey, or a path subject to no stated rule at all.
8. Every KIND of constraint the spec could state, it states for every member it should cover — or for none of them, which is the same defect and a bigger one. Work by class, not by member: take each kind of rule (a bound on a numeric input, uniqueness on an identifier, case-sensitivity on a comparison, what supplies a required column, a guard on an access path, a timezone on a stored time) and list EVERY member of the spec it could apply to, then say which members carry it. The members that do not are ONE finding covering all of them, never one finding each. Run this class even when the spec states that rule nowhere at all — especially then.

Axis 7 is the one that catches what a cell can state and still call fine. A ledger row once read "Product.stock_qty — entered on the Products form; maintained by Stock In (+), checkout (−), void (+)" and was verdicted OK: everything in it is true, something creates the field and something maintains it, so axis 4 is satisfied. What nobody asked is whether the form's direct write obeys the movement-log and negative-stock rules the other three paths obey. It does not, and that took three rounds to surface. Two more went the same way — a non-nullable `invoice_no` whose one creation path had no format or uniqueness rule, and a read carve-out for a role whose write on the same field stayed banned. Existence was never the problem in any of them.

Axis 8 exists because axis 7 can only see a member that is OUT OF STEP with its siblings, and when every sibling is equally unconstrained there is nothing for it to see. Measured: a ledger row read "Product.purchase_price — 2 paths: the form (direct entry) and stock-in (BR11 overwrite); both stated, neither carries a further guard" and was verdicted OK. Three rounds later, after other rounds had put bounds on quantities and on the discount value, the SAME two paths with the SAME missing bound became CRITICAL — "neither carries a lower bound, while every other entered numeric input now carries one". The spec fact never changed; only the comparison class did. Unbounded quantities, an unbounded discount and unbounded prices were one defect reported over three separate rounds, each costing a full round, because they were only ever asked about one field at a time. Ask the class once and they are one finding.

Do not assume the earlier rounds were exhaustive. A critical that follows from the spec text alone — one that needs no decision from the overlay above in order to see — should have been caught in an earlier round, and the fact that it is still here means an earlier round missed it. Assume such misses exist and hunt for them deliberately.

=== THE COVERAGE LEDGER — HOW THAT PASS IS MADE COMPLETE ===

The critical pass is not a reading you summarise; it is a table you fill in. Write the ledger BEFORE you write a single finding, and derive the findings file from it — the findings file is a projection of the ledger's CRITICAL rows, never a free-form report. This is what stops the pass from stopping at a plausible number of findings: a table has a known number of rows, and a row nobody filled in is visible.

Save the ledger as a NEW file in ${coverage_dir}, named `coverage-<YYYYMMDD-HHMMSS>.md` using the current date/time. Create that folder if it does not exist. Never delete, overwrite or edit a ledger already there — each round's ledger is the record of what that round actually checked.

PART 1 — INVENTORY. Transcribe, each with an id and where the spec defines it: every role; every capability each role is given; every screen; every endpoint; every entity; every field; every state and every state transition; every business rule; every acceptance criterion. Include everything the ALREADY-DECIDED RESOLUTIONS overlay adds, exactly as if it were already written into the PRD. This part is transcription, not judgement — never leave an item out because it looks fine, and never collapse a group ("the usual CRUD endpoints") instead of listing its members.

Two rules decide whether Part 1 is right. Both are here because a round once wrote an inventory with no entry at all for a capability the spec grants: nothing downstream could check what was never listed, and the contradiction that capability was part of went unreported for two whole rounds. An item missing from Part 1 is worse than an unchecked row, because an unchecked row at least admits it exists.

QUOTE WHAT GRANTS IT. Every entry names the section it comes from AND quotes the phrase that establishes it — a few words, no more. An entry you cannot quote is one you inferred rather than read; either find the text that grants it or leave it out and raise the gap as a finding in its own right.

EXPAND EVERY BLANKET STATEMENT. A sentence that quantifies — "Admin sees everything", "full access", "all master data", "no master data", "every screen", "read-only everywhere" — is a grant or a ban over a SET, and it produces one entry per member of that set, never one entry for the sentence. Enumerate the members and list them individually, each quoting that same blanket phrase as its source. This is exactly where an inventory goes quietly short: a blanket sentence reads like flavour text next to the explicit lists around it, and skipping it silently removes every check that would have quantified over what it grants.

Then state the size of what you transcribed, exactly once, as its own marker. It is read mechanically, so keep the attribute names and quoting exactly as shown, all on one line:

```
<!-- coverage:inventory roles="…" capabilities="…" screens="…" endpoints="…" entities="…" fields="…" transitions="…" rules="…" criteria="…" -->
```

Those counts are compared against the previous round's. Within a phase the spec only gains surface, so a category coming back smaller than last round's is the signal that something dropped out of the INVENTORY rather than out of the spec.

PART 2 — THE CHECK TABLE. One row for every (axis, subject) pair: for each of the eight axes above, one row per inventory item that axis quantifies over — axis 1 one row per role capability, axis 2 one row per acceptance criterion, axis 3 one row per screen AND per role that reaches it (a screen two roles reach is two rows, since the answer can differ by role), axis 4 one row per entity and per field, axis 5 one row per state transition, axis 6 one row per business rule, axis 7 one row per subject whose OWN axis-4 or axis-3 row named more than one path reaching it, axis 8 one row per CONSTRAINT CLASS (not per member) — its cell names the members that carry the rule and the members that do not. Axis 7 costs no new enumeration: its population is read off the rows you have already written, so a field whose axis-4 cell says "entered on S6; maintained by S7, checkout, void" is four paths and gets an axis-7 row. Write it as a Markdown table:

```
| # | axis | subject | what must exist for this to be buildable | verdict | finding |
|---|------|---------|------------------------------------------|---------|---------|
| 1 | 1 cap->screen | Admin: void a sale | a screen an Admin can reach that lists sales | CRITICAL | C1 |
| 2 | 1 cap->screen | Cashier: checkout | POS page, in Cashier navigation | OK | — |
| 3 | 3 screen->data | POS page as Cashier | reads Product — and the Cashier may read it | CRITICAL | C2 |
| 4 | 7 agreement | Product.stock_qty | 4 paths: Products form (direct, no guard), Stock In / checkout / void (via StockMovement, negative-stock guard) — the form escapes both | CRITICAL | C3 |
| 5 | 8 class | lower bound on a numeric input | carried by: none. Missing on: cart quantity, stock-in quantity, discount_value, sell_price, purchase_price, paid_amount — one finding, not six | CRITICAL | C4 |
```

`verdict` is exactly one of:

- OK — the thing that must exist does exist. Name it in the "what must exist" cell, so the claim is checkable rather than asserted.
- CRITICAL — it does not, and that makes the spec unbuildable as written. Every CRITICAL row carries the id of the finding that reports it, and every critical finding traces back to at least one CRITICAL row.
- N/A — the axis genuinely does not apply to this subject. Give the reason in one clause; an "N/A" with no reason is an unchecked row wearing a verdict.

A row you cannot decide is UNCHECKED: leave its verdict cell empty rather than guessing. An honest unchecked row tells the next round exactly where to look; a guessed OK is the miss that costs a full round.

PART 3 — THE CARRY-OVER TABLE. Below is every finding the previous round raised at a severity this round is scoped to. Each one gets exactly one row here saying what became of it. Wrap the table in these two markers exactly as shown — it is read mechanically, and an id missing from it counts the same as a row nobody checked:

```
<!-- coverage:carried -->
| prior finding | verdict | where it stands now |
|---------------|---------|---------------------|
| C1 | RESOLVED | the overlay's decision on it adds the Sales page and its Void action |
| C2 | STILL OPEN | re-raised this round as C1 |
| C3 | WITHDRAWN | §3's ERD does define it — the previous round misread that section |
<!-- coverage:endcarried -->
```

`verdict` is exactly one of:

- RESOLVED — a decision in the overlay above, or the PRD as it now reads, closes it. Name the decision or the section that does. "It looks fine now" accounts for nothing.
- STILL OPEN — it is still true of the spec. Raise it again as a finding this round and name the id it became. A finding that is still open and is not re-raised is the single most expensive thing this table exists to prevent.
- WITHDRAWN — it was never a finding at this severity. Say exactly what the previous round misread. Use this sparingly, and never to make the list shorter.

Part 1 is re-derived every round and may legitimately be grouped a new way — within the limit its own counts marker sets. Part 3 is not re-derived at all: a finding somebody already raised cannot quietly stop existing. Account for every id below, including the ones you disagree with.

=== FINDINGS THE PREVIOUS ROUND RAISED ===

${carried_findings}

=== END FINDINGS THE PREVIOUS ROUND RAISED ===

Finish the ledger with this marker, exactly once, as its LAST line. It is read mechanically, so keep the attribute names, the order and the quoting exactly as shown:

```
<!-- coverage:summary checks="<total rows>" ok="<OK rows>" critical="<CRITICAL rows>" na="<N/A rows>" unchecked="<rows with an empty verdict>" -->
```

Write the ledger even when the round finds nothing. A round reporting zero criticals is only believable with a full table behind it, and that table is what lets the next round move on to the lower severities instead of sweeping for criticals again.

=== THE PREVIOUS ROUND'S COVERAGE LEDGER ===

${previous_coverage_ledger}

Rules for using it — follow all five:

1. RE-DERIVE, DO NOT TRUST. Build Part 1 from the spec and the overlay again this round. A previous ledger tells you what was checked, not what is true: if it is missing a screen the spec has, that omission IS the miss you are hunting.
2. RE-LIST EVERY ROW. This round's ledger has to be complete on its own. A row whose subject and inputs are unchanged since the previous round, and whose verdict was OK, may be carried over without re-arguing it — but it must still appear, with its verdict, or the counts mean nothing.
3. NEW SURFACE, NEW ROWS. Everything the overlay has added since that ledger — a screen, a field, an endpoint, a rule, a state — is inventory now, and every axis that quantifies over it gets its own row. This is where a decision that added something without wiring it in becomes visible.
4. AN UNCHECKED ROW IS THE FIRST THING TO CHECK. Any row the previous ledger left with an empty verdict is this round's highest priority.
5. NOTHING DROPS OUT SILENTLY. Re-deriving the inventory is what keeps a round honest about the spec; it is not licence to lose a finding. Anything the previous round raised is accounted for in Part 3 above, one row each, whatever the re-derived inventory looks like.

=== END COVERAGE LEDGER ===

For EVERY finding (${finding_scope}), wrap it in HTML comment markers and write it using exactly this structure and order, so it can also be read/answered through the clarification-answer UI:

```
<!-- clarify:item id="<unique-id>" severity="critical|major|minor" -->
### <short title for the finding>

**Where:** — `<path/to/file.md>` — the section/location in that PRD file the finding refers to

<one short paragraph: what the spec actually says there that is wrong, conflicting, or missing>

<one short paragraph: what breaks because of it>

**Question:** — the specific question that needs resolving

**Recommendation:** — your recommended resolution, placed immediately below the question (NOT collected into a separate "Recommendations" section at the end of the document)

**Your answer:**
<!-- clarify:answer-start -->

<!-- clarify:answer-end -->
<!-- clarify:enditem -->
```

Writing style — a finding is read in a review UI, not in a report, so keep every part short, plain, and to the point:
- SHORT PARAGRAPHS, NEVER A WALL OF TEXT. Every paragraph is at most 3 sentences (~50 words). If a part needs more than that, split it into another paragraph — do not keep extending one.
- BLANK LINE BETWEEN PARAGRAPHS. Separate every paragraph with one truly empty line, and put a blank line before and after each `**Where:**` / `**Question:**` / `**Recommendation:**` label. Consecutive lines with no blank line between them are rendered as a single block of text, which is exactly what makes findings unreadable.
- Where: start the line with the PRD file's path relative to `${sources.prd}`, in backticks (comma-separate them if the finding genuinely spans more than one file), then keep the location itself to one line (just the section/step names). Write every requirement, rule or decision id exactly as the PRD itself writes it — the review UI turns those into links straight to the line that defines them, and a reworded or abbreviated id won't resolve. Then the two paragraphs above — what's wrong, then what it breaks. Quote at most a short phrase from the PRD to pin it down; do not reproduce whole passages, do not narrate your reasoning, and do not walk through worked numeric examples unless one number IS the contradiction.
- Question: exactly one question, one sentence. If it is a choice between options, put the one sentence first and then list the options as bullets (`- Option A — ...`), one line each.
- Recommendation: lead with the resolution itself in one sentence — the concrete text or rule to adopt. If a reason is needed, add it as a second short paragraph, not as a clause bolted onto the first.
- MAKE EVERY RECOMMENDATION SELF-CONTAINED. It may be accepted verbatim, at which point it IS the specification — so run the four checks in the section below over it before you write it into the file.
- Cut anything that does not change what the reader has to decide: no restating the title, no summarizing the PRD, no "as noted above", no hedging.

=== BEFORE YOU WRITE A RECOMMENDATION DOWN ===

A recommendation is usually accepted verbatim — the answer UI's "Follow the recommendation" is one click — at which point EVERY word of it, the reason paragraph included, is written into the PRD as specification and judged next round by the axes above. Most findings in a late round are not holes in the original spec; they are holes an earlier round's recommendation opened. Run all four checks below over your own text before you write it into the file.

1. ANSWER YOUR OWN QUESTION, AND NOTHING ELSE. Resolve the Question with the fewest new nouns — prefer choosing between things the spec already has over inventing a field, a setting, a message, a screen or an endpoint. Where two resolutions are equally correct, take the one that adds nothing. Never bundle a fix, a tidy-up or an improvement this finding's Question did not ask for: it is unreviewed surface, and it comes back as next round's finding.

2. THE REASON PARAGRAPH IS SPECIFICATION TOO. It may only restate rules that already exist, or that this same recommendation just stated — it may not introduce a rule of its own. In particular, never write a universal ("only X does Y", "always", "never", "every", "nothing else") unless that universal IS the resolution and you have checked every case it quantifies over, the overlay included. A universal dropped into a reason paragraph as reassurance is the most common way one decision silently forbids another.

3. COLLISION CHECK. List the surfaces your recommendation touches — every named rule, message string, enumeration, field, endpoint, guard, screen and state. For each one, search the overlay above AND the other findings in this file for a decision that already sets it. If one exists, quote it and write your recommendation as a delta that preserves it; if it genuinely must be overridden, say which decision is superseded and restate that decision's surface in full, so nothing of it is lost by omission. Two findings in this file that touch the same surface are one finding — merge them.

4. CLOSE WHAT YOU ADD, inside the same recommendation:
- a new field — what writes it, what reads it, and what it holds on rows that already exist and on every path that creates one, including paths another decision added;
- a new endpoint — its role guard, its navigation entry, which parameters are optional, what an absent or empty parameter returns, and the order and limit of what it returns;
- a rule keyed on an enum, a type or a state — one clause per member, the default value and the empty case included;
- a new message or label — that no other decision words the same guard differently, and that the wording stays true for every reason the guard can fire;
- a guard on one endpoint or screen — every sibling of the same class named in the same sentence, not only the one this finding is about;
- a changed rule — every enumeration, list, flow or acceptance criterion stated elsewhere that has to change with it, and a check that it does not remove the only route to something the spec still needs.

=== END RECOMMENDATION CHECKS ===

Rules:
- `id` must be short and unique within the file (e.g. `C1`, `M1`, `N1`, ...).
- Do not create a separate "Recommendations" section — each finding must carry its own recommendation directly beneath its question.
- Leave the space between `<!-- clarify:answer-start -->` and `<!-- clarify:answer-end -->` blank (a few empty lines), so a human reviewer — or the clarification-answer UI — can write an answer there, overriding the recommendation.
- Keep every `clarify:` HTML comment marker exactly as shown, including on re-runs — the answer UI depends on them to locate and update each finding. The answer UI may itself write `<!-- clarify:answer-start mode="recommendation" -->` (with an empty body) when a human chooses "Follow the recommendation" instead of typing an answer — treat that the same as any other answered finding; do not alter or remove the `mode` attribute yourself.

Save the results as a NEW file in ${sources.clarifications} — name it `clarification-<YYYYMMDD-HHMMSS>.md` using the current date/time, so it doesn't collide with any file already in that folder.

Do NOT delete, overwrite, or modify any other file already present in ${sources.clarifications} — every past round's file is a record the user still needs to see and must be left exactly as it is, answers included.

After that, update the "last_clarification_findings" property in ${config_path} with the number of findings that are still critical, major, and minor
