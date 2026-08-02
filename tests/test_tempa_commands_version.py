"""Tests for the `tempa version` / `tempa check-update` support functions in
tempa_commands.py: reading the local VERSION file and parsing/handling the GitHub
"latest release" API response, including its failure modes."""

from __future__ import annotations

import io
import json
import urllib.error

import tempa_commands as tc


def test_get_local_version_reads_version_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0\n", encoding="utf-8")

    assert tc.get_local_version() == "0.3.0"


def test_get_local_version_missing_file_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)

    assert tc.get_local_version() == "unknown"


def test_get_local_version_blank_file_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("   \n", encoding="utf-8")

    assert tc.get_local_version() == "unknown"


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
    monkeypatch.setattr(tc.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload))

    assert tc.get_latest_release_version() == "0.4.0"


def test_get_latest_release_version_network_failure_returns_none(monkeypatch):
    def _raise(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(tc.urllib.request, "urlopen", _raise)

    assert tc.get_latest_release_version() is None


def test_get_latest_release_version_bad_json_returns_none(monkeypatch):
    monkeypatch.setattr(tc.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"not json"))

    assert tc.get_latest_release_version() is None


def test_print_version_outputs_local_version(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")

    tc.print_version()

    assert "0.3.0" in capsys.readouterr().out


def test_print_check_update_reports_up_to_date(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tc, "get_latest_release_version", lambda: "0.3.0")

    tc.print_check_update()

    assert "up to date" in capsys.readouterr().out.lower()


def test_print_check_update_reports_update_available(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tc, "get_latest_release_version", lambda: "0.4.0")

    tc.print_check_update()

    assert "update available" in capsys.readouterr().out.lower()


def test_print_check_update_handles_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tc, "SCRIPT_DIR", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0", encoding="utf-8")
    monkeypatch.setattr(tc, "get_latest_release_version", lambda: None)

    tc.print_check_update()

    assert "could not reach github" in capsys.readouterr().out.lower()
