#!/usr/bin/env python3
"""Tempa launcher.

The stable entry point at the repo root (invoked by tempa.cmd / the `tempa` shell script,
and re-invoked as a subprocess by the dashboard). All implementation modules live in the
src/ folder; this launcher puts src/ on sys.path so they import by top-level name
(`import tempa_config`, `from dashboard_ui import ...`) and then runs the CLI.

Keeping the modules in src/ (rather than as a package with relative imports) is what lets
Tempa still run as a plain script — `py tempa.py <cmd>` — from any working directory.
"""

import contextlib
import sys
from pathlib import Path

# Force UTF-8 output so log()/print() calls containing non-ASCII characters (→, ✅, 🔧,
# ⬜, …) never crash with UnicodeEncodeError on a Windows console using a non-UTF-8
# codepage (e.g. cp1252) — covers both interactive runs and re-invocation as a subprocess
# by the dashboard. errors="replace" degrades to "?" instead of crashing if a console
# still can't render a given character even in UTF-8 mode.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from tempa_cli import run  # noqa: E402  (import must follow the sys.path setup above)

if __name__ == "__main__":
    run()
