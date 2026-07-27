# Folders & Paths

See [README.md](../README.md) for a workflow summary. This document explains the details of
the folder structure Tempa uses for the project you're working on.

## What is a "working folder"?

A **working folder** is the root folder of the project you're working on (not Tempa's
installation folder) along with its standard sub-folder structure — where Tempa reads
specifications, documentation, code, and writes output (plans, QA reports, etc). Set
**once** via `init` (see Workflow Step 1 in the README), stored in `config.json` under the
`workspace` key.

Tempa only needs to know **one absolute path**: `workspace.root`. Every other sub-folder
(both under `workspace` and `sources`, see below) is stored **relative** to this root, so no
absolute path is repeated throughout the config.

## Working folder (`workspace`)

| Folder | Contents | Default (relative to root) |
|--------|-----|---------------------------|
| `root` | parent folder of every other folder (**absolute**) | — (set by `init`) |
| `docs` | **current** application documentation | `docs` |
| `adr` | Architecture Decision Records | `adr` |
| `specs` | **new** specifications to be worked on | `specs` |
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

`sources` is derived from `workspace`: concrete paths (relative to `workspace.root`) that
each command actually reads/writes. Current defaults:

| Key | Path | Used by |
|-----|------|--------------|
| `prd` | `specs/prd` | **PRD** = the **new** specification to be worked on (incoming work folder) |
| `docs` | `docs` | **current** system documentation — reference for "what already exists" |
| `clarifications` | `specs/clarifications` | clarification results output |
| `epics` | `specs/pbi/epics` | epic/feature/task output from plan drafting (automatic via `implement`) |
| `apps` | `apps` | monorepo root — **every service**; each service's source & tests live inside its own folder |

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

## Tempa's internal folders

These folders live inside **Tempa's own installation** (where `tempa.py` lives) — not in the
project you're working on:

| Folder | Contents |
|--------|-----|
| [`prompt/`](../prompt/) | prompt templates (`.md`), one file per prompt |
| `docs/` (this folder) | supplementary README documentation |
| `logs/` | logs for every Claude session + the runner process log |
| `qa/` | QA reports per epic |
| `verify/` | manual verification reports (`verify`) |
