"""Clarification file parsing, stats, and overview — ported from the former clarify_ui.py.

Parses a clarification result file into ClarificationItem findings (via the clarify:item /
clarify:answer HTML-comment markers), computes answered/total and per-severity stats, and
builds the dashboard's file overview + finalize-readiness state."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
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
    r'<!--\s*clarify:answer-start(?:\s+mode="(?P<mode>[^"]*)")?\s*-->'
    r'(?P<answer>.*?)'
    r'<!--\s*clarify:answer-end\s*-->',
    re.DOTALL,
)


LABEL_RE = re.compile(r'\*\*(Where|Question|Recommendation|Your answer):\*\*')


HEADING_RE = re.compile(r'^\s{0,3}#{1,6}\s+(.+?)\s*$', re.MULTILINE)


FILENAME_TS_RE = re.compile(r'(\d{8})-(\d{6})')


SEVERITY_LABELS = {"critical": "Critical", "major": "Major", "minor": "Minor"}


def _file_started_at(path: Path) -> float:
    """Best-effort epoch-seconds timestamp for when this clarification round
    started: parsed from the `clarification-YYYYMMDD-HHMMSS.md` naming convention
    (see prompt/clarification.md, which stamps the name at creation and never
    renames it) so it stays stable even after the file is edited later — answering
    a finding rewrites the file and bumps its mtime, which would otherwise make an
    old round look like it just started. Falls back to the file's mtime for any
    file that doesn't match the naming convention."""
    m = FILENAME_TS_RE.search(path.name)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").timestamp()
        except ValueError:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


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
    answer_mode: str
    file: Path
    answer_start: int
    answer_end: int
    has_markers: bool

    @property
    def resolved_answer(self) -> str:
        """The effective answer text regardless of how it's stored on disk: the typed
        answer if present, otherwise the recommendation IF this item's answer was saved
        as "follow the recommendation" (answer_mode == "recommendation") — the one case
        where the persisted file deliberately doesn't carry the answer text itself, to
        avoid duplicating the recommendation (see apply_answers_to_file /
        _fill_unanswered_with_recommendations). Everywhere the code needs "what did the
        user decide" (answered/total counts, the pending overlay) should use this, not
        `existing_answer` directly — the UI's "which radio is checked" logic is the one
        exception, since it needs the raw distinction between the two."""
        if self.existing_answer:
            return self.existing_answer
        if self.answer_mode == "recommendation":
            return self.recommendation
        return ""


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
        answer_mode = am.group("mode") or ""
    else:
        existing_answer = ya_text.strip()
        answer_start = ya_abs_start
        answer_end = ya_abs_end
        has_markers = False
        answer_mode = ""

    return ClarificationItem(
        key=f"f{file_index}-{raw_id}",
        raw_id=raw_id,
        severity=match.group("severity"),
        title=title,
        where=seg_text("Where"),
        question=seg_text("Question"),
        recommendation=seg_text("Recommendation"),
        existing_answer=existing_answer,
        answer_mode=answer_mode,
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
    return (sum(1 for it in items if it.resolved_answer), len(items))


def _file_severity_stats(path: Path, timings: dict | None = None) -> dict | None:
    """Return per-file finding stats: name/path, an {answered,total} pair per severity
    (critical/major/minor), the overall answered/total, when the evaluation round that
    produced this file started ("started_at", see _file_started_at), and — if present in
    `timings` (caller's responsibility to load it, e.g. via
    dashboard_config._load_clarify_file_timings(), keyed by filename) — how long that
    evaluation and its most recent apply took ("clarify_seconds"/"apply_seconds", either
    may be None if that step hasn't happened yet or predates timing instrumentation).
    None if the file has no recognized clarification items."""
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
        if it.resolved_answer:
            by_severity[it.severity]["answered"] += 1
    answered = sum(v["answered"] for v in by_severity.values())
    file_timing = (timings or {}).get(path.name, {})
    return {
        "name": path.name, "path": path.name,
        "critical": by_severity["critical"], "major": by_severity["major"], "minor": by_severity["minor"],
        "answered": answered, "total": len(items),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "started_at": _file_started_at(path),
        "clarify_seconds": file_timing.get("clarify_seconds"),
        "apply_seconds": file_timing.get("apply_seconds"),
    }


def _latest_evaluation_findings(files: list[dict], clean_since: float = 0) -> dict:
    """True critical/major/minor counts from ONLY the most recently started
    evaluation round's file (by "started_at", see _file_started_at) — NOT summed
    across every clarification file ever produced. Every past round's file is
    deliberately kept forever as a historical record (see prompt/clarification.md)
    and its severity tags are never removed even once answered/applied, so summing
    across all of them would mean the count could never go back to 0 once a single
    critical finding had ever appeared, in any round, ever — which is exactly what
    made the finalize/implement gates get stuck reporting stale critical counts
    long after they'd actually been resolved. "Ready to finalize"/"ready to
    implement" only care about what the LATEST evaluation pass found.

    Also NOT config.json's "last_clarification_findings", which is the agent
    session's own self-reported opinion of what's "still critical" and can say 0
    right after an apply even though the finding's
    <!-- clarify:item ... severity="critical" --> tag is still sitting right there
    in the file, answered but not removed (applying edits the PRD, never the
    clarification file itself — see _record_clarify_applied_state's docstring
    below). A finding counts here whether or not it's been answered: being
    answered means a resolution was proposed, not that the file stopped listing it
    as a finding.

    `clean_since` (config.json's "last_clean_evaluation_at", stamped by
    _stamp_clean_evaluation_if_zero in tempa_clarify.py) is the one signal this
    file-based approach can't produce on its own: a fresh evaluate pass that finds
    zero findings across every severity leaves no new file behind (per
    prompt/clarification.md, the agent only writes a file when there's a finding to
    record), so without this, the gate would keep reading whatever the last
    finding-bearing file said even though a more recent, genuinely clean round has
    already superseded it. If `clean_since` is newer than the latest file's
    started_at (or there's no file at all), the round it represents wins and this
    returns all-zero."""
    latest_started_at = max((f.get("started_at", 0) for f in files), default=0)
    if clean_since and clean_since > latest_started_at:
        return {"critical": 0, "major": 0, "minor": 0}
    if not files:
        return {"critical": 0, "major": 0, "minor": 0}
    latest = max(files, key=lambda f: f.get("started_at", 0))
    return {sev: latest[sev]["total"] for sev in ("critical", "major", "minor")}


def _clarify_finalize_status(
    findings: dict, last_action: str | None, round_: int = 0, max_round: int = 0,
    allow_finalize_with_critical: bool = False, finalize_round: int = 0,
    pending_overlay_findings: int = 0,
) -> dict:
    """How ready "Finalized Clarification" is to run — the "Finalize readiness" panel's
    state, and also the actual precondition gate: the button (Home and Clarification
    Overview) stays disabled, and POST /api/clarify/run (mode=finalize) returns 409,
    until "ready" is true (see renderFinalizeGate/renderHomeWorkflow in dashboard.js,
    and _handle_clarify_run_start in dashboard_server.py). "ready" is also what the
    dashboard uses to decide whether to relabel Start Clarification -> Continue
    Clarification and explain what's still blocking Finalize.

    "ready" is True when `allow_finalize_with_critical` is on (config.json's
    "allow_finalize_with_critical", the dashboard Settings toggle; off by default) —
    that override waives every other requirement below, so Finalize is clickable even
    before clarification has ever been run: its own evaluate/apply loop is what
    establishes and then resolves the finding set, unsupervised, so there's nothing
    left to check up front. This setting never affects the separate Start
    Implementation gate (_handle_implement_run_start), which is a real gate and
    follows its own config.json setting, "implementation_start_requirement" — see
    _implement_readiness_status below.

    With the override off, "ready" is True when all of:
      - at least one clarification action has ever completed ("hasRun")
      - that most recent action was a fresh evaluate pass, not a bare apply
        ("lastAction" == "evaluate") — answering criticals and applying them isn't
        enough on its own, since applying doesn't independently re-verify against
        the live PRD the way a fresh evaluate does, and doesn't touch the
        clarification files' severity tags either
      - the most recent evaluation round's file shows zero critical findings
        (`findings`, from _latest_evaluation_findings — the actual tag count for
        that one round, not a self-reported opinion and not summed across every
        past round)

    `last_action` is config.json's "last_clarification_action" (caller's
    responsibility to load it, e.g. via dashboard_config._load_dashboard_config()) —
    stamped by tempa.py right after each `clarify` (evaluate) / `clarify --apply`
    (apply) / `clarify --finalize` (both, alternating) run — see run_clarify_once(),
    _run_apply_step(), and run_clarify_finalize() there.

    `pending_overlay_findings` (pending_overlay_stats()["findings"]) is reported back for
    display only and deliberately does NOT affect "ready": finalize CONSUMES the pending
    overlay — every evaluate pass in its loop carries it, and its compaction step is what
    writes it into the PRD — so treating an unapplied overlay as "not ready to finalize"
    would pin the button to "Continue Clarification" exactly when finalize is the thing that
    would resolve it. The gate that does require an empty overlay is Start Implementation
    (_implement_readiness_status below), because implementation reads the PRD itself.

    `round_`/`max_round`/`finalize_round` are config.json's "last_clarification_round" /
    "max_clarification_run" / "last_finalize_round" — passed straight through so the
    dashboard can show both numbers without a separate request. `round_` is a running
    total across every evaluate pass ever (manual `clarify` or one iteration of
    `clarify --finalize`) and is NOT bounded by `max_round` — only `finalize_round` is:
    it resets to 0 at the start of every `clarify --finalize` run and counts up to
    `max_round` within that one run (see run_clarify_finalize in tempa_clarify.py).
    Shown next to "Finalize readiness" is just `round_` on its own (no total, since it
    isn't bounded); `finalize_round`/`max_round` together are shown as progress next to
    the "Finalized Clarification" button instead."""
    fresh_evaluate = last_action == "evaluate"
    critical_ok = findings["critical"] == 0 or allow_finalize_with_critical
    ready = allow_finalize_with_critical or (fresh_evaluate and critical_ok)
    return {
        "hasRun": last_action is not None,
        "lastAction": last_action,
        "critical": findings["critical"],
        "ready": ready,
        "round": round_,
        "maxRound": max_round,
        "finalizeRound": finalize_round,
        "allowFinalizeWithCritical": allow_finalize_with_critical,
        "pendingOverlay": pending_overlay_findings,
    }


def _implement_readiness_status(
    findings: dict, has_run: bool, requirement: str, pending_overlay_findings: int = 0,
) -> dict:
    """Whether "Start Implementation" is currently allowed to run, per config.json's
    "implementation_start_requirement" (the dashboard Settings "Start Implementation
    requires" control; one of tempa_config.IMPLEMENTATION_START_REQUIREMENTS):
      - "no_critical_or_major" (default): zero critical AND zero major findings in the
        most recent evaluation round — the original, safest behavior.
      - "no_critical": zero critical findings; major findings may remain open.
      - "none": no clarification-findings condition at all.

    `has_run` always gates regardless of `requirement` (config.json's
    "last_clarification_action" is not None) — a workspace where clarification was
    never run has zero findings simply from having no clarification files yet, which
    would otherwise trivially satisfy every requirement level, including the default.

    `pending_overlay_findings` (pending_overlay_stats()["findings"]) also gates regardless
    of `requirement`, including "none". That setting expresses how strict to be about OPEN
    QUESTIONS; this is a different thing — a correctness invariant. Implementation reads the
    PRD/spec documents, so an answer that has been recorded but not yet applied
    (`clarify --apply`) is invisible to it: the epic would be built from a specification the
    user has already decided against. Since clarification no longer has to apply after every
    round (answers are carried in the evaluate prompt as a pending overlay — see
    pending_resolutions), this is the only thing left forcing the PRD to be current before
    any code gets written.

    This is the single source of truth shared by the server-side gate
    (_handle_implement_run_start in dashboard_server.py) and every dashboard surface
    that shows Start Implementation readiness (Home step 3, the Clarification
    overview's ready-for-implementation banner, and the Implementation page's
    readiness gate) — see /api/tree's "clarify.implementReadiness"."""
    critical_ok = requirement == "none" or findings["critical"] == 0
    major_ok = requirement in ("none", "no_critical") or findings["major"] == 0
    return {
        "hasRun": has_run,
        "critical": findings["critical"],
        "major": findings["major"],
        "requirement": requirement,
        "pendingOverlay": pending_overlay_findings,
        "ready": has_run and critical_ok and major_ok and pending_overlay_findings == 0,
    }


def _clarify_files_overview(
    clar_dir: Path, applied_hashes: dict, timings: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Every clarification result file (flat, excluding claude.md) with recognized
    findings, split into (unanswered, fully_answered), each sorted by "started_at"
    (most recently started evaluation round first — see _file_started_at). Fully
    answered files also get an "applied" bool: whether their current content (i.e.
    current answers) matches what was last applied to the PRD/spec, per
    `applied_hashes` (caller's responsibility to load it, e.g. via
    dashboard_config._load_clarify_applied_hashes()) — so the dashboard knows
    whether an "Apply Answer(s)" action is actually needed or would be a no-op.
    `timings` (dashboard_config._load_clarify_file_timings()) is passed straight
    through to _file_severity_stats for the per-file clarify/apply duration."""
    unanswered: list[dict] = []
    answered: list[dict] = []
    if not clar_dir.exists():
        return unanswered, answered
    for p in sorted(clar_dir.glob("*.md")):
        if p.name.lower() == "claude.md":
            continue
        stats = _file_severity_stats(p, timings)
        if stats is None:
            continue
        if stats["answered"] == stats["total"]:
            stats["applied"] = applied_hashes.get(stats["name"]) == stats["content_hash"]
            answered.append(stats)
        else:
            unanswered.append(stats)
    unanswered.sort(key=lambda f: f["started_at"], reverse=True)
    answered.sort(key=lambda f: f["started_at"], reverse=True)
    return unanswered, answered


@dataclass
class PendingResolution:
    """One already-decided-but-not-yet-applied finding: its question and the answer
    recorded for it. See pending_resolutions."""
    file_name: str
    started_at: float
    round_index: int
    raw_id: str
    severity: str
    title: str
    where: str
    question: str
    answer: str


def pending_resolutions(clar_dir: Path, applied_hashes: dict) -> list[PendingResolution]:
    """Every ANSWERED clarification finding whose answer isn't in the PRD yet — the
    "pending overlay" carried into each clarification evaluation so the PRD no longer has
    to be rewritten (`clarify --apply`) after every round just to keep evaluating.

    Membership is exactly the answered half of what an apply pass would write: an item
    counts iff it has an answer AND its file's current content hash doesn't match
    `applied_hashes` (config.json's "clarify_applied_hashes", the only record of applied
    state there is — see _record_clarify_applied_state in tempa_clarify.py). So an empty
    result means every recorded answer is already reflected in the PRD/spec, which is what
    the Start Implementation gate keys off (_implement_readiness_status below).

    A partially-answered file contributes its answered items and skips the rest — those
    answers are decided and absent from the PRD regardless of what else in the file is
    still open, and _clarification_backlog already sends such files to apply for the same
    reason. A file edited after a previous apply re-contributes everything it holds: the
    hash no longer matches, so the PRD can no longer be trusted to reflect it.

    Ordered OLDEST ROUND FIRST (by _file_started_at, then name). The order is load-bearing:
    prompt/clarification.md tells the agent that a later round's decision supersedes an
    earlier one, which only works if the rounds arrive in chronological order. Filenames
    happen to sort chronologically too, but that's the naming convention's doing, not a
    guarantee — sort on the parsed timestamp."""
    contributing: list[tuple[Path, float, list[ClarificationItem]]] = []
    for path in sorted(clar_dir.glob("*.md")) if clar_dir.exists() else []:
        if path.name.lower() == "claude.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        items, _ = parse_file(path, text, 0)
        if not items:
            continue
        if applied_hashes.get(path.name) == hashlib.sha256(text.encode("utf-8")).hexdigest():
            continue
        answered = [it for it in items if it.resolved_answer]
        if answered:
            contributing.append((path, _file_started_at(path), answered))

    contributing.sort(key=lambda entry: (entry[1], entry[0].name))
    pending: list[PendingResolution] = []
    for round_index, (path, started_at, items) in enumerate(contributing, start=1):
        for it in items:
            pending.append(PendingResolution(
                file_name=path.name, started_at=started_at, round_index=round_index,
                raw_id=it.raw_id, severity=it.severity, title=it.title,
                where=it.where, question=it.question, answer=it.resolved_answer,
            ))
    return pending


def pending_overlay_stats(clar_dir: Path, applied_hashes: dict) -> dict:
    """How big the pending overlay is: {"files", "findings", "chars"} — what the dashboard
    shows on the "Pending resolutions" card and what the Start Implementation gate checks
    ("findings" == 0 means the PRD is up to date).

    "chars" is an APPROXIMATION of the rendered block's size (the sum of the text actually
    carried, plus a per-item allowance for labels), not a byte-exact measurement: the block
    is rendered by _render_pending_overlay in tempa_prompts.py, which belongs to the CLI
    half and is deliberately not imported here. It's only ever displayed as a rounded "~N KB"
    hint, so a few percent either way costs nothing."""
    pending = pending_resolutions(clar_dir, applied_hashes)
    chars = sum(len(p.title) + len(p.where) + len(p.question) + len(p.answer) + 48 for p in pending)
    return {
        "files": len({p.file_name for p in pending}),
        "findings": len(pending),
        "chars": chars,
    }
