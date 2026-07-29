"""Tests for dashboard_spec.py — file-tree building and the path-traversal safety check
(_resolve_within) that keeps the dashboard's file browser confined to its root folder."""

from __future__ import annotations

from pathlib import Path

import pytest

import dashboard_spec as ds


# ---------------------------------------------------------------------------
# _is_text_file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext", sorted(e for e in ds.TEXT_EXTENSIONS if e.startswith(".") and e != ".gitignore"))
def test_is_text_file_known_extensions(ext):
    assert ds._is_text_file(Path(f"file{ext}")) is True


def test_is_text_file_unknown_extension():
    assert ds._is_text_file(Path("file.exe")) is False
    assert ds._is_text_file(Path("file.png")) is False


def test_is_text_file_name_as_extension_gitignore():
    assert ds._is_text_file(Path(".gitignore")) is True


def test_is_text_file_case_insensitive():
    assert ds._is_text_file(Path("FILE.MD")) is True


# ---------------------------------------------------------------------------
# build_tree
# ---------------------------------------------------------------------------

@pytest.fixture
def tree_root(tmp_path):
    # A dedicated, otherwise-empty subdirectory: tmp_path itself is shared with the
    # autouse isolate_tempa_paths fixture, which creates its own sibling folders
    # (install_root/, prompt/) directly under tmp_path.
    root = tmp_path / "tree_root"
    root.mkdir()
    return root


def test_build_tree_empty_directory(tree_root):
    tree = ds.build_tree(tree_root)
    assert tree["type"] == "dir"
    assert tree["path"] == ""
    assert tree["children"] == []


def test_build_tree_sorts_files_case_insensitively(tree_root):
    (tree_root / "Banana.md").write_text("x", encoding="utf-8")
    (tree_root / "apple.md").write_text("x", encoding="utf-8")
    (tree_root / "Cherry.md").write_text("x", encoding="utf-8")

    tree = ds.build_tree(tree_root)
    names = [c["name"] for c in tree["children"]]
    assert names == ["apple.md", "Banana.md", "Cherry.md"]


def test_build_tree_dirs_before_files(tree_root):
    (tree_root / "aaa_file.md").write_text("x", encoding="utf-8")
    (tree_root / "zzz_dir").mkdir()

    tree = ds.build_tree(tree_root)
    types = [c["type"] for c in tree["children"]]
    assert types == ["dir", "file"]


def test_build_tree_nested_directories(tree_root):
    nested = tree_root / "sub" / "deeper"
    nested.mkdir(parents=True)
    (nested / "file.md").write_text("x", encoding="utf-8")

    tree = ds.build_tree(tree_root)
    sub_node = tree["children"][0]
    assert sub_node["path"] == "sub"
    deeper_node = sub_node["children"][0]
    assert deeper_node["path"] == "sub/deeper"
    file_node = deeper_node["children"][0]
    assert file_node["path"] == "sub/deeper/file.md"


def test_build_tree_markdown_and_text_flags(tree_root):
    (tree_root / "doc.md").write_text("x", encoding="utf-8")
    (tree_root / "script.py").write_text("x", encoding="utf-8")
    (tree_root / "binary.exe").write_bytes(b"\x00\x01")

    tree = ds.build_tree(tree_root)
    by_name = {c["name"]: c for c in tree["children"]}
    assert by_name["doc.md"]["markdown"] is True
    assert by_name["script.py"]["markdown"] is False
    assert by_name["script.py"]["text"] is True
    assert by_name["binary.exe"]["markdown"] is False
    assert by_name["binary.exe"]["text"] is False


def test_build_tree_nonexistent_root_returns_empty_children(tree_root):
    missing = tree_root / "does_not_exist"
    tree = ds.build_tree(missing)
    assert tree["type"] == "dir"
    assert tree["children"] == []


# ---------------------------------------------------------------------------
# _resolve_within
# ---------------------------------------------------------------------------

def test_resolve_within_valid_relative_path(tmp_path):
    result = ds._resolve_within(tmp_path, "sub/dir/file.md")
    assert result == (tmp_path / "sub" / "dir" / "file.md").resolve()


@pytest.mark.parametrize("rel", ["", "   "])
def test_resolve_within_empty_or_whitespace(tmp_path, rel):
    assert ds._resolve_within(tmp_path, rel) is None


def test_resolve_within_rejects_dotdot_traversal(tmp_path):
    assert ds._resolve_within(tmp_path, "../../etc/passwd") is None


def test_resolve_within_rejects_backslash_traversal(tmp_path):
    assert ds._resolve_within(tmp_path, "..\\..\\secrets.txt") is None


def test_resolve_within_rejects_drive_letter_absolute_path(tmp_path):
    # A Windows drive-rooted path replaces the join entirely, so it lands outside
    # root — this is the real, non-obvious escape vector to lock down.
    assert ds._resolve_within(tmp_path, "C:/Windows/System32/drivers/etc/hosts") is None


def test_resolve_within_leading_slashes_stripped_stays_inside_root(tmp_path):
    # lstrip("/") strips ALL leading slashes before the join, so this is NOT an
    # escape — it resolves to root/etc/passwd, safely inside root.
    result = ds._resolve_within(tmp_path, "////etc/passwd")
    assert result == (tmp_path / "etc" / "passwd").resolve()


def test_resolve_within_does_not_require_existence(tmp_path):
    result = ds._resolve_within(tmp_path, "sub/does/not/exist.md")
    assert result == (tmp_path / "sub" / "does" / "not" / "exist.md").resolve()
    assert not result.exists()


def test_resolve_within_prefix_string_confusion_rejected(tmp_path):
    # A sibling directory sharing root's name as a prefix must not be treated as
    # "inside" root just because its string representation starts with root's.
    root = tmp_path / "project"
    root.mkdir()
    sibling = tmp_path / "project_evil"
    sibling.mkdir()

    result = ds._resolve_within(root, "../project_evil/secret.txt")
    assert result is None
