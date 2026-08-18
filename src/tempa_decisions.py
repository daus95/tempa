"""Features that can't be finished without a decision only a human can make.

Every other stop condition in Tempa answers "is this epic still working?". This one answers a
different question: the epic is working fine, the session understood the task, and the honest
outcome is still "someone has to choose". A spec that names a feature the product no longer
wants, a QA report whose own recommended fix is "implement this *or* descope it", a migration
whose blast radius needs signing off — none of these are bugs, and none of them get better by
running the same session again.

Before this existed the runner had no state for that answer. The feature-status vocabulary was
`done` / `pending` / `require_fixing`, and the only sanctioned "I'm stuck" channel was
`blocked_by_epic`, which by construction can only point at *another epic in the same plan*. A
session that had correctly worked out that a human must decide could therefore only leave the
feature `require_fixing` and explain itself in prose — which reads to `_handle_stalled_round`
exactly like a session that achieved nothing. Two of those in a row and the epic was marked
`failed` and the whole runner stopped, with 49 features in later epics that had nothing to do
with the question left unbuilt overnight.

So: a feature may be `blocked`, carrying the question and the session's own recommendation. The
epic is only `deferred` once the blocked features are all that's left of it — there is no reason
to stop building features 5-7 because feature 4 needs an answer. Deferring never stops the
runner; the plan moves on and the epic comes back on its own the moment an answer is written.

The abuse concern is real and is handled in the prompt (see `_blocked_feature_block` in
tempa_prompts), not here: this module only reads the state, it can't tell a well-founded block
from a lazy one. What it does do is make the lazy version cost the same as the honest one —
`no_progress_rounds` keeps counting for an epic that still has other work, and a blocked feature
is never counted as complete, so nothing about this path lets an epic reach `done`.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from tempa_config import get_decisions_dir

# Set by the session on a feature it cannot finish without an answer; see _blocked_feature_block.
FEATURE_BLOCKED = "blocked"
# Statuses that mean "there is still implementation work here the runner can hand to a session".
_ACTIONABLE_FEATURE_STATUSES = ("pending", "require_fixing")

EPIC_DEFERRED = "deferred"


def blocked_features(epic: dict) -> list[dict]:
    """Every feature of `epic` waiting on an answer, in plan order.

    A feature whose `blocked_answer` has been filled in is NOT one of these — it has what it was
    waiting for and belongs back in the implementation queue (see `answered_features`)."""
    return [
        feature for feature in (epic.get("features") or [])
        if feature.get("status") == FEATURE_BLOCKED and not str(feature.get("blocked_answer") or "").strip()
    ]


def answered_features(epic: dict) -> list[dict]:
    """Every blocked feature of `epic` whose decision has since been written in."""
    return [
        feature for feature in (epic.get("features") or [])
        if feature.get("status") == FEATURE_BLOCKED and str(feature.get("blocked_answer") or "").strip()
    ]


def has_other_work(epic: dict) -> bool:
    """True while `epic` still has a feature a session could work on that isn't blocked.

    This is what keeps a single unanswerable feature from idling the whole epic: features 5-7
    don't stop being buildable because feature 4 needs a decision."""
    return any(
        feature.get("status") in _ACTIONABLE_FEATURE_STATUSES
        for feature in (epic.get("features") or [])
    )


def describe(feature: dict) -> str:
    """The question, and the session's own recommendation for it, as a human-readable block —
    for `tempa status`, the halt log and the notification body.

    The recommendation is deliberately carried everywhere the question goes: an answer of
    "yes, do what you suggested" is the common case, and making the reader open a log file to
    find out what was suggested is what turns a 30-second decision into a deferred one."""
    lines = [f"{feature.get('id', '?')} — {feature.get('name', '')}".rstrip(" —")]
    question = str(feature.get("blocked_question") or "").strip()
    recommendation = str(feature.get("blocked_recommendation") or "").strip()
    if question:
        lines.append(f"  Question: {question}")
    if recommendation:
        lines.append(f"  Its recommendation: {recommendation}")
    return "\n".join(lines)


def answer_hint(config_path: str, epic_name: str) -> str:
    """How to actually answer, named precisely enough to act on without reading the docs."""
    return (
        f"To answer: open {config_path}, find the entry with \"epic_name\": \"{epic_name}\", and "
        "write your decision into the blocked feature's \"blocked_answer\" field. The epic goes "
        "back into the queue by itself on the next run — the answer is handed to the session that "
        "picks it up. To drop the feature instead, set its \"status\" to \"done\" and say why in "
        "\"blocked_answer\". Or answer it from the dashboard's Implementation page, which "
        "does all of that for you."
    )


# ---------------------------------------------------------------------------
# Recorded answers — how a decision made outside the runner survives long enough to be acted
# on.
#
# config.json cannot carry it alone. The runner's session threads read-modify-write that file
# constantly and the spawned agent is told to edit its own epic's entry directly, so a field
# written from a third process can be overwritten by whichever of them next saves from a copy
# it read earlier — the same reasoning that made the graceful-stop request a sentinel file
# rather than a config key (see tempa_config.get_graceful_stop_path). A lost answer is the
# worst failure this feature has: the user believes they decided, and the epic never comes
# back.
#
# So an answer is recorded here first, in its own file, and re-applied by the runner on every
# poll until it has demonstrably been acted on. One file per answered feature, so two answers
# saved at once are two files with one writer each rather than one file two processes are both
# rewriting.
# ---------------------------------------------------------------------------
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _answer_filename(epic_name: str, feature_id: str) -> str:
    """A filesystem-safe name for the (epic, feature) pair an answer belongs to. Only that pair
    identifies the file, so re-answering overwrites rather than accumulating."""
    slug = _SLUG_UNSAFE.sub("-", f"{epic_name}__{feature_id}").strip("-")
    return f"{slug or 'decision'}.json"


def record_answer(epic_name: str, feature_id: str, answer: str, drop: bool = False) -> Path:
    """Record a decision under .tempa/decisions/ and return the file it was written to.

    Written the way config.json is (temp file in the same directory, then os.replace), so a
    reader sees either the whole answer or none of it. Callers write this BEFORE touching
    config.json: crashing between the two then leaves a decision the runner will still apply,
    whereas the reverse order could lose one the user has already been told was saved."""
    directory = get_decisions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "epic_name": epic_name,
        "feature_id": feature_id,
        "answer": answer,
        "drop": bool(drop),
        "written_at": datetime.now().isoformat(),
    }
    target = directory / _answer_filename(epic_name, feature_id)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".decision.", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise
    return target


def pending_answers() -> list[tuple[Path, dict]]:
    """Every recorded answer not yet retired, as (path, payload) pairs in a stable order.

    An unreadable or malformed file is skipped rather than raised on: one corrupt sidecar must
    not stop the others from being applied, nor stop the runner from polling at all."""
    results: list[tuple[Path, dict]] = []
    try:
        entries = sorted(get_decisions_dir().glob("*.json"))
    except OSError:
        return results
    for entry in entries:
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("epic_name") and payload.get("feature_id"):
            results.append((entry, payload))
    return results


def _retire(path: Path) -> None:
    """Drop a recorded answer that has been acted on, or that has nothing left to act on."""
    with contextlib.suppress(OSError):
        path.unlink()


def find_feature(config: dict, epic_name: str, feature_id: str) -> tuple[dict | None, dict | None]:
    """The (epic, feature) entries `epic_name`/`feature_id` name, either of which may be None
    if it isn't there — a plan can be re-planned out from under a recorded answer."""
    for epic in (config.get("epic") or []):
        if epic.get("epic_name") != epic_name:
            continue
        for feature in (epic.get("features") or []):
            if feature.get("id") == feature_id:
                return epic, feature
        return epic, None
    return None, None


def apply_answer_to_config(config: dict, epic_name: str, feature_id: str, answer: str,
                           drop: bool = False) -> bool:
    """Write one decision into `config` (mutated in place); return whether it changed anything.

    The single definition of what answering actually does, shared by the dashboard's own write
    and by the runner re-applying a recorded answer, so the two can never drift apart. Dropping
    additionally marks the feature `done`; the epic then leaves `deferred` through the same
    "nothing left waiting on you" rule an ordinary answer takes (see _resume_answered_decisions
    in tempa_implement), rather than needing a second, parallel recovery path."""
    _, feature = find_feature(config, epic_name, feature_id)
    if feature is None:
        return False
    already_answered = feature.get("blocked_answer") == answer
    if already_answered and (not drop or feature.get("status") == "done"):
        return False
    feature["blocked_answer"] = answer
    if drop:
        feature["status"] = "done"
    return True


def apply_pending_answers(config: dict) -> bool:
    """Re-apply every recorded answer to `config` (mutated in place); return whether anything
    changed, so the caller knows to save.

    This is the half of the design that makes a decision durable. Nothing coordinates the
    processes writing config.json, so an answer written into it can be overwritten by a runner
    thread, or by the spawned agent saving a copy it read beforehand. Re-applying on every poll
    makes such a loss cost one poll interval instead of costing the decision.

    An answer is retired once it has demonstrably been acted on — present in config.json AND
    the feature no longer `blocked`, meaning _resume_answered_decisions has already put the epic
    back in the queue. Retiring any earlier would reopen the exact window this closes."""
    changed = False
    for path, payload in pending_answers():
        epic_name = str(payload.get("epic_name") or "")
        feature_id = str(payload.get("feature_id") or "")
        answer = str(payload.get("answer") or "")
        drop = bool(payload.get("drop"))
        _, feature = find_feature(config, epic_name, feature_id)
        if feature is None:
            # Re-planned or cleared out from under it: there is nothing left to answer, so
            # retire it rather than retrying forever.
            _retire(path)
            continue
        if apply_answer_to_config(config, epic_name, feature_id, answer, drop):
            changed = True
        elif feature.get("status") != FEATURE_BLOCKED:
            _retire(path)
    return changed
