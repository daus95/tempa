TASK: Review the epics/features/tasks that were JUST created and FIX any issues directly.

STEP 1 — RE-READ:
1. The new specification in the PRD ${sources.prd} and the current system documentation at ${sources.docs}.
2. The new epics produced at ${sources.epics} and the new epic entries in ${config_path}.
3. The actual code at ${sources.apps}.

STEP 2 — CHECK QUALITY:
a. COVERAGE — is there ANY new specification in the PRD (${sources.prd}) that was MISSED (not covered by any epic/feature)? Compare the PRD vs. the current system documentation (${sources.docs}) / code (${sources.apps}) vs. the new epics. Add epics/features if anything was missed.
b. FEATURE SIZE — is any feature too LARGE for a single Claude session (< 300K tokens)? If so, SPLIT it into multiple features and adjust the numbering + config.json accordingly.
c. TESTABILITY — does every feature have a clear way to test it (CURL/CLI/Playwright)? If not, ADD one.
d. PARALLELISM — are the task ordering & parallelism notes sensible and consistent with their dependencies?
e. CONSISTENCY — epic/feature/task numbering doesn't clash with existing ones; file format follows the existing epic template.

STEP 3 — FIX:
Directly edit the problematic .md files in ${sources.epics} AND the entries in ${config_path} (add/split/fix as needed). Make sure "total_features" in each config.json entry matches the actual number of features in its epic file. DO NOT change/remove old epics that are already complete.
