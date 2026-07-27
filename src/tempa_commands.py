"""Thin CLI command handlers: workspace, models, status, spec/dashboard, verify, test.

The mostly-stateless subcommands - printing/setting the workspace layout and per-stage models,
initializing folders, showing status, opening the dashboard on a given section, running the
permission test, and the one-shot epic verification. Heavier workflows live in
tempa_implement / tempa_clarify / tempa_maintenance.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dashboard_ui import run_dashboard

from tempa_config import (
    DEFAULT_WORKSPACE, LOGS_DIR, VERIFY_DIR, WORKING_DIR, WORKSPACE_LABELS,
    get_model, get_models, get_sources, get_workspace, load_config,
    resolve_workspace_paths, save_config, _resolve_model_alias,
)
from tempa_config import resolve_prd_dir as _resolve_prd_dir
from tempa_config import resolve_clar_dir as _resolve_clar_dir
from tempa_logging import _state, _banner, _print_log_tail, log
from tempa_prompts import build_prompt, load_prompt, _resolve_template_params
from tempa_session import build_claude_cmd, _run_claude_session, _stream_claude_process


def run_test() -> None:
    claude_exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude_exe:
        raise FileNotFoundError("claude CLI not found in PATH")

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

    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"test_{timestamp}.txt"

    log(f"Permission test starting — claude: {claude_exe} | log: {log_path.name}")

    # Same pattern as every other session runner: capture the raw --output-format
    # stream-json output and parse it into readable lines (written to the log file)
    # instead of dumping raw JSON straight to the console.
    try:
        exit_code = _stream_claude_process(
            build_claude_cmd(claude_exe, get_model(load_config(), "implement")),
            test_prompt, log_path, "permission test", [0],
        )
    except Exception as e:
        log(f"TEST FAILED — error running claude: {e}")
        return

    if test_file.exists():
        test_file.unlink()

    if _state.auth_error_hit:
        log(f"TEST stopped — authentication failed (see message above; log: {log_path.name})")
    elif _state.usage_limit_hit:
        log(f"TEST stopped — Claude usage limit reached (see log: {log_path.name})")
    elif exit_code != 0:
        log(f"TEST FAILED — claude exited with code {exit_code} (see log: {log_path.name})")
    elif not done_file.exists():
        log(f"TEST FAILED — claude did not complete all steps (done marker missing) (see log: {log_path.name})")
    else:
        done_file.unlink()
        log("TEST PASSED — all steps completed successfully")


def run_verify(epic: str) -> None:
    config = load_config()
    VERIFY_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = VERIFY_DIR / f"{epic}-verify-{timestamp}.md"

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

    exit_code, log_path = _run_claude_session(
        prompt,
        lambda claude_exe: build_claude_cmd(claude_exe, get_model(config, "implement")),
        log_prefix=f"verify_{epic}",
        banner_label=f"Verification for [{epic}]",
        progress_tag=f"VERIFY {epic}",
        on_json_event=_on_json_event,
    )

    if _state.auth_error_hit:
        sys.exit(3)
    if _state.usage_limit_hit:
        log(f"Verification stopped — Claude usage limit reached.")
        sys.exit(2)

    if exit_code != 0:
        log(f"Verification FAILED for [{epic}] — exit code {exit_code}")
        _print_log_tail(log_path)
        return

    if output_file.exists():
        log(f"Verification complete — report: {output_file}")
    elif result_holder[0]:
        output_file.write_text(result_holder[0], encoding="utf-8")
        log(f"Verification complete — report saved from response: {output_file}")
    else:
        log(f"Verification finished but no report was generated. Check log: {log_path}")


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
    """Clear workspace.root in config.json (the Home page's "close working folder"
    icon) — lets Tempa be pointed at a different project without touching the current
    project's own files, since nothing on disk is deleted here, only config.json.

    Only allowed once the harness's own state has already been cleared (`tempa clear`:
    "epic" array emptied, last_auto_answer reset to 0) — otherwise closing the folder
    would silently orphan in-progress epic/session tracking that still refers to it.
    """
    config = load_config()
    epic = config.get("epic") or []
    if epic or config.get("last_auto_answer", 0):
        log("ERROR: Run `tempa clear` first — the working folder can only be closed "
            "once the \"epic\" array is empty and last_auto_answer is 0.")
        sys.exit(1)

    workspace = get_workspace(config)
    workspace["root"] = ""
    config["workspace"] = workspace
    save_config(config)
    log("Working folder closed — workspace.root cleared in config.json.")


def run_init(args: argparse.Namespace) -> None:
    """Initialize working folders: set workspace.root in config.json, then create the
    default working folders on disk (docs, adr, specs, apps, infra, archive) under root,
    plus every configured `sources` folder (prd, epics, clarifications, ...) so the
    expected structure (e.g. specs/prd) exists upfront instead of only appearing once
    clarify/implement first write to it.

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
    for key, path_str in get_sources(config).items():
        if not path_str:
            continue
        folder = Path(path_str)
        if folder.exists():
            log(f"Folder already exists, skipping: {folder}")
        else:
            folder.mkdir(parents=True, exist_ok=True)
            log(f"Folder created: {folder}")

    # Ensure the specs/ folder (working specifications — not meant to be version
    # controlled) is git-ignored: create .gitignore if missing, append the entry if absent.
    gitignore_path = root_path / ".gitignore"
    specs_entry = f"{workspace['specs']}/"
    if not gitignore_path.exists():
        with open(gitignore_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(specs_entry + "\n")
        log(f".gitignore created: {gitignore_path}")
    else:
        existing_text = gitignore_path.read_text(encoding="utf-8")
        existing_lines = existing_text.splitlines()
        if specs_entry in existing_lines or workspace["specs"] in existing_lines:
            log(f".gitignore already ignores '{specs_entry}', skipping: {gitignore_path}")
        else:
            with open(gitignore_path, "a", encoding="utf-8", newline="\n") as f:
                if existing_text and not existing_text.endswith("\n"):
                    f.write("\n")
                f.write(specs_entry + "\n")
            log(f"Added '{specs_entry}' to .gitignore: {gitignore_path}")

    print_workspace(config)


def print_models(config: dict | None = None) -> None:
    """Display the AI model configured for each harness stage."""
    if config is None:
        config = load_config()
    models = get_models(config)
    _banner("AI MODEL PER STAGE")
    labels = {
        "clarify": "Clarify   (clarify)",
        "plan": "Plan      (plan)",
        "implement": "Implement (implement, QA, verify)",
    }
    for stage in ("clarify", "plan", "implement"):
        print(f"  {labels[stage]:<34} {models.get(stage, '?')}", flush=True)


def set_models(args: argparse.Namespace) -> None:
    """Set the AI model per stage in config.json (key "models").

    Usage:
      tempa set-model [--clarify <model>] [--plan <model>] [--implement <model>]

    <model> accepts a friendly alias (opus-4.8, sonnet-5, haiku-4.5, fable-5) or a full
    model id (e.g. claude-opus-4-8). Stages omitted keep their current/default value.
    """
    config = load_config()
    models = get_models(config)

    changed = False
    for stage in ("clarify", "plan", "implement"):
        value = getattr(args, stage)
        if value is not None:
            models[stage] = _resolve_model_alias(value)
            changed = True

    config["models"] = models
    save_config(config)
    if changed:
        log("AI model saved to config.json (key \"models\").")
    else:
        log("No model flag given (--clarify/--plan/--implement) — showing the current configuration.")
    print_models(config)


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
