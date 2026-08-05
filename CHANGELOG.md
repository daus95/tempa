# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once the first tagged release is cut.

## [Unreleased]

## [0.4.8] - 2026-08-05

### Fixed

- **A clarification round that finds zero findings could leave "Finalized Clarification"
  and "Start Implementation" stuck blocked forever**, even though the PRD was genuinely
  clean. The finalize/implement readiness gates read the most recently *started*
  clarification file's own finding tags (deliberately, so a resolved critical finding can't
  keep counting forever — see `_latest_evaluation_findings`) — but a fresh evaluate pass
  that finds nothing leaves no new file behind to read (there's nothing to write per
  `prompt/clarification.md`), so the gate kept reading whichever older file still had
  findings in it. Added `config.json`'s `last_clean_evaluation_at`, stamped whenever a
  fresh evaluate pass reports zero critical/major/minor findings
  (`_stamp_clean_evaluation_if_zero` in `tempa_clarify.py`); the readiness gate now treats
  a clean stamp newer than the latest finding-bearing file as the current, authoritative
  state.
- **Claude Code's "weekly limit" message wasn't recognized as a usage-limit stop**, so
  hitting it made `clarify`/`implement`/`verify` exit with a plain error instead of waiting
  30 minutes and retrying (the behavior added in 0.4.6). The CLI's actual wording — "You've
  hit your weekly limit · resets ..." — didn't match any of `CLAUDE.usage_limit_markers`,
  which only covered the 5-hour/session limit phrasings. Added `"hit your weekly limit"`
  and `"weekly limit reached"` markers.

## [0.4.7] - 2026-08-05

### Added

- **`clarify`/`clarify --finalize` can now skip minor findings entirely** via a new
  `--skip-minor` CLI flag, or persistently through `config.json`'s `skip_minor_findings`
  (default `true`). A new "Evaluation scope" card on the dashboard's Clarification page —
  above "Finalize readiness" — exposes this as an "Only evaluate critical & major findings"
  switch that persists immediately (`POST /api/clarify/skip-minor`) and is honored by any
  dashboard-triggered clarify/finalize run automatically.

## [0.4.6] - 2026-08-04

### Added

- **A backend usage limit no longer stops `clarify`/`implement`/`verify` — they wait 30
  minutes and retry the interrupted step automatically** — previously, whenever the
  configured CLI backend's usage/session limit was hit, these commands exited immediately
  (exit code 2), leaving the user to notice and re-run the command by hand once the limit
  reset. Every entry point that can hit this (`clarify`, `clarify --auto-answer`,
  `clarify --apply`, `clarify --finalize`'s evaluate/apply loop, `implement`'s plan step and
  its per-epic/QA poll loop, and `verify`) now treats it as a pause instead: it logs that
  it's waiting (explicitly "this is not an error"), a periodic heartbeat during the wait, then
  automatically retries — repeating for as long as the limit is still in effect. Nothing is
  lost across the wait, since the retried step resumes from what's already recorded in the
  clarification files and `config.json` (epic/feature/QA progress left exactly as it was),
  instead of starting over. This is core CLI behavior (`tempa_session.run_with_usage_limit_retry`),
  so the dashboard's background clarify/implement runs get it for free too — they just spawn
  `tempa.py clarify`/`tempa.py implement` as a subprocess and stream its console output, and
  **Stop Implementation**'s existing kill-the-process-tree already cancels a paused wait the
  same way it cancels a running session. Authentication failures (exit 3) are unaffected —
  waiting can't fix a bad credential, so those still stop immediately.

## [0.4.5] - 2026-08-04

### Added

- **`clarify --finalize` now resolves any pre-existing clarification backlog before its
  loop starts** — previously, running Finalize while some clarification files still had
  unanswered findings or fully-answered-but-unapplied files left that backlog untouched
  (the dashboard's Finalize button was in fact gated closed in exactly this situation,
  requiring a fresh zero-critical evaluate on record first). Finalize now checks
  `sources.clarifications` up front: files with every finding answered but not yet
  applied get applied, and files with unanswered findings get each one filled in with
  its own Recommendation text (mechanically, no agent call — the same resolution the
  dashboard's "Follow the recommendation" button would save) and then applied too — all
  in a single apply pass, since apply always processes the whole clarifications folder
  regardless. Only once that's done does the usual evaluate/apply loop start. The
  dashboard's "Finalized Clarification" button (Home and Clarification Overview) is no
  longer disabled by the old precondition either — its readiness checklist is now
  informational rather than blocking.

## [0.4.4] - 2026-08-03

### Fixed

- **Fixed two stale tests in `test_dashboard_clarify_parse.py`** left over from the 0.4.3
  finalize/implement-readiness fix that renamed `_live_clarification_findings` (summed
  findings across every clarification file ever produced) to `_latest_evaluation_findings`
  (only the most-recently-started round) — the tests still called the old name and had
  been failing on every run since.
- **"Apply Answers" now applies every ready clarification file before evaluating again** —
  previously, whenever more than one fully-answered file was still waiting to be applied,
  the dashboard chained straight into a fresh Start Clarification (evaluate) run after
  the first successful apply, stranding the rest of the backlog until the user came back
  and clicked Apply again for each remaining file. It now keeps re-running `clarify
  --apply` while any ready file is still unapplied, and only starts a fresh evaluate once
  every one of them has been applied.

## [0.4.3] - 2026-08-03

### Fixed

- **Finalize/implement readiness now checks only the most recent evaluation round** —
  previously the "Most recent evaluation still shows N critical finding(s)" check (and the
  Start Implementation gate) summed critical/major/minor tags across every clarification
  file ever produced, including past rounds kept as historical record with their findings
  already resolved. Since those tags are never removed from old files, the count could
  never return to 0 once a single critical finding had ever appeared in any round. It now
  reads only the most-recently-started evaluation round's file.
- **Higher-contrast critical/major/minor severity colors** — the old major badge color
  (`#d97706`) failed WCAG AA contrast against its white badge text, and the old minor color
  was identical to the app's own accent blue, so a minor-severity badge/border was
  indistinguishable from an ordinary link or button. Now `#b91c1c` / `#c2410c` / `#a16207`.

### Added

- **"Started" column on the Clarification overview's Unanswered/Fully answered tables** —
  shows when each file's evaluation round began (`dd/MM HH:mm`, parsed from the
  `clarification-YYYYMMDD-HHMMSS.md` filename, falling back to file mtime), with both
  tables now sorted most-recent-first instead of alphabetically.
- **Merged Critical/Major/Minor columns into a single "Findings" column** with one icon per
  severity present (🔴/🟠/🟡), each carrying a native hover tooltip (e.g. "Critical: 2/2") —
  frees up table width for the new Started column.
- **Clicking a Clarification overview row now opens a detail dialog** (started time,
  per-severity findings, status, and — new — how long that file's evaluation and its most
  recent apply pass took) instead of jumping straight into the answer-editing view; an
  "Open File" button in the dialog still does that.
- **Configurable "Start Implementation requires" setting** — previously Start
  Implementation always required zero critical *and* zero major findings in the most
  recent clarification evaluation, with no way to relax it. A new Settings control lets
  you choose "No critical or major findings" (the previous, still-default behavior), "No
  critical findings" (major findings may remain open), or "No condition" (clarification
  must have run at least once, but its findings never block). The two relaxed options
  show an inline risk warning and a confirmation prompt before they can be selected. The
  gate is enforced both client-side (Home, Clarification, and Implementation pages) and
  server-side (`_handle_implement_run_start`), all driven by the same
  `implementation_start_requirement` config.json setting.

## [0.4.2] - 2026-08-02

### Changed

- **Documented the Settings page's Updates card** — new [docs/updates.md](docs/updates.md)
  reference page, a summary section in README.md's Dashboard walkthrough, and cross-references
  from README's Further Reference list and `docs/command-reference.md`'s `check-update`/`update`
  rows. Covers where the check/update UI lives, how it works, and why the Tempa process needs
  restarting afterward (a page reload alone doesn't pick up the new code).

## [0.4.1] - 2026-08-02

### Added

- **Updates card in the Settings page** — check the installed Tempa version against the
  latest GitHub release and apply the update directly from the dashboard (mirrors
  `tempa check-update` / `tempa update --yes`), without needing a terminal. After a
  successful update the dashboard tells the user to restart the Tempa application, since
  the running process keeps its old code loaded in memory until then — reloading the page
  alone does not pick up the new version.

## [0.4.0] - 2026-08-02

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

[Unreleased]: https://github.com/daus95/tempa/compare/v0.4.4...HEAD
[0.4.4]: https://github.com/daus95/tempa/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/daus95/tempa/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/daus95/tempa/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/daus95/tempa/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/daus95/tempa/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/daus95/tempa/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/daus95/tempa/compare/v.0.1.0...v0.2.0
[0.1.0]: https://github.com/daus95/tempa/releases/tag/v.0.1.0
