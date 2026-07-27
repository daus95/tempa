#!/usr/bin/env python3
"""Tempa launcher.

The stable entry point at the repo root (invoked by tempa.cmd / the `tempa` shell script,
and re-invoked as a subprocess by the dashboard). All implementation modules live in the
src/ folder; this launcher puts src/ on sys.path so they import by top-level name
(`import tempa_config`, `from dashboard_ui import ...`) and then runs the CLI.

Keeping the modules in src/ (rather than as a package with relative imports) is what lets
Tempa still run as a plain script — `py tempa.py <cmd>` — from any working directory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from tempa_cli import run  # noqa: E402  (import must follow the sys.path setup above)

if __name__ == "__main__":
    run()
