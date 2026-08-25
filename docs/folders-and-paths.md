# Folders & Paths

See [README.md](../README.md) for a workflow summary. This document explains the details of
the folder structure Tempa uses for the project you're working on.

## The Tempa install folder

What's inside the folder you get from extracting `tempa.zip` (see
[README.md](../README.md#step-2--download--install)) — separate from, and outside, the repo
you're working on:

```
C:\repo\<your-repo>\      (your repo — left untouched)
C:\tools\tempa\           (Tempa folder — separate)
├─ tempa.py               (launcher / entry point)
├─ tempa.cmd              (Windows launcher)
├─ tempa                  (macOS/Linux launcher)
├─ Open Dashboard.cmd     (Windows: double-click to open the dashboard, no terminal needed)
├─ Open Dashboard.command (macOS: double-click to open the dashboard, no terminal needed)
├─ Open Dashboard.sh      (Linux: double-click to open the dashboard, no terminal needed)
└─ src\                   (everything the tool ships with)
   ├─ tempa_cli.py           (CLI dispatch)
   ├─ tempa_*.py             (config, logging, prompts, session,
   │                            implement, clarify, maintenance, commands)
   ├─ dashboard_*.py         (ui, server, config, spec, clarify_parse,
   │                            clarify_render, runs, winui, macui, linuxui,
   │                            assets)
   ├─ prompt\                (prompt templates, one .md per prompt)
   └─ assets\                (dashboard.html / .css + js\ parts)
```

Keep the whole folder together: the launcher puts `src/` on the import path and the modules
import each other by name, so nothing works if they're separated. Note there's no
`config.json` at the top level — see "Tempa's own folder (before any workspace is active)"
below for where it actually lives.

## What is a "working folder" (workspace)?

A **working folder** (workspace) is the root folder of the project you're working on (not
Tempa's installation folder) along with its standard sub-folder structure — where Tempa reads
specifications, documentation, code, and writes output (plans, QA reports, etc).

Set via `init <abs>` (see Workflow Step 1 in the README). Unlike everything else described in
this doc, Tempa needs to know which workspace is active **before** it can even read
`config.json` — since `config.json` itself now lives inside the workspace (see "Tempa's own
folder" below). So `init` first writes a tiny pointer file, `.active-workspace`, at Tempa's
own install root (one absolute path, nothing else) — then every other path (`workspace.root`
and everything below it) is read from/stored in that workspace's own `config.json`.

- `tempa close-folder` detaches the active workspace: it only deletes `.active-workspace`.
  The workspace's own `.tempa/` folder (config, epic/session history, logs, QA reports,
  specs) is left exactly as it was.
- `tempa init <abs>` on a folder used before reopens it as-is (loads its existing
  `.tempa/config.json` unchanged); on a new folder, it creates a fresh one.
- This is how two workspaces stay independent: closing one and opening another never mixes
  or overwrites either one's config/history.
- Every `init` (and, for the folder being left behind, every `close-folder`) also records
  the workspace's absolute path into `.workspace-history.json` — another small file at
  Tempa's own install root, next to `.active-workspace` — an MRU list of up to the 10 most
  recently opened workspaces. This is what powers the dashboard Home page's "recent working
  folders" list, and it is the only place a workspace's path is remembered once you've
  closed it. `close-folder` never touches this file's contents beyond adding that one entry
  — it is a separate, longer-lived history, not a second pointer.

Every workspace sub-folder (both under `workspace` and `sources`, see below) is stored
**relative** to `workspace.root`, so no absolute path (other than `root` itself) is repeated
throughout the config.

## Working folder (`workspace`)

| Folder | Contents | Default (relative to root) |
|--------|-----|---------------------------|
| `root` | parent folder of every other folder (**absolute**) | — (set by `init`) |
| `docs` | **current** application documentation | `docs` |
| `adr` | Architecture Decision Records | `adr` |
| `specs` | **new** specifications to be worked on | `.tempa/specs` (Tempa-managed, see below — kept alongside config/logs/qa/verify, unlike the other folders here which sit directly under root) |
| `apps` | application implementation | `src` |
| `infra` | infrastructure scripts (e.g. docker compose) | `infra` |
| `archive` | archive of old specs no longer in use | `archive` |

One more folder can appear directly under `root`, but it isn't part of the `workspace` table
above and isn't created by `init`: **`prd-backup/`**, where `clarify --finalize` writes the PRD
ZIP snapshots it takes at each checkpoint and at the end of a successful run. It is created on
demand the first time a snapshot is written, its name/location comes from
`finalize_checkpoint_backup_dir` (relative → under `root`, absolute → as-is; see
[config-json.md](config-json.md)), and it deliberately sits outside `.tempa/` — unlike logs and
QA reports, these are deliverables you may want to open or hand over. Nothing prunes it.

```bash
tempa show-folders            # check the active layout + resolved absolute paths

# override a sub-folder name (optional), config-only — doesn't create folders on disk:
tempa set-folders --root C:\repo\<your-repo> --specs new-specs --archive old-specs
```

- `init <abs>` — sets `workspace.root` **and** creates the folders on disk (safe to re-run;
  the contents of existing folders are never overwritten).
- `set-folders --root <abs> [...]` — only sets/overrides the config (`workspace.root` +
  sub-folder names), **does not** create any folder on disk.
- `--root` on `set-folders` **must be absolute**;
  `--docs/--adr/--specs/--apps/--infra/--archive` **must be relative** to root.

## Sources — concrete paths per command

`sources` is **computed from `workspace`** — nothing needs to be set in `config.json` for
it. `docs` and `apps` mirror `workspace.docs`/`workspace.apps` exactly; `prd`, `epics` and
`clarifications` default to a fixed suffix under `workspace.specs`. Current defaults:

| Key | Path (default) | Used by |
|-----|------|--------------|
| `prd` | `<specs>/prd` (i.e. `.tempa/specs/prd`) | **PRD** = the **new** specification to be worked on (incoming work folder) |
| `docs` | `workspace.docs` | **current** system documentation — reference for "what already exists" |
| `clarifications` | `<specs>/clarifications` (i.e. `.tempa/specs/clarifications`) | clarification results output |
| `epics` | `<specs>/pbi/epics` (i.e. `.tempa/specs/pbi/epics`) | epic/feature/task output from plan drafting (automatic via `implement`) |
| `apps` | `workspace.apps` | monorepo root — **every service**; each service's source & tests live inside its own folder |

To point any one of these somewhere else, set it explicitly under `sources` in `config.json`
(relative values are joined onto `workspace.root`; absolute values are used as-is) — an
explicit `sources.<key>` always overrides its computed default.

> **Multi-service monorepo.** The application consists of many services (e.g.
> `src/backend`, `src/web`, …), each holding both its own source code **and** its own
> tests. That's why there's no `sources.implementation`/`sources.tests` pinned to a single
> path — `sources.apps` is the one reference, and the agent opens whichever service is
> relevant to the epic's spec.

> **PRD vs docs.** `sources.prd` (the PRD) contains ONLY the new specification to be worked
> on — not full system documentation. Up-to-date system documentation ("what already exists
> now") lives in `sources.docs` (`docs/`). Plan drafting builds epics from the PRD using
> `docs/` (and the code) as context for the existing system.

> Absolute paths are still supported: if a `sources.*` value is already absolute, it's used
> as-is (not joined with root).

## The workspace's `.tempa/` folder

Unlike the folders above, `config.json`, `architecture-principles.md`, `logs/`, `qa/`,
`verify/`, and `specs/` are NOT scattered across the workspace root or Tempa's own install —
they all live together under one hidden folder **inside the active workspace**:
`<workspace_root>/.tempa/`. That keeps every workspace's state self-contained, so switching to a
different workspace and back never loses or overwrites anything.

| Folder/file | Contents |
|--------|-----|
| `.tempa/config.json` | this workspace's config — epic/session history, models, workspace/sources overrides, etc. |
| `.tempa/architecture-principles.md` | optional project-wide rules, prepended to every stage's prompt (see [architecture-principles.md](architecture-principles.md)). Absent = unset |
| `.tempa/logs/` | logs for every agent session + the runner process log (plus a `*.prompt.md` sidecar per session for backends that need the prompt delivered via a file — see [ai-models.md](ai-models.md)) |
| `.tempa/qa/` | QA reports per epic |
| `.tempa/verify/` | manual verification reports (`verify`) |
| `.tempa/specs/` | new specifications to be worked on (`workspace.specs`, `sources.prd/epics/clarifications` — see above) |
| `.tempa/graceful-stop-implement`<br>`.tempa/graceful-stop-clarify` | transient — present only while a **Stop After Current Session/Round** request is pending. Its presence *is* the request (the timestamp inside is just for whoever finds a stray one); it's how the dashboard, or a second terminal, reaches a `tempa implement` / `clarify --finalize` running as its own process. Written by the dashboard button or `--stop-graceful`, and removed when the request is honoured, cancelled, or a new run starts. Safe to delete by hand — that simply cancels the request |

`tempa init <abs>` creates `<abs>/.gitignore` (or appends to it) with a `.tempa/` entry, so
none of this is committed to the workspace's own repo.

`init`/`close-folder` only ever touch `.active-workspace` (see above) and the contents of
`.tempa/` — they never read or write anything under `docs/`, `adr/`, `src/`, `infra/`, or
`archive/`.

## Tempa's own folder (before any workspace is active)

Until `init` is run for the first time (or right after `close-folder`), there's no active
workspace to hold `.tempa/` — so Tempa falls back to the exact same layout inside its **own**
install folder (where `tempa.py` lives): `<tempa_install>/.tempa/config.json`,
`<tempa_install>/.tempa/logs/`, etc. This is scratch space only, so commands like
`set-model`/`test`/`show-models` still work pre-`init`; it is never migrated into a workspace
once one is selected.

| File | Contents |
|--------|-----|
| [`src/prompt/`](../src/prompt/) | prompt templates (`.md`), one file per prompt — shipped with Tempa, not workspace-specific |
| `docs/` (this folder) | supplementary README documentation |
| `.active-workspace` | the active-workspace pointer (absolute path, or absent = no active workspace) |
| `.workspace-history.json` | MRU list (up to 10) of previously opened workspaces — survives `close-folder`, unlike `.active-workspace` |

> **`specs` is the one exception.** Every other pre-`init` fallback above lives inside
> `<tempa_install>/.tempa/`. `specs` doesn't — with no active workspace, it resolves to
> `<parent of the Tempa install folder>/specs` instead (see `resolve_specs_dir()` in
> `tempa_config.py`), so it lands as a sibling of the Tempa install folder, not inside it.
> If you ever run a spec-touching command (e.g. `clarify`) before `init`-ing a workspace,
> you'll see a stray `specs/` folder show up one level above where Tempa itself is
> installed — that's this fallback, not a bug. Once a workspace is active, `specs` always
> resolves under `<workspace_root>/.tempa/specs` like everything else (see
> [Sources](#sources--concrete-paths-per-command) above).
