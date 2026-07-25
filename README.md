# Tempa — Claude Automation Harness

> A long-running agent harness for building software from ideas.

Official repo: [github.com/daus95/tempa](https://github.com/daus95/tempa)

Tempa is a harness that runs the **Claude CLI** automatically and repeatedly to build an
application from a specification: clarifying the PRD, drafting a plan (epic/feature/task),
implementing it, and running QA — all without needing to be watched.

All state is stored in [`config.json`](config.json). All prompt templates are stored as
`.md` files in the [`prompt/`](prompt/) folder so they're easy to read and edit.

> Every interaction is a CLI command: `py tempa.py <command>`.

---

## Setup (One-Time Only)

> Done once per machine/project. Once this is done, the recurring work happens in the
> [Workflow (End-to-End)](#workflow-end-to-end) section below.

### Step 1 — Prerequisites

1. **Python 3** installed (`py` / `python` on PATH).
2. **Claude CLI** installed and on PATH (`claude` or `claude.cmd`).
   The harness calls it with `--dangerously-skip-permissions` (fully automated mode, no
   human confirmation).
3. Already logged in / authenticated with Claude.

---

### Step 2 — Download & Install

Download the Tempa code as a ZIP file (no clone needed), then extract it to the folder you
want:

[⬇ Download tempa.zip](https://github.com/daus95/tempa/archive/refs/heads/main.zip)

Put the extracted contents in a folder **separate from, and outside**, the repo you're going
to work on — don't put it inside your repo folder, so it doesn't get committed to your git
repo. The `tempa.py`, `config.json`, and `prompt/` structure lives directly inside that
`tempa` folder. For example, if your repo is at `C:\repo\<your-repo>`, place Tempa as a
sibling folder, e.g. `C:\tools\tempa` or `C:\repo\tempa` (not `C:\repo\<your-repo>\tempa`):

```
C:\repo\<your-repo>\      (your repo — left untouched)
C:\tools\tempa\           (Tempa folder — separate)
├─ tempa.py
├─ config.json
└─ prompt\
```

Quickly check everything is ready before moving to the next step:

```bash
py tempa.py test            # verifies the Claude CLI can Write/Read/Delete a file
```

---

### Step 3 — Initial Setup

Point Tempa at the project you want to work on with `init`, passing the (absolute) path
to your project folder:

```bash
py tempa.py init C:\repo\<your-repo>
```

This command will:

- Save the project location (`workspace.root`) to `config.json`.
- Create the standard working folders in your project if they don't already exist — one of
  them being the `specs` folder, where you'll place the specification you want to work on.

> Run this once per project. Safe to re-run — folders that already exist on disk are not
> re-created, and their contents are never overwritten.

What you need to know at this point: **put your new specification in the `specs` folder**
(default: `specs/prd`) inside that project. Full details on the working-folder structure and
AI model configuration are in [docs/folders-and-paths.md](docs/folders-and-paths.md) and
[docs/ai-models.md](docs/ai-models.md) — not required reading to get started.

---

## Workflow (End-to-End)

> Prerequisite: the **Setup (One-Time Only)** section above has been done.

```
┌───────────────────────────┐
│  1. Write Specification    │
└─────────────┬──────────────┘
              │
              ▼
┌───────────────────────────┐
│  2. Answer Clarifications  │  (loop: evaluate → answer → apply, until clean)
└─────────────┬──────────────┘
              │
              ▼
┌───────────────────────────┐
│  3. Run Implementation     │  (loop per epic: feature → QA → fixes)
└───────────────────────────┘
```

### Step 1 — Write the specification

Save it in the `sources.prd` folder (default `specs/prd`) — and **only** that: the **new**
specification you want implemented. Don't put old/already-implemented specifications here.

Specifications for the **existing** system should be kept separately in the `sources.docs`
folder (default `docs/`) — not rewritten inside `specs/prd`. Plan drafting (part of Step 3)
uses `docs/` (and the code) as a reference for "what already exists", so it doesn't
duplicate work that's already done.

The new specification should ideally cover:

- **Purpose of the application** — what problem it solves, for whom.
- **Business process** — the workflow/steps the system needs to support.
- **Data model** — the main entities and their relationships.
- **UI concept** — a picture of the pages/interactions you want.
- **Tech stack** you want (language, framework, database, etc).

The more completely these five aspects are written up, the closer the resulting application
will match what you actually want.

Not sure how to start, or just want to try Tempa out first? The [examples/](examples/)
folder has ready-to-use sample PRDs (from a simple client-side app to a full web app with a
database) you can copy straight into `specs/prd` — see [examples/README.md](examples/README.md).

### Step 2 — Answer clarifications (`clarify`)

The goal: the system asks about anything ambiguous/unclear in the PRD, you answer, and those
answers get applied back into the PRD — repeated until there are no more `critical`/`major`
findings. There are two ways to answer; both are used in sequence as part of the same flow,
not as alternatives to pick from.

#### A. Answer manually — during the early iterations

```bash
py tempa.py clarify          # evaluate: system writes questions + recommended answers to a file
```

Once the evaluation finishes, Tempa opens the **clarification-answer web UI** on the result
file so you can answer right there (add `--noui` to skip it) — saving in the UI immediately
applies your answers back into the PRD.

Prefer editing the markdown file by hand instead? That still works: edit the result file
yourself, then run `py tempa.py clarify --apply` to apply it back into the PRD. Re-open the
UI anytime with `py tempa.py answer` — no file argument needed: it scans
`sources.clarifications` for every result file and, as long as at least one still has an
unanswered finding, opens them **all** at once (one tab per file, badged complete/incomplete)
so you never have to hunt down which file still needs an answer.

After answers are applied (from the UI, or via `--apply`), Tempa asks whether to run another
clarification round right away — answer `y` to loop straight back into `clarify`, or `N` to
stop and review manually. (Only asked in an interactive terminal — non-interactive runs just
exit.)

**When:** during the early iterations (usually the first 3–4 rounds).
**Why:** early on, the system doesn't yet have enough project context to guess your intent
accurately — its recommended answers can still be off. Important decisions (application
purpose, business process, tech stack, etc.) need your direct input first, so the PRD
starts off pointed in the right direction.

#### B. Answer automatically — once recommendations prove accurate

```bash
py tempa.py clarify --finalize
```

**When:** after a few manual rounds, once you notice the system's recommended answers over
the last 2 iterations are already consistent/accurate with what you intend (this usually
starts showing around iteration 4 or 5).
**Why:** by that point the system has enough context (from the PRD plus prior answers) that
its recommendations can be trusted — continuing manually would just be repeating work with
the same outcome. `--finalize` runs evaluate + answer + apply in a single automatic loop
until clean, without you having to answer one by one anymore.

`--finalize` stops as soon as there are no more `critical`/`major` findings — some `minor`
findings may still remain, and that's **fine**: minor findings will be resolved anyway
during implementation (Step 3). So once `--finalize` finishes, you can move straight on to
implementation without needing to chase down the remaining minor findings.

Full reference for every mode (`clarify` manual, `--auto-answer`, `--apply`, `--finalize`):
see [docs/clarify-modes.md](docs/clarify-modes.md).

### Step 3 — Run implementation (`implement`)

```bash
py tempa.py implement
```

Just run this. The system will (automatically, unattended): draft a plan if there's no task
yet → implement features one by one → run QA once an epic is done → fix any findings →
move on to the next epic, until everything is done.

Full details (the `--replan`/`--features` flags, work priority, monitoring progress,
recovering from problems, manual verification): see
[docs/start-implementation.md](docs/start-implementation.md).

---

## Further Reference

> Not required reading to get started — see Setup Step 3 & Workflow above first.

- **Folder & Path Structure** — what a working folder is, `workspace.*`, `sources.*`:
  [docs/folders-and-paths.md](docs/folders-and-paths.md)
- **AI Model per Stage** — why it's differentiated per stage, default table, how to change it:
  [docs/ai-models.md](docs/ai-models.md)
- **Command Reference** — full list of every command:
  [docs/command-reference.md](docs/command-reference.md)
- **`config.json` structure** — every key and what it does:
  [docs/config-json.md](docs/config-json.md)
- **Prompt templates** (the `prompt/` folder) — file list & how to customize harness behavior:
  [docs/prompt-templates.md](docs/prompt-templates.md)
- **Logs & output** — where logs for each session/stage live:
  [docs/logging.md](docs/logging.md)
