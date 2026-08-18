"""Tests for tempa_commands.py's run_init/run_close_folder — specifically that both feed
the recent-workspaces history (see tempa_config.record_workspace_history), which is what
lets the dashboard Home page's "recent working folders" list survive a `close-folder`."""

from __future__ import annotations

import argparse

import tempa_commands as tcmd
import tempa_config


def test_run_init_records_the_workspace_in_history(tmp_path, isolate_tempa_paths):
    root = tmp_path / "ws"
    tcmd.run_init(argparse.Namespace(root=str(root)))

    entries = tempa_config.read_workspace_history()
    assert [e["root"] for e in entries] == [str(root)]


def test_run_close_folder_records_the_previously_active_workspace(tmp_path, isolate_tempa_paths):
    root = tmp_path / "ws"
    tcmd.run_init(argparse.Namespace(root=str(root)))

    tcmd.run_close_folder()

    entries = tempa_config.read_workspace_history()
    assert [e["root"] for e in entries] == [str(root)]
    assert tempa_config.get_active_workspace_root() is None


def test_run_close_folder_with_no_active_workspace_does_not_raise(isolate_tempa_paths):
    tcmd.run_close_folder()
    assert tempa_config.read_workspace_history() == []
