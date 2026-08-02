# AI Model & CLI Backend per Stage

See [README.md](../README.md) for a summary. This document explains why the AI model (and
CLI backend) is differentiated per stage, and how to configure it.

## CLI backend

Each stage (`clarify`, `plan`, `implement`) is also driven by a specific **CLI backend** —
which agentic coding CLI Tempa shells out to for that stage. Stored in `config.json` under
the `backends` key, default `claude` for every stage:

| Backend | CLI | Notes |
|---------|-----|-------|
| `claude` | Claude Code (`claude`) | Default. Model aliases (opus-5, sonnet-5, ...) apply. |
| `copilot` | GitHub Copilot CLI (`copilot`) | Model catalog is Copilot's own — pass a real model id or `auto`. |
| `codex` | OpenAI Codex CLI (`codex`) | Model catalog is Codex's own — pass a real model id. |

```bash
tempa set-backend --clarify claude --plan copilot --implement codex
tempa show-backends                              # show the backend per stage
```

Each CLI must already be installed and authenticated on your machine (`claude`/`copilot`/
`codex` on PATH, logged in via their own `login` flow) — Tempa only invokes them, it doesn't
manage credentials. Switching a stage's backend mid-epic starts that stage's next session
fresh instead of trying to resume a session id captured under a different CLI.

## Why differentiate per stage?

Each stage has a different workload profile:

- **Clarification** (`clarify`) doesn't run very often (a handful of times per project), but
  its output determines the validity of every plan & implementation that follows — it needs
  the most careful reasoning to catch subtle ambiguity/conflicts in the PRD. Default: the
  most capable model (`claude-opus-5`).
- **Plan drafting** (`plan`) and **implementation** (`implement`) run repeatedly and
  automatically at high volume (per epic/feature/session), unattended — here speed & cost
  matter more, as long as quality stays adequate. Default: a faster/cheaper model that's
  still capable (`claude-sonnet-5`).

Because the needs differ, each stage's model can be set independently — e.g. keep Opus for
`clarify` (critical, rarely runs) but Sonnet for `implement` (runs often, high volume), or
drop everything to Haiku for a quick/cheap trial run.

## Table & commands

The AI model is stored in `config.json` under the `models` key.

| Stage | Used by | Default |
|-------|----------------|---------|
| `clarify` | `clarify` | `claude-opus-5` (opus-5) |
| `plan` | plan drafting (automatic via `implement`, or `implement --replan`) | `claude-sonnet-5` (sonnet-5) |
| `implement` | `implement`, QA, `verify` | `claude-sonnet-5` (sonnet-5) |

```bash
tempa set-model --clarify opus-5 --plan sonnet-5 --implement sonnet-5
tempa set-model --implement claude-opus-5      # accepts an alias or a full model id
tempa show-models                              # show the model per stage
```

- Values accept an **alias** (`opus-5`, `sonnet-5`, `haiku-4.5`, `fable-5`) or a **full
  model id** (e.g. `claude-opus-5`) — **only when that stage's backend is `claude`**.
  Aliases are Claude-specific; for a `copilot`/`codex` stage the value is stored exactly
  as given, since those CLIs' model catalogs aren't hardcoded into Tempa.
- A stage that isn't specified keeps its previous/default value.

## Reasoning Effort

Each stage also has a **Reasoning Effort** setting — how hard the backend CLI should
"think" before responding — stored under the `reasoning_efforts` key in `config.json`,
default `""` (no override; the CLI/model's own default is used) for every stage.

```bash
tempa set-effort --clarify high --plan medium --implement high
tempa set-effort --implement ""                  # clear it back to the CLI/model default
tempa show-efforts                                # show the reasoning effort per stage
```

**The value must be supported by that stage's currently configured backend *and* model** —
Tempa validates this both in the dashboard (Settings → the effort dropdown next to each
stage's model field only offers the valid choices for whatever's currently typed there) and
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
