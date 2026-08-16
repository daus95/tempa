"""Tests for tempa_update.py — Tempa updating itself.

Two halves, both moved here (verbatim, only the module they point at changed) when the
self-update commands moved out of tempa_commands.py: reading the local VERSION file and
parsing/handling GitHub's "latest release" response including its failure modes, and
`tempa update` itself — the confirm-before-apply flow, the already-up-to-date/offline
short-circuits, and the guarantee that applying an update only overwrites files present in
the downloaded archive, never anything else on disk.
"""

from __future__ import annotations

import io
import json
import urllib.error
import zipfile

import pytest

import tempa_update as tu


# ---------------------------------------------------------------------------
# version / check-update
# ---------------------------------------------------------------------------
def test_get_local_version_reads_version_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0\n", encoding="utf-8")

    assert tu.get_local_version() == "0.3.0"


def test_get_local_version_missing_file_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)

    assert tu.get_local_version() == "unknown"


def test_get_local_version_blank_file_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("   \n", encoding="utf-8")

    assert tu.get_local_version() == "unknown"


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_get_latest_release_version_strips_v_prefix(monkeypatch):
    payload = json.dumps({"tag_name": "v0.4.0"}).encode("utf-8")
    monkeypatch.setattr(tu.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload))

    assert tu.get_latest_release_version() == "0.4.0"


def test_get_latest_release_version_network_failure_returns_none(monkeypatch):
    def _raise(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(tu.urllib.request, "urlopen", _raise)

    assert tu.get_latest_release_version() is None


def test_get_latest_release_version_bad_json_returns_none(monkeypatch):
    monkeypatch.setattr(tu.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"not json"))

    assert tu.get_latest_release_version() is None


def test_print_version_outputs_local_version(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")

    tu.print_version()

    assert "0.3.0" in capsys.readouterr().out


def test_print_check_update_reports_up_to_date(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tu, "get_latest_release_version", lambda: "0.3.0")

    tu.print_check_update()

    assert "up to date" in capsys.readouterr().out.lower()


def test_print_check_update_reports_update_available(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tu, "get_latest_release_version", lambda: "0.4.0")

    tu.print_check_update()

    assert "update available" in capsys.readouterr().out.lower()


def test_print_check_update_handles_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tu, "get_latest_release_version", lambda: None)

    tu.print_check_update()

    assert "could not reach github" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
def _make_fake_release_zip(zip_path, files: dict[str, str]) -> None:
    """Build a zip shaped like the real release asset: every path nested under `tempa/`."""
    with zipfile.ZipFile(zip_path, "w") as archive:
        for relative_path, content in files.items():
            archive.writestr(f"tempa/{relative_path}", content)


def test_run_update_already_up_to_date(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tu, "get_latest_release_version", lambda: "0.3.0")
    monkeypatch.setattr(
        tu, "_download_release_zip", lambda dest: pytest.fail("should not download")
    )

    tu.run_update()

    assert "already up to date" in capsys.readouterr().out.lower()


def test_run_update_offline_aborts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tu, "get_latest_release_version", lambda: None)

    with pytest.raises(SystemExit) as exc:
        tu.run_update()

    assert exc.value.code == 1
    assert "could not reach github" in capsys.readouterr().out.lower()


def test_run_update_requires_confirmation_non_interactive(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tu, "get_latest_release_version", lambda: "0.4.0")
    monkeypatch.setattr(tu.sys, "argv", ["tempa", "update"])
    monkeypatch.setattr(tu.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        tu, "_download_release_zip", lambda dest: pytest.fail("should not download")
    )

    with pytest.raises(SystemExit) as exc:
        tu.run_update()

    assert exc.value.code == 1


def test_run_update_yes_downloads_and_overwrites_matching_files_only(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    (tmp_path / "tempa.py").write_text("old launcher\n", encoding="utf-8")
    # Local-only file that must never be touched — it's not part of any release archive.
    (tmp_path / ".active-workspace").write_text("C:\\some\\workspace", encoding="utf-8")

    monkeypatch.setattr(tu, "get_latest_release_version", lambda: "0.4.0")
    monkeypatch.setattr(tu.sys, "argv", ["tempa", "update", "--yes"])

    def _fake_download(dest):
        _make_fake_release_zip(
            dest,
            {
                "VERSION": "0.4.0",
                "tempa.py": "new launcher\n",
            },
        )

    monkeypatch.setattr(tu, "_download_release_zip", _fake_download)

    tu.run_update()

    assert (tmp_path / "VERSION").read_text(encoding="utf-8") == "0.4.0"
    assert (tmp_path / "tempa.py").read_text(encoding="utf-8") == "new launcher\n"
    assert (tmp_path / ".active-workspace").read_text(encoding="utf-8") == "C:\\some\\workspace"
