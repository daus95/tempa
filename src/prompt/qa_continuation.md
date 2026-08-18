Continue the interrupted QA session for ${epic}.

Resume the review from the last feature that was checked. Finish evaluating all remaining features.
If there is API testing (curl) or Web UI testing (Playwright) left unfinished, continue that testing as well.

${previous_qa_findings}
EVALUATION RESULT — THREE LEVELS, ONLY TWO OF THEM BLOCK:

  ❌ BLOCKING — not implemented at all, or it fails when you actually build/run/test it.

  ⚠️ BLOCKING — implemented, but its OBSERVABLE BEHAVIOUR OR CONTRACT differs from the
     specification. To use this level you must be able to say concretely what goes wrong at
     run time, and for whom.

  📝 ADVISORY — the behaviour is correct and you verified it, but you would still like
     something improved: a passing test whose name doesn't literally match a "How to test"
     bullet, extra coverage for behaviour that is already correct, a guarantee that holds
     structurally but has no test named after it, or a naming/structure/documentation
     suggestion. THIS IS NOT A QA FAILURE. It never changes a feature's verdict and never
     marks a feature "require_fixing".

  The test suite is evidence, not the deliverable. If the behaviour the specification requires
  is present and you verified it works, the feature is ✅. "I would have written another test
  here" is 📝, never ⚠️.

  A feature is ✅ when it has no ❌ and no ⚠️ item. Any number of 📝 items still leaves it ✅.

When done:

a. You MUST write the QA report to EXACTLY this path (do not write to any other location):
   ${qa_output_file}
   Include a clearly separated "Advisory notes (non-blocking)" section for every 📝 item.

b. UPDATE ${config_path} — READ first, then EDIT:

   IF ANY FEATURE HAS AN ❌ OR ⚠️ ITEM:
   - Set "status" AT THE EPIC LEVEL to "require_fixing"
   - Change the affected feature(s) to "require_fixing" — a feature whose only findings are
     📝 advisory is NOT affected and keeps its current status
   - Recalculate "completed_features"
   - Set "qa_status" to "done"
   - DO NOT change "qa_passed"

   IF EVERY FEATURE IS ✅ (advisory 📝 notes may still exist — they do not block):
   - Set "qa_passed" to true
   - Set "qa_status" to "done"
   - Set EVERY feature in the "features" array to "status": "done" — a previous failed QA
     round may have left some marked "require_fixing", and you just verified they all pass
   - Set "completed_features" = the total number of features in the array

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
