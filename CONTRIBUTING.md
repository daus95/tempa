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
clear at a glance which side of the tool (CLI vs. dashboard) it belongs to.

## Making changes

- Keep pull requests focused — one behavior change or fix per PR is much easier to review
  than a bundle of unrelated tweaks.
- There is currently no automated test suite or CI (see
  [Testing](#testing) below), so manual verification is the review bar for now:
  describe in your PR description exactly what you ran and what you observed (CLI output,
  dashboard screenshots, etc.).
- If your change affects user-facing behavior (a command, a config key, dashboard flow),
  update the relevant file in `docs/` and/or `README.md` in the same PR.
- If your change affects a prompt template in `src/prompt/`, mention which stage(s) you
  tested it against (clarify, plan, implement, QA, verify).
- No linter/formatter is enforced yet — just follow the style of the surrounding code
  (this will likely tighten up as the project grows; see the open items tracked in
  [SECURITY.md](SECURITY.md) and the project's issue tracker for where things are headed).

## Testing

Tempa doesn't have an automated test suite yet — this is one of the most valuable areas to
contribute to. If you're adding tests, a `tests/` folder using `pytest` (or `unittest`, to
stay dependency-free) covering the pure-logic modules in `src/tempa_*.py` /
`src/dashboard_*.py` first (config parsing, clarify-result parsing, spec handling) is a
great starting point before tackling anything that shells out to the `claude` CLI.

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
