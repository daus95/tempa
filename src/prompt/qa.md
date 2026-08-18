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

${previous_qa_findings}
EVALUATION RESULT — THREE LEVELS, ONLY TWO OF THEM BLOCK:

  ❌ BLOCKING — not implemented at all, or it fails when you actually build/run/test it.

  ⚠️ BLOCKING — it is implemented, but its OBSERVABLE BEHAVIOUR OR CONTRACT differs from the
     specification. To use this level you must be able to say concretely what goes wrong at
     run time, and for whom. Examples: it computes the wrong figure; it returns the wrong
     status code; a published event's payload doesn't match its own declared schema, so a
     consumer reads nothing; a rule the spec states is not enforced anywhere; an endpoint or
     screen the spec requires does not exist.

  📝 ADVISORY — the behaviour is correct and you verified it, but you would still like
     something improved. THIS IS NOT A QA FAILURE. It never changes a feature's verdict and
     never marks a feature "require_fixing". Use it for:
       - a test that exists and passes, but whose name or shape doesn't literally match the
         wording of a "How to test" / Definition-of-Done bullet
       - additional test coverage you would like for behaviour that is already correct and
         already covered, directly or indirectly
       - a guarantee that currently holds structurally but has no test named after it
       - naming, structure, documentation or refactoring suggestions
       - a deviation from the spec's literal wording that is deliberate, documented, and whose
         correctness you confirmed

  The test suite is evidence, not the deliverable. If the behaviour the specification requires
  is present and you verified it works, the feature is ✅ — even when you can imagine a test
  that would prove it more directly, and even when a DoD bullet's exact phrasing has no test
  named after it. "I would have written another test here" is 📝, never ⚠️.

  A feature is ✅ when it has no ❌ and no ⚠️ item. Any number of 📝 items still leaves it ✅.

REPORT — always write it, whatever the verdicts:
You MUST write the QA report to EXACTLY this path (do not write to any other location):
   ${qa_output_file}
Report format:
   - Title: QA Report — ${epic}
   - Date
   - Summary: total ✅/⚠️/❌, and separately the number of 📝 advisory notes
   - Per-feature detail (include curl / Playwright output if tested)
   - A clearly separated "Advisory notes (non-blocking)" section for every 📝 item, so the next
     round can see they were already raised and deliberately not treated as failures
   - Recommended fixes — blocking items only

THEN UPDATE ${config_path} — READ first, then EDIT:

IF ANY FEATURE HAS AN ❌ OR ⚠️ ITEM:
   - Find the entry with "epic_name": "${epic}" in the "epic" array
   - Change "status" AT THE EPIC LEVEL to "require_fixing"
   - For every feature that has an ❌ or ⚠️ item: change its "status" to "require_fixing"
   - Do NOT change the status of a feature whose only findings are 📝 advisory
   - Recalculate "completed_features" = the number of features in the array still marked "done"
   - Set "qa_status" to "done"
   - DO NOT change "qa_passed" (leave it false)

IF EVERY FEATURE IS ✅ (advisory 📝 notes may still exist — they do not block):
   - Find the entry with "epic_name": "${epic}" in the "epic" array
   - Set "qa_passed" to true
   - Set "qa_status" to "done"
   - Set EVERY feature in the "features" array to "status": "done" — a previous failed QA
     round may have left some marked "require_fixing", and you just verified they all pass
   - Set "completed_features" = the total number of features in the array

IMPORTANT: You MUST update ${config_path} (set qa_status="done") under one of the two conditions above.
Otherwise, the agent runner will detect that QA is still ongoing and try to resume this session.

FIELDS YOU MUST NEVER WRITE in ${config_path}:
   "qa_history", "qa_loop_strikes", "blocked_reason", "total_run", "qa_total_run",
   "no_progress_rounds", "last_round_note", "last_round_note_kind", "cut_short_rounds",
   "ended_waiting_halts"
These belong to the agent runner, which maintains them itself once your session ends. Writing
them corrupts its record of how many QA rounds this epic has actually had — an extra
"qa_history" entry duplicates the round you are working on right now, and two rounds with an
identical failing set is exactly what the runner reads as "this epic is going in circles",
which halts the whole run. Change only the fields listed above.

Source:
${sources}
