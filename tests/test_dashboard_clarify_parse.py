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


# ---------------------------------------------------------------------------
# _clarify_finalize_status
# ---------------------------------------------------------------------------

def test_clarify_finalize_status_no_action_yet():
    result = dcp._clarify_finalize_status({"critical": 0}, None)
    assert result == {
        "hasRun": False, "lastAction": None, "critical": 0, "ready": False,
        "round": 0, "maxRound": 0, "allowFinalizeWithCritical": False,
    }


def test_clarify_finalize_status_round_passthrough():
    result = dcp._clarify_finalize_status({"critical": 0}, "evaluate", round_=3, max_round=20)
    assert result["round"] == 3
    assert result["maxRound"] == 20


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


def test_clarify_finalize_status_allow_with_critical_still_needs_fresh_evaluate():
    result = dcp._clarify_finalize_status(
        {"critical": 2}, "apply", allow_finalize_with_critical=True
    )
    assert result["ready"] is False


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
