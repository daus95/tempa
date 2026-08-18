"""Tests for dashboard_api_workspace.py's newer working-folder actions: the recent-
workspaces list (open/remove) and the two-step "Create New Working Folder" flow
(pick-parent + create). init_workspace/open_workspace_folder/close_workspace already have
end-to-end coverage in test_dashboard_server_routes.py; these are unit-level, exercising
the module functions directly with `_run_tempa` faked so nothing shells out to a real
`tempa.py` subprocess."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import dashboard_api_workspace as api
import tempa_config


class _FakeServer(SimpleNamespace):
    """Only needs to accept prd_dir/clar_dir/epics_dir assignment, same as the real
    dashboard server object (see _refresh_source_dirs)."""


def _ok_result() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["init"], returncode=0, stdout="ok\n")


# ---------------------------------------------------------------------------
# _validate_new_folder_name
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", [
    "", "   ", " leading-space", "trailing-space ", ".", "..",
    "a/b", "a\b", 'bad"name', "bad<name", "bad>name", "bad:name", "bad|name",
    "bad?name", "bad*name", "trailing-dot.", "trailing-space-before-end ",
    "CON", "con", "NUL", "com1", "LPT9", "CON.txt",
    "x" * 101,
])
def test_validate_new_folder_name_rejects(name):
    assert api._validate_new_folder_name(name) is not None


@pytest.mark.parametrize("name", ["my-app", "MyApp_2", "a.b.c", "CONcert"])
def test_validate_new_folder_name_accepts(name):
    assert api._validate_new_folder_name(name) is None


# ---------------------------------------------------------------------------
# create_workspace
# ---------------------------------------------------------------------------
def test_create_workspace_rejects_relative_parent(isolate_tempa_paths):
    status, body = api.create_workspace(_FakeServer(), {"parent": "relative/path", "name": "app"})
    assert status == 400
    assert body["ok"] is False


def test_create_workspace_rejects_nonexistent_parent(tmp_path, isolate_tempa_paths):
    missing = tmp_path / "does-not-exist"
    status, body = api.create_workspace(_FakeServer(), {"parent": str(missing), "name": "app"})
    assert status == 400
    assert body["ok"] is False


def test_create_workspace_rejects_an_already_existing_target(tmp_path, isolate_tempa_paths):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "app").mkdir()
    status, body = api.create_workspace(_FakeServer(), {"parent": str(parent), "name": "app"})
    assert status == 409
    assert "already exists" in body["error"]


def test_create_workspace_rejects_an_invalid_name_before_running_tempa(tmp_path, isolate_tempa_paths, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    called = []
    monkeypatch.setattr(api, "_run_tempa", lambda *a, **k: called.append(a) or _ok_result())

    status, body = api.create_workspace(_FakeServer(), {"parent": str(parent), "name": "bad/name"})
    assert status == 400
    assert not called, "must not shell out to tempa.py for an invalid name"


def test_create_workspace_success_runs_init_on_parent_joined_with_name(tmp_path, isolate_tempa_paths, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    captured = {}

    def fake_run_tempa(args, timeout):
        captured["args"] = args
        return _ok_result()

    monkeypatch.setattr(api, "_run_tempa", fake_run_tempa)

    status, body = api.create_workspace(_FakeServer(), {"parent": str(parent), "name": "my-app"})
    assert status == 200
    assert body["ok"] is True
    assert captured["args"] == ["init", str(parent / "my-app")]


# ---------------------------------------------------------------------------
# pick_parent_folder
# ---------------------------------------------------------------------------
def test_pick_parent_folder_unavailable_platform():
    status, body = api.pick_parent_folder(None)
    assert status == 200
    assert body["ok"] is False
    assert "error" in body


def test_pick_parent_folder_cancelled():
    status, body = api.pick_parent_folder(lambda: None)
    assert status == 200
    assert body == {"ok": False, "cancelled": True}


def test_pick_parent_folder_success():
    status, body = api.pick_parent_folder(lambda: r"C:\some\path")
    assert status == 200
    assert body == {"ok": True, "path": r"C:\some\path"}


# ---------------------------------------------------------------------------
# open_recent_workspace
# ---------------------------------------------------------------------------
def test_open_recent_workspace_rejects_a_path_not_in_history(tmp_path, isolate_tempa_paths, monkeypatch):
    called = []
    monkeypatch.setattr(api, "_run_tempa", lambda *a, **k: called.append(a) or _ok_result())

    status, body = api.open_recent_workspace(_FakeServer(), {"path": str(tmp_path / "unknown")})
    assert status == 404
    assert not called, "must not shell out to tempa.py for a path outside the history"


def test_open_recent_workspace_accepts_a_known_path(tmp_path, isolate_tempa_paths, monkeypatch):
    root = tmp_path / "ws"
    tempa_config.record_workspace_history(root)
    captured = {}

    def fake_run_tempa(args, timeout):
        captured["args"] = args
        return _ok_result()

    monkeypatch.setattr(api, "_run_tempa", fake_run_tempa)

    status, body = api.open_recent_workspace(_FakeServer(), {"path": str(root)})
    assert status == 200
    assert body["ok"] is True
    assert captured["args"] == ["init", str(root)]


# ---------------------------------------------------------------------------
# remove_recent_workspace
# ---------------------------------------------------------------------------
def test_remove_recent_workspace_returns_the_refreshed_list(tmp_path, isolate_tempa_paths):
    a, b = tmp_path / "a", tmp_path / "b"
    tempa_config.record_workspace_history(a)
    tempa_config.record_workspace_history(b)

    status, body = api.remove_recent_workspace({"path": str(a)})
    assert status == 200
    assert [e["root"] for e in body["recent"]] == [str(b)]
