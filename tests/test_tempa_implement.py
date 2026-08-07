"""Tests for tempa_implement.py — currently the recovery path that runs when the backend's
API reported itself overloaded (a transient 529): the wait-then-retry in `main` must clear a
leftover `failed` epic status first, or check_and_run would refuse to resume anything.

No backend process is ever spawned here: `check_and_run` and `wait_out_server_overload` are
stubbed so the poll loop's control flow can be driven directly.
"""

from __future__ import annotations

import pytest

import tempa_config
import tempa_implement as ti
from tempa_logging import _state


@pytest.fixture(autouse=True)
def reset_runner_state():
    """`_state` is a process-wide singleton — reset the flags the poll loop branches on so
    one test's leftover stop/overload flag can't leak into the next."""
    yield
    _state.stop_event.clear()
    _state.all_done = False
    _state.usage_limit_hit = False
    _state.auth_error_hit = False
    _state.server_overloaded_hit = False
    _state.running_thread = None
    _state.running_index = None


# ---------------------------------------------------------------------------
# _reset_failed_before_retry
# ---------------------------------------------------------------------------

def test_reset_failed_before_retry_flips_failed_to_pending(isolate_tempa_paths):
    tempa_config.save_config({"epic": [
        {"epic_name": "e1", "status": "failed", "session_id": "abc"},
        {"epic_name": "e2", "status": "pending"},
    ]})

    ti._reset_failed_before_retry("Implementation")

    saved = tempa_config.load_config()
    assert saved["epic"][0]["status"] == "pending"
    assert "session_id" not in saved["epic"][0]
    assert saved["epic"][1]["status"] == "pending"


def test_reset_failed_before_retry_no_failed_epic_leaves_config_untouched(isolate_tempa_paths):
    tempa_config.save_config({"epic": [{"epic_name": "e1", "status": "on_progress"}]})
    before = tempa_config.get_config_path().read_text(encoding="utf-8")

    ti._reset_failed_before_retry("Implementation")

    assert tempa_config.get_config_path().read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# main() — the overload wait-then-retry branch
# ---------------------------------------------------------------------------

def _drive_poll_loop(monkeypatch, *, first_stop: str) -> dict:
    """Stub out check_and_run + wait_out_server_overload so main()'s loop runs twice: the
    first poll stops the runner the way `first_stop` says ("overload"/"failure"), the second
    reports everything done (exit 0). Returns a dict recording what happened."""
    seen: dict = {"polls": 0, "waits": []}

    def fake_check_and_run(features_override: int | None = None) -> None:
        seen["polls"] += 1
        if seen["polls"] == 1 and first_stop == "overload":
            _state.server_overloaded_hit = True
        elif seen["polls"] > 1:
            _state.all_done = True
        _state.stop_event.set()

    def fake_wait_out_server_overload(label: str, attempt: int) -> None:
        seen["waits"].append((label, attempt))
        _state.server_overloaded_hit = False
        _state.stop_event.clear()

    monkeypatch.setattr(ti, "check_and_run", fake_check_and_run)
    monkeypatch.setattr(ti, "wait_out_server_overload", fake_wait_out_server_overload)
    return seen


def test_main_resets_failed_epic_after_overload_wait(isolate_tempa_paths, monkeypatch):
    # e2 is on_progress so there IS pending work (no plan run); e1 was left `failed` by the
    # session the overload cut short — without the reset, the retry's very first
    # check_and_run would halt on it.
    tempa_config.save_config({"epic": [
        {"epic_name": "e1", "status": "failed", "session_id": "abc"},
        {"epic_name": "e2", "status": "on_progress"},
    ]})
    seen = _drive_poll_loop(monkeypatch, first_stop="overload")

    with pytest.raises(SystemExit) as exc:
        ti.main()

    assert exc.value.code == 0
    assert seen["waits"] == [("Implementation", 1)]
    assert seen["polls"] == 2  # the retry actually got to poll again
    saved = tempa_config.load_config()
    assert saved["epic"][0]["status"] == "pending"
    assert saved["epic"][1]["status"] == "on_progress"


def test_main_plain_session_failure_keeps_failed_status(isolate_tempa_paths, monkeypatch):
    # A real (non-overload) failure must stay `failed` and stop the runner with exit 1 —
    # the reset is only for the transient-overload retry path.
    tempa_config.save_config({"epic": [
        {"epic_name": "e1", "status": "failed"},
        {"epic_name": "e2", "status": "on_progress"},
    ]})
    seen = _drive_poll_loop(monkeypatch, first_stop="failure")

    with pytest.raises(SystemExit) as exc:
        ti.main()

    assert exc.value.code == 1
    assert seen["waits"] == []
    assert tempa_config.load_config()["epic"][0]["status"] == "failed"
