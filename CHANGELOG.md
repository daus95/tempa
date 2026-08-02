# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once the first tagged release is cut.

## [Unreleased]

### Added

- **`tempa version`** — shows the locally installed Tempa version (read from a `VERSION`
  file at the install root, bumped as part of cutting each release).
- **`tempa check-update`** — checks GitHub for the latest published release and compares it
  against the installed version, printing a download link if an update is available (fails
  gracefully with a manual-check link if GitHub can't be reached).
- **`tempa update [--yes]`** — if a newer release exists, downloads it and applies it on top
  of the current install after an interactive confirmation (current → latest version shown
  first; `--yes` skips the prompt). Only overwrites files actually shipped in the release
  archive, so local-only files/folders (`.tempa/`, `.active-workspace`, etc.) are untouched.
- **CLI backend readiness** — the Home page's workspace info area and the Settings page's
  AI Backend & Model section now show, per CLI backend (Claude Code, GitHub Copilot CLI,
  OpenAI Codex CLI), whether it's ready to use for the active workspace: installed/on PATH
  and able to write to the workspace folder. Not-ready backends are also flagged inline in
  each stage's Backend dropdown. Settings has a **Detect CLI Backends** button to re-check
  on demand (e.g. right after installing or logging into a CLI) without reloading the form.

## [0.3.0] - 2026-08-02

### Added

- **Multi-CLI backend support** — each pipeline stage (Clarification, Planning,
  Implementation) can now be driven by GitHub Copilot CLI or OpenAI Codex CLI, not just
  Claude Code. Configurable per stage from the dashboard's Settings page (a Backend
  dropdown next to each stage's model field) or from the CLI (`tempa set-backend`/
  `show-backends`). Switching a stage's backend mid-epic starts its next session fresh
  instead of trying to resume a session under a different CLI.
- **Reasoning Effort** setting per stage, next to the model field, validated against the
  selected backend/model before it's ever sent to the CLI — per-model for OpenAI Codex CLI
  (whose supported levels vary by model), per-backend for Claude Code/GitHub Copilot CLI.
  Configurable via the dashboard or `tempa set-effort`/`show-efforts`.

## [0.2.0] - 2026-08-01

### Added

- Settings page toggle, **Allow finalizing with critical findings** (off by default).
  Finalized Clarification normally refuses to start while any critical finding is still
  open; enabling this lets its automated evaluate/apply loop attempt to resolve them
  unsupervised instead, with an explanation of the consequences shown when turning it on
  and a persistent warning while it's active. The Home and Clarification pages' Finalize
  buttons follow this setting; Start Implementation's separate zero-critical/zero-major
  gate is unaffected.
- Dashboard's Clarification page shows **Round N of M** (current clarification round vs.
  `max_clarification_run`) next to the Finalize readiness panel.

### Fixed

- Clarification result files are no longer overwritten or deleted by later evaluation/apply
  passes — each `clarify` evaluation now writes a new, uniquely-named file, and `clarify
  --apply` never touches the clarification files themselves. Previously answered rounds now
  stay visible in the dashboard's "Fully answered" list instead of disappearing after a
  later round or apply step.

## [0.1.0] - 2026-07-30

Initial tagged release.

### Added

- Initial Tempa harness: a Claude-driven CLI runner for PRD clarification, planning,
  and implementation.
- Cross-platform `tempa` / `tempa.cmd` launcher scripts, plus double-click
  `Open Dashboard` launchers for Windows/macOS/Linux.
- Unified web **dashboard** (`tempa dashboard`), replacing the earlier separate
  spec/clarify UIs — Home workflow page, live Implementation run controls and log,
  Clarification run controls (Start/Finalize/Apply) with live log, a Specification
  file browser (add/rename/delete, view/edit), and a Settings page for AI models and
  run limits.
- `spec --show`: an Explorer-style web UI to browse and edit spec files.
- Clarification answer web UI (`tempa answer`), including scanning for every
  unanswered clarification file at once (tabbed UI).
- Auto-apply of clarification answers back into the PRD right after saving in the UI,
  with a prompt to run another clarification round afterward.
- **Apply Answers** button on the dashboard Home page's Clarification step, and an
  **Answer Findings** shortcut that jumps straight to the first unanswered file.
- Auto-continuation of the clarify → answer → apply loop after Apply, with a clearer
  "not ready" state on Home while critical/major findings remain.
- Native macOS folder picker and Finder open/focus for workspace init.
- Per-workspace `.tempa/` folder holding config/logs/qa/verify/specs, so Tempa's own
  install folder stays clean across projects.
- Auto-creation of `config.json` with sane defaults if missing.
- **Architecture Principles** — project-wide rules injected into every Claude prompt
  (clarify, plan, implement, QA, verify), editable from the dashboard.
- Detection of `claude` CLI authentication failures, with a plain-language explanation
  and fix instructions instead of a raw error.
- `pytest` unit test suite (`tests/`) covering the pure-logic modules, with 100%
  coverage on those modules, and a GitHub Actions CI workflow running it on every
  push/PR to `main`.
- `ruff` linting, enforced as a separate CI job.
- CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, and GitHub issue/PR templates.

### Changed

- CLI moved to `argparse` subcommands; session runners deduplicated and consolidated
  under shared runner state.
- Config structure: `sources.*` paths are now derived from `workspace.*` instead of
  being duplicated in `config.json`.
- Codebase refactored into cohesive modules under `src/` (`tempa_*.py` for the CLI,
  `dashboard_*.py` for the dashboard).
- Default workspace app folder changed from `apps/` to `src/`.
- README rewritten with a problem/solution-framed introduction, a Quick Start section,
  and a table of contents.
- Home page's Clarification step now mirrors the dedicated Clarification page's
  Continue/Answer Findings behavior.
- Incomplete-answer warnings now use the dashboard's styled modal instead of a native
  `alert()`.
- **Start Implementation** now requires clarification to have run at least once,
  instead of being reachable before any clarification pass.

### Fixed

- `TypeError` when creating `.gitignore` during `init`.
- `TypeError` crashing the `/api/clarify/run` finalize gate.
- Top-level dashboard toolbar not hiding correctly; stray `+` icons on Add File/Folder
  buttons.
- `spec --show` opening the whole `specs` folder instead of `specs/prd`.
- Dashboard Implementation log tab not using the full panel height / live progress not
  visible.
- Crashes from non-UTF-8 Windows console codepages when printing Unicode log output.
- A path-traversal test (`_resolve_within`) that only reflected Windows path semantics,
  failing on non-Windows CI runners.

[Unreleased]: https://github.com/daus95/tempa/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/daus95/tempa/compare/v.0.1.0...v0.2.0
[0.1.0]: https://github.com/daus95/tempa/releases/tag/v.0.1.0
