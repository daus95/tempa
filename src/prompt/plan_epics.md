TASK: Study the NEW specification in the PRD, then lay out Epics, Features, and Tasks to implement it.

IMPORTANT NOTE ABOUT SOURCES:
- The PRD (${sources.prd}) contains ONLY the NEW specification to be worked on — this is the work you must organize into epics.
- The CURRENT system documentation is in ${sources.docs} — this is the reference for "what ALREADY EXISTS in the system today" (not the PRD).

STEP 1 — STUDY THE SOURCES (MUST be read before planning):
1. Read the entire new specification & technical design in the PRD folder: ${sources.prd}
2. Read the current system documentation in the folder: ${sources.docs} — to understand the EXISTING system so the new epics are consistent and don't duplicate what's already there.
3. Read the EXISTING epics in the folder: ${sources.epics} — to learn the FORMAT CONVENTIONS, numbering, and scope already planned. Use one of the most recent epics there as the format TEMPLATE.
4. Read the "epic" array in ${config_path} — to see which epics are already completed/registered.
5. Check the actual code at: ${sources.apps} — to confirm the real implementation state.

STEP 2 — DETERMINE SCOPE:
Lay out epics/features/tasks for ALL new specifications in the PRD (${sources.prd}) that are not yet reflected in the current system documentation (${sources.docs}) or code (${sources.apps}). DO NOT create epics/features for things that already exist in the system. If the PRD contains no new work at all, DO NOT create any file — just report that there is no new work.

STEP 3 — LAY OUT EPIC / FEATURE / TASK:
- EPIC = a body of work carried out SEQUENTIALLY. Continue numbering from the HIGHEST existing epic number (check both the epics folder AND config.json). E.g. if the highest is EPIC-21 → the new epic starts at EPIC-22, etc.
- FEATURE = a body of work that can be completed within ONE agent session (roughly < 300K tokens). Features MUST be small enough; split them if too large. Every feature MUST be TESTABLE via CURL / CLI / Playwright so that if it doesn't match the spec it can be fixed — WRITE DOWN how to test it. Id format FEAT-NN-XX.
- TASK = a concrete breakdown of work within a feature. Id format TASK-NN-XX-YY. For each feature, CONSIDER the task execution order AND whether tasks can be done in PARALLEL (subagents) or must be SEQUENTIAL — write the parallelism/dependency notes explicitly.

STEP 4 — WRITE FILES:
For EVERY new epic, write one markdown file to the folder ${sources.epics} named: EPIC-NN-kebab-title.md
Follow EXACTLY the structure of existing epics (Goal, In/Out of Scope, Main Deliverables, Dependencies, Definition of Done, Key Spec References, then a "Features & Tasks" section with PARALLELISM NOTES at the top + each FEAT having 'Parallel with/Depends on/Description' and each TASK having Scope/Output/Reference/Prerequisites).

STEP 5 — UPDATE config.json (MANDATORY):
READ ${config_path} then EDIT: add an entry for EVERY new epic to the END of the "epic" array, with EXACTLY this structure:
{
  "epic_name": "EPIC-NN",
  "last_run": "",
  "status": "pending",
  "total_features": <number of features>,
  "completed_features": 0,
  "features": [ { "id": "FEAT-NN-01", "name": "...", "status": "pending" }, ... ],
  "response_message": "",
  "total_run": 0,
  "qa_passed": false,
  "qa_status": "idle",
  "qa_session_id": "",
  "qa_total_run": 0
}
Make sure "total_features" matches the number of elements in the "features" array. DO NOT change/remove existing epic entries.
