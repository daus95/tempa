Re-evaluate all PRD documents at (${sources.prd}) to check whether any specifications are still conflicting or need clarification. The PRD may consist of multiple document files — check all of them.

For EVERY finding (critical, major, or minor), wrap it in HTML comment markers and write it using exactly this structure and order, so it can also be read/answered through the clarification-answer UI:

```
<!-- clarify:item id="<unique-id>" severity="critical|major|minor" -->
### <short title for the finding>

**Where:** — the section/location in the PRD the finding refers to

<a short paragraph describing the issue and its impact>

**Question:** — the specific question that needs resolving

**Recommendation:** — your recommended resolution, placed immediately below the question (NOT collected into a separate "Recommendations" section at the end of the document)

**Your answer:**
<!-- clarify:answer-start -->

<!-- clarify:answer-end -->
<!-- clarify:enditem -->
```

Rules:
- `id` must be short and unique within the file (e.g. `C1`, `M1`, `N1`, ...).
- Do not create a separate "Recommendations" section — each finding must carry its own recommendation directly beneath its question.
- Leave the space between `<!-- clarify:answer-start -->` and `<!-- clarify:answer-end -->` blank (a few empty lines), so a human reviewer — or the clarification-answer UI — can write an answer there, overriding the recommendation.
- Keep every `clarify:` HTML comment marker exactly as shown, including on re-runs — the answer UI depends on them to locate and update each finding.

Save the results to ${sources.clarifications}

After that, update the "last_clarification_findings" property in ${config_path} with the number of findings that are still critical, major, and minor
