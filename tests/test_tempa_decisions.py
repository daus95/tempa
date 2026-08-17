"""Tests for tempa_decisions.py: reading the "this needs a human" state off an epic.

The module is deliberately pure — it decides nothing and writes nothing, it only answers what
the config says. The judgement calls built on it live in tempa_session_outcome (when to defer),
tempa_implement (when to resume) and tempa_prompts (what to tell the session), and are tested
there. What matters here is that the three questions those callers ask are answered exactly:
which features are still waiting, which have an answer to act on, and whether the epic has
anything else it could be getting on with.
"""

from __future__ import annotations

import tempa_decisions as td


def _epic(*features, **overrides):
    epic = {"epic_name": "EPIC-04", "features": list(features)}
    epic.update(overrides)
    return epic


def _feature(fid, status, answer=None):
    feature = {"id": fid, "name": f"{fid} name", "status": status}
    if answer is not None:
        feature["blocked_answer"] = answer
    return feature


# ---------------------------------------------------------------------------
# blocked_features / answered_features — the two halves of "blocked"
# ---------------------------------------------------------------------------

def test_blocked_features_are_the_ones_still_without_an_answer():
    epic = _epic(_feature("F1", "done"), _feature("F2", "blocked"), _feature("F3", "pending"))
    assert [f["id"] for f in td.blocked_features(epic)] == ["F2"]


def test_an_answered_feature_is_no_longer_waiting():
    """The whole resume path hangs on this split: same status, and the answer is what decides
    which side of it the feature is on."""
    epic = _epic(_feature("F2", "blocked", answer="Descope it."))
    assert td.blocked_features(epic) == []
    assert [f["id"] for f in td.answered_features(epic)] == ["F2"]


def test_a_whitespace_only_answer_does_not_count_as_answered():
    """Otherwise a stray newline typed into the field would silently requeue the epic with
    nothing for the session to act on."""
    epic = _epic(_feature("F2", "blocked", answer="   \n  "))
    assert [f["id"] for f in td.blocked_features(epic)] == ["F2"]
    assert td.answered_features(epic) == []


def test_a_missing_answer_field_reads_as_unanswered():
    assert len(td.blocked_features(_epic(_feature("F2", "blocked")))) == 1


def test_neither_list_reaches_into_features_that_are_not_blocked():
    epic = _epic(_feature("F1", "require_fixing", answer="stale text"))
    assert td.blocked_features(epic) == []
    assert td.answered_features(epic) == []


def test_an_epic_with_no_features_has_nothing_waiting():
    assert td.blocked_features({"epic_name": "EPIC-04"}) == []
    assert td.answered_features({"epic_name": "EPIC-04"}) == []


# ---------------------------------------------------------------------------
# has_other_work — what keeps one question from idling a whole epic
# ---------------------------------------------------------------------------

def test_has_other_work_sees_a_pending_feature():
    assert td.has_other_work(_epic(_feature("F2", "blocked"), _feature("F3", "pending"))) is True


def test_has_other_work_sees_a_feature_still_needing_fixes():
    assert td.has_other_work(_epic(_feature("F2", "blocked"), _feature("F3", "require_fixing"))) is True


def test_has_other_work_is_false_when_only_done_and_blocked_remain():
    assert td.has_other_work(_epic(_feature("F1", "done"), _feature("F2", "blocked"))) is False


def test_a_blocked_feature_is_not_itself_other_work():
    """Otherwise an epic whose last feature is blocked would never defer — it would look busy
    forever and burn rounds until the stall guard failed it."""
    assert td.has_other_work(_epic(_feature("F2", "blocked"))) is False


# ---------------------------------------------------------------------------
# describe / answer_hint — what the human actually reads
# ---------------------------------------------------------------------------

def test_describe_carries_both_the_question_and_the_recommendation():
    text = td.describe({
        "id": "FEAT-04-04", "name": "Workflow engine",
        "blocked_question": "Migrate, or descope?",
        "blocked_recommendation": "Descope — merge semantics first.",
    })
    assert "FEAT-04-04 — Workflow engine" in text
    assert "Migrate, or descope?" in text
    assert "Descope — merge semantics first." in text


def test_describe_survives_a_feature_that_recorded_nothing():
    """A session can set the status and skip the prose. Rendering that as a bare id still beats
    raising inside the notification path that was trying to tell someone about it."""
    assert td.describe({"id": "FEAT-04-04"}) == "FEAT-04-04"


def test_answer_hint_names_the_file_the_field_and_the_way_out():
    hint = td.answer_hint(r"C:\ws\.tempa\config.json", "EPIC-04")
    assert r"C:\ws\.tempa\config.json" in hint
    assert "EPIC-04" in hint
    assert "blocked_answer" in hint
    # Dropping the feature is a legitimate answer and has to be spelled out — it's the one the
    # QA report itself keeps recommending.
    assert "drop the feature" in hint
