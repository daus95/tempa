"""Tests for dashboard_clarify_overlap.py — the "decided elsewhere" cross-check the
Clarification pane shows above a finding's answer controls.

The cases below are modelled on the real failure this exists to catch: round 3 decides that
voiding a sale sets `Product.is_active` back to true, round 4 rewords a delete guard and
closes with "only Archive/Unarchive changes `is_active`", and nothing puts the two in front of
the same reader until round 5 raises the contradiction as a fresh critical."""

from __future__ import annotations

import dashboard_clarify_overlap as dco


def _item(item_id, severity, heading, where, question, recommendation, answer=""):
    answer_block = f"<!-- clarify:answer-start -->\n{answer}\n<!-- clarify:answer-end -->"
    return (
        f'<!-- clarify:item id="{item_id}" severity="{severity}" -->\n'
        f"### {heading}\n"
        f"**Where:** {where}\n"
        f"**Question:** {question}\n"
        f"**Recommendation:** {recommendation}\n"
        f"**Your answer:** {answer_block}\n"
        f"<!-- clarify:enditem -->\n"
    )


def _round(clar_dir, stamp, *items):
    path = clar_dir / f"clarification-{stamp}.md"
    path.write_text("".join(items), encoding="utf-8")
    return path


def _void_reactivation(item_id="C3", answer="Yes, reactivate it."):
    return _item(
        item_id, "critical", "Voiding a sale pushes stock onto an archived product",
        "`PRD.md` — section 2 step 6", "What happens on void?",
        "Voiding a sale sets `is_active` = true on any archived product among its items.",
        answer,
    )


def _delete_guard(item_id="M2"):
    return _item(
        item_id, "major", "The delete guards never say whether archived products count",
        "`PRD.md` — section 4", "Do the guards count archived products?",
        "Count archived products in both guards. Archiving stays guarded by `stock_qty` = 0, "
        "so editing an archived product changes no stock; only Archive/Unarchive changes "
        "`is_active`.",
    )


def test_a_later_finding_is_told_which_earlier_one_named_the_same_field(tmp_path):
    _round(tmp_path, "20260826-023338", _void_reactivation())
    later = _round(tmp_path, "20260826-024325", _delete_guard())

    overlaps = dco.overlaps_for_file(tmp_path, later)

    assert list(overlaps) == ["M2"]
    (source,) = overlaps["M2"]
    assert (source.raw_id, source.file_name) == ("C3", "clarification-20260826-023338.md")
    assert source.surfaces == ("is_active",)
    assert source.decided is True


def test_the_note_only_ever_points_backwards(tmp_path):
    # The earlier round cannot be warned about a decision that did not exist when it was
    # answered — a note there would be noise on a file nobody is about to change.
    earlier = _round(tmp_path, "20260826-023338", _void_reactivation())
    _round(tmp_path, "20260826-024325", _delete_guard())

    assert dco.overlaps_for_file(tmp_path, earlier) == {}


def test_rounds_are_ordered_by_the_name_stamp_not_mtime(tmp_path):
    # Answering a finding rewrites its file, so the oldest round routinely has the newest
    # mtime. Ordering on mtime would point every note at the wrong end of the history.
    later = _round(tmp_path, "20260826-024325", _delete_guard())
    earlier = _round(tmp_path, "20260826-023338", _void_reactivation())
    earlier.touch()

    assert "M2" in dco.overlaps_for_file(tmp_path, later)
    assert dco.overlaps_for_file(tmp_path, earlier) == {}


def test_two_wordings_of_one_message_are_one_surface(tmp_path):
    _round(tmp_path, "20260826-013351", _item(
        "C3", "critical", "The supplier delete guard ignores movements",
        "`PRD.md` — section 4", "What does it say?",
        'Word the refusal "In use by N product(s) and M stock movement(s)".', "Agreed."))
    later = _round(tmp_path, "20260826-024325", _item(
        "M2", "major", "The delete guards never say whether archived products count",
        "`PRD.md` — section 4", "Do they count archived products?",
        'Word the refusal "In use by N product(s), including M archived".'))

    (source,) = dco.overlaps_for_file(tmp_path, later)["M2"]
    assert source.raw_id == "C3"
    assert source.surfaces == ('"in use by n product…"',)


def test_findings_in_the_same_round_can_overlap_each_other(tmp_path):
    # Two findings of one round that reach for the same surface are the case the evaluation
    # prompt asks to be merged; when they reach the pane unmerged, saying so is the next best
    # thing — they are about to be answered in one sitting.
    path = _round(tmp_path, "20260826-024325", _void_reactivation("C1"), _delete_guard("M2"))

    overlaps = dco.overlaps_for_file(tmp_path, path)

    assert list(overlaps) == ["M2"]
    assert overlaps["M2"][0].raw_id == "C1"


def test_an_unanswered_earlier_finding_is_flagged_as_undecided(tmp_path):
    _round(tmp_path, "20260826-023338", _void_reactivation(answer=""))
    later = _round(tmp_path, "20260826-024325", _delete_guard())

    (source,) = dco.overlaps_for_file(tmp_path, later)["M2"]
    assert source.decided is False


def test_a_followed_recommendation_counts_as_decided(tmp_path):
    # "Follow the recommendation" stores an empty body behind a mode marker, so only
    # resolved_answer can tell it apart from a finding nobody has answered.
    earlier = _void_reactivation(answer="").replace(
        "<!-- clarify:answer-start -->", '<!-- clarify:answer-start mode="recommendation" -->')
    _round(tmp_path, "20260826-023338", earlier)
    later = _round(tmp_path, "20260826-024325", _delete_guard())

    (source,) = dco.overlaps_for_file(tmp_path, later)["M2"]
    assert source.decided is True


def test_file_paths_and_literals_in_backticks_are_not_surfaces(tmp_path):
    # Every finding cites `PRD.md` and half of them say `true` — keying on either would fire
    # the note on everything, which is the same as not having it.
    _round(tmp_path, "20260826-023338", _item(
        "C1", "critical", "One", "`PRD.md` — section 1", "What?",
        "Set the flag to `true`.", "Agreed."))
    later = _round(tmp_path, "20260826-024325", _item(
        "M1", "major", "Two", "`PRD.md` — section 2", "What?", "Set the other flag to `true`."))

    assert dco.overlaps_for_file(tmp_path, later) == {}


def test_a_surface_the_whole_corpus_names_is_dropped(tmp_path):
    # `stock_qty` in one finding out of eight is a decision; `stock_qty` in every one of them
    # is the workspace's vocabulary.
    for index in range(8):
        _round(tmp_path, f"2026082{index}-010101", _item(
            f"M{index}", "major", f"Finding {index}", "`PRD.md`", "What?",
            "Guard it on `stock_qty` = 0.", "Agreed."))
    last = tmp_path / "clarification-20260827-010101.md"

    assert dco.overlaps_for_file(tmp_path, last) == {}


def test_an_empty_or_missing_folder_is_not_an_error(tmp_path):
    assert dco.overlaps_for_file(tmp_path, tmp_path / "nothing.md") == {}
    assert dco.overlaps_for_file(tmp_path / "gone", tmp_path / "nothing.md") == {}


def test_at_most_five_sources_are_reported(tmp_path):
    # Past a handful the note stops prompting a check and becomes a wall over the controls.
    # Each earlier round names a different field, so no one of them is common enough to be
    # dropped as vocabulary — the cap is what limits the list, not the generic filter.
    for index in range(7):
        _round(tmp_path, f"2026080{index}-010101", _item(
            f"E{index}", "major", f"Earlier {index}", "`PRD.md`", "What?",
            f"Store it on `field_{index}`.", "Agreed."))
    fields = " ".join(f"`field_{index}`" for index in range(7))
    later = _round(tmp_path, "20260826-024325", _item(
        "M2", "major", "Later", "`PRD.md`", "What?", f"Rewrite all of {fields}."))

    assert len(dco.overlaps_for_file(tmp_path, later)["M2"]) == 5

# ---------------------------------------------------------------------------
# applied vs still-pending sources
# ---------------------------------------------------------------------------

def _applied_hash(path):
    """What tempa_clarify._record_clarify_applied_state writes after a successful apply —
    over the DECODED text, not the raw bytes, so a CRLF checkout hashes the same as an LF one."""
    import hashlib
    text = path.read_text(encoding="utf-8")
    return {path.name: hashlib.sha256(text.encode("utf-8")).hexdigest()}


def test_every_earlier_round_is_reported_applied_or_not(tmp_path):
    """Applying a round to the PRD does not retire its decisions — a recommendation that
    rewords one is still worth reading side by side, so the note still fires. What applying
    changes is the label, not whether the source is reported."""
    earlier = _round(tmp_path, "20260826-023338", _void_reactivation())
    later = _round(tmp_path, "20260826-024325", _delete_guard())

    (fresh,) = dco.overlaps_for_file(tmp_path, later)["M2"]
    (settled,) = dco.overlaps_for_file(tmp_path, later, _applied_hash(earlier))["M2"]

    assert (fresh.raw_id, fresh.applied) == ("C3", False)
    assert (settled.raw_id, settled.applied) == ("C3", True)


def test_an_unknown_applied_state_counts_as_not_applied(tmp_path):
    # Over-warning is the safe direction: the note says "check the PRD" for something already
    # in it, rather than staying quiet about a collision no document shows.
    _round(tmp_path, "20260826-023338", _void_reactivation())
    later = _round(tmp_path, "20260826-024325", _delete_guard())

    assert dco.overlaps_for_file(tmp_path, later)["M2"][0].applied is False


def test_a_round_edited_since_its_apply_is_pending_again(tmp_path):
    # The hash is of the file's current bytes, so answering one more finding in an applied
    # round makes it unapplied again — the PRD no longer reflects what it says.
    earlier = _round(tmp_path, "20260826-023338", _void_reactivation())
    hashes = _applied_hash(earlier)
    earlier.write_text(earlier.read_text(encoding="utf-8") + "\n<!-- edited -->\n", encoding="utf-8")
    later = _round(tmp_path, "20260826-024325", _delete_guard())

    assert dco.overlaps_for_file(tmp_path, later, hashes)["M2"][0].applied is False
