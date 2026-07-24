Perform Quality Assurance for ${epic}.

QA TASKS:
1. Read the ${epic} specification file from the epics folder (found inside ${sources})
2. Check every feature/requirement listed in the specification
3. Find its implementation in the source code and unit tests (found in ${sources})
4. Compare: has each feature been implemented according to the specification?
5. API TESTING — do this if this epic has an API endpoint:
   a. Check whether the application is already running. If not, start it and wait until it's ready.
   b. Use curl to test every endpoint listed in the specification.
   c. Verify: HTTP status code, response body, and behavior match the specification.
   d. Record the result per endpoint: ✅ matches / ❌ doesn't match / ⚠️ there's a discrepancy.
6. WEB UI TESTING — do this if this epic has a user interface:
   a. Use Playwright (browser_navigate, browser_click, etc.) to open the relevant pages.
   b. Test every UI interaction mentioned in the specification.
   c. Verify the appearance, flow, and behavior match the specification.
   d. Record the result: ✅ matches / ❌ doesn't match / ⚠️ there's a discrepancy.

EVALUATION RESULT:
For each feature, mark it with:
  ✅ Implemented according to spec and passes testing
  ❌ Not implemented, or failed during testing
  ⚠️ Incomplete implementation, discrepancy found, or testing shows behavior that doesn't match

IF THERE ARE ANY ❌ OR ⚠️ ITEMS (discrepancies found):
a. You MUST write the QA report to EXACTLY this path (do not write to any other location):
   ${qa_output_file}
   Report format:
   - Title: QA Report — ${epic}
   - Date
   - Summary: total ✅/⚠️/❌
   - Per-feature detail (include curl / Playwright output if tested)
   - Recommended fixes

b. UPDATE ${config_path} — READ first, then EDIT:
   - Find the entry with "epic_name": "${epic}" in the "epic" array
   - Change "status" AT THE EPIC LEVEL to "require_fixing"
   - For every feature that is ❌ or ⚠️: change its "status" to "require_fixing"
   - Recalculate "completed_features" = the number of features in the array still marked "done"
   - Set "qa_status" to "done"
   - DO NOT change "qa_passed" (leave it false)

IF ALL ITEMS ARE ✅ (no discrepancies):
a. UPDATE ${config_path} — READ first, then EDIT:
   - Find the entry with "epic_name": "${epic}" in the "epic" array
   - Set "qa_passed" to true
   - Set "qa_status" to "done"

IMPORTANT: You MUST update ${config_path} (set qa_status="done") under one of the two conditions above.
Otherwise, the agent runner will detect that QA is still ongoing and try to resume this session.

Source:
${sources}
