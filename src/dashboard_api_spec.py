"""Specification-pane endpoints: browsing, editing and reorganizing the PRD folder.

Every function here takes `prd_dir` explicitly and returns the `(status, payload)` the
handler sends back verbatim — no HTTP objects, no server state. `_resolve_within` is what
keeps all of it confined to that folder: a path that resolves outside it comes back as None
and is refused, so neither `..` nor an absolute path can reach anything else on disk.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from dashboard_spec import MARKDOWN_EXTENSIONS, _is_text_file, _resolve_within

Response = tuple[int, dict]


def read_file(prd_dir: Path, rel: str) -> Response:
    """One spec file's content for the viewer/editor. A file that exists but isn't
    viewable as text still answers 200 with `text: false` and a reason — the pane shows
    that instead of an error, since "this is a PNG" is not a failure."""
    target = _resolve_within(prd_dir, rel)
    if target is None or not target.is_file():
        return 404, {"ok": False, "error": "File not found."}
    if not _is_text_file(target):
        return 200, {
            "ok": True, "path": rel, "markdown": False, "text": False,
            "content": "", "reason": "This file type is not viewable as text.",
        }
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return 200, {
            "ok": True, "path": rel, "markdown": False, "text": False,
            "content": "", "reason": "This file is not valid UTF-8 text.",
        }
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not read file: {e}"}
    return 200, {
        "ok": True, "path": rel,
        "markdown": target.suffix.lower() in MARKDOWN_EXTENSIONS,
        "text": True, "content": content,
    }


def save_file(prd_dir: Path, payload: dict | list | None) -> Response:
    """Overwrite one existing spec file with edited text."""
    if payload is None or not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    rel = payload.get("path", "")
    content = payload.get("content", "")
    if not isinstance(content, str):
        return 400, {"ok": False, "error": "Content must be text."}
    target = _resolve_within(prd_dir, rel)
    if target is None:
        return 400, {"ok": False, "error": "Invalid path."}
    if not target.exists() or not target.is_file():
        return 404, {"ok": False, "error": "File no longer exists."}
    if not _is_text_file(target):
        return 400, {"ok": False, "error": "This file type cannot be edited here."}
    try:
        # Path.write_text()'s `newline` kwarg needs Python 3.10+; use open() directly
        # so this also works on 3.9 (otherwise Windows' text-mode translation would
        # silently re-insert \r\n and undo the normalization above).
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(content.replace("\r\n", "\n").replace("\r", "\n"))
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not save file: {e}"}
    print(f"[saved] {rel}")
    return 200, {"ok": True, "path": rel}


def upload_file(prd_dir: Path, rel: str, data: bytes) -> Response:
    """Add a file to the Specification (PRD) folder — used by the "Add File" /
    "Add Folder" buttons. `rel` is the destination relative to prd_dir (for a folder
    upload this includes the folder name and any subfolders); `data` is the raw file
    bytes. Overwrites an existing file at that path."""
    target = _resolve_within(prd_dir, rel)
    if target is None:
        return 400, {"ok": False, "error": "Invalid path."}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not write file: {e}"}
    print(f"[added] {rel}")
    return 200, {"ok": True, "path": rel}


def delete_path(prd_dir: Path, payload: dict | list | None) -> Response:
    """Delete one spec file, or a whole subfolder with everything in it."""
    if payload is None or not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    rel = payload.get("path", "")
    target = _resolve_within(prd_dir, rel)
    if target is None:
        return 400, {"ok": False, "error": "Invalid path."}
    if not target.exists():
        return 404, {"ok": False, "error": "File or folder no longer exists."}
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not delete: {e}"}
    print(f"[deleted] {rel}")
    return 200, {"ok": True, "path": rel}


def rename_path(prd_dir: Path, payload: dict | list | None) -> Response:
    """Rename one spec file or folder in place. The new name is a bare name, never a
    path — anything with a separator in it would move the entry somewhere else."""
    if payload is None or not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Malformed request."}
    rel = payload.get("path", "")
    new_name = (payload.get("new_name") or "").strip()
    target = _resolve_within(prd_dir, rel)
    if target is None:
        return 400, {"ok": False, "error": "Invalid path."}
    if not target.exists():
        return 404, {"ok": False, "error": "File or folder no longer exists."}
    if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        return 400, {"ok": False, "error": "Invalid new name."}
    new_target = target.parent / new_name
    if new_target.exists():
        return 409, {"ok": False, "error": f'"{new_name}" already exists.'}
    try:
        target.rename(new_target)
    except OSError as e:
        return 500, {"ok": False, "error": f"Could not rename: {e}"}
    new_rel = str(new_target.relative_to(prd_dir)).replace("\\", "/")
    print(f"[renamed] {rel} -> {new_rel}")
    return 200, {"ok": True, "path": new_rel}
