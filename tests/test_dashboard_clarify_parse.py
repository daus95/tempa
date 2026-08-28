"""Tests for dashboard_clarify_parse.py — regex-based clarification file parsing and
stats. After the Phase 0 testability refactor, _clarify_finalize_status and
_clarify_files_overview take their config-derived inputs as explicit parameters, so this
module has no dependency on dashboard_config and needs no monkeypatching."""

from __future__ import annotations

from pathlib import Path

import dashboard_clarify_parse as dcp


def _item(item_id, severity, heading, where, question, recommendation, answer, wrap_answer=True):
    if wrap_answer:
        answer_block = f"<!-- clarify:answer-start -->\n{answer}\n<!-- clarify:answer-end -->"
    else:
        answer_block = answer
    return (
        f'<!-- clarify:item id="{item_id}" severity="{severity}" -->\n'
        f"### {heading}\n"
        f"**Where:** {where}\n"
        f"**Question:** {question}\n"
        f"**Recommendation:** {recommendation}\n"
        f"**Your answer:** {answer_block}\n"
        f"<!-- clarify:enditem -->\n"
    )


def _item_followed_recommendation(item_id, severity, heading, where, question, recommendation):
    """A finding saved via "Follow the recommendation": a mode="recommendation" marker
    with an empty body — no duplicated recommendation text."""
    answer_block = '<!-- clarify:answer-start mode="recommendation" -->\n\n<!-- clarify:answer-end -->'
    return (
        f'<!-- clarify:item id="{item_id}" severity="{severity}" -->\n'
        f"### {heading}\n"
        f"**Where:** {where}\n"
        f"**Question:** {question}\n"
        f"**Recommendation:** {recommendation}\n"
        f"**Your answer:** {answer_block}\n"
        f"<!-- clarify:enditem -->\n"
    )


# ---------------------------------------------------------------------------
# parse_file
# ---------------------------------------------------------------------------

def test_parse_file_single_item_unanswered():
    text = _item("1", "critical", "Title", "here", "what?", "do X", "")
    items, blocks = dcp.parse_file(Path("f.md"), text, 0)
    assert len(items) == 1
    assert items[0].existing_answer == ""
    assert items[0].has_markers is True
    assert items[0].severity == "critical"


def test_parse_file_single_item_answered():
    text = _item("1", "major", "Title", "here", "what?", "do X", "My answer text")
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert items[0].existing_answer == "My answer text"
    assert items[0].has_markers is True


def test_parse_file_answer_without_markers():
    text = _item("1", "minor", "Title", "here", "what?", "do X", "plain answer", wrap_answer=False)
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert len(items) == 1
    assert items[0].has_markers is False
    assert items[0].existing_answer == "plain answer"


def test_parse_file_answer_block_without_label():
    """A round that writes the answer block with no "**Your answer:**" label above it
    (format drift observed in live workspaces) must still parse into a reviewable
    finding — the answer-start/end markers are the authoritative anchors."""
    text = (
        '<!-- clarify:item id="C1" severity="critical" -->\n'
        "### The OAuth attach transfer leaves live sessions\n"
        "**Where:** PRD.md — §8\n"
        "**Question:** what?\n"
        "**Recommendation:** revoke the rows\n"
        "<!-- clarify:answer-start -->\n"
        "\n"
        "<!-- clarify:answer-end -->\n"
        "<!-- clarify:enditem -->\n"
    )
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert len(items) == 1
    assert items[0].raw_id == "C1"
    assert items[0].severity == "critical"
    assert items[0].existing_answer == ""
    assert items[0].resolved_answer == ""
    assert items[0].has_markers is True


def test_parse_file_multiple_items_all_severities():
    text = (
        _item("1", "critical", "A", "w", "q", "r", "")
        + _item("2", "major", "B", "w", "q", "r", "")
        + _item("3", "minor", "C", "w", "q", "r", "")
    )
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert [it.severity for it in items] == ["critical", "major", "minor"]


def test_parse_file_missing_your_answer_label_skipped_but_kept_as_text_block():
    text = (
        '<!-- clarify:item id="1" severity="critical" -->\n'
        "### Title\n"
        "**Where:** here\n"
        "**Question:** what?\n"
        "**Recommendation:** do X\n"
        "<!-- clarify:enditem -->\n"
    )
    items, blocks = dcp.parse_file(Path("f.md"), text, 0)
    assert items == []
    assert any(kind == "text" for kind, _ in blocks)


def test_parse_file_no_label_matches_at_all():
    text = (
        '<!-- clarify:item id="1" severity="critical" -->\n'
        "just some prose, no labels\n"
        "<!-- clarify:enditem -->"
    )
    items, blocks = dcp.parse_file(Path("f.md"), text, 0)
    assert items == []
    assert blocks == [("text", text)]


def test_parse_file_empty_id_falls_back_to_synthesized():
    text = (
        '<!-- clarify:item id="" severity="critical" -->\n'
        "**Where:** w\n**Question:** q\n**Recommendation:** r\n"
        "**Your answer:** <!-- clarify:answer-start -->\n<!-- clarify:answer-end -->\n"
        "<!-- clarify:enditem -->\n"
    )
    items, _ = dcp.parse_file(Path("f.md"), text, 3)
    assert items[0].raw_id.startswith("item3-")


def test_parse_file_title_from_heading_and_fallback():
    with_heading = _item("1", "critical", "My Heading", "w", "q", "r", "")
    items, _ = dcp.parse_file(Path("f.md"), with_heading, 0)
    assert items[0].title == "My Heading"

    without_heading = (
        '<!-- clarify:item id="42" severity="major" -->\n'
        "**Where:** w\n**Question:** q\n**Recommendation:** r\n"
        "**Your answer:** <!-- clarify:answer-start -->\n<!-- clarify:answer-end -->\n"
        "<!-- clarify:enditem -->\n"
    )
    items2, _ = dcp.parse_file(Path("f.md"), without_heading, 0)
    assert items2[0].title == "Finding 42"


def test_parse_file_missing_optional_label_defaults_to_empty_string():
    # "Recommendation" omitted entirely (only Where/Question/Your answer present) —
    # seg_text() must return "" for it rather than raising a KeyError.
    text = (
        '<!-- clarify:item id="1" severity="minor" -->\n'
        "**Where:** w\n**Question:** q\n"
        "**Your answer:** <!-- clarify:answer-start -->\n<!-- clarify:answer-end -->\n"
        "<!-- clarify:enditem -->\n"
    )
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert items[0].recommendation == ""
    assert items[0].where == "w"
    assert items[0].question == "q"


def test_parse_file_text_blocks_preserved_in_order():
    text = "Intro text\n" + _item("1", "critical", "T", "w", "q", "r", "") + "Trailing text\n"
    items, blocks = dcp.parse_file(Path("f.md"), text, 0)
    kinds = [k for k, _ in blocks]
    assert kinds == ["text", "item", "text"]


def test_parse_file_blank_surrounding_text_not_added_as_block():
    text = "   \n" + _item("1", "critical", "T", "w", "q", "r", "") + "   \n"
    items, blocks = dcp.parse_file(Path("f.md"), text, 0)
    kinds = [k for k, _ in blocks]
    assert kinds == ["item"]


def test_parse_file_empty_input():
    assert dcp.parse_file(Path("f.md"), "", 0) == ([], [])


def test_parse_file_no_markers_at_all():
    text = "Just plain markdown, no clarify markers."
    items, blocks = dcp.parse_file(Path("f.md"), text, 0)
    assert items == []
    assert blocks == [("text", text)]


# ---------------------------------------------------------------------------
# mode="recommendation" marker / resolved_answer — "follow the recommendation" no
# longer duplicates the recommendation text into the file; it's recorded via the
# marker's mode attribute instead, with an empty body.
# ---------------------------------------------------------------------------

def test_parse_file_followed_recommendation_marker_has_empty_answer_and_mode_set():
    text = _item_followed_recommendation("1", "major", "T", "w", "q", "do the thing")
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert items[0].existing_answer == ""
    assert items[0].answer_mode == "recommendation"
    assert items[0].has_markers is True


def test_parse_file_own_answer_has_no_mode():
    text = _item("1", "major", "T", "w", "q", "do the thing", "my own text")
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert items[0].answer_mode == ""


def test_resolved_answer_prefers_existing_answer_when_present():
    text = _item("1", "major", "T", "w", "q", "do the thing", "my own text")
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert items[0].resolved_answer == "my own text"


def test_resolved_answer_falls_back_to_recommendation_when_mode_is_recommendation():
    text = _item_followed_recommendation("1", "major", "T", "w", "q", "do the thing")
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert items[0].resolved_answer == "do the thing"


def test_resolved_answer_empty_when_unanswered():
    text = _item("1", "major", "T", "w", "q", "do the thing", "")
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert items[0].resolved_answer == ""


def test_forward_only_legacy_duplicated_text_without_mode_marker_is_not_reclassified():
    # A pre-existing file where the recommendation text was copied verbatim into "Your
    # answer" (the old bug) but with no mode="recommendation" marker must NOT be
    # reclassified as "followed recommendation" — forward-only, by design.
    text = _item("1", "major", "T", "w", "q", "do the thing", "do the thing")
    items, _ = dcp.parse_file(Path("f.md"), text, 0)
    assert items[0].existing_answer == "do the thing"
    assert items[0].answer_mode == ""
    assert items[0].resolved_answer == "do the thing"


# ---------------------------------------------------------------------------
# file_answer_status
# ---------------------------------------------------------------------------

def test_file_answer_status_unreadable_file(tmp_path):
    assert dcp.file_answer_status(tmp_path / "missing.md") == (0, 0)


def test_file_answer_status_no_findings(tmp_path):
    path = tmp_path / "f.md"
    path.write_text("no findings here", encoding="utf-8")
    assert dcp.file_answer_status(path) == (0, 0)


def test_file_answer_status_mixed(tmp_path):
    path = tmp_path / "f.md"
    text = (
        _item("1", "critical", "A", "w", "q", "r", "answered")
        + _item("2", "major", "B", "w", "q", "r", "")
        + _item("3", "minor", "C", "w", "q", "r", "also answered")
    )
    path.write_text(text, encoding="utf-8")
    assert dcp.file_answer_status(path) == (2, 3)


def test_file_answer_status_counts_followed_recommendation_as_answered(tmp_path):
    path = tmp_path / "f.md"
    text = (
        _item_followed_recommendation("1", "critical", "A", "w", "q", "r")
        + _item("2", "major", "B", "w", "q", "r", "")
    )
    path.write_text(text, encoding="utf-8")
    assert dcp.file_answer_status(path) == (1, 2)


# ---------------------------------------------------------------------------
# _file_severity_stats
# ---------------------------------------------------------------------------

def test_file_severity_stats_no_findings_returns_none(tmp_path):
    path = tmp_path / "f.md"
    path.write_text("nothing", encoding="utf-8")
    assert dcp._file_severity_stats(path) is None


def test_file_severity_stats_mixed(tmp_path):
    import hashlib
    path = tmp_path / "f.md"
    text = (
        _item("1", "critical", "A", "w", "q", "r", "answered")
        + _item("2", "critical", "B", "w", "q", "r", "")
        + _item("3", "major", "C", "w", "q", "r", "answered")
    )
    path.write_text(text, encoding="utf-8")

    stats = dcp._file_severity_stats(path)
    assert stats["critical"] == {"answered": 1, "total": 2}
    assert stats["major"] == {"answered": 1, "total": 1}
    assert stats["minor"] == {"answered": 0, "total": 0}
    assert stats["answered"] == 2
    assert stats["total"] == 3
    assert stats["content_hash"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_file_severity_stats_unreadable_returns_none(tmp_path):
    assert dcp._file_severity_stats(tmp_path / "missing.md") is None


# ---------------------------------------------------------------------------
# _latest_evaluation_findings
# ---------------------------------------------------------------------------

def test_latest_evaluation_findings_uses_only_the_most_recently_started_file():
    files = [
        {"started_at": 100, "critical": {"total": 2}, "major": {"total": 1}, "minor": {"total": 0}},
        {"started_at": 200, "critical": {"total": 1}, "major": {"total": 0}, "minor": {"total": 3}},
    ]
    assert dcp._latest_evaluation_findings(files) == {"critical": 1, "major": 0, "minor": 3}


def test_latest_evaluation_findings_empty_list():
    assert dcp._latest_evaluation_findings([]) == {"critical": 0, "major": 0, "minor": 0}


def test_latest_evaluation_findings_clean_since_newer_than_latest_file_wins():
    # Regression: a stale finding-bearing file from an old round must not keep
    # blocking finalize/implement once a more recent evaluate pass came back
    # completely clean (and therefore left no new file behind to read instead).
    files = [{"started_at": 100, "critical": {"total": 2}, "major": {"total": 1}, "minor": {"total": 0}}]
    assert dcp._latest_evaluation_findings(files, clean_since=200) == {"critical": 0, "major": 0, "minor": 0}


def test_latest_evaluation_findings_clean_since_older_than_latest_file_is_ignored():
    # A clean stamp from BEFORE the latest finding-bearing file must not mask
    # findings a later round genuinely reported.
    files = [{"started_at": 200, "critical": {"total": 2}, "major": {"total": 1}, "minor": {"total": 0}}]
    assert dcp._latest_evaluation_findings(files, clean_since=100) == {"critical": 2, "major": 1, "minor": 0}


def test_latest_evaluation_findings_clean_since_with_no_files_at_all():
    assert dcp._latest_evaluation_findings([], clean_since=100) == {"critical": 0, "major": 0, "minor": 0}


# ---------------------------------------------------------------------------
# _clarify_finalize_status
# ---------------------------------------------------------------------------

def test_clarify_finalize_status_no_action_yet():
    result = dcp._clarify_finalize_status({"critical": 0}, None)
    assert result == {
        "hasRun": False, "lastAction": None, "critical": 0, "ready": False,
        "round": 0, "maxRound": 0, "finalizeRound": 0, "allowFinalizeWithCritical": False,
        "pendingOverlay": 0,
    }


def test_clarify_finalize_status_round_passthrough():
    result = dcp._clarify_finalize_status(
        {"critical": 0}, "evaluate", round_=3, max_round=20, finalize_round=1)
    assert result["round"] == 3
    assert result["maxRound"] == 20
    assert result["finalizeRound"] == 1


def test_clarify_finalize_status_apply_alone_not_enough():
    result = dcp._clarify_finalize_status({"critical": 0}, "apply")
    assert result["ready"] is False


def test_clarify_finalize_status_fresh_evaluate_zero_critical_ready():
    result = dcp._clarify_finalize_status({"critical": 0}, "evaluate")
    assert result["ready"] is True
    assert result["hasRun"] is True


def test_clarify_finalize_status_fresh_evaluate_with_critical_not_ready():
    result = dcp._clarify_finalize_status({"critical": 2}, "evaluate")
    assert result["ready"] is False


def test_clarify_finalize_status_allow_with_critical_overrides():
    result = dcp._clarify_finalize_status(
        {"critical": 2}, "evaluate", allow_finalize_with_critical=True
    )
    assert result["ready"] is True
    assert result["allowFinalizeWithCritical"] is True


def test_clarify_finalize_status_allow_with_critical_waives_fresh_evaluate_too():
    result = dcp._clarify_finalize_status(
        {"critical": 2}, "apply", allow_finalize_with_critical=True
    )
    assert result["ready"] is True


def test_clarify_finalize_status_allow_with_critical_ready_before_any_run():
    result = dcp._clarify_finalize_status(
        {"critical": 0}, None, allow_finalize_with_critical=True
    )
    assert result["hasRun"] is False
    assert result["ready"] is True


# ---------------------------------------------------------------------------
# _clarify_files_overview
# ---------------------------------------------------------------------------

def test_clarify_files_overview_dir_missing(tmp_path):
    unanswered, answered = dcp._clarify_files_overview(tmp_path / "nope", {})
    assert unanswered == []
    assert answered == []


def test_clarify_files_overview_excludes_claude_md_case_insensitive(tmp_path):
    (tmp_path / "CLAUDE.MD").write_text(_item("1", "critical", "A", "w", "q", "r", ""), encoding="utf-8")
    unanswered, answered = dcp._clarify_files_overview(tmp_path, {})
    assert unanswered == []
    assert answered == []


def test_clarify_files_overview_split_and_sorted(tmp_path):
    (tmp_path / "b_partial.md").write_text(
        _item("1", "critical", "A", "w", "q", "r", ""), encoding="utf-8")
    (tmp_path / "a_full.md").write_text(
        _item("1", "minor", "A", "w", "q", "r", "answered"), encoding="utf-8")

    unanswered, answered = dcp._clarify_files_overview(tmp_path, {})
    assert [f["name"] for f in unanswered] == ["b_partial.md"]
    assert [f["name"] for f in answered] == ["a_full.md"]


def test_clarify_files_overview_applied_flag_match_and_mismatch(tmp_path):
    text = _item("1", "minor", "A", "w", "q", "r", "answered")
    path = tmp_path / "full.md"
    path.write_text(text, encoding="utf-8")

    import hashlib
    current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    _, answered_match = dcp._clarify_files_overview(tmp_path, {"full.md": current_hash})
    assert answered_match[0]["applied"] is True

    _, answered_mismatch = dcp._clarify_files_overview(tmp_path, {"full.md": "stale-hash"})
    assert answered_mismatch[0]["applied"] is False


def test_clarify_files_overview_skips_files_with_no_findings(tmp_path):
    (tmp_path / "garbage.md").write_text("no findings here at all", encoding="utf-8")
    unanswered, answered = dcp._clarify_files_overview(tmp_path, {})
    assert unanswered == []
    assert answered == []


# ---------------------------------------------------------------------------
# pending_resolutions / pending_overlay_stats — the overlay carried into every
# clarification evaluation (answered findings not yet written into the PRD).
# ---------------------------------------------------------------------------

def _hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_pending_resolutions_empty_dir(tmp_path):
    assert dcp.pending_resolutions(tmp_path / "missing", {}) == []
    assert dcp.pending_resolutions(tmp_path, {}) == []


def test_pending_resolutions_never_applied_file_contributes_everything(tmp_path):
    text = _item("C1", "critical", "Token lifetime", "PRD 4.2", "expire?", "30 days", "30 days, rotating")
    (tmp_path / "clarification-20260101-101500.md").write_text(text, encoding="utf-8")

    pending = dcp.pending_resolutions(tmp_path, {})
    assert len(pending) == 1
    assert pending[0].raw_id == "C1"
    assert pending[0].severity == "critical"
    assert pending[0].title == "Token lifetime"
    assert pending[0].where == "PRD 4.2"
    assert pending[0].question == "expire?"
    assert pending[0].answer == "30 days, rotating"
    assert pending[0].round_index == 1


def test_pending_resolutions_carries_full_text_for_followed_recommendation(tmp_path):
    # The persisted file doesn't store the recommendation text a second time, but the
    # overlay carried into the next clarification round must still see the full
    # resolution text — that's what resolved_answer reconstructs.
    text = _item_followed_recommendation("C1", "critical", "T", "PRD 4.2", "expire?", "30 days")
    (tmp_path / "clarification-20260101-101500.md").write_text(text, encoding="utf-8")

    pending = dcp.pending_resolutions(tmp_path, {})
    assert len(pending) == 1
    assert pending[0].answer == "30 days"


def test_pending_resolutions_skips_already_applied_file(tmp_path):
    text = _item("C1", "critical", "T", "w", "q", "r", "decided")
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    assert dcp.pending_resolutions(tmp_path, {"a.md": _hash(text)}) == []


def test_pending_resolutions_partially_answered_file_contributes_only_answered_items(tmp_path):
    text = (_item("C1", "critical", "A", "w", "q1", "r1", "decided")
            + _item("C2", "major", "B", "w", "q2", "r2", ""))
    (tmp_path / "a.md").write_text(text, encoding="utf-8")

    pending = dcp.pending_resolutions(tmp_path, {})
    assert [p.raw_id for p in pending] == ["C1"]


def test_pending_resolutions_reappears_after_edit_following_an_apply(tmp_path):
    # A file edited since the last apply can no longer be trusted to match the PRD, so
    # everything it holds goes back into the overlay.
    original = _item("C1", "critical", "T", "w", "q", "r", "first answer")
    path = tmp_path / "a.md"
    path.write_text(original, encoding="utf-8")
    applied = {"a.md": _hash(original)}
    assert dcp.pending_resolutions(tmp_path, applied) == []

    path.write_text(_item("C1", "critical", "T", "w", "q", "r", "revised answer"), encoding="utf-8")
    pending = dcp.pending_resolutions(tmp_path, applied)
    assert [p.answer for p in pending] == ["revised answer"]


def test_pending_resolutions_excludes_claude_md_and_finding_less_files(tmp_path):
    (tmp_path / "claude.md").write_text(_item("C1", "critical", "T", "w", "q", "r", "a"), encoding="utf-8")
    (tmp_path / "notes.md").write_text("just prose, no findings", encoding="utf-8")
    assert dcp.pending_resolutions(tmp_path, {}) == []


def test_pending_resolutions_ordered_oldest_round_first(tmp_path):
    # Names sort the other way round from the timestamps they carry — ordering must follow
    # _file_started_at, since "a later round supersedes an earlier one" depends on it.
    (tmp_path / "a-clarification-20260201-090000.md").write_text(
        _item("C1", "critical", "T", "w", "q", "r", "newer"), encoding="utf-8")
    (tmp_path / "b-clarification-20260101-090000.md").write_text(
        _item("C1", "critical", "T", "w", "q", "r", "older"), encoding="utf-8")

    pending = dcp.pending_resolutions(tmp_path, {})
    assert [p.answer for p in pending] == ["older", "newer"]
    assert [p.round_index for p in pending] == [1, 2]


def test_pending_overlay_stats_counts_files_and_findings(tmp_path):
    (tmp_path / "clarification-20260101-090000.md").write_text(
        _item("C1", "critical", "A", "w", "q", "r", "one") + _item("C2", "major", "B", "w", "q", "r", "two"),
        encoding="utf-8")
    (tmp_path / "clarification-20260102-090000.md").write_text(
        _item("C1", "critical", "C", "w", "q", "r", "three"), encoding="utf-8")

    stats = dcp.pending_overlay_stats(tmp_path, {})
    assert stats["files"] == 2
    assert stats["findings"] == 3
    assert stats["chars"] > 0


def test_pending_overlay_stats_empty(tmp_path):
    assert dcp.pending_overlay_stats(tmp_path, {}) == {"files": 0, "findings": 0, "chars": 0}


# ---------------------------------------------------------------------------
# _implement_readiness_status — the pending overlay is a hard gate
# ---------------------------------------------------------------------------

_CLEAN = {"critical": 0, "major": 0, "minor": 0}


def test_implement_readiness_blocked_by_pending_overlay_even_with_no_requirement():
    # "none" says don't gate on OPEN QUESTIONS. It doesn't license implementing from a PRD
    # that's missing decisions the user already made.
    result = dcp._implement_readiness_status(_CLEAN, True, "none", 3)
    assert result["ready"] is False
    assert result["pendingOverlay"] == 3


def test_implement_readiness_ready_when_overlay_is_empty():
    assert dcp._implement_readiness_status(_CLEAN, True, "no_critical_or_major", 0)["ready"] is True


def test_implement_readiness_overlay_defaults_to_zero():
    # Existing callers that don't pass the argument keep their previous behavior.
    assert dcp._implement_readiness_status(_CLEAN, True, "no_critical_or_major")["ready"] is True


def test_implement_readiness_blocked_while_the_critical_sweep_is_still_running():
    """A critical-only round reports zero majors because it never looked for any, and a clean
    one writes no findings file at all — so these zeroes are unmeasured, not answers."""
    result = dcp._implement_readiness_status(
        _CLEAN, True, "no_critical_or_major", 0, severity_sweep_pending=True)
    assert result["ready"] is False
    assert result["severitySweepPending"] is True


def test_the_critical_sweep_does_not_gate_a_requirement_that_ignores_majors():
    """"no_critical" says majors may remain open, so a round that measured only criticals
    answers exactly the question that setting asks."""
    for requirement in ("no_critical", "none"):
        result = dcp._implement_readiness_status(
            _CLEAN, True, requirement, 0, severity_sweep_pending=True)
        assert result["ready"] is True
        assert result["severitySweepPending"] is False


def test_implement_readiness_sweep_flag_defaults_to_not_pending():
    # Callers that don't pass it keep their previous behavior.
    assert dcp._implement_readiness_status(_CLEAN, True, "no_critical_or_major")["ready"] is True


def test_clarify_finalize_status_reports_overlay_without_gating_on_it():
    # Finalize CONSUMES the overlay, so it must stay "ready" while one is pending.
    result = dcp._clarify_finalize_status({"critical": 0}, "evaluate", pending_overlay_findings=7)
    assert result["pendingOverlay"] == 7
    assert result["ready"] is True
