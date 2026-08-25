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

CRITICAL FINDINGS COME FIRST, AND THEY MUST BE COMPLETE. Before evaluating anything else, make one full pass over every spec document hunting ONLY for critical findings, and do not begin writing the findings file until that pass is finished. Report EVERY critical it turns up — there is no cap, and no "reasonable report size" to stay within. A round that reports fewer criticals than the spec actually contains costs the user one more full round for each one it held back, so holding one back is the most expensive thing you can do here. Only once that pass is exhausted, look for the other severities this round is scoped to.

Run the critical pass along these axes, checking each one ACROSS THE WHOLE SPEC rather than section by section — most criticals live between two sections that each read fine on their own:

1. Every capability a role is given has a screen (or endpoint) that role can actually reach, and that screen is listed in that role's navigation.
2. Every acceptance criterion has something in the spec that implements it — a screen, a rule, an entity, a field.
3. Every screen has the entities and fields it reads and writes.
4. Every entity and field has something that creates it and something that maintains it.
5. Every state transition names who can trigger it, from which screen, and under what guard.
6. Every rule names what enforces it and what the user sees when it is violated.

Do not assume the earlier rounds were exhaustive. A critical that follows from the spec text alone — one that needs no decision from the overlay above in order to see — should have been caught in an earlier round, and the fact that it is still here means an earlier round missed it. Assume such misses exist and hunt for them deliberately.

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
