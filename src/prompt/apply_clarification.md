Read exactly these clarification result file(s) — every other file already in ${sources.clarifications} has been fully applied to the PRD/spec in a previous apply pass, so it does NOT need to be read again:
${clarification_files}

The files above are listed in chronological order, oldest round first. If two findings across these files decide the SAME point, the one from the LATER file wins on that point — apply only the later decision there, and do not merge the two.

That precedence is narrow, and reading it widely is how an apply pass destroys a decision nobody meant to reverse. A later decision wins only on the point it actually decides. Where its wording ALSO negates something an earlier decision established that the later one does not itself re-decide — most often a sweeping clause in a reason paragraph, an "only X does Y", a "never", an "always" — do NOT apply it as written: apply the narrowest reading that keeps both decisions true, and say in your final response which sentence you narrowed and how.

If two findings in the SAME file contradict each other, do not apply either silently: pick the one that keeps the rest of the specification consistent, and say explicitly in your final response which one you dropped and why.

For each finding, use the content written under its "Your answer:" label if present; otherwise fall back to the finding's own "Recommendation:" line.

Apply every resulting resolution to the related PRD and technical spec documents in the folder: ${sources.prd}

Do NOT delete, rename, or modify the clarification result files themselves (in ${sources.clarifications}) — they are a historical record the user needs to keep seeing, even after their findings are fully resolved. Only ever write to the PRD/spec documents and ${config_path}.

This pass may be compacting several rounds' worth of decisions at once. Rewrite each affected PRD section ONCE, to its final state, rather than layering successive edits on top of each other.

Repeat the following steps (LOOP) until there are no more critical or major findings:

STEP 1 — APPLY:
- For every critical or major finding: change/add/remove the conflicting section in the relevant PRD/spec document
- Make sure the changes are consistent across documents (do not introduce new conflicts)
- Do not change the structure or format of documents that don't need to be changed

STEP 2 — RE-VERIFY:
- After all changes are applied, re-read the documents that were changed
- Check whether any conflicts or ambiguities remain
- If critical or major findings remain: go back to STEP 1
- If clean (no critical/major remaining): proceed to STEP 3

STEP 3 — UPDATE CONFIG:
- READ ${config_path} then EDIT:
  Update the "last_clarification_findings" property with the count of remaining findings:
  set critical=0, major=0 if everything is clean, or fill in with the actual remaining findings that truly cannot be resolved
