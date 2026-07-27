# AI Model per Stage

See [README.md](../README.md) for a summary. This document explains why the AI model is
differentiated per stage, and how to configure it.

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
  model id** (e.g. `claude-opus-5`).
- A stage that isn't specified keeps its previous/default value.
