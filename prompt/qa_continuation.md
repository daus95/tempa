Continue the interrupted QA session for ${epic}.

Resume the review from the last feature that was checked. Finish evaluating all remaining features.
If there is API testing (curl) or Web UI testing (Playwright) left unfinished, continue that testing as well.

When done:

IF THERE ARE ANY ❌ OR ⚠️ ITEMS:
a. You MUST write the QA report to EXACTLY this path (do not write to any other location):
   ${qa_output_file}
b. UPDATE ${config_path} — READ first, then EDIT:
   - Set "status" AT THE EPIC LEVEL to "require_fixing"
   - Change the affected feature(s) to "require_fixing"
   - Recalculate "completed_features"
   - Set "qa_status" to "done"
   - DO NOT change "qa_passed"

IF ALL ✅:
a. UPDATE ${config_path} — READ first, then EDIT:
   - Set "qa_passed" to true
   - Set "qa_status" to "done"

Source:
${sources}
