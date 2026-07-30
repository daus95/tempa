# Contributing to Tempa

Thanks for considering a contribution. Tempa is a small, dependency-free Python project —
contributing to it should be just as low-friction.

## Getting the code

Unlike the ZIP-based install described in the [README](README.md) (aimed at end users), work
from a clone so you can open a pull request:

```bash
git clone https://github.com/daus95/tempa.git
cd tempa
```

No virtual environment or `pip install` step is needed — Tempa only uses the Python 3
standard library (see [README.md](README.md#step-1--prerequisites)). You'll also need the
`claude` CLI on `PATH` and authenticated, since most of Tempa's behavior is only observable
by actually driving it.

Sanity-check your setup with:

```bash
./tempa test
```

## Project layout

- `tempa.py` / `tempa` / `tempa.cmd` — entry-point launchers, dispatch into `src/tempa_cli.py`.
- `src/tempa_*.py` — CLI-side logic: config, clarify, implement, session, prompts, logging,
  maintenance, commands.
- `src/dashboard_*.py` — the local web dashboard: server, UI, config, spec, clarify
  parsing/rendering.
- `src/prompt/*.md` — the prompt templates sent to Claude, one file per prompt stage. Prefer
  editing these over changing prompt strings inline in Python.
- `src/assets/` — the dashboard's HTML/CSS/JS.
- `docs/` — reference docs for each subsystem (config, folders, prompts, models, logging).

If you add a module, follow the existing `tempa_*.py` / `dashboard_*.py` naming split so it's
clear at a glance which side of the tool (CLI vs. dashboard) it belongs to. For the full module
map, the import/dependency rules between the CLI and the dashboard, and where to add a new
command/prompt-stage/dashboard-route, see [docs/architecture.md](docs/architecture.md).

## Making changes

- Keep pull requests focused — one behavior change or fix per PR is much easier to review
  than a bundle of unrelated tweaks.
- Run the test suite (see [Testing](#testing) below) before opening a PR, and add tests
  for new pure-logic behavior where practical. Code that shells out to the `claude` CLI or
  serves the dashboard isn't covered by tests yet, so for changes there, describe in your
  PR description exactly what you ran and what you observed (CLI output, dashboard
  screenshots, etc.).
- If your change affects user-facing behavior (a command, a config key, dashboard flow),
  update the relevant file in `docs/` and/or `README.md` in the same PR.
- If your change affects a prompt template in `src/prompt/`, mention which stage(s) you
  tested it against (clarify, plan, implement, QA, verify).
- Run `ruff check .` before opening a PR — CI enforces it (`.github/workflows/tests.yml`,
  `lint` job) and will fail the build on any violation. Config lives in `pyproject.toml`
  under `[tool.ruff]`.

## Testing

Tempa has a `pytest`-based unit test suite under `tests/`, covering the pure-logic
modules in `src/tempa_*.py` / `src/dashboard_*.py` (config parsing, clarify-result
parsing, spec handling, prompt templating, destructive-clear safety checks, and the
pure parsing/formatting helpers in `tempa_session.py`). Code that shells out to the
`claude` CLI or serves the dashboard's HTTP handler isn't covered yet — that's next on
the list if you're looking to contribute here.

Run it locally:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

CI (`.github/workflows/tests.yml`) runs the same suite on every push/PR to `main`, plus
a coverage report (`--cov=src --cov-report=term-missing`) — run that flag locally too if
you want to see which lines a change still needs a test for.

Tests are isolated from your local Tempa install and any active workspace via an
autouse fixture in `tests/conftest.py` that redirects Tempa's path constants into a
temp directory — new tests don't need to worry about touching real `.active-workspace`
state, but should rely on that isolation rather than reading/writing real paths.

## Reporting bugs / requesting features

Open a GitHub issue with:
- What you ran (command or dashboard action).
- What you expected vs. what happened.
- Relevant log output — see [docs/logging.md](docs/logging.md) for where logs live.

## Security issues

Please do **not** open a public issue for a security vulnerability — see
[SECURITY.md](SECURITY.md) for how to report it privately.

## Submitting a pull request

1. Fork the repo and create a branch off `main`.
2. Make your change, following the guidance above.
3. Push and open a PR describing the change and how you verified it.
4. Be responsive to review feedback — this is a young project and conventions are still
   settling, so expect some back-and-forth on style/approach.
