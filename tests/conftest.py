"""Shared pytest fixtures for the Tempa test suite.

The autouse `isolate_tempa_paths` fixture is the load-bearing piece: it redirects every
module-level path constant Tempa computes at import time (SCRIPT_DIR, WORKING_DIR,
PROMPT_DIR, ACTIVE_WORKSPACE_POINTER, WORKSPACE_HISTORY_PATH) into a per-test tmp_path, so
no test ever reads or writes the real dev machine's actual install folder, its real
`.active-workspace` pointer, or its real recent-workspaces history (which, outside tests,
may point at/list real, unrelated workspaces)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_tempa_paths(tmp_path, monkeypatch):
    """Redirect tempa_config's module-level path constants into tmp_path.

    tempa_prompts.py does `from tempa_config import PROMPT_DIR`, a value import that binds
    its own local name at import time — patching tempa_config.PROMPT_DIR alone would NOT
    affect tempa_prompts.PROMPT_DIR, so both are patched here.
    """
    import tempa_config
    import tempa_prompts

    fake_script_dir = tmp_path / "install_root"
    fake_script_dir.mkdir(parents=True, exist_ok=True)
    fake_working_dir = fake_script_dir.parent
    fake_prompt_dir = tmp_path / "prompt"
    fake_prompt_dir.mkdir(parents=True, exist_ok=True)
    fake_pointer = fake_script_dir / ".active-workspace"
    fake_history_path = fake_script_dir / ".workspace-history.json"

    monkeypatch.setattr(tempa_config, "SCRIPT_DIR", fake_script_dir)
    monkeypatch.setattr(tempa_config, "WORKING_DIR", fake_working_dir)
    monkeypatch.setattr(tempa_config, "PROMPT_DIR", fake_prompt_dir)
    monkeypatch.setattr(tempa_config, "ACTIVE_WORKSPACE_POINTER", fake_pointer)
    monkeypatch.setattr(tempa_config, "WORKSPACE_HISTORY_PATH", fake_history_path)
    monkeypatch.setattr(tempa_prompts, "PROMPT_DIR", fake_prompt_dir)

    return {
        "script_dir": fake_script_dir,
        "working_dir": fake_working_dir,
        "prompt_dir": fake_prompt_dir,
        "pointer": fake_pointer,
        "history_path": fake_history_path,
    }
