Read all clarification result files in the folder: ${sources.clarifications}

For EVERY clarification finding/question in these files, check whether the blank line(s) under that finding's "Your answer:" label already contain a human-written answer.

IF a finding's "Your answer:" section is EMPTY:
- Determine the best answer/resolution based on analysis of the PRD document at ${sources.prd} (use the finding's own "Recommendation:" line as your starting point, if present).
- WRITE that answer directly into the blank line(s) under that finding's "Your answer:" label, between the `<!-- clarify:answer-start -->` / `<!-- clarify:answer-end -->` markers if present — keep those markers intact, don't remove or rename them.
- DO NOT change or overwrite "Your answer:" sections that already contain a human-written answer.

IF ALL findings already have an answer:
- DO NOT modify any file.

When done, READ ${config_path} then EDIT: set the "last_auto_answer" property = the number of findings you NEWLY answered in this session (0 if there were no findings to answer).
