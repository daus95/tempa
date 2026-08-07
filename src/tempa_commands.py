"""Thin CLI command handlers: workspace, models, status, spec/dashboard, verify, test.

The mostly-stateless subcommands - printing/setting the workspace layout and per-stage models,
initializing folders, showing status, opening the dashboard on a given section, running the
permission test, and the one-shot epic verification. Heavier workflows live in
tempa_implement / tempa_clarify / tempa_maintenance.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from dashboard_ui import run_dashboard
from tempa_backend import BACKENDS, get_backend_def, is_valid_reasoning_effort
from tempa_config import (
    DEFAULT_WORKSPACE,
    SCRIPT_DIR,
    WORKING_DIR,
    WORKSPACE_LABELS,
    _resolve_model_alias,
    clear_active_workspace_root,
    get_backend,
    get_backends,
    get_logs_dir,
    get_model,
    get_models,
    get_principles_path,
    get_qa_dir,
    get_reasoning_effort,
    get_reasoning_efforts,
    get_sources,
    get_verify_dir,
    get_workspace,
    load_config,
    read_principles,
    resolve_workspace_paths,
    save_config,
    set_active_workspace_root,
)
from tempa_config import resolve_clar_dir as _resolve_clar_dir
from tempa_config import resolve_prd_dir as _resolve_prd_dir
from tempa_logging import _banner, _print_log_tail, _state, log
from tempa_notifications import AttentionEventType, flush_pending_notifications, notify_attention
from tempa_prompts import _resolve_template_params, build_prompt, load_prompt
from tempa_session import (
    _run_backend_session,
    _stream_backend_process,
    prepare_backend_invocation,
    run_with_usage_limit_retry,
)


def run_test() -> None:
    """Exercise whichever backend is configured for the "implement" stage end-to-end —
    this is the command that answers "can Tempa actually drive this CLI," so it should
    reflect whichever backend the pipeline will actually use."""
    config = load_config()
    flush_pending_notifications()
    backend = get_backend_def(get_backend(config, "implement"))

    test_file = WORKING_DIR / "permission-test.txt"
    done_file = WORKING_DIR / "permission-test-done.txt"

    for f in (test_file, done_file):
        if f.exists():
            f.unlink()

    test_prompt = (
        f"Execute these exact steps using your file tools, one by one, with no confirmation needed: "
        f"(1) Write the text 'permission test ok' to the file {test_file}. "
        f"(2) Read that file back and confirm the content. "
        f"(3) Delete that file. "
        f"(4) Write the text 'done' to the file {done_file}."
    )

    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"test_{timestamp}.txt"

    log(f"Permission test starting — backend: {backend.label} | log: {log_path.name}")

    # Same pattern as every other session runner: capture the raw JSON-lines output and
    # parse it into readable lines (written to the log file) instead of dumping raw JSON
    # straight to the console.
    try:
        cmd, stdin_text = prepare_backend_invocation(
            backend, get_model(config, "implement"), None, test_prompt, log_path,
            get_reasoning_effort(config, "implement"),
        )
        exit_code = _stream_backend_process(backend, cmd, stdin_text, log_path, "permission test", [0])
    except Exception as e:
        log(f"TEST FAILED — error running {backend.label}: {e}")
        notify_attention(AttentionEventType.BACKEND_TEST_FAILED, "Backend test",
                         "Backend permission test failed", "Review the backend configuration and test log.",
                         details={"backend": backend.name})
        return

    if test_file.exists():
        test_file.unlink()

    if _state.auth_error_hit:
        log(f"TEST stopped — authentication failed (see message above; log: {log_path.name})")
    elif _state.usage_limit_hit:
        log(f"TEST stopped — usage limit reached (see log: {log_path.name})")
    elif _state.server_overloaded_hit:
        log(f"TEST stopped — backend API overloaded, a transient issue (see log: {log_path.name})")
    elif exit_code != 0:
        log(f"TEST FAILED — {backend.label} exited with code {exit_code} (see log: {log_path.name})")
        notify_attention(AttentionEventType.BACKEND_TEST_FAILED, "Backend test",
                         "Backend permission test failed", "Review the backend configuration and test log.",
                         log_path=log_path, details={"backend": backend.name, "exit_code": exit_code})
    elif not done_file.exists():
        log(f"TEST FAILED — {backend.label} did not complete all steps (done marker missing) (see log: {log_path.name})")
        notify_attention(AttentionEventType.BACKEND_TEST_FAILED, "Backend test",
                         "Backend permission test did not complete", "Review the backend configuration and test log.",
                         log_path=log_path, details={"backend": backend.name})
    else:
        done_file.unlink()
        log("TEST PASSED — all steps completed successfully")


def run_verify(epic: str) -> None:
    config = load_config()
    flush_pending_notifications()
    verify_dir = get_verify_dir()
    verify_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = verify_dir / f"{epic}-verify-{timestamp}.md"

    params = _resolve_template_params(config, epic)
    params["output_file"] = str(output_file)

    template = load_prompt("verify")
    prompt = build_prompt(template, params)

    # Extract report content from the final result event as a fallback, in case the
    # session didn't actually write output_file itself.
    result_holder: list[str | None] = [None]

    def _on_json_event(data: dict) -> None:
        if data.get("type") == "result" and data.get("result"):
            result_holder[0] = data["result"]

    session_result: list[tuple[int, Path]] = [(-1, Path())]

    def _run_once() -> bool:
        exit_code, log_path = _run_backend_session(
            get_backend_def(get_backend(config, "implement")),
            prompt,
            get_model(config, "implement"),
            log_prefix=f"verify_{epic}",
            banner_label=f"Verification for [{epic}]",
            reasoning_effort=get_reasoning_effort(config, "implement"),
            progress_tag=f"VERIFY {epic}",
            on_json_event=_on_json_event,
        )
        session_result[0] = (exit_code, log_path)
        return exit_code == 0

    # A usage-limit hit is waited out and retried in place (see run_with_usage_limit_retry
    # in tempa_session.py) rather than failing verification over it.
    run_with_usage_limit_retry(_run_once, f"Verification for [{epic}]")
    exit_code, log_path = session_result[0]

    if _state.auth_error_hit:
        sys.exit(3)

    if exit_code != 0:
        log(f"Verification FAILED for [{epic}] — exit code {exit_code}")
        _print_log_tail(log_path)
        notify_attention(AttentionEventType.VERIFICATION_FAILED, "Verification",
                         f"{epic} verification failed", "Review the verification log and report, then rerun verification.",
                         epic=epic, log_path=log_path, details={"exit_code": exit_code})
        return

    if output_file.exists():
        log(f"Verification complete — report: {output_file}")
    elif result_holder[0]:
        output_file.write_text(result_holder[0], encoding="utf-8")
        log(f"Verification complete — report saved from response: {output_file}")
    else:
        log(f"Verification finished but no report was generated. Check log: {log_path}")
        notify_attention(AttentionEventType.VERIFICATION_FAILED, "Verification",
                         f"{epic} verification produced no report", "Review the verification log and rerun verification.",
                         epic=epic, log_path=log_path)


def print_workspace(config: dict | None = None) -> None:
    """Display the configured working-folder layout with resolved absolute paths."""
    if config is None:
        config = load_config()
    workspace = get_workspace(config)
    resolved = resolve_workspace_paths(config)

    _banner("WORKING FOLDERS")

    root = workspace.get("root", "")
    if not root:
        print("  ⚠ Root folder has not been set. Set it with: tempa set-folders --root <absolute_path>", flush=True)
        return

    for key in DEFAULT_WORKSPACE:
        label = WORKSPACE_LABELS.get(key, key)
        if key == "root":
            print(f"  {label:<26} {workspace[key]}", flush=True)
        else:
            print(f"  {label:<26} {workspace[key]:<12} -> {resolved.get(key, '')}", flush=True)

    if not Path(root).exists():
        print(f"  ⚠ Root folder does not exist on disk yet: {root}", flush=True)


def set_working_folders(args: argparse.Namespace) -> None:
    """Set the default working-folder layout in config.json (key "workspace").

    Usage:
      tempa set-folders --root <absolute_path>
                 [--docs <rel>] [--adr <rel>] [--specs <rel>]
                 [--apps <rel>] [--infra <rel>] [--archive <rel>]

    `--root` MUST be an absolute path. Every other folder is relative to root and
    falls back to its default when omitted (docs, adr, specs, apps, infra, archive).
    """
    config = load_config()
    workspace = get_workspace(config)

    if args.root is not None:
        root_path = Path(args.root)
        if not root_path.is_absolute():
            log(f"ERROR: --root must be an absolute path, not '{args.root}'")
            sys.exit(1)
        workspace["root"] = str(root_path)

    for flag, key in (
        ("--docs", "docs"),
        ("--adr", "adr"),
        ("--specs", "specs"),
        ("--apps", "apps"),
        ("--infra", "infra"),
        ("--archive", "archive"),
    ):
        value = getattr(args, key)
        if value is not None:
            if Path(value).is_absolute():
                log(f"ERROR: {flag} must be a path relative to root, not an absolute path '{value}'")
                sys.exit(1)
            workspace[key] = value

    if not workspace.get("root"):
        log("ERROR: root folder must be set (absolute path). "
            "Example: tempa set-folders --root C:\\work\\repo\\qlar-medical-clinic-backoffice")
        sys.exit(1)

    config["workspace"] = workspace
    save_config(config)

    log("Working folders saved to config.json (key \"workspace\").")
    print_workspace(config)


def run_close_folder() -> None:
    """Detach the active workspace (the Home page's "close working folder" icon) — lets
    Tempa be pointed at a different project. Only the active-workspace pointer is cleared;
    nothing on disk is deleted or modified. The workspace's own `.tempa/` folder
    (config.json, epic/session state, logs, qa, specs) is left exactly as it was, so
    reopening it later with `tempa init <same_path>` resumes right where it left off.
    """
    clear_active_workspace_root()
    log("Working folder closed — no workspace is active. Its .tempa/ folder "
        "(config, logs, qa, specs) was left untouched; reopen it with `tempa init <path>`.")


def run_init(args: argparse.Namespace) -> None:
    """Point Tempa at a workspace and initialize its working folders.

    Sets the active-workspace pointer to `root` FIRST, so every config.json access below
    (load_config/save_config) resolves to that workspace's own `<root>/.tempa/config.json` —
    not Tempa's install folder. If that workspace was used before, its existing config.json
    is loaded as-is (epic/session history, models, etc. all resume unchanged); otherwise a
    fresh default one is created there.

    Then creates the default working folders on disk (docs, adr, specs, apps, infra, archive)
    under root — `specs` lands under `<root>/.tempa/specs` (see resolve_workspace_paths) —
    plus every configured `sources` folder (prd, epics, clarifications, ...) and the
    `.tempa/logs`, `.tempa/qa`, `.tempa/verify` output folders, so the expected structure
    exists upfront instead of only appearing once a session/QA/verify run first writes to it.

    Usage:
      tempa init <absolute_path>

    Folders that already exist on disk are NOT recreated and their contents are NEVER
    overwritten — only folders that don't exist yet are created.
    """
    root = args.root
    if root is None:
        log("ERROR: init requires a root folder path (absolute). "
            "Example: tempa init C:\\work\\repo\\qlar-medical-clinic-backoffice")
        sys.exit(1)

    root_path = Path(root)
    if not root_path.is_absolute():
        log(f"ERROR: root must be an absolute path, not '{root}'")
        sys.exit(1)

    set_active_workspace_root(root_path)
    config = load_config()
    workspace = get_workspace(config)
    workspace["root"] = str(root_path)
    config["workspace"] = workspace
    save_config(config)
    log("Working folders saved to config.json (key \"workspace\").")

    # Create the root folder first (if missing), then every sub-folder under it.
    # exist_ok=True makes this operation idempotent: folders that already exist
    # are not recreated and their contents are never touched/overwritten.
    if root_path.exists():
        log(f"Root folder already exists, skipping: {root_path}")
    else:
        root_path.mkdir(parents=True, exist_ok=True)
        log(f"Root folder created: {root_path}")

    resolved = resolve_workspace_paths(config)
    for key in DEFAULT_WORKSPACE:
        if key == "root":
            continue
        folder = Path(resolved[key])
        if folder.exists():
            log(f"Folder already exists, skipping: {folder}")
        else:
            folder.mkdir(parents=True, exist_ok=True)
            log(f"Folder created: {folder}")

    # Also create every configured `sources` folder (e.g. specs/prd, specs/pbi/epics,
    # specs/clarifications) so the expected input/output structure exists from the start,
    # not just the ones clarify/implement happen to create lazily on first write.
    for _key, path_str in get_sources(config).items():
        if not path_str:
            continue
        folder = Path(path_str)
        if folder.exists():
            log(f"Folder already exists, skipping: {folder}")
        else:
            folder.mkdir(parents=True, exist_ok=True)
            log(f"Folder created: {folder}")

    # Also create logs/qa/verify upfront (otherwise only created lazily on first
    # session/QA/verify run) so `.tempa/` looks complete right after init.
    for folder in (get_logs_dir(), get_qa_dir(), get_verify_dir()):
        if folder.exists():
            log(f"Folder already exists, skipping: {folder}")
        else:
            folder.mkdir(parents=True, exist_ok=True)
            log(f"Folder created: {folder}")

    # Ensure the .tempa/ folder (config.json, logs, qa, verify, specs — all Tempa-managed
    # state, not meant to be version controlled) is git-ignored: create .gitignore if
    # missing, append the entry if absent.
    gitignore_path = root_path / ".gitignore"
    tempa_entry = ".tempa/"
    if not gitignore_path.exists():
        with open(gitignore_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(tempa_entry + "\n")
        log(f".gitignore created: {gitignore_path}")
    else:
        existing_text = gitignore_path.read_text(encoding="utf-8")
        existing_lines = existing_text.splitlines()
        if tempa_entry in existing_lines or ".tempa" in existing_lines:
            log(f".gitignore already ignores '{tempa_entry}', skipping: {gitignore_path}")
        else:
            with open(gitignore_path, "a", encoding="utf-8", newline="\n") as f:
                if existing_text and not existing_text.endswith("\n"):
                    f.write("\n")
                f.write(tempa_entry + "\n")
            log(f"Added '{tempa_entry}' to .gitignore: {gitignore_path}")

    print_workspace(config)


STAGE_LABELS = {
    "clarify": "Clarify   (clarify)",
    "clarify_apply": "Clarify   (clarify --apply, --auto-answer)",
    "plan": "Plan      (plan)",
    "implement": "Implement (implement, QA, verify)",
}


def print_models(config: dict | None = None) -> None:
    """Display the AI model configured for each harness stage."""
    if config is None:
        config = load_config()
    models = get_models(config)
    _banner("AI MODEL PER STAGE")
    for stage in ("clarify", "clarify_apply", "plan", "implement"):
        print(f"  {STAGE_LABELS[stage]:<34} {models.get(stage, '?')}", flush=True)


def print_backends(config: dict | None = None) -> None:
    """Display the CLI backend configured for each harness stage."""
    if config is None:
        config = load_config()
    backends = get_backends(config)
    _banner("CLI BACKEND PER STAGE")
    for stage in ("clarify", "clarify_apply", "plan", "implement"):
        name = backends.get(stage, "claude")
        print(f"  {STAGE_LABELS[stage]:<34} {name:<10} ({get_backend_def(name).label})", flush=True)


def print_efforts(config: dict | None = None) -> None:
    """Display the reasoning effort configured for each harness stage."""
    if config is None:
        config = load_config()
    efforts = get_reasoning_efforts(config)
    _banner("AI REASONING EFFORT PER STAGE")
    for stage in ("clarify", "clarify_apply", "plan", "implement"):
        value = efforts.get(stage) or "(default)"
        print(f"  {STAGE_LABELS[stage]:<34} {value}", flush=True)


def set_efforts(args: argparse.Namespace) -> None:
    """Set the reasoning effort per stage in config.json (key "reasoning_efforts").

    Usage:
      tempa set-effort [--clarify <level>] [--clarify-apply <level>] [--plan <level>] [--implement <level>]

    <level> must be supported by that stage's currently configured backend+model (see
    `tempa show-models`/`show-backends`) — e.g. low/medium/high/xhigh/max for Claude Code,
    up to xhigh/max/ultra depending on the OpenAI Codex CLI model. Pass an empty string
    ("") to clear it (use the CLI/model's own default). Stages omitted keep their
    current/default value.
    """
    config = load_config()
    efforts = get_reasoning_efforts(config)
    models = get_models(config)
    backends = get_backends(config)

    changed = False
    for stage in ("clarify", "clarify_apply", "plan", "implement"):
        value = getattr(args, stage)
        if value is not None:
            value = value.strip()
            backend_def = get_backend_def(backends.get(stage, "claude"))
            model = models.get(stage, "")
            if not is_valid_reasoning_effort(backend_def, model, value):
                choices = ", ".join(backend_def.reasoning_effort_choices(model))
                log(f"ERROR: '{value}' is not a supported reasoning effort for {backend_def.label} model "
                    f"'{model}' — must be empty or one of: {choices}")
                sys.exit(1)
            efforts[stage] = value
            changed = True

    config["reasoning_efforts"] = efforts
    save_config(config)
    if changed:
        log("Reasoning effort saved to config.json (key \"reasoning_efforts\").")
    else:
        log("No effort flag given (--clarify/--clarify-apply/--plan/--implement) — showing the current configuration.")
    print_efforts(config)


def print_principles() -> None:
    """Display the workspace's architecture principles, applied to every stage's prompt."""
    _banner("ARCHITECTURE PRINCIPLES")
    print(f"  File: {get_principles_path()}", flush=True)
    print(flush=True)
    principles = read_principles()
    if not principles:
        print("  Not set — Tempa runs without project-wide principles.", flush=True)
        print("  Set them in the dashboard (tempa dashboard -> Architecture Principles).", flush=True)
        return
    for line in principles.splitlines():
        print(f"  {line}", flush=True)


def set_models(args: argparse.Namespace) -> None:
    """Set the AI model per stage in config.json (key "models").

    Usage:
      tempa set-model [--clarify <model>] [--clarify-apply <model>] [--plan <model>] [--implement <model>]

    <model> accepts a friendly alias (opus-5, sonnet-5, haiku-4.5, fable-5) or a full
    model id (e.g. claude-opus-5) when the stage's backend is "claude" — aliases are
    Claude-only, so for a "copilot"/"codex" stage the value is stored as-is (their model
    catalogs move independently of Tempa and aren't hardcoded here; see `tempa set-backend`).
    Stages omitted keep their current/default value.

    "clarify_apply" (--clarify-apply) is the model used to apply resolutions to the
    PRD/spec + auto-answer — mechanical work compared to evaluate, hence a separate,
    cheaper-by-default model. It's a full stage in its own right (has its own
    `set-backend`/`set-effort` too), same as clarify/plan/implement.
    """
    config = load_config()
    models = get_models(config)
    backends = get_backends(config)

    changed = False
    for stage in ("clarify", "clarify_apply", "plan", "implement"):
        value = getattr(args, stage)
        if value is not None:
            backend = backends.get(stage, "claude")
            models[stage] = _resolve_model_alias(value) if backend == "claude" else value
            changed = True

    config["models"] = models
    save_config(config)
    if changed:
        log("AI model saved to config.json (key \"models\").")
    else:
        log("No model flag given (--clarify/--clarify-apply/--plan/--implement) — showing the current configuration.")
    print_models(config)


def set_backends(args: argparse.Namespace) -> None:
    """Set the CLI backend per stage in config.json (key "backends").

    Usage:
      tempa set-backend [--clarify <claude|copilot|codex>] [--clarify-apply ...] [--plan ...] [--implement ...]

    Stages omitted keep their current/default value ("claude").
    """
    config = load_config()
    backends = get_backends(config)

    changed = False
    for stage in ("clarify", "clarify_apply", "plan", "implement"):
        value = getattr(args, stage)
        if value is not None:
            if value not in BACKENDS:
                log(f"ERROR: unknown backend '{value}' — must be one of: {', '.join(BACKENDS)}")
                sys.exit(1)
            backends[stage] = value
            changed = True

    config["backends"] = backends
    save_config(config)
    if changed:
        log("CLI backend saved to config.json (key \"backends\").")
    else:
        log("No backend flag given (--clarify/--clarify-apply/--plan/--implement) — showing the current configuration.")
    print_backends(config)


def print_status() -> None:
    config = load_config()
    sessions = (config.get("epic") or [])
    _banner("SESSION STATUS")
    for s in sessions:
        epic = s.get("epic_name", "?")
        status = s["status"]
        total_f = s.get("total_features", 0)
        completed_f = s.get("completed_features", 0)
        last_run = s.get("last_run", "")[:16].replace("T", " ") if s.get("last_run") else "-"

        status_icons = {"done": "✅", "on_progress": "🔄", "pending": "⬜", "failed": "❌", "require_fixing": "🔧"}
        icon = status_icons.get(status, "?")
        qa_tag = ""
        if status == "done":
            qa_tag = "  [QA ok]" if s.get("qa_passed", False) else "  [QA --]"
        print(f"{icon} {epic:<10} {status:<16} {completed_f}/{total_f} features   last run: {last_run}{qa_tag}")

        feat_icons = {"done": "✅", "failed": "❌", "require_fixing": "🔧"}
        for feat in s.get("features", []):
            feat_icon = feat_icons.get(feat["status"], "⬜")
            print(f"   {feat_icon} {feat['id']} — {feat['name']}")


GITHUB_RELEASES_API = "https://api.github.com/repos/daus95/tempa/releases/latest"
GITHUB_RELEASES_PAGE = "https://github.com/daus95/tempa/releases/latest"
GITHUB_LATEST_DOWNLOAD_URL = "https://github.com/daus95/tempa/releases/latest/download/tempa.zip"


def get_local_version() -> str:
    """Read the installed Tempa version from the VERSION file at the install root
    (SCRIPT_DIR) — that file is bumped as part of cutting each GitHub release."""
    try:
        return (SCRIPT_DIR / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def print_version() -> None:
    """`tempa version` — show the locally installed Tempa version."""
    print(f"Tempa {get_local_version()}", flush=True)


def get_latest_release_version(timeout: float = 5.0) -> str | None:
    """Query GitHub for the tag of the latest published release. Returns None (rather than
    raising) on any network failure or unexpected response, since this is a best-effort
    check, not something the rest of the CLI depends on."""
    request = urllib.request.Request(
        GITHUB_RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "tempa-cli"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    tag = data.get("tag_name", "")
    return tag.removeprefix("v") or None


def print_check_update() -> None:
    """`tempa check-update` — compare the installed version against GitHub's latest release."""
    local = get_local_version()
    _banner("CHECK FOR UPDATES")
    print(f"  Installed version : {local}", flush=True)

    latest = get_latest_release_version()
    if latest is None:
        print("  Could not reach GitHub to check the latest release (offline, or "
              "api.github.com unreachable).", flush=True)
        print(f"  Check manually: {GITHUB_RELEASES_PAGE}", flush=True)
        return

    print(f"  Latest release    : {latest}", flush=True)
    if local != "unknown" and local == latest:
        print("  You're up to date.", flush=True)
    else:
        print(f"  Update available — download: {GITHUB_LATEST_DOWNLOAD_URL}", flush=True)


def _confirm_update() -> None:
    """Ask for interactive "yes" confirmation before downloading and applying an update
    (skippable with --yes). Exits the process if not confirmed — never returns in that case."""
    if "--yes" in sys.argv:
        return
    if not sys.stdin.isatty():
        log("Aborted — confirmation required. Run in an interactive terminal, or add --yes.")
        sys.exit(1)
    try:
        answer = input('Type "yes" to download and apply this update (anything else cancels): ').strip().lower()
    except EOFError:
        answer = ""
    if answer != "yes":
        log("UPDATE CANCELLED — nothing was changed.")
        sys.exit(0)


def _download_release_zip(dest: Path) -> None:
    """Download the latest release's tempa.zip asset to `dest`, printing basic progress
    (a self-overwriting line on a real terminal, periodic lines otherwise)."""
    request = urllib.request.Request(GITHUB_LATEST_DOWNLOAD_URL, headers={"User-Agent": "tempa-cli"})
    is_tty = sys.stdout.isatty()
    with urllib.request.urlopen(request, timeout=30) as response, open(dest, "wb") as out_file:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if total:
                line = f"  Downloading... {downloaded * 100 // total}% ({downloaded // 1024} KB / {total // 1024} KB)"
            else:
                line = f"  Downloading... {downloaded // 1024} KB"
            if is_tty:
                print(f"\r{line}   ", end="", flush=True)
            elif downloaded // 65536 % 16 == 0:
                print(line, flush=True)
    if is_tty:
        print(flush=True)


def run_update() -> None:
    """`tempa update` — check GitHub for a newer release and, once confirmed, download and
    apply it on top of this install. Only overwrites files that are actually part of the
    release archive (tracked repo files); anything else already on disk — `.tempa/`,
    `.active-workspace`, `__pycache__`, a dev checkout's `.git`, etc. — is left untouched
    since none of those are ever included in the archive in the first place."""
    local = get_local_version()
    _banner("UPDATE TEMPA")
    print(f"  Installed version : {local}", flush=True)
    print(f"  Install location  : {SCRIPT_DIR}", flush=True)

    latest = get_latest_release_version()
    if latest is None:
        print("  Could not reach GitHub to check the latest release (offline, or "
              "api.github.com unreachable). Nothing was changed.", flush=True)
        sys.exit(1)

    print(f"  Latest release    : {latest}", flush=True)
    if local != "unknown" and local == latest:
        print("  Already up to date — nothing to do.", flush=True)
        return

    print(f"  This will download release {latest} and overwrite the matching files in "
          f"{SCRIPT_DIR}.", flush=True)
    _confirm_update()

    with tempfile.TemporaryDirectory(prefix="tempa-update-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        zip_path = tmp_path / "tempa.zip"
        log(f"Downloading release {latest}...")
        try:
            _download_release_zip(zip_path)
        except (urllib.error.URLError, OSError) as exc:
            log(f"Download failed: {exc}. Nothing was changed.")
            sys.exit(1)

        extract_dir = tmp_path / "extracted"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        source_root = extract_dir / "tempa"
        if not source_root.is_dir():
            log("Unexpected archive layout (no 'tempa/' folder inside it) — aborting, "
                "nothing was changed.")
            sys.exit(1)

        shutil.copytree(source_root, SCRIPT_DIR, dirs_exist_ok=True)

    log(f"Updated to {latest}. Restart any running 'tempa dashboard' or 'tempa implement' "
        "session so it picks up the new code.")


def run_spec_show() -> None:
    """`tempa spec --show` — open the dashboard directly on the Specification section:
    a tree of PRD files/subfolders on the left, a markdown view/edit pane on the right.
    Blocks until the user stops the server (Ctrl+C)."""
    config = load_config()
    prd_dir = _resolve_prd_dir(config)
    if not prd_dir.exists():
        log(f"PRD folder not found: {prd_dir}")
        log("Create it (or point sources.prd at the right folder in config.json / "
            "'tempa init <root>') and add specification files first.")
        sys.exit(1)
    if not prd_dir.is_dir():
        log(f"PRD path is not a folder: {prd_dir}")
        sys.exit(1)
    run_dashboard(prd_dir, _resolve_clar_dir(config), initial_view="specification")


def run_dashboard_command() -> None:
    """`tempa dashboard` — open the web dashboard on the Home view."""
    config = load_config()
    run_dashboard(_resolve_prd_dir(config), _resolve_clar_dir(config), initial_view="home")
