"""Clarification-pane endpoints: reading a findings file, writing answers back into it,
and the server-side gate on starting a Finalized Clarification run.

Like dashboard_api_spec, these take what they need explicitly and return the
`(status, payload)` the handler sends verbatim. Reads and writes are confined to
`clar_dir` by `_resolve_within`.
"""

from __future__ import annotations

from pathlib import Path

import tempa_config
from dashboard_clarify_overlap import overlaps_for_file
from dashboard_clarify_parse import (
    _clarify_files_overview,
    _clarify_finalize_status,
    _latest_evaluation_findings,
    file_answer_status,
    parse_file,
)
from dashboard_clarify_render import _render_blocks_html, render_finding_peek_html
from dashboard_config import (
    _load_clarify_applied_hashes,
    _load_clarify_file_timings,
    _load_dashboard_config,
)
from dashboard_spec import _resolve_within
from dashboard_spec_refs import make_linkifier

Response = tuple[int, dict]


def apply_answers_to_file(path: Path, payload: list[dict]) -> tuple[int, int]:
    """Write the given answers into `path` (one clarification result file) and return
    its updated (answered, total) counts."""
    text = path.read_text(encoding="utf-8")
    items, _ = parse_file(path, text, 0)
    items_by_key = {it.key: it for it in items}

    edits: list[tuple[int, int, str]] = []
    for entry in payload:
        item = items_by_key.get(entry.get("id"))
        if item is None:
            continue
        mode = entry.get("mode")
        if mode == "recommendation" and item.recommendation:
            # Don't duplicate the recommendation text into the file — it's already
            # shown once under "Recommendation:". Record the choice via the marker's
            # mode attribute instead; ClarificationItem.resolved_answer reconstructs
            # the full text for anything that needs it (answered counts, the pending
            # overlay carried into the next clarification round).
            new_text = ""
            start_marker = '<!-- clarify:answer-start mode="recommendation" -->'
        else:
            new_text = (entry.get("answer") or "").strip()
            start_marker = "<!-- clarify:answer-start -->"
        if item.has_markers:
            replacement = f"{start_marker}\n{new_text}\n<!-- clarify:answer-end -->"
        else:
            replacement = f"\n{start_marker}\n{new_text}\n<!-- clarify:answer-end -->\n"
        edits.append((item.answer_start, item.answer_end, replacement))

    for start, end, replacement in sorted(edits, key=lambda s: s[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    path.write_text(text, encoding="utf-8")
    return file_answer_status(path)


def read_file(clar_dir: Path, rel: str, prd_dir: Path | None = None) -> Response:
    """One clarification file rendered for the pane: its findings as HTML, plus the
    severity summary and answered/total counts shown above them.

    `prd_dir` is what makes the spec references inside a finding clickable — it is resolved
    against the PRD as it stands right now, so the line a link points at is never stale.
    Optional because a caller without a PRD folder should still get a readable file, just
    without links.

    Findings are also cross-checked against every earlier round in `clar_dir` for surfaces
    that were already decided, and each match is rendered above that finding's answer
    controls (see dashboard_clarify_overlap) — the cheapest moment to notice that accepting
    this recommendation would reword a decision somebody already made."""
    target = _resolve_within(clar_dir, rel)
    if target is None or not target.is_file():
        return 404, {"ok": False, "error": "File not found."}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not read file: {e}"}
    items, blocks = parse_file(target, text, 0)
    if not items:
        return 200, {
            "ok": True, "path": rel, "name": target.name,
            "summary": "No recognized clarification items in this file.",
            "html": "<p>No recognized clarification items in this file.</p>",
            "answered": 0, "total": 0,
        }
    counts = {"critical": 0, "major": 0, "minor": 0}
    for it in items:
        counts[it.severity] += 1
    answered = sum(1 for it in items if it.resolved_answer)
    summary = (
        f"{len(items)} finding(s) — {counts['critical']} critical · "
        f"{counts['major']} major · {counts['minor']} minor"
    )
    return 200, {
        "ok": True, "path": rel, "name": target.name,
        "summary": summary,
        "html": _render_blocks_html(
            blocks, make_linkifier(prd_dir),
            overlaps_for_file(clar_dir, target, _load_clarify_applied_hashes())),
        "answered": answered, "total": len(items),
    }


def read_finding(clar_dir: Path, rel: str, item_id: str) -> Response:
    """One finding from another round, rendered read-only for the peek drawer — what a
    "Decided elsewhere" link opens (see dashboard_clarify_overlap for where those come from).

    Kept separate from read_file rather than made a mode of it: this returns one finding with
    no answer controls and a **Decided** block, and it must never be mistaken for the file the
    pane is editing. `name` is what the drawer's header shows.

    Spec references inside it are deliberately NOT linkified. The drawer is already showing
    this finding because the reader followed a link into it; a second link that replaced the
    drawer's content would lose the very thing they opened it to compare against.
    """
    target = _resolve_within(clar_dir, rel)
    if target is None or not target.is_file():
        return 404, {"ok": False, "error": "File not found."}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not read file: {e}"}
    items, _ = parse_file(target, text, 0)
    item = next((it for it in items if it.raw_id == item_id), None)
    if item is None:
        return 404, {"ok": False, "error": f"Finding {item_id} is no longer in this file."}
    return 200, {
        "ok": True, "path": rel, "id": item.raw_id,
        "name": f"{item.raw_id} — {target.name}",
        "html": render_finding_peek_html(item),
    }

def save_answers(clar_dir: Path, payload: dict | list | None, finalize_running: bool) -> Response:
    """Save hand-written answers into one clarification file.

    `finalize_running` is the authoritative lock, not a courtesy: Finalized Clarification
    auto-answers findings itself (mechanical recommendation fill + agent-applied
    resolutions), and a hand save racing with that loop's own reads/writes to the same file
    could corrupt or lose an auto-answer. The client already disables editing (see
    isClarifyFinalizeLocked in assets/js/94-clarify-answers.js) — this also covers a stale
    tab that had the file open before finalize started.
    """
    if finalize_running:
        return 409, {
            "ok": False,
            "error": "Finalized Clarification is running and auto-answering these "
                     "findings — answers are locked until it stops.",
        }
    if payload is None or not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    rel = payload.get("path", "")
    items = payload.get("items", [])
    if not isinstance(items, list):
        return 400, {"ok": False, "error": "Malformed request."}
    target = _resolve_within(clar_dir, rel)
    if target is None or not target.exists() or not target.is_file():
        return 404, {"ok": False, "error": "File no longer exists."}
    try:
        answered, total = apply_answers_to_file(target, items)
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not save file: {e}"}
    print(f"[saved] {rel} ({answered}/{total} answered)")
    return 200, {"ok": True, "path": rel, "answered": answered, "total": total}


def finalize_gate_error(clar_dir: Path) -> str | None:
    """Why Finalized Clarification may not start yet, or None if it may.

    A server-side gate, not just a disabled button client-side — mirrors the implement
    gate. `tempa clarify --finalize` itself has no awareness of this precondition and
    would happily run regardless.
    """
    unanswered, answered = _clarify_files_overview(
        clar_dir, _load_clarify_applied_hashes(), _load_clarify_file_timings()
    )
    dashboard_config = _load_dashboard_config()
    findings = _latest_evaluation_findings(
        unanswered + answered, dashboard_config.get("last_clean_evaluation_at", 0)
    )
    last_action = dashboard_config.get("last_clarification_action")
    allow_finalize_with_critical = bool(dashboard_config.get("allow_finalize_with_critical"))
    if _clarify_finalize_status(
        findings, last_action, allow_finalize_with_critical=allow_finalize_with_critical
    )["ready"]:
        return None
    error = ("Cannot finalize yet — run Start Clarification once more and confirm "
             "it shows zero critical findings first.")
    if findings["critical"] > 0 and not allow_finalize_with_critical:
        error += (" Or enable \"Allow finalizing with critical findings\" in "
                  "Settings to skip this requirement.")
    return error


def save_skip_minor(payload: dict | list | None) -> Response:
    """Persist the Clarification page's "Only evaluate critical & major findings" switch
    (config.json's "skip_minor_findings") — a narrow, single-purpose save endpoint rather
    than routing through /api/config/save, since that handler requires the full Settings
    form payload (including a required max_clarification_run) which isn't loaded if the
    Settings pane was never opened this session."""
    if payload is None or not isinstance(payload, dict) or not isinstance(payload.get("skip_minor_findings"), bool):
        return 400, {"ok": False, "error": "Malformed request."}
    skip_minor_findings = payload["skip_minor_findings"]
    config = tempa_config.load_config()
    config["skip_minor_findings"] = skip_minor_findings
    tempa_config.save_config(config)
    return 200, {"ok": True, "skipMinorFindings": skip_minor_findings}


def save_clarify_language(payload: dict | list | None) -> Response:
    """Persist the Clarification page's Evaluation -> Language selector (config.json's
    "clarification_language"), the language a clarification round writes its findings in.

    Same single-purpose shape as save_skip_minor above, and for the same reason. An
    unrecognized code is rejected rather than stored: the value is substituted into the
    clarification prompt (tempa_prompts._output_language_block), so anything not in
    CLARIFICATION_LANGUAGES would be free text reaching the agent."""
    if payload is None or not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    language = payload.get("clarification_language")
    if language not in tempa_config.CLARIFICATION_LANGUAGE_NAMES:
        return 400, {"ok": False, "error": "Unsupported language."}
    config = tempa_config.load_config()
    config["clarification_language"] = language
    tempa_config.save_config(config)
    return 200, {"ok": True, "clarificationLanguage": language}
