"""Prompt loading and construction.

Loads the `.md` templates from PROMPT_DIR and builds the full prompt string sent to Claude
for each harness stage (implementation, QA, clarification, apply, auto-answer, plan-epics,
review-epics). `build_prompt` does the `${...}` placeholder substitution and prepends the
workspace's architecture principles; the higher-level `build_*_prompt` functions assemble the
per-stage substitution parameters.
"""

from __future__ import annotations

from pathlib import Path

from tempa_config import PROMPT_DIR, get_config_path, get_sources, read_principles
from tempa_logging import log


def load_prompt(name: str, fallback: str = "") -> str:
    """Load a prompt template from PROMPT_DIR/<name>.md.

    Returns the file content verbatim (with its ${...} placeholders intact).
    If the file is missing, returns `fallback`; if there is no fallback either,
    logs an error and returns an empty string so the failure is visible.
    """
    path = PROMPT_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    if fallback:
        return fallback
    log(f"ERROR: prompt template '{name}' not found at {path}")
    return ""


def _substitute(template: str, parameters: dict) -> str:
    result = template
    for key, value in parameters.items():
        result = result.replace(f"${{{key}}}", value)
    return result


def _principles_block() -> str:
    """The workspace's architecture principles, framed as binding rules — or "" if unset."""
    principles = read_principles()
    if not principles:
        return ""
    return (
        "=== ARCHITECTURE PRINCIPLES — NON-NEGOTIABLE ===\n"
        "These principles are defined for this project and apply to EVERY decision you make in\n"
        "this session: the questions you raise, the answers you give, the plans you produce, the\n"
        "code you write, and the QA you perform. They outrank convention, convenience, and your\n"
        "own defaults.\n"
        "If a principle conflicts with anything else in this prompt or in the specification, do\n"
        "NOT silently pick a side — report the conflict explicitly and stop.\n"
        "\n"
        f"{principles}\n"
        "=== END ARCHITECTURE PRINCIPLES ===\n"
        "\n"
    )


def build_prompt(template: str, parameters: dict) -> str:
    """Substitute ${...} placeholders, prefixed with the architecture principles block.

    Every stage's prompt (clarify, apply, auto-answer, plan, review, implementation, QA, verify)
    is built through this one function, so prepending here is what makes the principles apply
    consistently everywhere instead of per-stage.
    """
    return _principles_block() + _substitute(template, parameters)


def _resolve_template_params(config: dict, epic_name: str) -> dict:
    """Build the full substitution dict: epic_name + sources + config_path."""
    sources = get_sources(config)
    sources_str = "\n".join(sources.values())
    params = {
        "epic": epic_name,
        "sources": sources_str,
        "config_path": str(get_config_path()),
    }
    for key, value in sources.items():
        params[f"sources.{key}"] = value
    return params


def _build_features_block(config: dict, epic: str) -> str:
    """Build a text block showing features status (done/require_fixing/pending) for the given epic."""
    session_features: list[dict] = next(
        (s.get("features", []) for s in (config.get("epic") or [])
         if s.get("epic_name") == epic),
        [],
    )
    if not session_features:
        return ""

    done = [f for f in session_features if f["status"] == "done"]
    require_fixing = [f for f in session_features if f["status"] == "require_fixing"]
    pending = [f for f in session_features if f["status"] == "pending"]

    lines = ["FEATURES FOR THIS EPIC:"]
    if done:
        lines.append("Already done (DO NOT re-implement):")
        lines.extend(f"  ✅ {f['id']} — {f['name']}" for f in done)
    if require_fixing:
        lines.append("Needs fixing — already implemented but QA findings were found (read the QA report):")
        lines.extend(f"  🔧 {f['id']} — {f['name']}" for f in require_fixing)
    if pending:
        lines.append("Needs implementing (never built):")
        lines.extend(f"  ⬜ {f['id']} — {f['name']}" for f in pending)
    lines.append("")
    return "\n".join(lines) + "\n"


def _build_qa_report_section(config: dict, epic: str) -> str:
    """Return a prompt section pointing to previous QA findings if a report file exists."""
    epic_entry = next((s for s in (config.get("epic") or []) if s.get("epic_name") == epic), None)
    if not epic_entry:
        return ""
    qa_report_filename = epic_entry.get("qa_report_filename", "")
    if not qa_report_filename or not Path(qa_report_filename).exists():
        return ""
    return (
        f"PREVIOUS QA FINDINGS — MUST BE READ BEFORE IMPLEMENTATION:\n"
        f"Read the following QA report to understand the findings that must be fixed:\n"
        f"  {qa_report_filename}\n"
        f"All ❌ and ⚠️ findings in that report MUST be fixed in this implementation session.\n\n"
    )


def build_session_prompt(
    config: dict,
    epic_name: str,
    is_continuation: bool = False,
    features_override: int | None = None,
) -> str:
    """Build a full prompt for a new session, incorporating continuation and features_per_session."""
    params = _resolve_template_params(config, epic_name)
    epic = epic_name

    if is_continuation:
        template = load_prompt("continuation") or load_prompt("implementation")
    else:
        template = load_prompt("implementation")

    # Prepend critical config update rule so it's not missed at the end of a long session
    features_per_session = features_override if features_override is not None else config.get("features_per_session")

    features_block = _build_features_block(config, epic)

    config_path_note = (
        f"AGENT CONFIG FILE: {get_config_path()}\n"
        f"Always use Read first, then Edit. Do not use Glob — use the absolute path above.\n"
    )
    if features_per_session:
        config_rule = (
            f"MANDATORY RULE — DO NOT SKIP:\n"
            f"Implement or fix the features from the 🔧 and ⬜ list above, one at a time, in order.\n"
            f"Every time you finish 1 feature:\n"
            f"  1. READ {get_config_path()} then EDIT:\n"
            f"     a. Find the entry with \"epic_name\": \"{epic}\" in the \"epic\" array\n"
            f"     b. In that entry's \"features\" array, find the object whose \"id\" = the feature just finished\n"
            f"     c. Change its \"status\" to \"done\"\n"
            f"     d. Increment \"completed_features\" by 1\n"
            f"     e. If ALL features now have status \"done\":\n"
            f"        ALSO change \"status\" AT THE EPIC LEVEL (the field directly on that entry,\n"
            f"        not the \"status\" inside the \"features\" array) to \"done\"\n"
            f"\n"
            f"Limit for this session: at most {features_per_session} feature(s).\n"
            f"Stop once you reach the limit (or all features are done).\n"
        )
    else:
        config_rule = (
            f"MANDATORY RULE — DO THIS EVERY TIME YOU FINISH 1 FEATURE (before moving to the next one):\n"
            f"A 🔧 feature means it's already implemented but has QA findings — fix it per the QA report.\n"
            f"A ⬜ feature means it was never built — implement it from scratch.\n"
            f"  1. READ {get_config_path()} then EDIT:\n"
            f"     a. Find the entry with \"epic_name\": \"{epic}\" in the \"epic\" array\n"
            f"     b. In that entry's \"features\" array, find the object whose \"id\" = the feature just finished\n"
            f"     c. Change its \"status\" to \"done\"\n"
            f"     d. Increment \"completed_features\" by 1\n"
            f"     ⚠ \"status\" AT THE EPIC LEVEL (the field directly on the entry, not inside the \"features\" array)\n"
            f"       is the overall epic status — DO NOT change it until all features are done\n"
            f"\n"
            f"MANDATORY RULE — AFTER THE ENTIRE EPIC IS DONE:\n"
            f"  READ {get_config_path()} then EDIT: change \"status\" AT THE EPIC LEVEL\n"
            f"  (the field directly on the entry \"epic_name\": \"{epic}\", not \"status\" inside the \"features\" array)\n"
            f"  to \"done\".\n"
            f"  ⚠ CRITICAL: If the epic's \"status\" is not changed to \"done\",\n"
            f"    the agent runner will keep restarting this session endlessly.\n"
        )

    qa_report_section = _build_qa_report_section(config, epic)
    prompt = build_prompt(template, params) + "\n\n" + features_block + qa_report_section + config_rule + "\n" + config_path_note

    return prompt


def build_qa_prompt(config: dict, epic_name: str, qa_output_file: Path, is_continuation: bool = False) -> str:
    params = _resolve_template_params(config, epic_name)
    params["qa_output_file"] = str(qa_output_file)
    if is_continuation:
        template = load_prompt("qa_continuation") or load_prompt("qa")
    else:
        template = load_prompt("qa")
    return build_prompt(template, params)


def build_clarification_prompt(config: dict) -> str:
    sources = get_sources(config)
    template = load_prompt("clarification")
    params = {
        "sources.prd": sources.get("prd", ""),
        "sources.clarifications": sources.get("clarifications", ""),
        "config_path": str(get_config_path()),
    }
    return build_prompt(template, params)


def build_apply_clarification_prompt(config: dict) -> str:
    sources = get_sources(config)
    template = load_prompt("apply_clarification")
    params = {
        "sources.prd": sources.get("prd", ""),
        "sources.clarifications": sources.get("clarifications", ""),
        "config_path": str(get_config_path()),
    }
    return build_prompt(template, params)


def build_auto_answer_prompt(config: dict) -> str:
    sources = get_sources(config)
    template = load_prompt("auto_answer")
    params = {
        "sources.prd": sources.get("prd", ""),
        "sources.clarifications": sources.get("clarifications", ""),
        "config_path": str(get_config_path()),
    }
    return build_prompt(template, params)


def _plan_epics_params(config: dict) -> dict:
    """Substitution params for the plan-epics / review prompts (no single epic context)."""
    sources = get_sources(config)
    return {
        "sources.prd": sources.get("prd", ""),
        "sources.docs": sources.get("docs", ""),
        "sources.epics": sources.get("epics", ""),
        "sources.apps": sources.get("apps", ""),
        "config_path": str(get_config_path()),
    }


def build_plan_epics_prompt(config: dict) -> str:
    return build_prompt(load_prompt("plan_epics"), _plan_epics_params(config))


def build_review_epics_prompt(config: dict) -> str:
    return build_prompt(load_prompt("review_epics"), _plan_epics_params(config))
