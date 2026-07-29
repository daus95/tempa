"""Clarification file parsing, stats, and overview — ported from the former clarify_ui.py.

Parses a clarification result file into ClarificationItem findings (via the clarify:item /
clarify:answer HTML-comment markers), computes answered/total and per-severity stats, and
builds the dashboard's file overview + finalize-readiness state."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Clarification answering — ported from the former clarify_ui.py.
# ---------------------------------------------------------------------------
ITEM_RE = re.compile(
    r'<!--\s*clarify:item\s+id="(?P<id>[^"]*)"\s+severity="(?P<severity>critical|major|minor)"\s*-->'
    r'(?P<body>.*?)'
    r'<!--\s*clarify:enditem\s*-->',
    re.DOTALL,
)


ANSWER_RE = re.compile(
    r'<!--\s*clarify:answer-start\s*-->(?P<answer>.*?)<!--\s*clarify:answer-end\s*-->',
    re.DOTALL,
)


LABEL_RE = re.compile(r'\*\*(Where|Question|Recommendation|Your answer):\*\*')


HEADING_RE = re.compile(r'^\s{0,3}#{1,6}\s+(.+?)\s*$', re.MULTILINE)


SEVERITY_LABELS = {"critical": "Critical", "major": "Major", "minor": "Minor"}


@dataclass


class ClarificationItem:
    key: str
    raw_id: str
    severity: str
    title: str
    where: str
    question: str
    recommendation: str
    existing_answer: str
    file: Path
    answer_start: int
    answer_end: int
    has_markers: bool


def _parse_item_match(match: re.Match, text: str, path: Path, file_index: int) -> ClarificationItem | None:
    body = match.group("body")
    body_abs_start = match.start("body")
    raw_id = match.group("id") or f"item{file_index}-{match.start()}"

    label_matches = list(LABEL_RE.finditer(body))
    if not label_matches:
        return None
    preamble = body[: label_matches[0].start()]

    segments: dict[str, tuple[int, int]] = {}
    for i, lm in enumerate(label_matches):
        seg_start = lm.end()
        seg_end = label_matches[i + 1].start() if i + 1 < len(label_matches) else len(body)
        segments[lm.group(1)] = (seg_start, seg_end)

    if "Your answer" not in segments:
        return None

    def seg_text(name: str) -> str:
        if name not in segments:
            return ""
        s, e = segments[name]
        return body[s:e].strip()

    heading_m = HEADING_RE.search(preamble)
    title = heading_m.group(1).strip() if heading_m else f"Finding {raw_id}"

    ya_start, ya_end = segments["Your answer"]
    ya_abs_start = body_abs_start + ya_start
    ya_abs_end = body_abs_start + ya_end
    ya_text = text[ya_abs_start:ya_abs_end]

    am = ANSWER_RE.search(ya_text)
    if am:
        existing_answer = am.group("answer").strip()
        answer_start = ya_abs_start + am.start(0)
        answer_end = ya_abs_start + am.end(0)
        has_markers = True
    else:
        existing_answer = ya_text.strip()
        answer_start = ya_abs_start
        answer_end = ya_abs_end
        has_markers = False

    return ClarificationItem(
        key=f"f{file_index}-{raw_id}",
        raw_id=raw_id,
        severity=match.group("severity"),
        title=title,
        where=seg_text("Where"),
        question=seg_text("Question"),
        recommendation=seg_text("Recommendation"),
        existing_answer=existing_answer,
        file=path,
        answer_start=answer_start,
        answer_end=answer_end,
        has_markers=has_markers,
    )


def parse_file(path: Path, text: str, file_index: int) -> tuple[list[ClarificationItem], list[tuple[str, object]]]:
    """Return (items, blocks). `blocks` is the document in order — ('text', str) for
    plain markdown in between/around findings, ('item', ClarificationItem) for each
    recognized finding — so the rendered page mirrors the source file's structure."""
    items: list[ClarificationItem] = []
    blocks: list[tuple[str, object]] = []
    pos = 0
    for m in ITEM_RE.finditer(text):
        if m.start() > pos:
            prefix = text[pos:m.start()]
            if prefix.strip():
                blocks.append(("text", prefix))
        item = _parse_item_match(m, text, path, file_index)
        if item is not None:
            items.append(item)
            blocks.append(("item", item))
        else:
            blocks.append(("text", m.group(0)))
        pos = m.end()
    if pos < len(text):
        tail = text[pos:]
        if tail.strip():
            blocks.append(("text", tail))
    return items, blocks


def file_answer_status(path: Path) -> tuple[int, int]:
    """Return (answered, total) recognized clarification findings in `path`. (0, 0) if
    the file can't be read or has no recognized findings."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return (0, 0)
    items, _ = parse_file(path, text, 0)
    if not items:
        return (0, 0)
    return (sum(1 for it in items if it.existing_answer), len(items))


def _file_severity_stats(path: Path) -> dict | None:
    """Return per-file finding stats: name/path, an {answered,total} pair per severity
    (critical/major/minor), and the overall answered/total. None if the file has no
    recognized clarification items."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    items, _ = parse_file(path, text, 0)
    if not items:
        return None
    by_severity = {sev: {"answered": 0, "total": 0} for sev in ("critical", "major", "minor")}
    for it in items:
        by_severity[it.severity]["total"] += 1
        if it.existing_answer:
            by_severity[it.severity]["answered"] += 1
    answered = sum(v["answered"] for v in by_severity.values())
    return {
        "name": path.name, "path": path.name,
        "critical": by_severity["critical"], "major": by_severity["major"], "minor": by_severity["minor"],
        "answered": answered, "total": len(items),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _live_clarification_findings(files: list[dict]) -> dict:
    """True critical/major/minor counts, computed directly from the severity tags
    currently present across the given clarification files (as returned by
    _clarify_files_overview) — NOT config.json's "last_clarification_findings",
    which is the Claude session's own self-reported opinion of what's "still
    critical" and can say 0 right after an apply even though the finding's
    <!-- clarify:item ... severity="critical" --> tag is still sitting right there
    in the file, answered but not removed (applying edits the PRD, never the
    clarification file itself — see _record_clarify_applied_state's docstring
    below). A finding counts here whether or not it's been answered: being
    answered means a resolution was proposed, not that the file stopped listing it
    as a finding."""
    totals = {"critical": 0, "major": 0, "minor": 0}
    for f in files:
        for sev in totals:
            totals[sev] += f[sev]["total"]
    return totals


def _clarify_finalize_status(findings: dict, last_action: str | None) -> dict:
    """Whether "Finalized Clarification" is currently allowed to run.

    Requires all of:
      - at least one clarification action has ever completed ("hasRun")
      - that most recent action was a fresh evaluate pass, not a bare apply
        ("lastAction" == "evaluate") — answering criticals and applying them isn't
        enough on its own, since applying doesn't independently re-verify against
        the live PRD the way a fresh evaluate does, and doesn't touch the
        clarification files' severity tags either
      - the clarification files currently show zero critical findings (`findings`,
        from _live_clarification_findings — the actual tag count, not a
        self-reported opinion)

    `last_action` is config.json's "last_clarification_action" (caller's
    responsibility to load it, e.g. via dashboard_config._load_dashboard_config()) —
    stamped by tempa.py right after each `clarify` (evaluate) / `clarify --apply`
    (apply) / `clarify --finalize` (both, alternating) run — see run_clarify_once(),
    _run_apply_step(), and run_clarify_finalize() there."""
    fresh_evaluate = last_action == "evaluate"
    ready = fresh_evaluate and findings["critical"] == 0
    return {
        "hasRun": last_action is not None,
        "lastAction": last_action,
        "critical": findings["critical"],
        "ready": ready,
    }


def _clarify_files_overview(clar_dir: Path, applied_hashes: dict) -> tuple[list[dict], list[dict]]:
    """Every clarification result file (flat, excluding claude.md) with recognized
    findings, split into (unanswered, fully_answered), each sorted by name. Fully
    answered files also get an "applied" bool: whether their current content (i.e.
    current answers) matches what was last applied to the PRD/spec, per
    `applied_hashes` (caller's responsibility to load it, e.g. via
    dashboard_config._load_clarify_applied_hashes()) — so the dashboard knows
    whether an "Apply Answer(s)" action is actually needed or would be a no-op."""
    unanswered: list[dict] = []
    answered: list[dict] = []
    if not clar_dir.exists():
        return unanswered, answered
    for p in sorted(clar_dir.glob("*.md")):
        if p.name.lower() == "claude.md":
            continue
        stats = _file_severity_stats(p)
        if stats is None:
            continue
        if stats["answered"] == stats["total"]:
            stats["applied"] = applied_hashes.get(stats["name"]) == stats["content_hash"]
            answered.append(stats)
        else:
            unanswered.append(stats)
    return unanswered, answered
