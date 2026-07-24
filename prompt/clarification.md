Re-evaluate all PRD documents at (${sources.prd}) to check whether any specifications are still conflicting or need clarification. The PRD may consist of multiple document files — check all of them.

For EVERY finding (critical, major, or minor), write it using exactly this structure and order:
1. `**Where:**` — the section/location in the PRD the finding refers to
2. A short paragraph describing the issue and its impact
3. `**Question:**` — the specific question that needs resolving
4. `**Recommendation:**` — your recommended resolution, placed immediately below the question (NOT collected into a separate "Recommendations" section at the end of the document)
5. `**Your answer:**` followed by a few empty lines, so a human reviewer can write their own answer in place of (or overriding) the recommendation

Do not create a separate "Recommendations" section — each finding must carry its own recommendation directly beneath its question, followed by its own blank "Your answer:" lines.

Save the results to ${sources.clarifications}

After that, update the "last_clarification_findings" property in ${config_path} with the number of findings that are still critical, major, and minor
