# Tempa — Claude Automation Harness

> A long-running agent harness for building software from ideas.

Official repo: [github.com/daus95/tempa](https://github.com/daus95/tempa)

Tempa is a harness that runs the **Claude CLI** automatically and repeatedly to build an
application from a specification: clarifying the PRD, drafting a plan (epic/feature/task),
implementing it, and running QA — all without needing to be watched.

All state is stored in [`config.json`](config.json). All prompt templates are stored as
`.md` files in the [`src/prompt/`](src/prompt/) folder so they're easy to read and edit.

> Once set up, the recommended way to drive Tempa is the web **dashboard**
> (`tempa dashboard`) — see [Dashboard (Recommended)](#dashboard-recommended) below. Every
> dashboard action also has an equivalent CLI command (`tempa <command>`), for power users
> or scripting.

---

## Setup (One-Time Only)

> Done once per machine. Once this is done, the recommended way to do the recurring work
> is the [Dashboard](#dashboard-recommended) section below — power users can use the CLI
> [Command Line Interface (CLI)](#command-line-interface-cli) section instead (its first step points
> Tempa at your project).

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
repo. The `tempa.py` launcher and `config.json` sit at the top of that `tempa` folder;
everything the tool ships with — all implementation modules, the prompt templates, and the
dashboard's assets — lives in the `src/` subfolder. Keep the whole folder together: the
launcher puts `src/` on the import path and the modules import each other by name, so nothing
works if they're separated. For example, if your repo is at `C:\repo\<your-repo>`, place Tempa
as a sibling folder, e.g. `C:\tools\tempa` or `C:\repo\tempa` (not `C:\repo\<your-repo>\tempa`):

```
C:\repo\<your-repo>\      (your repo — left untouched)
C:\tools\tempa\           (Tempa folder — separate)
├─ tempa.py               (launcher / entry point)
├─ tempa.cmd              (Windows launcher)
├─ tempa                  (macOS/Linux launcher)
├─ config.json            (per-project settings — edited by you)
└─ src\                   (everything the tool ships with)
   ├─ tempa_cli.py           (CLI dispatch)
   ├─ tempa_*.py             (config, logging, prompts, session,
   │                            implement, clarify, maintenance, commands)
   ├─ dashboard_*.py         (ui, server, config, spec, clarify_parse,
   │                            clarify_render, runs, winui, assets)
   ├─ prompt\                (prompt templates, one .md per prompt)
   └─ assets\                (dashboard.html / .css / .js)
```

Add that Tempa folder to your `PATH` so the `tempa` command works from anywhere:

- **Windows:** add the folder (e.g. `C:\tools\tempa`) to your user `PATH`, then open a new
  terminal. `tempa <command>` runs `tempa.cmd`, which calls `tempa <command>` internally.
- **macOS/Linux:** add the folder to your `PATH` (e.g. in `~/.zshrc` / `~/.bashrc`:
  `export PATH="$HOME/tools/tempa:$PATH"`), then open a new terminal. `tempa <command>` runs
  the `tempa` script, which calls `python3 tempa.py <command>` internally — no `chmod` needed,
  the executable bit is already set in the repo.

Prefer not to touch `PATH`? Run it directly instead, from inside the Tempa folder:
`./tempa.cmd <command>` (Windows) or `./tempa <command>` (macOS/Linux).

Quickly check everything is ready before moving to the next step:

```bash
tempa test            # verifies the Claude CLI can Write/Read/Delete a file
```

---

## Dashboard (Recommended)

> Prefer the terminal, or need to script Tempa? Every action below has a matching CLI
> command — skip ahead to [Command Line Interface (CLI)](#command-line-interface-cli).

The dashboard walks you through the same specification → clarification → implementation
flow as the CLI, but with buttons, inline file editing, and live progress instead of
memorizing commands and flags. Start it with:

```bash
tempa dashboard
```

This opens `http://127.0.0.1:<port>/` in your browser (`Ctrl+C` in the terminal stops the
server). If the project hasn't been set up yet, the **Home** page tells you to run
`tempa init <path>` first (Workflow Step 1 below) — that one step still needs the CLI.

The Home page is a 3-step checklist; each step unlocks once the one before it is satisfied:

```
┌───────────────────────────┐
│  1. Upload Specification    │
└─────────────┬──────────────┘
              │
              ▼
┌───────────────────────────┐
│  2. Clarification           │  (Start Clarification → answer → Apply Answers → repeat)
└─────────────┬──────────────┘
              │
              ▼
┌───────────────────────────┐
│  3. Start Implementation    │
└───────────────────────────┘
```

### Step 1 — Upload Specification

Click **Add File** or **Add Folder** to upload your PRD/spec documents straight from the
browser (they land in the same `sources.prd` folder `tempa init` created). Uploaded files
appear under **Specification** in the left sidebar — click one to **View** it as rendered
markdown, or switch to **Edit** and **Save** to change it right there, no separate editor
needed. Right-click a file or folder in the sidebar for **Rename** / **Delete**.

### Step 2 — Clarification

**Start Clarification** runs one evaluation pass and lists every finding (critical/major/
minor), grouped by file, under **Clarification** in the sidebar. Open a file to answer its
findings inline: for each finding, choose **Follow the recommendation** or **I'll write my
own answer** (a text box appears), then **Save**. Once a file is fully answered, click
**Apply Answers** to write those resolutions back into the PRD/spec — then run **Start
Clarification** again to confirm nothing critical remains. This loop (evaluate → answer →
apply → re-evaluate) is the dashboard version of Workflow Step 3 below.

**Finalized Clarification** automates the rest of that loop (evaluate + apply, repeating
until clean) — it stays disabled until a **Finalize readiness** panel shows all 3
conditions met: clarification has run at least once, the latest result came from **Start
Clarification** itself (not just **Apply Answers** — applying edits the PRD, not the
finding record, so a fresh evaluation is what actually confirms nothing critical is left),
and that evaluation shows 0 critical findings. Once it's enabled, click it to let Tempa
finish off any remaining major/minor findings on its own.

### Step 3 — Start Implementation

Once no critical or major findings remain, **Start Implementation** unlocks (on the Home
page and in the **Implementation** section) — click it to run the same automated
plan → implement → QA loop as `tempa implement`. The **Implementation** section's
**Status** tab shows live epic/feature progress and QA results; **Log** shows the raw
console output; **Stop Implementation** is available while it's running.

**Clear All**, at the bottom of Home, deletes all plan/QA/log/clarification results
(your specification files are never touched) — useful for restarting the clarification or
implementation loop from scratch. This cannot be undone.

The **✕** next to the working-folder path (top of Home) appears once **Clear All** has been
run — it closes the link to the current project (clears `workspace.root` in `config.json`;
no files are deleted, same as `tempa close-folder` on the CLI) so you can point Tempa at a
different project next.

> The folder picker, "open in explorer", and the **✕** icon are Windows-only conveniences.
> On macOS/Linux, set the working folder with `tempa init <path>` (CLI) first, then use the
> dashboard normally for the rest of the workflow.

---

## Command Line Interface (CLI)

> Prerequisite: the **Setup (One-Time Only)** section above has been done. This is the
> CLI/power-user path — the [Dashboard](#dashboard-recommended) above runs this exact same
> workflow through buttons and inline forms instead.

```
┌───────────────────────────┐
│  1. Initial Setup          │
└─────────────┬──────────────┘
              │
              ▼
┌───────────────────────────┐
│  2. Write Specification    │
└─────────────┬──────────────┘
              │
              ▼
┌───────────────────────────┐
│  3. Answer Clarifications  │  (loop: evaluate → answer → apply, until clean)
└─────────────┬──────────────┘
              │
              ▼
┌───────────────────────────┐
│  4. Run Implementation     │  (loop per epic: feature → QA → fixes)
└───────────────────────────┘
```

### Step 1 — Initial Setup

Point Tempa at the project you want to work on with `init`, passing the (absolute) path
to your project folder:

```bash
tempa init C:\repo\<your-repo>
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

### Step 2 — Write the specification

Save it in the `sources.prd` folder (default `specs/prd`) — and **only** that: the **new**
specification you want implemented. Don't put old/already-implemented specifications here.

Specifications for the **existing** system should be kept separately in the `sources.docs`
folder (default `docs/`) — not rewritten inside `specs/prd`. Plan drafting (part of Step 4)
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

### Step 3 — Answer clarifications (`clarify`)

The goal: the system asks about anything ambiguous/unclear in the PRD, you answer, and those
answers get applied back into the PRD — repeated until there are no more `critical`/`major`
findings. There are two ways to answer; both are used in sequence as part of the same flow,
not as alternatives to pick from.

#### A. Answer manually — during the early iterations

```bash
tempa clarify          # evaluate: system writes questions + recommended answers to a file
```

Once the evaluation finishes, Tempa opens the **clarification-answer web UI** on the result
file so you can answer right there (add `--noui` to skip it) — saving in the UI immediately
applies your answers back into the PRD.

Prefer editing the markdown file by hand instead? That still works: edit the result file
yourself, then run `tempa clarify --apply` to apply it back into the PRD. Re-open the
UI anytime with `tempa answer` — no file argument needed: it scans
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
tempa clarify --finalize
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
during implementation (Step 4). So once `--finalize` finishes, you can move straight on to
implementation without needing to chase down the remaining minor findings.

Full reference for every mode (`clarify` manual, `--auto-answer`, `--apply`, `--finalize`):
see [docs/clarify-modes.md](docs/clarify-modes.md).

### Step 4 — Run implementation (`implement`)

```bash
tempa implement
```

Just run this. The system will (automatically, unattended): draft a plan if there's no task
yet → implement features one by one → run QA once an epic is done → fix any findings →
move on to the next epic, until everything is done.

Full details (the `--replan`/`--features` flags, work priority, monitoring progress,
recovering from problems, manual verification): see
[docs/start-implementation.md](docs/start-implementation.md).

---

## Further Reference

> Not required reading to get started — see the Dashboard or Workflow above
> first.

- **Folder & Path Structure** — what a working folder is, `workspace.*`, `sources.*`:
  [docs/folders-and-paths.md](docs/folders-and-paths.md)
- **AI Model per Stage** — why it's differentiated per stage, default table, how to change it:
  [docs/ai-models.md](docs/ai-models.md)
- **Command Reference** — full list of every command:
  [docs/command-reference.md](docs/command-reference.md)
- **`config.json` structure** — every key and what it does:
  [docs/config-json.md](docs/config-json.md)
- **Prompt templates** (the `src/prompt/` folder) — file list & how to customize harness behavior:
  [docs/prompt-templates.md](docs/prompt-templates.md)
- **Logs & output** — where logs for each session/stage live:
  [docs/logging.md](docs/logging.md)
