# Checking for Updates

See [README.md](../README.md) for a summary. This document explains how to check for and
apply Tempa updates — both the dashboard's **Settings → Maintenance tab → Updates** card and the CLI
equivalents (`tempa version`, `tempa check-update`, `tempa update`) — and what has to happen
afterward for the update to actually take effect.

## Where it's shown

- **Settings → Maintenance tab → Updates card** — shows the installed version against the latest
  published GitHub release, with a **Check for Updates** button and, only when a newer
  release actually exists, an **Update Now** button.
- **CLI** — `tempa version`, `tempa check-update`, `tempa update [--yes]` do the same three
  things from a terminal instead of the dashboard. See
  [command-reference.md](command-reference.md).

Both surfaces drive the same underlying check/download/apply logic — the dashboard isn't a
separate implementation, just a UI on top of it.

## How it works

- The installed version comes from the `VERSION` file at the install root, bumped as part of
  cutting each GitHub release — `tempa version` (and the Updates card) just reads it.
- The latest version comes from GitHub's "latest release" API. This is a best-effort check:
  if GitHub can't be reached (offline, rate-limited, etc.), both the dashboard and the CLI
  fail gracefully with a message instead of erroring out.
- **Update Now** / `tempa update` downloads that release's `tempa.zip` asset and overwrites
  this install's files with whatever's actually inside the archive. Local-only files/folders
  — `.tempa/`, `.active-workspace`, `__pycache__`, a dev checkout's `.git`, etc. — are never
  touched, because none of them are ever part of the release archive in the first place.

## Restart required after updating

Applying an update overwrites the Python source files Tempa is currently running from, but a
running Python process doesn't reload changed modules on its own. **After a successful
update, restart whichever Tempa process is running** — close the current `tempa dashboard`
session and start it again, or restart any running `tempa implement` session — before the new
code actually takes effect.

Reloading the dashboard page in your browser is **not** enough: the backend process behind it
keeps serving the old code in memory until it's actually restarted. Both surfaces say this
explicitly once the update finishes — the dashboard with a "Restart Required" dialog, the CLI
with a closing message from `tempa update`.

## Under the hood

Reference for anyone extending this — see [architecture.md](architecture.md) for the module
map these live in:

- `tempa_commands.get_local_version()` / `get_latest_release_version()` — read `VERSION` /
  query GitHub's latest-release API. Shared by both the CLI and the dashboard.
- `tempa_commands.run_update()` — the CLI's confirm → download → extract → copy-over flow
  behind `tempa update`.
- `GET /api/update/status` (`dashboard_server.py`) — what the Settings page's Maintenance tab
  calls to compare installed vs. latest.
- `POST /api/update/run` — runs `tempa.py update --yes` as a subprocess rather than calling
  `run_update()` in-process, so a failed update can't take the dashboard server down with it.
  On success, the dashboard shows the "Restart Required" notice described above.
