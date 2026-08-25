"""Tests for tempa_backup.backup_prd_zip — the PRD snapshot behind the
finalize_checkpoint_backup setting.

The contract under test is mostly about what it REFUSES to do: it must never raise, and it
must never write outside a properly resolved, absolute backup folder — an unattended
`clarify --finalize` run depends on both.
"""

from __future__ import annotations

import io
import zipfile

import dashboard_zip
import tempa_backup as tb
import tempa_config


def _workspace(tmp_path, **extra):
    """A config with a real workspace root and a PRD folder holding two files."""
    root = tmp_path / "ws"
    prd = root / ".tempa" / "specs" / "prd"
    prd.mkdir(parents=True)
    (prd / "overview.md").write_text("# Overview\n", encoding="utf-8")
    (prd / "sub" / "detail.md").parent.mkdir()
    (prd / "sub" / "detail.md").write_text("# Detail\n", encoding="utf-8")
    return {"workspace": {"root": str(root)}, "sources": {"prd": str(prd)}, **extra}, root, prd


def _members(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(zf.namelist())


def test_relative_folder_is_resolved_under_the_workspace_root_and_created(tmp_path):
    config, root, _ = _workspace(tmp_path)

    outcome, detail = tb.backup_prd_zip(config, "round5")

    assert outcome == "saved"
    written = root / "prd-backup"
    assert written.is_dir()
    # `detail` is the full path of what was written, so the log line names the file.
    assert [str(p) for p in written.glob("*.zip")] == [detail]


def test_absolute_folder_is_honored_as_is(tmp_path):
    elsewhere = tmp_path / "somewhere-else"
    config, root, _ = _workspace(tmp_path, finalize_checkpoint_backup_dir=str(elsewhere))

    outcome, _ = tb.backup_prd_zip(config, "final")

    assert outcome == "saved"
    assert len(list(elsewhere.glob("*.zip"))) == 1
    assert not (root / "prd-backup").exists()


def test_filename_carries_a_timestamp_and_the_label(tmp_path):
    config, root, _ = _workspace(tmp_path)

    tb.backup_prd_zip(config, "round7")

    name = next((root / "prd-backup").glob("*.zip")).name
    # prd-YYYYMMDD-HHMMSS-<label>.zip — timestamp first so the folder sorts chronologically.
    assert name.startswith("prd-") and name.endswith("-round7.zip")
    stamp = name[len("prd-"):-len("-round7.zip")]
    date, _, clock = stamp.partition("-")
    assert len(date) == 8 and date.isdigit()
    assert len(clock) == 6 and clock.isdigit()


def test_the_archive_is_exactly_what_download_prd_produces(tmp_path):
    """The whole point of reusing dashboard_zip.build_zip: a checkpoint snapshot and the
    dashboard's Download PRD button must not drift apart."""
    config, root, prd = _workspace(tmp_path)

    tb.backup_prd_zip(config, "final")

    written = next((root / "prd-backup").glob("*.zip"))
    with zipfile.ZipFile(io.BytesIO(dashboard_zip.build_zip(prd))) as reference:
        assert _members(written) == sorted(reference.namelist())
    assert _members(written) == ["overview.md", "sub/detail.md"]


def test_missing_prd_folder_is_skipped_not_failed(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    config = {"workspace": {"root": str(root)}, "sources": {"prd": str(root / "nope")}}

    outcome, detail = tb.backup_prd_zip(config, "final")

    assert outcome == "skipped"
    assert "doesn't exist" in detail
    assert not (root / "prd-backup").exists()


def test_a_relative_folder_with_no_workspace_root_writes_nothing(tmp_path, monkeypatch):
    """Without this guard a relative default resolves against the process's current
    directory, so a run with no workspace configured would scatter ZIPs wherever it happened
    to be started from."""
    prd = tmp_path / "prd"
    prd.mkdir()
    (prd / "a.md").write_text("a", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    outcome, detail = tb.backup_prd_zip({"sources": {"prd": str(prd)}}, "final")

    assert outcome == "skipped"
    assert "no workspace root" in detail
    assert list(tmp_path.glob("**/*.zip")) == []


def test_a_backup_folder_inside_the_prd_folder_is_refused(tmp_path):
    """Otherwise every snapshot archives the ones before it and each ZIP roughly doubles."""
    config, _, prd = _workspace(tmp_path)
    config["finalize_checkpoint_backup_dir"] = str(prd / "backups")

    outcome, detail = tb.backup_prd_zip(config, "final")

    assert outcome == "skipped"
    assert "inside the PRD folder" in detail
    assert list(prd.glob("**/*.zip")) == []


def test_an_unwritable_backup_folder_fails_without_raising(tmp_path, monkeypatch):
    config, _, _ = _workspace(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(tb.Path, "mkdir", boom)

    outcome, detail = tb.backup_prd_zip(config, "final")

    assert outcome == "failed"
    assert "could not create the backup folder" in detail and "disk on fire" in detail


def test_a_failing_write_fails_without_raising(tmp_path, monkeypatch):
    config, _, _ = _workspace(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("no space left")

    monkeypatch.setattr(tb.Path, "write_bytes", boom)

    outcome, detail = tb.backup_prd_zip(config, "final")

    assert outcome == "failed"
    assert "could not write" in detail and "no space left" in detail


def test_a_blank_configured_folder_falls_back_to_the_default(tmp_path):
    config, root, _ = _workspace(tmp_path, finalize_checkpoint_backup_dir="   ")

    assert tb.backup_prd_zip(config, "final")[0] == "saved"
    assert len(list((root / "prd-backup").glob("*.zip"))) == 1


def test_the_backup_toggle_is_not_consulted_here(tmp_path):
    """backup_prd_zip is unconditional — the caller (_snapshot_and_commit) owns the toggle,
    so this stays a plain 'write the file' helper."""
    config, root, _ = _workspace(tmp_path, finalize_checkpoint_backup=False)

    assert tb.backup_prd_zip(config, "final")[0] == "saved"
    assert len(list((root / "prd-backup").glob("*.zip"))) == 1


def test_default_config_backup_dir_is_used_when_unset(tmp_path):
    config, root, _ = _workspace(tmp_path)

    tb.backup_prd_zip(config, "final")

    assert tempa_config.DEFAULT_CONFIG["finalize_checkpoint_backup_dir"] == "prd-backup"
    assert (root / "prd-backup").is_dir()
