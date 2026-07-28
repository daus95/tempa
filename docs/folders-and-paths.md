# Folders & Paths

See [README.md](../README.md) for a workflow summary. This document explains the details of
the folder structure Tempa uses for the project you're working on.

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
| `apps` | application implementation | `apps` |
| `infra` | infrastructure scripts (e.g. docker compose) | `infra` |
| `archive` | archive of old specs no longer in use | `archive` |

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
> `apps/backend`, `apps/web`, …), each holding both its own source code **and** its own
> tests. That's why there's no `sources.implementation`/`sources.tests` pinned to a single
> path — `sources.apps` is the one reference, and Claude opens whichever service is
> relevant to the epic's spec.

> **PRD vs docs.** `sources.prd` (the PRD) contains ONLY the new specification to be worked
> on — not full system documentation. Up-to-date system documentation ("what already exists
> now") lives in `sources.docs` (`docs/`). Plan drafting builds epics from the PRD using
> `docs/` (and the code) as context for the existing system.

> Absolute paths are still supported: if a `sources.*` value is already absolute, it's used
> as-is (not joined with root).

## The workspace's `.tempa/` folder

Unlike the folders above, `config.json`, `logs/`, `qa/`, `verify/`, and `specs/` are NOT
scattered across the workspace root or Tempa's own install — they all live together under one
hidden folder **inside the active workspace**: `<workspace_root>/.tempa/`. That keeps every
workspace's state self-contained, so switching to a different workspace and back never loses
or overwrites anything.

| Folder/file | Contents |
|--------|-----|
| `.tempa/config.json` | this workspace's config — epic/session history, models, workspace/sources overrides, etc. |
| `.tempa/logs/` | logs for every Claude session + the runner process log |
| `.tempa/qa/` | QA reports per epic |
| `.tempa/verify/` | manual verification reports (`verify`) |
| `.tempa/specs/` | new specifications to be worked on (`workspace.specs`, `sources.prd/epics/clarifications` — see above) |

`tempa init <abs>` creates `<abs>/.gitignore` (or appends to it) with a `.tempa/` entry, so
none of this is committed to the workspace's own repo.

`init`/`close-folder` only ever touch `.active-workspace` (see above) and the contents of
`.tempa/` — they never read or write anything under `docs/`, `adr/`, `apps/`, `infra/`, or
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
