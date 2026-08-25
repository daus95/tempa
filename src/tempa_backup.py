"""PRD snapshot backups for `clarify --finalize` checkpoints.

Single entry point, `backup_prd_zip`, used by `_run_checkpoint` and
`_finalize_success_backup` (tempa_clarify.py), gated by the
`finalize_checkpoint_backup` config setting (see `get_finalize_checkpoint_backup`
in tempa_config.py).

Modelled on tempa_git.py down to the return contract: a checkpoint backup is a
convenience, so this reports (outcome, detail) and never raises rather than being
able to kill an unattended finalize run.

Imports `dashboard_zip.build_zip` — a CLI-half module reaching into the dashboard
half. That is the established exception rather than a new one: `tempa_clarify`
already imports `dashboard_clarify_parse` for the same reason (see
docs/architecture.md, "The CLI/dashboard boundary"). `dashboard_zip` is a pure,
stdlib-only leaf that imports nothing local, so it can't create a cycle, and using
it is what guarantees a checkpoint archive is byte-for-byte what the dashboard's
"Download PRD" button produces.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dashboard_zip import build_zip
from tempa_config import (
    get_finalize_checkpoint_backup_dir,
    resolve_prd_dir,
    resolve_source_path,
)


def backup_prd_zip(config: dict, label: str) -> tuple[str, str]:
    """Write a ZIP of the PRD folder into the configured backup folder.

    Returns (outcome, detail): outcome is "saved", "skipped", or "failed". Never
    raises — an unwritable folder or a full disk is reported to the caller, which
    only logs it; losing the whole run over a missed snapshot would destroy far
    more than the snapshot protects.

    `label` is appended to the timestamped filename to say what the snapshot is
    ("round5", "final"), tying a ZIP to the log line and the commit that made it.
    """
    prd_dir = resolve_prd_dir(config)
    if not prd_dir.exists():
        return "skipped", f"the PRD folder doesn't exist yet ({prd_dir})"

    target = Path(resolve_source_path(config, get_finalize_checkpoint_backup_dir(config)))
    # resolve_source_path leaves a relative value alone when workspace.root isn't set. Writing
    # it anyway would drop archives wherever the process happens to be running (Tempa's own
    # install folder, or a test's working directory), which is never what was meant.
    if not target.is_absolute():
        return "skipped", (f"no workspace root configured — can't resolve the backup folder "
                           f"'{target}' to an absolute path")
    # A backup folder inside the PRD folder would make every snapshot include the previous
    # ones, so the archive doubles each time. Settings rejects this at save time; this is the
    # runtime guard for a hand-edited config.json.
    if target == prd_dir or target.is_relative_to(prd_dir):
        return "skipped", (f"the backup folder ({target}) is inside the PRD folder — each "
                           "snapshot would archive the previous ones")

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return "failed", f"could not create the backup folder {target}: {e}"

    # Timestamp first so the folder sorts chronologically by name, matching the
    # clarification-YYYYMMDD-HHMMSS.md convention the clarifications folder already uses.
    path = target / f"prd-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{label}.zip"
    try:
        path.write_bytes(build_zip(prd_dir))
    except OSError as e:
        return "failed", f"could not write {path}: {e}"

    return "saved", str(path)
