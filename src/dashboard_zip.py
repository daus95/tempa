"""ZIP archive building for the dashboard's "Download PRD"/"Download Plan" buttons."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


def build_zip(root: Path) -> bytes:
    """Zip every file under `root`, preserving its relative folder structure.

    A missing or empty directory yields a valid, structurally-empty zip rather than an
    error — callers only ever point this at server-resolved source dirs, so there's no
    untrusted input to reject, and an empty archive is a simpler contract than a 404.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(root).as_posix())
    return buffer.getvalue()
