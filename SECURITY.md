# Security Policy

## Important: how Tempa operates

Tempa drives whichever backend CLI you've configured per stage — **Claude Code**
(`claude`, via `--dangerously-skip-permissions`), **GitHub Copilot CLI** (`copilot`, via
`--allow-all-tools`), or **OpenAI Codex CLI** (`codex`, via
`--dangerously-bypass-approvals-and-sandbox`) — a **fully automated mode with no human
confirmation** for the actions it takes while clarifying, planning, implementing, and QA'ing
your project (see the [README](README.md#step-1--prerequisites) and
[Choosing a CLI Backend](README.md#choosing-a-cli-backend)).

This means Tempa (via whichever backend CLI is configured for that stage) can read, write,
and delete files, and run shell commands, inside whatever project folder you point it at,
without asking you first. Treat this as you would any other unattended, autonomous-execution
tool:

- Only run it against projects/folders you control and can afford to have modified
  automatically.
- Don't run it against a folder containing credentials, secrets, or production
  configuration you don't want touched.
- Review the working folder (e.g. via `git status`/`git diff`) after a run, the same way
  you would review any automated change before pushing it.
- Prefer running it inside a disposable environment (a fresh clone, a VM, a container) if
  you're evaluating Tempa on an unfamiliar or sensitive codebase.

This is expected, documented behavior, not a bug — it's the mechanism that lets Tempa run
unattended. Reports about "Tempa can modify files without confirmation" as such won't be
treated as vulnerabilities; reports about it doing something **outside** its documented
scope (e.g. escaping the target working folder, exfiltrating data, or executing something
other than the configured backend CLI invocation) are very much wanted — see below.

## Supported versions

Tempa does not yet have tagged releases — security fixes are applied to the `main` branch.
If you're running an older checkout, update to the latest `main` before reporting an issue
to make sure it hasn't already been fixed.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a security vulnerability.

Instead, use GitHub's private reporting: go to the
[Security tab](https://github.com/daus95/tempa/security) of this repository and click
**"Report a vulnerability"**. This opens a private advisory visible only to the maintainer
and you, so the issue can be discussed and fixed before any public disclosure.

Please include:
- A description of the issue and its potential impact.
- Steps to reproduce (a minimal PRD/spec and command sequence, if relevant).
- Which OS/Python version, and which backend CLI + version (`claude`/`copilot`/`codex`) you're
  running.

There's no formal SLA yet given the project's size, but reports will be acknowledged and
triaged as soon as possible.
