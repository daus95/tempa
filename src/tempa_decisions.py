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
        "\"blocked_answer\"."
    )
