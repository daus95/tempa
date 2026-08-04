"""Tests for tempa_clarify.py's clarification-backlog pre-check (see
_resolve_clarification_backlog, used by `clarify --finalize` before its evaluate/apply
loop starts): _clarification_backlog splits existing clarification result files into
"unanswered" vs "answered but not yet applied", and _fill_unanswered_with_recommendations
mechanically copies each unanswered finding's own Recommendation text into its answer
(no agent/LLM call — the "follow recommendation" resolution)."""

from __future__ import annotations

import hashlib

import tempa_clarify as tc


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


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# _clarification_backlog
# ---------------------------------------------------------------------------

def test_backlog_empty_dir(tmp_path):
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == []
    assert unapplied == []


def test_backlog_unanswered_file_detected(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(_item("1", "major", "T", "w", "q", "do X", ""), encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == [f]
    assert unapplied == []


def test_backlog_fully_answered_but_never_applied(tmp_path):
    f = tmp_path / "a.md"
    text = _item("1", "major", "T", "w", "q", "do X", "already answered")
    f.write_text(text, encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == []
    assert unapplied == [f]


def test_backlog_fully_answered_and_hash_matches_applied_hashes(tmp_path):
    f = tmp_path / "a.md"
    text = _item("1", "major", "T", "w", "q", "do X", "already answered")
    f.write_text(text, encoding="utf-8")
    applied_hashes = {"a.md": _hash(text)}
    unanswered, unapplied = tc._clarification_backlog(tmp_path, applied_hashes)
    assert unanswered == []
    assert unapplied == []


def test_backlog_answered_but_content_changed_since_last_apply(tmp_path):
    f = tmp_path / "a.md"
    original = _item("1", "major", "T", "w", "q", "do X", "first answer")
    applied_hashes = {"a.md": _hash(original)}
    edited = _item("1", "major", "T", "w", "q", "do X", "edited answer")
    f.write_text(edited, encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, applied_hashes)
    assert unanswered == []
    assert unapplied == [f]


def test_backlog_mixed_files_split_correctly(tmp_path):
    unanswered_file = tmp_path / "unanswered.md"
    unanswered_file.write_text(_item("1", "critical", "T1", "w", "q", "rec", ""), encoding="utf-8")

    unapplied_file = tmp_path / "unapplied.md"
    unapplied_file.write_text(_item("1", "major", "T2", "w", "q", "rec", "answer"), encoding="utf-8")

    applied_text = _item("1", "minor", "T3", "w", "q", "rec", "answer")
    applied_file = tmp_path / "applied.md"
    applied_file.write_text(applied_text, encoding="utf-8")

    applied_hashes = {"applied.md": _hash(applied_text)}
    unanswered, unapplied = tc._clarification_backlog(tmp_path, applied_hashes)
    assert unanswered == [unanswered_file]
    assert unapplied == [unapplied_file]


def test_backlog_ignores_claude_md_and_files_with_no_recognized_items(tmp_path):
    (tmp_path / "claude.md").write_text("not a clarification file", encoding="utf-8")
    (tmp_path / "empty.md").write_text("# nothing recognized here\n", encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == []
    assert unapplied == []


def test_backlog_file_with_at_least_one_unanswered_item_counts_as_unanswered(tmp_path):
    f = tmp_path / "a.md"
    text = _item("1", "critical", "T1", "w", "q", "rec1", "answered") + _item(
        "2", "major", "T2", "w", "q", "rec2", ""
    )
    f.write_text(text, encoding="utf-8")
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == [f]
    assert unapplied == []


# ---------------------------------------------------------------------------
# _fill_unanswered_with_recommendations
# ---------------------------------------------------------------------------

def test_fill_writes_recommendation_into_empty_answer(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(_item("1", "major", "T", "w", "q", "do the thing", ""), encoding="utf-8")
    filled = tc._fill_unanswered_with_recommendations([f])
    assert filled == 1
    items, _ = tc.parse_file(f, f.read_text(encoding="utf-8"), 0)
    assert items[0].existing_answer == "do the thing"


def test_fill_leaves_already_answered_items_untouched(tmp_path):
    f = tmp_path / "a.md"
    original = _item("1", "major", "T", "w", "q", "do the thing", "my own answer")
    f.write_text(original, encoding="utf-8")
    filled = tc._fill_unanswered_with_recommendations([f])
    assert filled == 0
    assert f.read_text(encoding="utf-8") == original


def test_fill_multiple_unanswered_items_in_one_file(tmp_path):
    f = tmp_path / "a.md"
    text = (
        _item("1", "critical", "T1", "w", "q", "rec one", "")
        + _item("2", "major", "T2", "w", "q", "rec two", "already answered")
        + _item("3", "minor", "T3", "w", "q", "rec three", "")
    )
    f.write_text(text, encoding="utf-8")
    filled = tc._fill_unanswered_with_recommendations([f])
    assert filled == 2
    items, _ = tc.parse_file(f, f.read_text(encoding="utf-8"), 0)
    by_id = {it.raw_id: it for it in items}
    assert by_id["1"].existing_answer == "rec one"
    assert by_id["2"].existing_answer == "already answered"
    assert by_id["3"].existing_answer == "rec three"


def test_fill_no_markers_form_still_gets_filled(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(
        _item("1", "major", "T", "w", "q", "the recommendation", "", wrap_answer=False),
        encoding="utf-8",
    )
    filled = tc._fill_unanswered_with_recommendations([f])
    assert filled == 1
    items, _ = tc.parse_file(f, f.read_text(encoding="utf-8"), 0)
    assert items[0].existing_answer == "the recommendation"


def test_fill_across_multiple_files(tmp_path):
    f1 = tmp_path / "a.md"
    f1.write_text(_item("1", "major", "T1", "w", "q", "rec a", ""), encoding="utf-8")
    f2 = tmp_path / "b.md"
    f2.write_text(_item("1", "minor", "T2", "w", "q", "rec b", ""), encoding="utf-8")
    filled = tc._fill_unanswered_with_recommendations([f1, f2])
    assert filled == 2
    items1, _ = tc.parse_file(f1, f1.read_text(encoding="utf-8"), 0)
    items2, _ = tc.parse_file(f2, f2.read_text(encoding="utf-8"), 0)
    assert items1[0].existing_answer == "rec a"
    assert items2[0].existing_answer == "rec b"


def test_fill_then_backlog_reclassifies_as_unapplied(tmp_path):
    """After filling, a file that was "unanswered" should reclassify as
    "unapplied" (fully answered, but not yet reflected in applied_hashes) —
    exactly the handoff _resolve_clarification_backlog relies on before its
    single apply pass."""
    f = tmp_path / "a.md"
    f.write_text(_item("1", "major", "T", "w", "q", "do X", ""), encoding="utf-8")
    tc._fill_unanswered_with_recommendations([f])
    unanswered, unapplied = tc._clarification_backlog(tmp_path, {})
    assert unanswered == []
    assert unapplied == [f]
