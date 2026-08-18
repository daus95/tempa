"""Answering a blocked feature's decision from the Implementation page.

A session that can't finish a feature without a human choice parks it (`status: "blocked"`
plus a `blocked_question` and the session's own `blocked_recommendation`) and the epic goes
`deferred` — see tempa_decisions for why that exists. Until this endpoint, the only way to
answer was to open config.json by hand, find the right entry among the epics and type into a
`blocked_answer` field. On a real plan that is a 300KB file being actively written to by a
running agent, which is a bad thing to ask anyone to edit and an easy thing to corrupt.

The write is deliberately done twice, in this order:

1. `record_answer` puts the decision in its own file under `.tempa/decisions/`. That file has
   exactly one writer and one reader, so nothing can race it, and the runner re-applies it
   every poll until it has been acted on.
2. `update_config` then writes it straight into config.json under the cross-process lock,
   re-reading the file inside the lock and touching only that one feature — so the answer is
   visible immediately, and this can never write back a document it read before some other
   process edited it.

Step 1 first, because a crash between the two then still leaves a decision the runner will
apply. The reverse order could lose one the user has already been told was saved.
"""

from __future__ import annotations

import re

import tempa_config
from tempa_decisions import FEATURE_BLOCKED, apply_answer_to_config, find_feature, record_answer

Response = tuple[int, dict]

# How the client says what kind of answer this is:
#   follow — go with the session's own recommendation, whatever it says
#   own    — the user wrote their own decision instead
#   drop   — the feature is dropped from the epic's scope, with the reason recorded
_MODES = ("follow", "own", "drop")

# Same constraint dashboard_api_spec puts on an epic label before matching it against a file
# name. Nothing here touches the filesystem by label, but `record_answer` builds a file name
# out of it, so it is validated at the door rather than trusted and sanitized later.
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def save_answer(payload: dict | list | None) -> Response:
    """Record the user's decision for one blocked feature and apply it to config.json.

    The answer text for `follow` is read out of the feature's own `blocked_recommendation`
    server-side rather than taken from the request: the client should not be able to decide
    what "I approve your recommendation" turns out to have meant, and it means the stored
    answer is exactly the text the session wrote."""
    if payload is None or not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    epic_name = str(payload.get("epic") or "").strip()
    feature_id = str(payload.get("feature") or "").strip()
    mode = str(payload.get("mode") or "").strip()
    if not epic_name or not _LABEL.match(epic_name):
        return 400, {"ok": False, "error": "Missing or invalid epic."}
    if not feature_id or not _LABEL.match(feature_id):
        return 400, {"ok": False, "error": "Missing or invalid feature."}
    if mode not in _MODES:
        return 400, {"ok": False, "error": "Invalid answer mode."}

    config = tempa_config.read_config_safe()
    epic, feature = find_feature(config, epic_name, feature_id)
    if epic is None:
        return 404, {"ok": False, "error": f'No epic named "{epic_name}" in the plan.'}
    if feature is None:
        return 404, {"ok": False, "error": f'No feature "{feature_id}" in {epic_name}.'}
    if feature.get("status") != FEATURE_BLOCKED:
        return 409, {
            "ok": False,
            "error": f"{feature_id} is no longer waiting on a decision — it is "
                     f"\"{feature.get('status') or 'unknown'}\". Refresh to see where it got to.",
        }

    if mode == "follow":
        answer = str(feature.get("blocked_recommendation") or "").strip()
        if not answer:
            return 409, {
                "ok": False,
                "error": "That feature has no recommendation to follow — write your own answer.",
            }
    else:
        answer = str(payload.get("answer") or "").strip()
        if not answer:
            return 400, {"ok": False, "error": "Write an answer before saving."}

    drop = mode == "drop"
    try:
        record_answer(epic_name, feature_id, answer, drop=drop)
    except OSError as e:
        # Nothing has been written anywhere yet, so failing here is safe to report as a
        # failure — the user can retry without having half-answered.
        return 500, {"ok": False, "error": f"Could not record the decision: {e}"}

    tempa_config.update_config(
        lambda cfg: apply_answer_to_config(cfg, epic_name, feature_id, answer, drop=drop)
    )
    print(f"[decision] {epic_name}/{feature_id} answered ({mode})")
    return 200, {"ok": True, "epic": epic_name, "feature": feature_id, "answer": answer,
                 "dropped": drop}
