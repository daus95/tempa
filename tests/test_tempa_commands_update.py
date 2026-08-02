"""Tests for `tempa update` (tc.run_update): the confirm-before-apply flow, the
already-up-to-date/offline short-circuits, and that applying an update only overwrites
files present in the downloaded archive — nothing else on disk is touched or deleted."""

from __future__ import annotations

import zipfile

import pytest

import tempa_commands as tc


def _make_fake_release_zip(zip_path, files: dict[str, str]) -> None:
    """Build a zip shaped like the real release asset: every path nested under `tempa/`."""
    with zipfile.ZipFile(zip_path, "w") as archive:
        for relative_path, content in files.items():
            archive.writestr(f"tempa/{relative_path}", content)


def test_run_update_already_up_to_date(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tc, "get_latest_release_version", lambda: "0.3.0")
    monkeypatch.setattr(
        tc, "_download_release_zip", lambda dest: pytest.fail("should not download")
    )

    tc.run_update()

    assert "already up to date" in capsys.readouterr().out.lower()


def test_run_update_offline_aborts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tc, "get_latest_release_version", lambda: None)

    with pytest.raises(SystemExit) as exc:
        tc.run_update()

    assert exc.value.code == 1
    assert "could not reach github" in capsys.readouterr().out.lower()


def test_run_update_requires_confirmation_non_interactive(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tc, "get_latest_release_version", lambda: "0.4.0")
    monkeypatch.setattr(tc.sys, "argv", ["tempa", "update"])
    monkeypatch.setattr(tc.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        tc, "_download_release_zip", lambda dest: pytest.fail("should not download")
    )

    with pytest.raises(SystemExit) as exc:
        tc.run_update()

    assert exc.value.code == 1


def test_run_update_yes_downloads_and_overwrites_matching_files_only(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    (tmp_path / "tempa.py").write_text("old launcher\n", encoding="utf-8")
    # Local-only file that must never be touched — it's not part of any release archive.
    (tmp_path / ".active-workspace").write_text("C:\\some\\workspace", encoding="utf-8")

    monkeypatch.setattr(tc, "get_latest_release_version", lambda: "0.4.0")
    monkeypatch.setattr(tc.sys, "argv", ["tempa", "update", "--yes"])

    def _fake_download(dest):
        _make_fake_release_zip(
            dest,
            {
                "VERSION": "0.4.0",
                "tempa.py": "new launcher\n",
            },
        )

    monkeypatch.setattr(tc, "_download_release_zip", _fake_download)

    tc.run_update()

    assert (tmp_path / "VERSION").read_text(encoding="utf-8") == "0.4.0"
    assert (tmp_path / "tempa.py").read_text(encoding="utf-8") == "new launcher\n"
    assert (tmp_path / ".active-workspace").read_text(encoding="utf-8") == "C:\\some\\workspace"
