Read every clarification result file in the folder: ${sources.clarifications} (there may be several, one per past clarification round — read them all, not just the most recent).

For each finding, use the content written under its "Your answer:" label if present; otherwise fall back to the finding's own "Recommendation:" line.

Apply every resulting resolution to the related PRD and technical spec documents in the folder: ${sources.prd}

Do NOT delete, rename, or modify the clarification result files themselves (in ${sources.clarifications}) — they are a historical record the user needs to keep seeing, even after their findings are fully resolved. Only ever write to the PRD/spec documents and ${config_path}.

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
