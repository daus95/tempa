"""Prompt loading and construction.

Loads the `.md` templates from PROMPT_DIR and builds the full prompt string sent to the
configured backend CLI for each harness stage (implementation, QA, clarification, apply,
auto-answer, plan-epics, review-epics). `build_prompt` does the `${...}` placeholder
substitution and prepends the workspace's architecture principles; the higher-level
`build_*_prompt` functions assemble the per-stage substitution parameters.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tempa_config import PROMPT_DIR, get_config_path, get_sources, read_principles
from tempa_logging import log
from tempa_qa_history import earlier_report_paths, last_report_path


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
    """Return a prompt section pointing to previous QA findings if a report file exists.

    Skipped once the epic has passed QA: a report is written on every round now, including a
    passing one (its advisory notes are the point), and telling a later session that a clean
    report holds findings it "MUST fix" would send it chasing items QA deliberately cleared."""
    epic_entry = next((s for s in (config.get("epic") or []) if s.get("epic_name") == epic), None)
    if not epic_entry or epic_entry.get("qa_passed"):
        return ""
    qa_report_filename = epic_entry.get("qa_report_filename", "")
    if not qa_report_filename or not Path(qa_report_filename).exists():
        return ""
    return (
        f"PREVIOUS QA FINDINGS — MUST BE READ BEFORE IMPLEMENTATION:\n"
        f"Read the following QA report to understand the findings that must be fixed:\n"
        f"  {qa_report_filename}\n"
        f"All ❌ and ⚠️ findings in that report MUST be fixed in this implementation session.\n"
        f"Its 📝 advisory notes are NOT findings — QA verified that behaviour as correct and\n"
        f"chose not to fail it. Do not spend this session's feature budget on them, and never at\n"
        f"the expense of a ❌ or ⚠️ item.\n"
        f"{_qa_report_staleness_note(epic_entry)}\n"
        f"{_build_settled_findings_section(epic_entry, qa_report_filename)}"
    )


def _qa_report_staleness_note(epic_entry: dict) -> str:
    """Tell the session when the QA report it is being handed predates work this epic has since
    completed — or "" when the report is still current.

    An epic only gets re-QA'd once it is `done` again, so a single finding nobody can close keeps
    the epic in `require_fixing` and the SAME report is re-fed, verbatim and still labelled "MUST
    be fixed", for as many rounds as the epic lasts. Meanwhile the features around it do get
    fixed, and other epics ship — so findings the report states as fact ("no import applier
    exists yet") quietly stop being true, while the prompt keeps presenting them with the same
    authority as the ones that still hold.

    Seen live: a report three days and one shipped dependency epic out of date, whose top finding
    had been false since the day before, re-fed as mandatory for four consecutive rounds. The
    session had to discover the world had moved by grepping config.json itself, and spent the
    round re-deriving what the report should have said.

    `qa_completed_features` is the count as the QA round that wrote this report left it (see
    `_stamp_qa_completed_features`); anything completed since is work that report never saw. It's
    a count rather than a timestamp on purpose — it moves only on real progress, so it can't be
    thrown off by a clock, a re-run, or a round that changed nothing."""
    at_qa = epic_entry.get("qa_completed_features")
    if at_qa is None:
        return ""
    now = epic_entry.get("completed_features", 0)
    if now <= at_qa:
        return ""
    feature_s = "feature" if now - at_qa == 1 else "features"
    return (
        f"\n⚠ THAT REPORT IS OUT OF DATE: it was written when {at_qa} of this epic's features\n"
        f"were done; {now} are done now, so it never saw the {now - at_qa} {feature_s} completed\n"
        f"since. Some of its findings may already be closed, and any that name a dependency as\n"
        f"missing may now have it. Re-verify each ❌/⚠️ against the CURRENT code before spending\n"
        f"the session on it, and say which ones you found already satisfied in your final\n"
        f"response. This does not downgrade the ones that still hold — those must still be fixed.\n"
    )


def _build_settled_findings_section(epic_entry: dict, current_report: str) -> str:
    """List this epic's older QA reports as closed findings the session must not re-break.

    Fixing what the newest report names, in isolation, is how an epic starts cycling: a round's
    fix reroutes control flow past code an earlier round already had to have fixed, that earlier
    finding comes back, and the loop guard stops the run. The session cannot avoid a defect it
    has never been told about, so it is told — not to fix them again (they are closed), but to
    check its own change against them and leave a test behind where one is missing.

    Only reports still on disk are listed: `qa_history` outlives no file it points at, but a
    workspace can be cleaned, and a prompt naming a path that isn't there wastes a session's time
    on a failed read. Empty string when there is no older round, so the caller's f-string closes
    cleanly on a first-round epic."""
    earlier = [p for p in earlier_report_paths(epic_entry, exclude=current_report)
               if Path(p).exists()]
    if not earlier:
        return ""
    return (
        "EARLIER QA ROUNDS — ALREADY FIXED, DO NOT RE-BREAK:\n"
        "This epic has been through QA before. These older reports are settled — their findings\n"
        "were fixed and a later round verified them (oldest first):\n"
        + "".join(f"  {path}\n" for path in earlier)
        + "Do NOT re-fix them and do not spend this session's feature budget on them. Do read\n"
        "them for the code paths they name: a fix that reroutes control flow around one of those\n"
        "paths brings its finding straight back, which is the single most common way an epic ends\n"
        "up cycling through QA. The oldest report is the likeliest to be undone this way, because\n"
        "its findings are the furthest from anything currently in view. Before you finish, check\n"
        "your own changes against them, and where one of those findings has no test guarding it,\n"
        "add one as part of this session's work.\n\n"
    )


def build_session_prompt(
    config: dict,
    epic_name: str,
    is_continuation: bool = False,
    features_override: int | None = None,
    is_resumed: bool = False,
) -> str:
    """Build a full prompt for a new session, incorporating continuation and features_per_session.

    `is_resumed` is True when this session will be launched with `--resume` against the
    epic's previous session (see check_and_run / tempa_config.get_resume_implementation_sessions)
    — the spec, code, and everything else that session already read is still in its
    context, so the prompt uses "continuation_resumed" instead of "continuation": it
    drops the "re-read the spec file" instruction that would otherwise make every
    session re-pay to read the epic spec from disk."""
    params = _resolve_template_params(config, epic_name)
    epic = epic_name

    if is_continuation:
        if is_resumed:
            template = load_prompt("continuation_resumed") or load_prompt("continuation") or load_prompt("implementation")
        else:
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

    dependency_block = (
        f"MANDATORY RULE — IF A FEATURE IS BLOCKED BY A DIFFERENT, NOT-YET-IMPLEMENTED EPIC:\n"
        f"If a feature genuinely cannot be completed because it depends on functionality owned\n"
        f"by a DIFFERENT epic that hasn't been implemented yet (an out-of-order dependency — not\n"
        f"a bug in this epic's own code, and not something a workaround should paper over):\n"
        f"  1. Explain the blocker clearly in your final response, as you already would.\n"
        f"  2. READ {get_config_path()} then EDIT: on the entry with \"epic_name\": \"{epic}\",\n"
        f"     set \"blocked_by_epic\" to the exact epic_name of the epic that must be\n"
        f"     implemented first (e.g. \"EPIC-17\"). Only set this when you are confident which\n"
        f"     specific epic owns the missing dependency — leave it unset if you are unsure.\n"
        f"Do NOT violate the architecture just to make progress on this epic.\n"
    )

    qa_report_section = _build_qa_report_section(config, epic)
    prompt = (
        build_prompt(template, params) + "\n\n" + features_block + qa_report_section
        + config_rule + "\n" + dependency_block + "\n" + config_path_note
    )

    return prompt


def _build_previous_qa_findings(config: dict, epic: str, qa_output_file: Path) -> str:
    """The ${previous_qa_findings} block: point this QA round at the previous round's report and
    make it build on that round instead of forming a fresh opinion of the epic.

    Without it every round re-derives its own view from scratch and flags a different subset of
    features — not because anything regressed, but because it looked at different things. That
    shifting subset is read by the loop guard as an epic cycling through QA (a feature absent
    from one round and back in the next looks like work being undone), so it halts a run that
    was actually converging. Making each round verify the last one's findings first is what
    turns "absent from that round" into evidence that it was genuinely re-checked and passed.

    Returns the first-round line when there is no earlier report — the placeholder must always
    resolve to something, or the template is left with a dangling section header."""
    epic_entry = next((s for s in (config.get("epic") or []) if s.get("epic_name") == epic), None)
    previous = last_report_path(epic_entry or {}, exclude=str(qa_output_file))
    if not previous or not Path(previous).exists():
        return (
            "PREVIOUS QA ROUNDS:\n"
            "None — this is the first QA round for this epic. Review it in full.\n"
        )
    return (
        "PREVIOUS QA ROUND — VERIFY IT FIRST, THEN LOOK FOR WHAT IS NEW:\n"
        "This epic has already been through QA. The previous round's report is at:\n"
        f"  {previous}\n"
        "Before you look for anything new:\n"
        "  1. Read that report in full.\n"
        "  2. Re-verify every ❌ and ⚠️ item in it. One that is still broken, or that was fixed\n"
        "     and has broken again, is blocking and must be reported again — say explicitly that\n"
        "     it is a repeat, and whether the earlier fix was undone.\n"
        "  3. Its 📝 advisory notes are settled. Do NOT re-raise them as ⚠️, and do NOT raise\n"
        "     new advisory-grade observations about the same code — that round already looked\n"
        "     there and deliberately did not treat them as failures.\n"
        "Then review the rest of the epic for genuinely NEW defects. Do not re-open a question a\n"
        "previous round examined and accepted unless you have concrete evidence the behaviour is\n"
        "wrong. State in the report which findings are repeats and which are new.\n"
    )


def build_qa_prompt(config: dict, epic_name: str, qa_output_file: Path, is_continuation: bool = False) -> str:
    params = _resolve_template_params(config, epic_name)
    params["qa_output_file"] = str(qa_output_file)
    params["previous_qa_findings"] = _build_previous_qa_findings(config, epic_name, qa_output_file)
    if is_continuation:
        template = load_prompt("qa_continuation") or load_prompt("qa")
    else:
        template = load_prompt("qa")
    return build_prompt(template, params)


def _format_answer(answer: str) -> str:
    """Indent a recorded answer under its `DECIDED:` label, preserving every line. Answers
    are never truncated or summarized here — shortening one would silently change the
    decision the agent is told to treat as settled."""
    lines = answer.strip().splitlines() or [""]
    return "\n".join([lines[0]] + [f"    {line.strip()}" if line.strip() else "" for line in lines[1:]])


def _render_pending_overlay(pending: list) -> str:
    """Render already-decided-but-unapplied resolutions as the ${pending_resolutions} block.

    `pending` is dashboard_clarify_parse.pending_resolutions()'s output — computed by the
    caller (tempa_clarify) and merely formatted here, so this module stays free of any
    dashboard-half import. It arrives ordered oldest round first, and that ordering IS the
    precedence rule prompt/clarification.md states ("a later round supersedes an earlier
    one"), so it is rendered through as-is.

    The findings' `clarify:` HTML-comment markers are deliberately NOT reproduced: they are
    file syntax the answer UI depends on, and putting them in a prompt invites the agent to
    think it's looking at a file it may edit. Neither is the finding's own Recommendation —
    the recorded answer supersedes it, and showing both invites the agent to arbitrate
    between them instead of treating the answer as settled."""
    if not pending:
        return "(None — every recorded answer has already been written into the PRD documents.)"

    rounds = sorted({p.round_index for p in pending})
    lines = [f"{len(rounds)} pending round(s), {len(pending)} already-decided resolution(s)."]
    for round_index in rounds:
        items = [p for p in pending if p.round_index == round_index]
        stamp = datetime.fromtimestamp(items[0].started_at).strftime("%Y-%m-%d %H:%M")
        lines.append("")
        lines.append(
            f"--- ROUND {round_index} of {len(rounds)} — {items[0].file_name} (recorded {stamp}) ---"
        )
        for item in items:
            lines.append("")
            lines.append(f"[{item.raw_id}] {item.severity} — {item.title}")
            if item.where:
                lines.append(f"  Where: {item.where}")
            if item.question:
                lines.append(f"  Question: {item.question}")
            lines.append(f"  DECIDED: {_format_answer(item.answer)}")
    return "\n".join(lines)


def build_clarification_prompt(config: dict, skip_minor: bool = False, pending: list | None = None) -> str:
    """`pending` is the pending-resolution overlay: every answered clarification finding
    whose answer hasn't been written into the PRD yet (see
    dashboard_clarify_parse.pending_resolutions, computed by tempa_clarify._pending_overlay).
    Carrying it in the prompt is what lets a round of clarification run without an apply
    pass first — the agent evaluates the PRD as it will read once those decisions are
    applied. None/empty renders an explicit "nothing pending" line rather than an empty
    block, so the template's overlay section never has a dangling header."""
    sources = get_sources(config)
    template = load_prompt("clarification")
    params = {
        "sources.prd": sources.get("prd", ""),
        "sources.clarifications": sources.get("clarifications", ""),
        "config_path": str(get_config_path()),
        "pending_resolutions": _render_pending_overlay(pending or []),
        "finding_scope": (
            "critical or major — do NOT look for, evaluate, or report MINOR findings at all"
            if skip_minor else "critical, major, or minor"
        ),
    }
    return build_prompt(template, params)


def build_apply_clarification_prompt(config: dict, files: list[Path]) -> str:
    """`files` is the exact set of clarification result files to read/apply — the
    apply backlog (see tempa_clarify._clarification_backlog), NOT necessarily every
    file in sources.clarifications. Reading only the backlog (rather than every
    clarification file ever written) is what keeps apply's input from growing
    O(N^2) with the number of past clarification rounds."""
    sources = get_sources(config)
    template = load_prompt("apply_clarification")
    params = {
        "sources.prd": sources.get("prd", ""),
        "sources.clarifications": sources.get("clarifications", ""),
        "config_path": str(get_config_path()),
        "clarification_files": "\n".join(str(f) for f in files),
    }
    return build_prompt(template, params)


def build_auto_answer_prompt(config: dict, files: list[Path]) -> str:
    """`files` is the exact set of clarification result files that still have at
    least one unanswered finding — see tempa_clarify._clarification_backlog's
    unanswered_files. Same O(N^2)-avoidance rationale as build_apply_clarification_prompt."""
    sources = get_sources(config)
    template = load_prompt("auto_answer")
    params = {
        "sources.prd": sources.get("prd", ""),
        "sources.clarifications": sources.get("clarifications", ""),
        "config_path": str(get_config_path()),
        "clarification_files": "\n".join(str(f) for f in files),
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
