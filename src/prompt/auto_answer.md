Read exactly these clarification result file(s) — every other file already in ${sources.clarifications} has every finding answered, so it does NOT need to be read again:
${clarification_files}

For EVERY clarification finding/question in these files, the blank line(s) under that finding's "Your answer:" label are EMPTY (that's why the file was selected).

For each one:
- Determine the best answer/resolution based on analysis of the PRD document at ${sources.prd} (use the finding's own "Recommendation:" line as your starting point, if present).
- WRITE that answer directly into the blank line(s) under that finding's "Your answer:" label, between the `<!-- clarify:answer-start -->` / `<!-- clarify:answer-end -->` markers if present — keep those markers intact, don't remove or rename them.

When done, READ ${config_path} then EDIT: set the "last_auto_answer" property = the number of findings you NEWLY answered in this session (0 if there were no findings to answer).
