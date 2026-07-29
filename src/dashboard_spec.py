"""Specification (PRD folder) browsing — ported from the former spec_ui.py.

Walks the PRD folder into a JSON-able tree for the sidebar, and safely resolves a
browser-supplied relative path back to a real file inside the folder (rejecting `..`/absolute
escapes)."""

from __future__ import annotations

from pathlib import Path

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".rst",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
    ".csv", ".tsv", ".log",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".scss", ".html", ".xml",
    ".sh", ".ps1", ".bat", ".sql", ".gitignore",
}


MARKDOWN_EXTENSIONS = {".md", ".markdown"}


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS or path.name.lower() in TEXT_EXTENSIONS


def build_tree(root: Path) -> dict:
    """Walk `root` and return a nested dict describing every folder and file below
    it. Directories are listed before files, both sorted case-insensitively. Paths
    are stored relative to `root` using forward slashes (stable identifiers the
    front-end sends back on read/save). Unreadable/missing directories are skipped."""

    def node_for(dir_path: Path, rel: str) -> dict:
        children: list[dict] = []
        try:
            entries = sorted(
                dir_path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            entries = []
        for entry in entries:
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                children.append(node_for(entry, child_rel))
            else:
                children.append({
                    "name": entry.name,
                    "path": child_rel,
                    "type": "file",
                    "markdown": entry.suffix.lower() in MARKDOWN_EXTENSIONS,
                    "text": _is_text_file(entry),
                })
        return {
            "name": dir_path.name,
            "path": rel,
            "type": "dir",
            "children": children,
        }

    tree = node_for(root, "")
    tree["name"] = root.name
    return tree


def _resolve_within(root: Path, rel: str) -> Path | None:
    """Resolve a browser-supplied relative path against `root` and return it only
    if it stays inside `root`. Returns None for any traversal/absolute-path attempt
    so the handler can reject it."""
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        return None
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate
