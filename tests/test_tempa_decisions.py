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


# ---------------------------------------------------------------------------
# Recorded answers — the sidecar that keeps a decision from being lost
# ---------------------------------------------------------------------------

def _blocked_config(answer="", status="blocked"):
    return {"epic": [{
        "epic_name": "EPIC-04", "status": "deferred",
        "features": [
            {"id": "F1", "name": "one", "status": "done"},
            {"id": "F2", "name": "two", "status": status, "blocked_answer": answer,
             "blocked_question": "Migrate, or descope?",
             "blocked_recommendation": "Descope for now."},
        ],
    }]}


def test_record_answer_round_trips_through_pending_answers(isolate_tempa_paths):
    td.record_answer("EPIC-04", "F2", "Descope it.")

    pending = td.pending_answers()
    assert len(pending) == 1
    _, payload = pending[0]
    assert payload["epic_name"] == "EPIC-04"
    assert payload["feature_id"] == "F2"
    assert payload["answer"] == "Descope it."
    assert payload["drop"] is False
    assert payload["written_at"]


def test_record_answer_leaves_no_temp_file_behind(isolate_tempa_paths):
    td.record_answer("EPIC-04", "F2", "Descope it.")
    names = sorted(p.name for p in td.get_decisions_dir().iterdir())
    assert names == ["EPIC-04__F2.json"]


def test_re_answering_the_same_feature_overwrites_rather_than_accumulates(isolate_tempa_paths):
    td.record_answer("EPIC-04", "F2", "First thought.")
    td.record_answer("EPIC-04", "F2", "Actually, descope it.")

    pending = td.pending_answers()
    assert len(pending) == 1
    assert pending[0][1]["answer"] == "Actually, descope it."


def test_two_features_answered_at_once_are_two_files(isolate_tempa_paths):
    """One file per answer is the point — a single shared file would be two writers racing on
    the very thing this exists to stop racing on."""
    td.record_answer("EPIC-04", "F2", "a")
    td.record_answer("EPIC-05", "F1", "b")
    assert len(td.pending_answers()) == 2


def test_a_label_with_path_separators_cannot_escape_the_decisions_folder(isolate_tempa_paths):
    td.record_answer("../../EPIC-04", "F2/../..", "Descope it.")
    written = list(td.get_decisions_dir().iterdir())
    assert len(written) == 1
    assert written[0].parent == td.get_decisions_dir()


def test_pending_answers_skips_a_corrupt_file_instead_of_raising(isolate_tempa_paths):
    """One unreadable sidecar must not stop the others being applied, nor stop the runner from
    polling at all."""
    td.record_answer("EPIC-04", "F2", "Descope it.")
    (td.get_decisions_dir() / "broken.json").write_text("{not json", encoding="utf-8")

    pending = td.pending_answers()
    assert [p[1]["feature_id"] for p in pending] == ["F2"]


def test_pending_answers_skips_a_file_that_names_no_feature(isolate_tempa_paths):
    directory = td.get_decisions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "half.json").write_text('{"epic_name": "EPIC-04"}', encoding="utf-8")
    assert td.pending_answers() == []


def test_pending_answers_is_empty_when_nothing_has_been_answered(isolate_tempa_paths):
    assert td.pending_answers() == []


def test_apply_answer_writes_the_decision_onto_the_feature(isolate_tempa_paths):
    config = _blocked_config()
    assert td.apply_answer_to_config(config, "EPIC-04", "F2", "Descope it.") is True
    assert config["epic"][0]["features"][1]["blocked_answer"] == "Descope it."
    assert config["epic"][0]["features"][1]["status"] == "blocked"


def test_apply_answer_reports_no_change_when_it_is_already_there(isolate_tempa_paths):
    config = _blocked_config(answer="Descope it.")
    assert td.apply_answer_to_config(config, "EPIC-04", "F2", "Descope it.") is False


def test_apply_answer_with_drop_marks_the_feature_done(isolate_tempa_paths):
    config = _blocked_config()
    assert td.apply_answer_to_config(config, "EPIC-04", "F2", "No longer wanted.", drop=True) is True
    feature = config["epic"][0]["features"][1]
    assert feature["status"] == "done"
    assert feature["blocked_answer"] == "No longer wanted."


def test_apply_answer_on_a_feature_that_is_not_there_changes_nothing(isolate_tempa_paths):
    config = _blocked_config()
    assert td.apply_answer_to_config(config, "EPIC-04", "NOPE", "x") is False
    assert td.apply_answer_to_config(config, "EPIC-99", "F2", "x") is False


def test_apply_pending_answers_applies_a_recorded_decision(isolate_tempa_paths):
    config = _blocked_config()
    td.record_answer("EPIC-04", "F2", "Descope it.")

    assert td.apply_pending_answers(config) is True
    assert config["epic"][0]["features"][1]["blocked_answer"] == "Descope it."


def test_a_recorded_answer_is_kept_until_the_feature_has_left_blocked(isolate_tempa_paths):
    """Retiring it the moment it lands in config.json would reopen the exact window this
    closes — the answer is only safe once the epic is actually back in the queue."""
    config = _blocked_config()
    td.record_answer("EPIC-04", "F2", "Descope it.")

    td.apply_pending_answers(config)
    assert td.apply_pending_answers(config) is False, "already applied, nothing to change"
    assert len(td.pending_answers()) == 1, "retired while the feature was still blocked"


def test_a_recorded_answer_is_retired_once_it_has_been_acted_on(isolate_tempa_paths):
    config = _blocked_config()
    td.record_answer("EPIC-04", "F2", "Descope it.")
    td.apply_pending_answers(config)

    # What _resume_answered_decisions does to it on the next poll.
    config["epic"][0]["features"][1]["status"] = "require_fixing"

    assert td.apply_pending_answers(config) is False
    assert td.pending_answers() == []


def test_a_recorded_answer_survives_being_clobbered_by_another_writer(isolate_tempa_paths):
    """The durability property this whole mechanism exists for: the runner or the spawned agent
    saving a config it read before the answer landed wipes the field, and the next poll simply
    puts it back."""
    config = _blocked_config()
    td.record_answer("EPIC-04", "F2", "Descope it.")
    td.apply_pending_answers(config)

    config["epic"][0]["features"][1]["blocked_answer"] = ""

    assert td.apply_pending_answers(config) is True
    assert config["epic"][0]["features"][1]["blocked_answer"] == "Descope it."


def test_a_recorded_answer_for_a_feature_that_no_longer_exists_is_retired(isolate_tempa_paths):
    """A re-plan can take the feature out from under a recorded answer. Keeping it would mean
    retrying forever against a plan that has moved on."""
    td.record_answer("EPIC-04", "GONE", "Descope it.")

    assert td.apply_pending_answers(_blocked_config()) is False
    assert td.pending_answers() == []


def test_find_feature_reports_the_epic_even_when_the_feature_is_missing(isolate_tempa_paths):
    epic, feature = td.find_feature(_blocked_config(), "EPIC-04", "NOPE")
    assert epic is not None and epic["epic_name"] == "EPIC-04"
    assert feature is None


def test_answer_hint_points_at_the_dashboard_as_well_as_the_file(isolate_tempa_paths):
    """The hint reaches the CLI, the halt log and the decision email — places with no button —
    so it has to name both ways of answering."""
    hint = td.answer_hint("/tmp/config.json", "EPIC-04")
    assert "blocked_answer" in hint
    assert "dashboard" in hint
