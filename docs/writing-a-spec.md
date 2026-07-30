# Writing a Specification

What to put in the PRD you upload in [Step 1 — Upload Specification](../README.md#step-1--upload-specification),
so Tempa spends less time asking clarifying questions and more time building.

## What it is

Your specification (PRD) describes the **new work** you want Tempa to build: what it does, for
whom, and roughly how. Tempa reads it during clarification (to find ambiguity), during planning
(to lay out epics/features/tasks), and during implementation (as the source of truth for what
"done" means).

## Where it lives

```
<workspace_root>/.tempa/specs/prd/
```

This is `sources.prd` (see [folders-and-paths.md](folders-and-paths.md)). A few things worth
knowing about it:

- **More than one file is fine.** Tempa reads every file in this folder together during
  clarification and planning — split a large specification into a few focused files if that's
  easier to write and review than one huge document.
- **Only the new work goes here.** This is not a place to describe the system as it already
  exists — that lives separately in `sources.docs` (the `docs/` folder), and planning reads it
  automatically as context so it doesn't re-plan work that's already done.

## The five things a good specification covers

1. **Purpose** — the problem solved, for whom, and any hard constraints ("client-side only, no
   backend").
2. **Business Process / Usage Flow** — the actual sequence of steps, numbered, with validation
   rules and edge cases called out explicitly rather than assumed obvious.
3. **Data Model** — the main entities, their fields, and how they relate. A plain list is enough;
   an ERD is welcome for anything with more than a few related entities.
4. **UI Concept** — the layout: what's on screen, how it's arranged, what changes on mobile vs.
   desktop. A written description is enough — no mockup required.
5. **Tech Stack** — language, framework, database (or explicitly "no backend/no database"). Leave
   it blank and it doesn't skip a decision, it just means Tempa picks one for you.

Two more sections consistently cut down on clarification questions, though they're not strictly
required: **Non-Goals** (what's explicitly out of scope) and **Acceptance Criteria** (a few
concrete input → expected-output examples).

```markdown
# PRD — <Feature or App Name>

## 1. Purpose
## 2. Business Process / Usage Flow
## 3. Data Model
## 4. UI Concept
## 5. Tech Stack
## 6. Non-Goals
## 7. Acceptance Criteria
```

The dashboard's **Learn more** page (`/spec-guide` while the dashboard is running, linked from the
Upload Specification step) walks through each of these with worked examples and the mistakes to
avoid.

## Just want to try Tempa first?

You don't have to write a specification from scratch to test-drive Tempa. The
[examples/](../examples/) folder ships two ready-to-use sample PRDs — a simple client-side
calculator and a full web app with a database — written following exactly the structure above.
Upload one via **Add File** on the Upload Specification step, or copy it straight into
`sources.prd`, and go straight into clarification with a specification that's already in good
shape. See [examples/README.md](../examples/README.md).
