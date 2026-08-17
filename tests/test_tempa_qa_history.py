"""Tests for the QA round history and its loop guard (src/tempa_qa_history.py)."""

from __future__ import annotations

import pytest

from tempa_qa_history import (
    _MAX_HISTORY,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_RESET,
    append_reset_marker,
    detect_qa_loop,
    format_qa_history,
    last_report_path,
    record_qa_round,
)

STRIKES = 2
MAX_FAILS = 6


def _epic(name: str = "EPIC-03") -> dict:
    return {"epic_name": name}


def _play(epic: dict, rounds: list[list[str] | None], strikes: int = STRIKES,
          max_fails: int = MAX_FAILS) -> list[str | None]:
    """Feed `rounds` through record+detect in order and return the guard's verdict per round.
    A list of feature ids is a failing round; None is a passing one."""
    verdicts: list[str | None] = []
    for failed in rounds:
        if failed is None:
            record_qa_round(epic, VERDICT_PASS)
        else:
            record_qa_round(epic, VERDICT_FAIL, failed_ids=failed)
        verdicts.append(detect_qa_loop(epic, strikes, max_fails))
    return verdicts


# --- recording ------------------------------------------------------------------------


def test_record_qa_round_numbers_rounds_and_sorts_failed_ids():
    epic = _epic()
    first = record_qa_round(epic, VERDICT_FAIL, failed_ids=["F2", "F1"], report="r1.md")
    second = record_qa_round(epic, VERDICT_PASS)

    assert first["round"] == 1
    assert first["failed"] == ["F1", "F2"]
    assert first["report"] == "r1.md"
    assert second["round"] == 2
    assert epic["qa_history"] == [first, second]


def test_record_qa_round_keeps_only_the_newest_entries_but_keeps_counting_rounds():
    """The list is trimmed to _MAX_HISTORY, so round numbers must come from the previous entry
    rather than from len(history) — otherwise they restart (and repeat) at the cap."""
    epic = _epic()
    for i in range(_MAX_HISTORY + 3):
        record_qa_round(epic, VERDICT_FAIL, failed_ids=[f"F{i}"])

    history = epic["qa_history"]
    assert len(history) == _MAX_HISTORY
    assert [entry["round"] for entry in history] == list(range(4, _MAX_HISTORY + 4))


def test_recording_the_same_report_twice_replaces_the_round_rather_than_adding_one():
    """One report file is one QA round. A second record for the same report is that round being
    written down twice, not the epic failing again."""
    epic = _epic()
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F1"], report="r1.md")
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F1", "F2"], report="r1.md")

    assert len(epic["qa_history"]) == 1
    assert epic["qa_history"][0]["round"] == 1
    # The later call wins — it is the runner's own, authoritative record of the round.
    assert epic["qa_history"][0]["failed"] == ["F1", "F2"]


def test_rounds_without_a_report_still_append():
    epic = _epic()
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F1"])
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F2"])

    assert [entry["round"] for entry in epic["qa_history"]] == [1, 2]


def test_a_reset_marker_is_never_swallowed_by_the_duplicate_check():
    epic = _epic()
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F1"], report="r1.md")
    append_reset_marker(epic)

    assert [entry["verdict"] for entry in epic["qa_history"]] == [VERDICT_FAIL, VERDICT_RESET]


def test_an_agent_written_entry_for_the_running_round_is_absorbed_not_duplicated():
    """The failure seen in the wild: a QA agent appended its own qa_history entry — for the very
    round it was still working on — straight into config.json, and the runner then recorded that
    same round properly. The epic ended up with two identical rounds pointing at one report file,
    which is the exact fingerprint the guard reads as an epic going in circles."""
    epic = _epic()
    for failed, report in [(["F1", "F2", "F3"], "r1.md"), (["F4"], "r2.md")]:
        record_qa_round(epic, VERDICT_FAIL, failed_ids=failed, report=report)

    # What the agent wrote by hand, invented timestamp and all.
    epic["qa_history"].append({
        "round": 3, "verdict": VERDICT_FAIL, "failed": ["F4", "F5"],
        "report": "r3.md", "at": "2026-08-17T09:15:00",
    })
    # ...and then the runner records the same round, as it always does.
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F4", "F5"], report="r3.md")

    assert len(epic["qa_history"]) == 3
    assert [entry["round"] for entry in epic["qa_history"]] == [1, 2, 3]
    assert "round 4" not in format_qa_history(epic)


# --- the scenario this guard exists for -----------------------------------------------


def test_alternating_failures_trip_on_the_second_strike_not_the_first():
    """QA fails F1+F2, they get fixed, QA fails F3+F4, fixing those regresses F1+F2, and so on.
    Round 3 is the first round that shows it; the guard tolerates one before stopping."""
    epic = _epic()
    verdicts = _play(epic, [["F1", "F2"], ["F3", "F4"], ["F1", "F2"], ["F3", "F4"]])

    assert verdicts[0] is None
    assert verdicts[1] is None
    assert verdicts[2] is None  # first strike — one round of tolerance
    assert verdicts[3] is not None
    assert "F3, F4" in verdicts[3]
    assert "cycling through QA" in verdicts[3]


def test_tripping_reason_carries_the_round_by_round_history():
    epic = _epic()
    verdicts = _play(epic, [["F1", "F2"], ["F3", "F4"], ["F1", "F2"], ["F3", "F4"]])

    assert "QA rounds so far:" in verdicts[3]
    assert "round 1" in verdicts[3]


def test_strikes_are_recorded_on_the_epic():
    epic = _epic()
    _play(epic, [["F1", "F2"], ["F3", "F4"], ["F1", "F2"]])

    assert epic["qa_loop_strikes"] == 1


# --- what must NOT trip ---------------------------------------------------------------


def test_shrinking_failure_set_never_trips():
    """Genuine convergence: each round fixes something and finds nothing new."""
    epic = _epic()
    verdicts = _play(epic, [["F1", "F2", "F3"], ["F1", "F2"], ["F1"]])

    assert verdicts == [None, None, None]
    assert epic["qa_loop_strikes"] == 0


def test_the_same_feature_failing_repeatedly_is_not_a_regression():
    """A feature that never passed in between is a fix that hasn't landed yet, not work being
    undone — that case is left to the plain fail-round backstop."""
    epic = _epic()
    verdicts = _play(epic, [["F1"], ["F1"], ["F1"], ["F1"]])

    assert verdicts == [None, None, None, None]
    assert epic["qa_loop_strikes"] == 0


def test_a_pass_clears_the_strike_count():
    epic = _epic()
    _play(epic, [["F1", "F2"], ["F3", "F4"], ["F1", "F2"]])
    assert epic["qa_loop_strikes"] == 1

    _play(epic, [None])
    assert epic["qa_loop_strikes"] == 0


def test_empty_failure_sets_never_trip_a_pattern_rule():
    """A fail verdict where the agent skipped the per-feature bookkeeping fingerprints as an
    empty set. Matching those against each other would report a cycle nothing supports."""
    epic = _epic()
    verdicts = _play(epic, [[], [], [], []])

    assert verdicts == [None, None, None, None]
    assert epic["qa_loop_strikes"] == 0


def test_no_history_at_all_is_not_a_loop():
    assert detect_qa_loop(_epic(), STRIKES, MAX_FAILS) is None


def test_a_round_that_flagged_nothing_is_not_evidence_a_feature_was_re_verified():
    """Both pattern rules key off a feature being ABSENT from a round in between, read as "QA
    looked and was satisfied". A round that flagged nothing at all didn't look at anything — its
    empty set makes every feature appear re-verified, so one round of missing bookkeeping would
    otherwise read as a wholesale regression. The plain fail-round backstop still bounds it."""
    epic = _epic()
    verdicts = _play(epic, [["F1", "F2"], [], ["F1", "F2"], [], ["F1", "F2"]])

    assert verdicts == [None] * 5
    assert epic["qa_loop_strikes"] == 0


def test_a_real_round_in_between_is_still_evidence():
    """The counterpart to the test above — the guard must not have been defanged: a round that
    genuinely flagged something else in between is what a regression looks like."""
    epic = _epic()
    verdicts = _play(epic, [["F1", "F2"], ["F3"], ["F1", "F2"], ["F3"]])

    assert verdicts[3] is not None
    assert "cycling through QA" in verdicts[3]


# --- last_report_path -----------------------------------------------------------------


def test_last_report_path_returns_the_newest_round_that_wrote_one():
    epic = _epic()
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F1"], report="r1.md")
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F2"], report="r2.md")

    assert last_report_path(epic) == "r2.md"


def test_last_report_path_skips_reset_markers_and_reportless_rounds():
    epic = _epic()
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F1"], report="r1.md")
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F2"])
    append_reset_marker(epic)

    assert last_report_path(epic) == "r1.md"


def test_last_report_path_can_exclude_the_round_being_started():
    """A resumed QA session reuses the previous file name, and a round must not be pointed at its
    own half-written report as though it were a finished earlier one."""
    epic = _epic()
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F1"], report="r1.md")
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F2"], report="r2.md")

    assert last_report_path(epic, exclude="r2.md") == "r1.md"


def test_last_report_path_with_no_history():
    assert last_report_path(_epic()) == ""
    assert last_report_path({}) == ""


# --- the pattern-free backstop --------------------------------------------------------


def test_backstop_trips_on_repeated_failures_with_no_usable_pattern():
    """The rules above all read feature statuses only the LLM writes. When it writes none, they
    correctly refuse to guess — so this is what guarantees the loop still terminates."""
    epic = _epic()
    verdicts = _play(epic, [[]] * MAX_FAILS)

    assert verdicts[:-1] == [None] * (MAX_FAILS - 1)
    assert verdicts[-1] is not None
    assert f"failed QA {MAX_FAILS} time(s)" in verdicts[-1]


def test_backstop_counts_only_failures_since_the_last_reset():
    epic = _epic()
    _play(epic, [[]] * (MAX_FAILS - 1))
    append_reset_marker(epic)
    epic["qa_loop_strikes"] = 0

    assert _play(epic, [[]] * (MAX_FAILS - 1)) == [None] * (MAX_FAILS - 1)


def test_passing_rounds_do_not_count_toward_the_backstop():
    epic = _epic()
    rounds: list[list[str] | None] = []
    for _ in range(MAX_FAILS - 1):
        rounds.extend([["F1"], None])

    assert _play(epic, rounds) == [None] * len(rounds)


# --- reset markers --------------------------------------------------------------------


def test_reset_marker_makes_detection_ignore_earlier_rounds():
    epic = _epic()
    _play(epic, [["F1", "F2"], ["F3", "F4"], ["F1", "F2"]])
    assert epic["qa_loop_strikes"] == 1

    append_reset_marker(epic)
    epic["qa_loop_strikes"] = 0

    # Post-reset this is only the first round again, so the pre-reset cycle can't trip it.
    assert _play(epic, [["F3", "F4"]]) == [None]
    assert epic["qa_loop_strikes"] == 0


def test_reset_marker_preserves_the_earlier_rounds_as_evidence():
    epic = _epic()
    _play(epic, [["F1", "F2"], ["F3", "F4"]])
    append_reset_marker(epic)

    verdicts = [entry["verdict"] for entry in epic["qa_history"]]
    assert verdicts == [VERDICT_FAIL, VERDICT_FAIL, VERDICT_RESET]


def test_reset_marker_is_a_no_op_without_history_or_when_already_reset():
    epic = _epic()
    append_reset_marker(epic)
    assert epic.get("qa_history") in (None, [])

    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F1"])
    append_reset_marker(epic)
    append_reset_marker(epic)
    assert [entry["verdict"] for entry in epic["qa_history"]] == [VERDICT_FAIL, VERDICT_RESET]


# --- rendering ------------------------------------------------------------------------


def test_format_qa_history_renders_each_round():
    epic = _epic()
    _play(epic, [["F1", "F2"], None])
    rendered = format_qa_history(epic)

    assert "round 1: ❌ F1, F2" in rendered
    assert "round 2: ✅ passed" in rendered


def test_format_qa_history_labels_a_failure_with_no_flagged_feature():
    epic = _epic()
    record_qa_round(epic, VERDICT_FAIL, failed_ids=[])

    assert "no feature was flagged" in format_qa_history(epic)


def test_format_qa_history_labels_a_reset():
    epic = _epic()
    record_qa_round(epic, VERDICT_FAIL, failed_ids=["F1"])
    append_reset_marker(epic)

    assert "reset by hand" in format_qa_history(epic)


def test_format_qa_history_with_no_rounds():
    assert format_qa_history(_epic()) == "No QA rounds recorded."


@pytest.mark.parametrize("strikes_limit", [1, 3])
def test_strikes_limit_is_honored(strikes_limit):
    epic = _epic()
    verdicts = _play(
        epic,
        [["F1", "F2"], ["F3", "F4"], ["F1", "F2"], ["F3", "F4"], ["F1", "F2"]],
        strikes=strikes_limit,
    )
    tripped_at = next(i for i, verdict in enumerate(verdicts) if verdict is not None)

    # Signalling starts at round 3 (index 2), so an N-strike limit trips N-1 rounds later.
    assert tripped_at == 2 + (strikes_limit - 1)
