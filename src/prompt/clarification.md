Re-evaluate all PRD documents at (${sources.prd}) to check whether any specifications are still conflicting or need clarification. The PRD may consist of multiple document files — check all of them.

=== ALREADY-DECIDED RESOLUTIONS NOT YET WRITTEN INTO THE PRD ===

The decisions below were raised in earlier clarification rounds and have already been answered by the user (or on the user's behalf). They have NOT yet been written into the PRD documents. Treat this block as an AUTHORITATIVE OVERLAY on top of the PRD: wherever a decision below contradicts what the PRD files currently say, THE DECISION BELOW IS THE TRUTH and the PRD text is simply stale.

${pending_resolutions}

Rules for using this overlay — follow all four:

1. ALREADY DECIDED. Do NOT raise a new finding for anything the overlay already settles, and do NOT re-ask a question it already answers, even if the PRD text still reads the old way. Those points are closed.
2. LAST ONE WINS. The rounds above are in chronological order, oldest first. If two decisions cover the same point, the one in the LATER round supersedes the earlier one completely. Do not merge them and do not average them.
3. CONTRADICTION IS A FINDING. If two decisions in the SAME round contradict each other, or a decision contradicts something in the PRD that it does not itself resolve (i.e. applying it would break a different, still-valid part of the spec), RAISE THAT AS A NEW FINDING using the format below. Quote both sides. This is the one case where the overlay produces findings rather than suppressing them.
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
- Cut anything that does not change what the reader has to decide: no restating the title, no summarizing the PRD, no "as noted above", no hedging.

Rules:
- `id` must be short and unique within the file (e.g. `C1`, `M1`, `N1`, ...).
- Do not create a separate "Recommendations" section — each finding must carry its own recommendation directly beneath its question.
- Leave the space between `<!-- clarify:answer-start -->` and `<!-- clarify:answer-end -->` blank (a few empty lines), so a human reviewer — or the clarification-answer UI — can write an answer there, overriding the recommendation.
- Keep every `clarify:` HTML comment marker exactly as shown, including on re-runs — the answer UI depends on them to locate and update each finding. The answer UI may itself write `<!-- clarify:answer-start mode="recommendation" -->` (with an empty body) when a human chooses "Follow the recommendation" instead of typing an answer — treat that the same as any other answered finding; do not alter or remove the `mode` attribute yourself.

Save the results as a NEW file in ${sources.clarifications} — name it `clarification-<YYYYMMDD-HHMMSS>.md` using the current date/time, so it doesn't collide with any file already in that folder.

Do NOT delete, overwrite, or modify any other file already present in ${sources.clarifications} — every past round's file is a record the user still needs to see and must be left exactly as it is, answers included.

After that, update the "last_clarification_findings" property in ${config_path} with the number of findings that are still critical, major, and minor
