# AI Model & CLI Backend per Stage

See [README.md](../README.md) for a summary. This document explains why the AI model (and
CLI backend) is differentiated per stage, and how to configure it.

## CLI backend

Each stage (`clarify`, `clarify_apply`, `plan`, `implement`) is also driven by a specific
**CLI backend** — which agentic coding CLI Tempa shells out to for that stage. Stored in
`config.json` under the `backends` key, default `claude` for every stage:

| Backend | CLI | Notes |
|---------|-----|-------|
| `claude` | Claude Code (`claude`) | Default. Model aliases (opus-5, sonnet-5, ...) apply. |
| `copilot` | GitHub Copilot CLI (`copilot`) | Model catalog is Copilot's own — pass a real model id or `auto`. |
| `codex` | OpenAI Codex CLI (`codex`) | Model catalog is Codex's own — pass a real model id. |

Tempa checks that the stage's model can actually run on the stage's backend before it starts
a session — see [Backend/model compatibility](#backendmodel-compatibility) below.

```bash
tempa set-backend --clarify claude --clarify-apply codex --plan copilot --implement codex
tempa show-backends                              # show the backend per stage
```

Each CLI must already be installed and authenticated on your machine (`claude`/`copilot`/
`codex` on PATH, logged in via their own `login` flow) — Tempa only invokes them, it doesn't
manage credentials. Switching a stage's backend mid-epic starts that stage's next session
fresh instead of trying to resume a session id captured under a different CLI.

Not sure which backends are actually usable right now? The dashboard's Home and Settings
pages show a live per-backend readiness checklist (installed on PATH + workspace folder
writable), with a button to re-check on demand — see
[cli-availability.md](cli-availability.md).

## Why differentiate per stage?

Each stage has a different workload profile:

- **Clarification** (`clarify`) doesn't run very often (a handful of times per project), but
  its output determines the validity of every plan & implementation that follows — it needs
  the most careful reasoning to catch subtle ambiguity/conflicts in the PRD. Default: the
  most capable model (`claude-opus-5`).
- **Clarify — apply / auto-answer** (`clarify_apply`) — applying already-decided
  answers/recommendations into the PRD/spec (`clarify --apply`, the apply half of
  `--finalize`) and auto-answering unanswered findings (`clarify --auto-answer`) — is
  mechanical compared to evaluating the PRD: it's copying an already-made decision, not
  making one. Default: a faster/cheaper model (`claude-sonnet-5`). A full stage of its own —
  its backend and reasoning effort are configured independently of `clarify`'s, since the
  optimal choice for mechanical apply work isn't necessarily the same as for evaluate (e.g.
  evaluate on Claude Opus at high effort, apply on a cheaper backend/model entirely).
- **Plan drafting** (`plan`) runs automatically (via `implement` / `implement --replan`) but,
  like `clarify`, its output determines the validity of every implementation that follows —
  it needs the most careful reasoning to lay out epics/features/tasks correctly. Default: the
  most capable model (`claude-opus-5`).
- **Implementation** (`implement`) runs repeatedly and automatically at high volume (per
  epic/feature/session), unattended — here speed & cost matter more, as long as quality stays
  adequate. Default: a faster/cheaper model that's still capable (`claude-sonnet-5`).

Because the needs differ, each stage's model can be set independently — e.g. keep Opus for
`clarify`/`plan` (critical, rarely/automatically run) but Sonnet for `implement` (runs often,
high volume), or drop everything to Haiku for a quick/cheap trial run.

## Table & commands

The AI model is stored in `config.json` under the `models` key.

| Stage | Used by | Default |
|-------|----------------|---------|
| `clarify` | `clarify` (evaluate) | `claude-opus-5` (opus-5) |
| `clarify_apply` | `clarify --apply`, `--auto-answer`, the apply half of `--finalize` | `claude-sonnet-5` (sonnet-5) |
| `plan` | plan drafting (automatic via `implement`, or `implement --replan`) | `claude-opus-5` (opus-5) |
| `implement` | `implement`, QA, `verify` | `claude-sonnet-5` (sonnet-5) |

```bash
tempa set-model --clarify opus-5 --clarify-apply sonnet-5 --plan opus-5 --implement sonnet-5
tempa set-model --implement claude-opus-5      # accepts an alias or a full model id
tempa show-models                              # show the model per stage
```

- Values accept an **alias** (`opus-5`, `sonnet-5`, `haiku-4.5`, `fable-5`) or a **full
  model id** (e.g. `claude-opus-5`) — **only when that stage's backend is `claude`**.
  Aliases are Claude-specific; for a `copilot`/`codex` stage the value is stored exactly
  as given, since those CLIs' model catalogs aren't hardcoded into Tempa.
- A stage that isn't specified keeps its previous/default value.

## Backend/model compatibility

The model field is free text, but Tempa refuses one specific mistake: **pointing a backend at
another vendor's model**. Leaving a stage on `claude-sonnet-5` after switching its backend to
`codex` used to save fine and then fail at run time with an error from the CLI that never
reached the console — this is that failure, caught up front.

Tempa recognizes model ids by **vendor family** (`claude-*` and the Claude aliases →
Anthropic; `gpt-*`, `o3-*`, `o4-*`, `codex-*` → OpenAI) rather than by an exact model list, so
the check does not go stale as vendors ship new ids. Three consequences worth knowing:

- **An id from no known family is never blocked.** `auto`, a private fine-tune, next quarter's
  release — Tempa cannot attribute it, so it passes through everywhere and reaches the CLI
  exactly as typed. The free-text model field keeps working.
- **`copilot` never mismatches.** GitHub Copilot CLI proxies several providers, so
  `copilot` + `claude-sonnet-5` and `copilot` + `gpt-5.6-sol` are both perfectly valid pairs.
- **Only the pair is wrong, never the model on its own.** The message names both halves and
  every backend that *could* run that model.

Where it applies:

| Where | Behaviour |
|---|---|
| Dashboard → Settings → AI Models | An inline red note appears under the model field as soon as the pair goes wrong, and **Save Settings** is refused with a message naming the stage. |
| `tempa set-backend` | **Refused.** The stage's model is already whatever you want it to be, so a mismatch here means the pair is simply wrong. The message names the `tempa set-model` command to run first. |
| `tempa set-model` | **Warned, but saved.** Blocking both commands would make migrating a stage impossible — each would reject the half-finished pair the other one needs first. So `set-model` then `set-backend` always works, and the transitional pair still cannot run. |
| `tempa show-models` / `show-backends` | A mismatched stage's row is marked `[!] not runnable on <backend>`. |
| Before any session starts | The run stops **before the CLI is spawned**, on the console, with the same message — and raises a `Backend/model mismatch` alert if email notifications are on. |

If a model still reaches the CLI and is rejected there (a retired id, or one this account has
no access to), Tempa recognizes the CLI's own model-rejection output and stops the same way,
rather than retrying or failing silently.

## Reasoning Effort

Each stage also has a **Reasoning Effort** setting — how hard the backend CLI should
"think" before responding — stored under the `reasoning_efforts` key in `config.json`,
default `""` (no override; the CLI/model's own default is used) for every stage.

```bash
tempa set-effort --clarify high --clarify-apply low --plan medium --implement high
tempa set-effort --implement ""                  # clear it back to the CLI/model default
tempa show-efforts                                # show the reasoning effort per stage
```

**The value must be supported by that stage's currently configured backend *and* model** —
Tempa validates this both in the dashboard (Settings → AI Models tab → the effort dropdown next to
each stage's model field only offers the valid choices for whatever's currently typed there) and
on the CLI (`set-effort` rejects an unsupported value with the list of what *is* valid). An
invalid combination never reaches the CLI.

| Backend | Levels | Per-model? |
|---|---|---|
| `claude` (Claude Code) | `low`, `medium`, `high`, `xhigh`, `max` | No — same list for every model. |
| `copilot` (GitHub Copilot CLI) | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | No — same list for every model. |
| `codex` (OpenAI Codex CLI) | depends on the model — see below | **Yes.** |

Codex is the only backend that actually varies by model (verified live against
`codex debug models` and the real API's validation error for an unsupported level):

| Model | Levels |
|---|---|
| `gpt-5.6-sol` / `gpt-5.6-terra` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `gpt-5.6-luna` / `codex-auto-review` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `gpt-5.5` / `gpt-5.4` / `gpt-5.4-mini` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh` |
| any other/future Codex model | `none`, `minimal`, `low`, `medium`, `high`, `xhigh` (conservative fallback) |

Claude sets this via `--effort <level>`, Copilot via `--reasoning-effort <level>`, Codex via
a config override (`-c model_reasoning_effort="<level>"` — there's no dedicated CLI flag).
Since Claude/Copilot don't expose finer-grained data than "one list per backend," that's the
validation ceiling for those two — Codex's real per-model catalog can go stale as Codex
adds/retires models, same tradeoff already accepted for the model-id suggestions above.
