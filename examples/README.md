# Example Usage Scenarios for Tempa

This folder contains sample specifications (PRDs) for different levels of application
complexity, as a reference for the PRD format that works well with Tempa (see [Step 1 —
Write the specification](../README.md#step-1--write-the-specification) in the main README).

| # | Scenario | Complexity | DB/API Access |
|---|----------|------------|----------------|
| [01-simple-web-app](01-simple-web-app/PRD.md) | Mortgage (KPR) Installment Simulator (client-side) | Medium | No — no backend/API, state lives in the browser only |
| [02-web-app-with-db](02-web-app-with-db/PRD.md) | Retail Store POS & Inventory System | Advanced | Yes — web app with a backend + relational database (ERD included) |

## How to use these examples

1. Pick the scenario closest to your needs, or use it purely as a reference for writing
   style.
2. Copy the contents of the relevant scenario's `PRD.md` into the `sources.prd` folder
   (default: `specs/prd`) inside your target project (the project you already ran `--init`
   on — not this Tempa folder).
3. Continue with Tempa's normal workflow: `py tempa.py --clarify` → answer the clarification
   questions → `py tempa.py --implement`.

Both PRDs are deliberately written in full (Purpose, Business Process, Data Model, UI
Concept, Tech Stack), following the five aspects recommended in the main README, so that
Tempa's clarification process stays short.
