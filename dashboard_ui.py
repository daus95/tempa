"""Local web UI: the `tempa dashboard` command.

Serves a single-page app on 127.0.0.1 with a Windows-Explorer-style selector on the
left (Home / Specification / Clarification / Implementation) and a content pane on
the right. Replaces the former standalone spec_ui.py and clarify_ui.py:

  - Home: watermark placeholder (nothing else built here yet).
  - Specification: the PRD folder (sources.prd) as a collapsible file/folder tree;
    clicking a file shows it as rendered/edit markdown, with save-to-disk. This is
    the same browsing behavior spec_ui.py used to provide standalone.
  - Clarification: the clarification files (sources.clarifications) that still have
    at least one unanswered finding; clicking one shows its findings with a
    recommendation-vs-own-answer textarea per finding, same mechanism clarify_ui.py
    used to provide as a one-shot form, but selected from the sidebar instead of a
    tab bar. Saving only writes the answer into the file — it does not re-run the
    apply-to-PRD step; that stays a separate, explicit `tempa clarify --apply`.
  - Implementation: placeholder — not built yet.

Unlike the old clarify_ui (which shut its server down as soon as the user saved or
cancelled), the dashboard stays up until the user stops it with Ctrl+C, matching
spec_ui's long-running browsing behavior — because the sidebar lets the user hop
between files/sections at will.

All file access is confined to the relevant root (prd_dir for Specification,
clar_dir for Clarification): every requested path is resolved and checked to be
inside its root before any read/write, so the browser cannot escape via `..` or
absolute paths.
"""

from __future__ import annotations

import ctypes
import hashlib
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Specification (PRD folder) browsing — ported from the former spec_ui.py.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Clarification answering — ported from the former clarify_ui.py.
# ---------------------------------------------------------------------------

ITEM_RE = re.compile(
    r'<!--\s*clarify:item\s+id="(?P<id>[^"]*)"\s+severity="(?P<severity>critical|major|minor)"\s*-->'
    r'(?P<body>.*?)'
    r'<!--\s*clarify:enditem\s*-->',
    re.DOTALL,
)
ANSWER_RE = re.compile(
    r'<!--\s*clarify:answer-start\s*-->(?P<answer>.*?)<!--\s*clarify:answer-end\s*-->',
    re.DOTALL,
)
LABEL_RE = re.compile(r'\*\*(Where|Question|Recommendation|Your answer):\*\*')
HEADING_RE = re.compile(r'^\s{0,3}#{1,6}\s+(.+?)\s*$', re.MULTILINE)

SEVERITY_LABELS = {"critical": "Critical", "major": "Major", "minor": "Minor"}


@dataclass
class ClarificationItem:
    key: str
    raw_id: str
    severity: str
    title: str
    where: str
    question: str
    recommendation: str
    existing_answer: str
    file: Path
    answer_start: int
    answer_end: int
    has_markers: bool


def _parse_item_match(match: re.Match, text: str, path: Path, file_index: int) -> ClarificationItem | None:
    body = match.group("body")
    body_abs_start = match.start("body")
    raw_id = match.group("id") or f"item{file_index}-{match.start()}"

    label_matches = list(LABEL_RE.finditer(body))
    if not label_matches:
        return None
    preamble = body[: label_matches[0].start()]

    segments: dict[str, tuple[int, int]] = {}
    for i, lm in enumerate(label_matches):
        seg_start = lm.end()
        seg_end = label_matches[i + 1].start() if i + 1 < len(label_matches) else len(body)
        segments[lm.group(1)] = (seg_start, seg_end)

    if "Your answer" not in segments:
        return None

    def seg_text(name: str) -> str:
        if name not in segments:
            return ""
        s, e = segments[name]
        return body[s:e].strip()

    heading_m = HEADING_RE.search(preamble)
    title = heading_m.group(1).strip() if heading_m else f"Finding {raw_id}"

    ya_start, ya_end = segments["Your answer"]
    ya_abs_start = body_abs_start + ya_start
    ya_abs_end = body_abs_start + ya_end
    ya_text = text[ya_abs_start:ya_abs_end]

    am = ANSWER_RE.search(ya_text)
    if am:
        existing_answer = am.group("answer").strip()
        answer_start = ya_abs_start + am.start(0)
        answer_end = ya_abs_start + am.end(0)
        has_markers = True
    else:
        existing_answer = ya_text.strip()
        answer_start = ya_abs_start
        answer_end = ya_abs_end
        has_markers = False

    return ClarificationItem(
        key=f"f{file_index}-{raw_id}",
        raw_id=raw_id,
        severity=match.group("severity"),
        title=title,
        where=seg_text("Where"),
        question=seg_text("Question"),
        recommendation=seg_text("Recommendation"),
        existing_answer=existing_answer,
        file=path,
        answer_start=answer_start,
        answer_end=answer_end,
        has_markers=has_markers,
    )


def parse_file(path: Path, text: str, file_index: int) -> tuple[list[ClarificationItem], list[tuple[str, object]]]:
    """Return (items, blocks). `blocks` is the document in order — ('text', str) for
    plain markdown in between/around findings, ('item', ClarificationItem) for each
    recognized finding — so the rendered page mirrors the source file's structure."""
    items: list[ClarificationItem] = []
    blocks: list[tuple[str, object]] = []
    pos = 0
    for m in ITEM_RE.finditer(text):
        if m.start() > pos:
            prefix = text[pos:m.start()]
            if prefix.strip():
                blocks.append(("text", prefix))
        item = _parse_item_match(m, text, path, file_index)
        if item is not None:
            items.append(item)
            blocks.append(("item", item))
        else:
            blocks.append(("text", m.group(0)))
        pos = m.end()
    if pos < len(text):
        tail = text[pos:]
        if tail.strip():
            blocks.append(("text", tail))
    return items, blocks


def file_answer_status(path: Path) -> tuple[int, int]:
    """Return (answered, total) recognized clarification findings in `path`. (0, 0) if
    the file can't be read or has no recognized findings."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return (0, 0)
    items, _ = parse_file(path, text, 0)
    if not items:
        return (0, 0)
    return (sum(1 for it in items if it.existing_answer), len(items))


def _md_inline(s: str) -> str:
    s = html_lib.escape(s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'`([^`]+?)`', r'<code>\1</code>', s)
    s = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'<em>\1</em>', s)
    return s


def render_markdown(text: str) -> str:
    text = text.strip("\n")
    if not text.strip():
        return ""
    parts: list[str] = []
    para: list[str] = []
    list_items: list[str] = []

    def flush_para() -> None:
        if para:
            parts.append(f"<p>{_md_inline(' '.join(para))}</p>")
            para.clear()

    def flush_list() -> None:
        if list_items:
            parts.append("<ul>" + "".join(f"<li>{_md_inline(li)}</li>" for li in list_items) + "</ul>")
            list_items.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_para()
            flush_list()
            continue
        heading_m = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        list_m = re.match(r'^[-*]\s+(.*)$', stripped)
        if heading_m:
            flush_para()
            flush_list()
            level = min(len(heading_m.group(1)) + 2, 6)
            parts.append(f"<h{level}>{_md_inline(heading_m.group(2))}</h{level}>")
        elif list_m:
            flush_para()
            list_items.append(list_m.group(1))
        else:
            flush_list()
            para.append(stripped)
    flush_para()
    flush_list()
    return "\n".join(parts)


def _attr(s: str) -> str:
    return html_lib.escape(s, quote=True)


def _render_item_html(item: ClarificationItem) -> str:
    key = _attr(item.key)
    has_recommendation = bool(item.recommendation)
    default_own = bool(item.existing_answer) or not has_recommendation

    # Unanswered items with a recommendation start with NEITHER radio checked, so the
    # user has to actively pick one — pre-selecting "recommendation" here meant a user
    # who wanted the default outcome never fired a `change` event, leaving clarifyDirty
    # false and the Save button stuck disabled (see followAllBtn for the bulk version).
    recommendation_radio = ""
    if has_recommendation:
        recommendation_radio = (
            f'<label><input type="radio" name="mode-{key}" value="recommendation"> '
            f"Follow the recommendation</label>"
        )
    own_checked = "checked" if default_own else ""
    own_radio = (
        f'<label><input type="radio" name="mode-{key}" value="own" {own_checked}> '
        f"I'll write my own answer</label>"
    )

    textarea_disabled = "" if default_own else "disabled"
    textarea_value = html_lib.escape(item.existing_answer) if default_own else ""

    recommendation_html = (
        f'<div class="field recommendation"><h4>Recommendation</h4>'
        f'<div class="md">{render_markdown(item.recommendation)}</div></div>'
        if has_recommendation else ""
    )

    return f"""
<section class="item sev-{item.severity}" data-key="{key}">
  <header>
    <span class="badge {item.severity}">{SEVERITY_LABELS.get(item.severity, item.severity)}</span>
    <h3>{_md_inline(item.title)}</h3>
  </header>
  <div class="field"><h4>Where</h4><div class="md">{render_markdown(item.where)}</div></div>
  <div class="field"><h4>Question</h4><div class="md">{render_markdown(item.question)}</div></div>
  {recommendation_html}
  <div class="answer-block">
    <div class="selector">
      {recommendation_radio}
      {own_radio}
    </div>
    <textarea rows="5" data-key="{key}" placeholder="Write your answer here..." {textarea_disabled}>{textarea_value}</textarea>
  </div>
</section>
""".strip()


def _render_blocks_html(blocks: list[tuple[str, object]]) -> str:
    parts: list[str] = []
    for kind, payload in blocks:
        if kind == "text":
            rendered = render_markdown(payload)  # type: ignore[arg-type]
            if rendered:
                parts.append(f'<div class="doc-text">{rendered}</div>')
        else:
            parts.append(_render_item_html(payload))  # type: ignore[arg-type]
    return "\n".join(parts)


def _file_severity_stats(path: Path) -> dict | None:
    """Return per-file finding stats: name/path, an {answered,total} pair per severity
    (critical/major/minor), and the overall answered/total. None if the file has no
    recognized clarification items."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    items, _ = parse_file(path, text, 0)
    if not items:
        return None
    by_severity = {sev: {"answered": 0, "total": 0} for sev in ("critical", "major", "minor")}
    for it in items:
        by_severity[it.severity]["total"] += 1
        if it.existing_answer:
            by_severity[it.severity]["answered"] += 1
    answered = sum(v["answered"] for v in by_severity.values())
    return {
        "name": path.name, "path": path.name,
        "critical": by_severity["critical"], "major": by_severity["major"], "minor": by_severity["minor"],
        "answered": answered, "total": len(items),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _load_dashboard_config() -> dict:
    """Read config.json directly (rather than importing tempa.py, which imports this
    module) so the two stay decoupled; config.json always lives next to this file,
    same as tempa.py. Returns {} if it can't be read/parsed."""
    config_path = Path(__file__).resolve().parent / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return config if isinstance(config, dict) else {}


def _load_clarify_applied_hashes() -> dict:
    """config.json's "clarify_applied_hashes" — {filename: content-hash-at-last-apply},
    stamped by tempa.py's _record_clarify_applied_state() right after a successful
    `tempa clarify --apply`."""
    hashes = _load_dashboard_config().get("clarify_applied_hashes")
    return hashes if isinstance(hashes, dict) else {}


def _workspace_initialized() -> bool:
    """Whether `tempa init` has ever been run — workspace.root is set once on first
    init and never cleared afterward, so it's the only reliable signal (specs/prd
    paths always resolve to *some* folder even when uninitialized, via WORKING_DIR
    fallbacks in tempa.py, so probing the filesystem can't distinguish the two)."""
    return bool(_load_dashboard_config().get("workspace", {}).get("root"))


def _workspace_root() -> str:
    """config.json's workspace.root, or "" if not set yet."""
    return _load_dashboard_config().get("workspace", {}).get("root", "") or ""


def _workspace_can_close() -> bool:
    """Whether the Home page's "close working folder" icon should be shown/allowed —
    only once the harness's own state has already been cleared via `tempa clear`
    ("epic" array emptied, last_auto_answer reset to 0), mirroring the same
    precondition tempa.py's run_close_folder() enforces server-side."""
    config = _load_dashboard_config()
    return not (config.get("epic") or []) and not config.get("last_auto_answer", 0)


def _resolve_source_dir(source_key: str, specs_fallback: str) -> Path:
    """Re-derive one `sources` folder (e.g. "prd", "clarifications") straight from
    config.json, mirroring tempa.py's resolve_source_path/resolve_specs_dir without
    importing tempa.py (see _load_dashboard_config). Used to refresh server.prd_dir /
    server.clar_dir right after workspace.root is set via the Home page, so the
    dashboard reflects the new location without a restart."""
    config = _load_dashboard_config()
    root = config.get("workspace", {}).get("root", "") or ""
    raw = config.get("sources", {}).get(source_key, "")
    if raw:
        path = Path(raw)
        return path if path.is_absolute() or not root else Path(root) / raw
    specs_rel = config.get("workspace", {}).get("specs") or "specs"
    base = Path(root) / specs_rel if root else Path(__file__).resolve().parent.parent / specs_rel
    return base / specs_fallback


def _pick_folder_dialog() -> str | None:
    """Open a native Windows folder-picker dialog and return the selected absolute
    path, or None if the user cancelled. Shelled out to PowerShell (WinForms'
    FolderBrowserDialog needs an STA apartment) rather than opened in-process, since
    ThreadingHTTPServer handles each request on its own worker thread and GUI toolkits
    aren't safe to drive from there."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = 'Select Working Folder'; "
        "$f.ShowNewFolderButton = $true; "
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Sta", "-Command", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = (result.stdout or "").strip()
    return path or None


def _find_explorer_window(folder_name: str) -> int | None:
    """Find a visible Explorer file-browser window (class "CabinetWClass") whose
    title starts with `folder_name` — Explorer titles a folder window "<name>" on
    Windows 10 but "<name> - File Explorer" on Windows 11, so match by prefix rather
    than equality. Returns the last matching HWND found (most Z-order relevant in
    practice), or None."""
    user32 = ctypes.windll.user32
    matches: list[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if cls_buf.value != "CabinetWClass":
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        if title_buf.value.startswith(folder_name):
            matches.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return matches[-1] if matches else None


def _bring_window_to_front(hwnd: int) -> None:
    """Force `hwnd` to the foreground. A background process's plain SetForegroundWindow
    call is normally ignored by Windows' foreground-lock (the window just flashes in
    the taskbar instead) — tapping ALT is the standard workaround: it resets the lock
    system-wide, letting the very next SetForegroundWindow call through. The
    topmost-flash + BringWindowToTop calls are the usual companions to that trick,
    for the cases where SetForegroundWindow alone still gets ignored."""
    user32 = ctypes.windll.user32
    VK_MENU, KEYEVENTF_KEYUP, SW_RESTORE = 0x12, 0x0002, 9
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)


def _live_clarification_findings(files: list[dict]) -> dict:
    """True critical/major/minor counts, computed directly from the severity tags
    currently present across the given clarification files (as returned by
    _clarify_files_overview) — NOT config.json's "last_clarification_findings",
    which is the Claude session's own self-reported opinion of what's "still
    critical" and can say 0 right after an apply even though the finding's
    <!-- clarify:item ... severity="critical" --> tag is still sitting right there
    in the file, answered but not removed (applying edits the PRD, never the
    clarification file itself — see _record_clarify_applied_state's docstring
    below). A finding counts here whether or not it's been answered: being
    answered means a resolution was proposed, not that the file stopped listing it
    as a finding."""
    totals = {"critical": 0, "major": 0, "minor": 0}
    for f in files:
        for sev in totals:
            totals[sev] += f[sev]["total"]
    return totals


def _clarify_finalize_status(findings: dict) -> dict:
    """Whether "Finalized Clarification" is currently allowed to run.

    Requires all of:
      - at least one clarification action has ever completed ("hasRun")
      - that most recent action was a fresh evaluate pass, not a bare apply
        ("lastAction" == "evaluate") — answering criticals and applying them isn't
        enough on its own, since applying doesn't independently re-verify against
        the live PRD the way a fresh evaluate does, and doesn't touch the
        clarification files' severity tags either
      - the clarification files currently show zero critical findings (`findings`,
        from _live_clarification_findings — the actual tag count, not a
        self-reported opinion)

    config.json's "last_clarification_action" is stamped by tempa.py right after each
    `clarify` (evaluate) / `clarify --apply` (apply) / `clarify --finalize` (both,
    alternating) run — see run_clarify_once(), _run_apply_step(), and
    run_clarify_finalize() there."""
    last_action = _load_dashboard_config().get("last_clarification_action")
    fresh_evaluate = last_action == "evaluate"
    ready = fresh_evaluate and findings["critical"] == 0
    return {
        "hasRun": last_action is not None,
        "lastAction": last_action,
        "critical": findings["critical"],
        "ready": ready,
    }


def _epic_sessions() -> list:
    """config.json's "epic" array — the same per-epic/feature progress data
    `tempa status` (print_status()) formats to the console."""
    epics = _load_dashboard_config().get("epic")
    return epics if isinstance(epics, list) else []


def _clarify_files_overview(clar_dir: Path) -> tuple[list[dict], list[dict]]:
    """Every clarification result file (flat, excluding claude.md) with recognized
    findings, split into (unanswered, fully_answered), each sorted by name. Fully
    answered files also get an "applied" bool: whether their current content (i.e.
    current answers) matches what was last applied to the PRD/spec, per
    config.json's clarify_applied_hashes — so the dashboard knows whether an
    "Apply Answer(s)" action is actually needed or would be a no-op."""
    unanswered: list[dict] = []
    answered: list[dict] = []
    if not clar_dir.exists():
        return unanswered, answered
    applied_hashes = _load_clarify_applied_hashes()
    for p in sorted(clar_dir.glob("*.md")):
        if p.name.lower() == "claude.md":
            continue
        stats = _file_severity_stats(p)
        if stats is None:
            continue
        if stats["answered"] == stats["total"]:
            stats["applied"] = applied_hashes.get(stats["name"]) == stats["content_hash"]
            answered.append(stats)
        else:
            unanswered.append(stats)
    return unanswered, answered


# ---------------------------------------------------------------------------
# Clarification run (Start Clarification / Finalized Clarification buttons) —
# spawns `tempa clarify` / `tempa clarify --finalize` as a subprocess and lets the
# dashboard poll its console output for the collapsible log panel.
# ---------------------------------------------------------------------------

# Matches the self-overwriting `\r[HH:MM:SS] [...] [rows]` progress line tempa.py
# prints once a second while a Claude session is running (see _display_progress in
# tempa.py). Kept out of the appended `lines` history entirely (see `progress` below)
# — tempa.py can go minutes between any other console output, so if this were folded
# into `lines` in place, the dashboard's index-based polling would fetch it once and
# then never notice it kept changing, making a live run look frozen.
_PROGRESS_LINE_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\].*\[\d+ rows\](\s*\[[^\]]*\])*\s*$")


def _new_clarify_run_state() -> dict:
    return {
        "lock": threading.Lock(),
        "running": False,
        "mode": None,
        "lines": [],
        "progress": None,
        "returncode": None,
    }


_CLARIFY_RUN_ARGS = {"run": ["--noui"], "finalize": ["--finalize"], "apply": ["--apply"]}


def _start_clarify_run(server, mode: str) -> bool:
    """Start `tempa clarify` (mode "run"), `tempa clarify --finalize` (mode "finalize"),
    or `tempa clarify --apply` (mode "apply") as a background subprocess, appending its
    console output to server.clarify_run["lines"] as it streams in. Returns False without
    starting anything if a run is already in progress (defense in depth alongside the
    dashboard disabling the buttons client-side)."""
    run = server.clarify_run
    with run["lock"]:
        if run["running"]:
            return False
        run["running"] = True
        run["mode"] = mode
        run["lines"] = []
        run["progress"] = None
        run["returncode"] = None

    def worker() -> None:
        tempa_py = Path(__file__).resolve().parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "clarify", *_CLARIFY_RUN_ARGS[mode]]
        returncode = -1
        try:
            process = subprocess.Popen(
                cmd,
                # `tempa clarify --apply` asks (via input()) whether to run another
                # clarification round right away, but only if stdin is a tty — DEVNULL
                # guarantees it never is, so a dashboard-triggered apply can't block
                # forever waiting for a keypress no one can give it.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                with run["lock"]:
                    if _PROGRESS_LINE_RE.match(line):
                        run["progress"] = line
                    else:
                        run["lines"].append(line)
            process.wait()
            returncode = process.returncode
        except OSError as e:
            with run["lock"]:
                run["lines"].append(f"[error] Could not start clarify process: {e}")
        with run["lock"]:
            run["running"] = False
            run["progress"] = None
            run["returncode"] = returncode

    threading.Thread(target=worker, daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# Implementation run (Start / Stop Implementation) — same subprocess/log-polling
# shape as the clarify run above, but `tempa implement` is a long-running poll loop
# (runs until every epic is done, not one bounded session), so this also tracks the
# live Popen so a Stop button can kill it.
# ---------------------------------------------------------------------------

def _new_implement_run_state() -> dict:
    return {
        "lock": threading.Lock(),
        "running": False,
        "lines": [],
        "progress": None,
        "returncode": None,
        "process": None,
    }


def _start_implement_run(server) -> bool:
    """Start `tempa implement` as a background subprocess, same log-streaming shape
    as _start_clarify_run. Returns False without starting anything if a run is
    already in progress."""
    run = server.implement_run
    with run["lock"]:
        if run["running"]:
            return False
        run["running"] = True
        run["lines"] = []
        run["progress"] = None
        run["returncode"] = None
        run["process"] = None

    def worker() -> None:
        tempa_py = Path(__file__).resolve().parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "implement"]
        returncode = -1
        try:
            process = subprocess.Popen(
                cmd,
                # implement's plain run path never calls input() (confirmed: only the
                # destructive --clear/--clear-plan/--reset* flags do, none of which
                # this spawns) — DEVNULL is defense in depth, matching the clarify
                # runner, in case that ever changes.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            with run["lock"]:
                run["process"] = process
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                with run["lock"]:
                    if _PROGRESS_LINE_RE.match(line):
                        run["progress"] = line
                    else:
                        run["lines"].append(line)
            process.wait()
            returncode = process.returncode
        except OSError as e:
            with run["lock"]:
                run["lines"].append(f"[error] Could not start implement process: {e}")
        with run["lock"]:
            run["running"] = False
            run["progress"] = None
            run["process"] = None
            run["returncode"] = returncode

    threading.Thread(target=worker, daemon=True).start()
    return True


def _stop_implement_run(server) -> bool:
    """Kill the running `tempa implement` subprocess. Uses `taskkill /T /F` on
    Windows to kill its whole process tree — implement spawns the actual `claude`
    CLI call as a child of this same process, and plain Popen.terminate() only
    kills the immediate process, leaving that child running (and still burning
    Claude usage) in the background. Returns False if nothing is running."""
    run = server.implement_run
    with run["lock"]:
        process = run["process"]
    if process is None:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            process.terminate()
    except OSError:
        return False
    return True


def apply_answers_to_file(path: Path, payload: list[dict]) -> tuple[int, int]:
    """Write the given answers into `path` (one clarification result file) and return
    its updated (answered, total) counts."""
    text = path.read_text(encoding="utf-8")
    items, _ = parse_file(path, text, 0)
    items_by_key = {it.key: it for it in items}

    edits: list[tuple[int, int, str]] = []
    for entry in payload:
        item = items_by_key.get(entry.get("id"))
        if item is None:
            continue
        mode = entry.get("mode")
        if mode == "recommendation" and item.recommendation:
            new_text = item.recommendation
        else:
            new_text = (entry.get("answer") or "").strip()
        if item.has_markers:
            replacement = f"<!-- clarify:answer-start -->\n{new_text}\n<!-- clarify:answer-end -->"
        else:
            replacement = f"\n<!-- clarify:answer-start -->\n{new_text}\n<!-- clarify:answer-end -->\n"
        edits.append((item.answer_start, item.answer_end, replacement))

    for start, end, replacement in sorted(edits, key=lambda s: s[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    path.write_text(text, encoding="utf-8")
    return file_answer_status(path)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Injected on the server instance by run_dashboard:
    #   server.prd_dir    -> Path of the specs/PRD folder (Specification section)
    #   server.clar_dir   -> Path of the clarifications folder (Clarification section)
    #   server.page_html  -> str of the index page
    #   server.any_saved  -> bool, set True the first time a clarification answer is saved

    def log_message(self, fmt: str, *args) -> None:  # silence per-request logging
        pass

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _send_json(self, status: int, obj: dict) -> None:
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    # -- GET ----------------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("/", ""):
            self._send(200, "text/html; charset=utf-8",
                       self.server.page_html.encode("utf-8"))
        elif route == "/api/tree":
            unanswered, answered = _clarify_files_overview(self.server.clar_dir)
            findings = _live_clarification_findings(unanswered + answered)
            self._send_json(200, {
                "ok": True,
                "workspace": {"initialized": _workspace_initialized(), "root": _workspace_root(),
                               "canClose": _workspace_can_close()},
                "spec": {"tree": build_tree(self.server.prd_dir)},
                "clarify": {"unanswered": unanswered, "answered": answered,
                            "findings": findings,
                            "finalize": _clarify_finalize_status(findings)},
            })
        elif route == "/api/spec/file":
            self._handle_spec_file(parse_qs(parsed.query))
        elif route == "/api/clarify/file":
            self._handle_clarify_file(parse_qs(parsed.query))
        elif route == "/api/clarify/run":
            self._handle_clarify_run_status(parse_qs(parsed.query))
        elif route == "/api/implement/run":
            self._handle_implement_run_status(parse_qs(parsed.query))
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _handle_spec_file(self, query: dict) -> None:
        rel = (query.get("path", [""])[0])
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None or not target.is_file():
            self._send_json(404, {"ok": False, "error": "File not found."})
            return
        if not _is_text_file(target):
            self._send_json(200, {
                "ok": True, "path": rel, "markdown": False, "text": False,
                "content": "", "reason": "This file type is not viewable as text.",
            })
            return
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self._send_json(200, {
                "ok": True, "path": rel, "markdown": False, "text": False,
                "content": "", "reason": "This file is not valid UTF-8 text.",
            })
            return
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not read file: {e}"})
            return
        self._send_json(200, {
            "ok": True, "path": rel,
            "markdown": target.suffix.lower() in MARKDOWN_EXTENSIONS,
            "text": True, "content": content,
        })

    def _handle_clarify_file(self, query: dict) -> None:
        rel = (query.get("path", [""])[0])
        target = _resolve_within(self.server.clar_dir, rel)
        if target is None or not target.is_file():
            self._send_json(404, {"ok": False, "error": "File not found."})
            return
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not read file: {e}"})
            return
        items, blocks = parse_file(target, text, 0)
        if not items:
            self._send_json(200, {
                "ok": True, "path": rel, "name": target.name,
                "summary": "No recognized clarification items in this file.",
                "html": "<p>No recognized clarification items in this file.</p>",
                "answered": 0, "total": 0,
            })
            return
        counts = {"critical": 0, "major": 0, "minor": 0}
        for it in items:
            counts[it.severity] += 1
        answered = sum(1 for it in items if it.existing_answer)
        summary = (
            f"{len(items)} finding(s) — {counts['critical']} critical · "
            f"{counts['major']} major · {counts['minor']} minor"
        )
        self._send_json(200, {
            "ok": True, "path": rel, "name": target.name,
            "summary": summary, "html": _render_blocks_html(blocks),
            "answered": answered, "total": len(items),
        })

    def _handle_clarify_run_status(self, query: dict) -> None:
        try:
            since = int((query.get("since", ["0"])[0]))
        except ValueError:
            since = 0
        run = self.server.clarify_run
        with run["lock"]:
            lines = list(run["lines"][max(since, 0):])
            total = len(run["lines"])
            self._send_json(200, {
                "ok": True, "running": run["running"], "mode": run["mode"],
                "returncode": run["returncode"], "lines": lines, "next": total,
                "progress": run["progress"],
            })

    def _handle_implement_run_status(self, query: dict) -> None:
        try:
            since = int((query.get("since", ["0"])[0]))
        except ValueError:
            since = 0
        run = self.server.implement_run
        with run["lock"]:
            lines = list(run["lines"][max(since, 0):])
            total = len(run["lines"])
            self._send_json(200, {
                "ok": True, "running": run["running"],
                "returncode": run["returncode"], "lines": lines, "next": total,
                "progress": run["progress"],
                # Epics are read fresh from config.json on every poll (not cached) —
                # the Status tab shows the same data the "Log" tab's run is actively
                # writing into config.json, so it needs to reflect live progress too.
                "epics": _epic_sessions(),
            })

    # -- POST ---------------------------------------------------------------
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/spec/save":
            self._handle_spec_save()
        elif parsed.path == "/api/spec/upload":
            self._handle_spec_upload(parse_qs(parsed.query))
        elif parsed.path == "/api/spec/delete":
            self._handle_spec_delete()
        elif parsed.path == "/api/spec/rename":
            self._handle_spec_rename()
        elif parsed.path == "/api/clarify/save":
            self._handle_clarify_save()
        elif parsed.path == "/api/clarify/run":
            self._handle_clarify_run_start()
        elif parsed.path == "/api/implement/run":
            self._handle_implement_run_start()
        elif parsed.path == "/api/implement/stop":
            self._handle_implement_run_stop()
        elif parsed.path == "/api/clear":
            self._handle_clear_all()
        elif parsed.path == "/api/workspace/init":
            self._handle_workspace_init()
        elif parsed.path == "/api/workspace/open":
            self._handle_workspace_open()
        elif parsed.path == "/api/workspace/close":
            self._handle_workspace_close()
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _read_json_body(self) -> dict | list | None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _handle_spec_save(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        rel = payload.get("path", "")
        content = payload.get("content", "")
        if not isinstance(content, str):
            self._send_json(400, {"ok": False, "error": "Content must be text."})
            return
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None:
            self._send_json(400, {"ok": False, "error": "Invalid path."})
            return
        if not target.exists() or not target.is_file():
            self._send_json(404, {"ok": False, "error": "File no longer exists."})
            return
        if not _is_text_file(target):
            self._send_json(400, {"ok": False, "error": "This file type cannot be edited here."})
            return
        try:
            # Path.write_text()'s `newline` kwarg needs Python 3.10+; use open() directly
            # so this also works on 3.9 (otherwise Windows' text-mode translation would
            # silently re-insert \r\n and undo the normalization above).
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(content.replace("\r\n", "\n").replace("\r", "\n"))
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not save file: {e}"})
            return
        print(f"[saved] {rel}")
        self._send_json(200, {"ok": True, "path": rel})

    def _handle_spec_upload(self, query: dict) -> None:
        """Add a file to the Specification (PRD) folder — used by the "Add File" /
        "Add Folder" buttons. `path` is the destination relative to prd_dir (for a
        folder upload this includes the folder name and any subfolders); the request
        body is the raw file bytes. Overwrites an existing file at that path."""
        rel = (query.get("path", [""])[0])
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None:
            self._send_json(400, {"ok": False, "error": "Invalid path."})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        data = self.rfile.read(length) if length else b""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as f:
                f.write(data)
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not write file: {e}"})
            return
        print(f"[added] {rel}")
        self._send_json(200, {"ok": True, "path": rel})

    def _handle_spec_delete(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        rel = payload.get("path", "")
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None:
            self._send_json(400, {"ok": False, "error": "Invalid path."})
            return
        if not target.exists():
            self._send_json(404, {"ok": False, "error": "File or folder no longer exists."})
            return
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not delete: {e}"})
            return
        print(f"[deleted] {rel}")
        self._send_json(200, {"ok": True, "path": rel})

    def _handle_spec_rename(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        rel = payload.get("path", "")
        new_name = (payload.get("new_name") or "").strip()
        target = _resolve_within(self.server.prd_dir, rel)
        if target is None:
            self._send_json(400, {"ok": False, "error": "Invalid path."})
            return
        if not target.exists():
            self._send_json(404, {"ok": False, "error": "File or folder no longer exists."})
            return
        if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
            self._send_json(400, {"ok": False, "error": "Invalid new name."})
            return
        new_target = target.parent / new_name
        if new_target.exists():
            self._send_json(409, {"ok": False, "error": f'"{new_name}" already exists.'})
            return
        try:
            target.rename(new_target)
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not rename: {e}"})
            return
        new_rel = str(new_target.relative_to(self.server.prd_dir)).replace("\\", "/")
        print(f"[renamed] {rel} -> {new_rel}")
        self._send_json(200, {"ok": True, "path": new_rel})

    def _handle_clarify_save(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        rel = payload.get("path", "")
        items = payload.get("items", [])
        if not isinstance(items, list):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        target = _resolve_within(self.server.clar_dir, rel)
        if target is None or not target.exists() or not target.is_file():
            self._send_json(404, {"ok": False, "error": "File no longer exists."})
            return
        try:
            answered, total = apply_answers_to_file(target, items)
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not save file: {e}"})
            return
        self.server.any_saved = True
        print(f"[saved] {rel} ({answered}/{total} answered)")
        self._send_json(200, {"ok": True, "path": rel, "answered": answered, "total": total})

    def _handle_clarify_run_start(self) -> None:
        payload = self._read_json_body()
        if payload is None or not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        mode = payload.get("mode")
        if mode not in ("run", "finalize", "apply"):
            self._send_json(400, {"ok": False, "error": "Invalid mode."})
            return
        unanswered, answered = _clarify_files_overview(self.server.clar_dir)
        findings = _live_clarification_findings(unanswered + answered)
        if mode == "finalize" and not _clarify_finalize_status(findings)["ready"]:
            # Server-side gate, not just a disabled button client-side — mirrors the
            # implement gate below. `tempa clarify --finalize` itself has no awareness
            # of this precondition and would happily run regardless.
            self._send_json(409, {
                "ok": False,
                "error": "Cannot finalize yet — run Start Clarification once more and confirm "
                         "it shows zero critical findings first.",
            })
            return
        if not _start_clarify_run(self.server, mode):
            self._send_json(409, {"ok": False, "error": "A clarification run is already in progress."})
            return
        self._send_json(200, {"ok": True})

    def _handle_implement_run_start(self) -> None:
        # Server-side gate, not just a disabled button client-side — tempa.py's
        # `implement` itself has no awareness of clarification findings and will
        # happily start regardless, so this is the only thing actually enforcing it.
        unanswered, answered = _clarify_files_overview(self.server.clar_dir)
        findings = _live_clarification_findings(unanswered + answered)
        if findings["critical"] or findings["major"]:
            self._send_json(409, {
                "ok": False,
                "error": "Cannot start implementation while critical/major clarification findings remain.",
            })
            return
        if not _start_implement_run(self.server):
            self._send_json(409, {"ok": False, "error": "Implementation is already running."})
            return
        self._send_json(200, {"ok": True})

    def _handle_implement_run_stop(self) -> None:
        if not _stop_implement_run(self.server):
            self._send_json(409, {"ok": False, "error": "Implementation is not running."})
            return
        self._send_json(200, {"ok": True})

    def _handle_clear_all(self) -> None:
        if self.server.clarify_run["running"] or self.server.implement_run["running"]:
            self._send_json(409, {
                "ok": False,
                "error": "Cannot clear while a clarify or implementation run is in progress.",
            })
            return
        tempa_py = Path(__file__).resolve().parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "clear", "--yes"]
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            self._send_json(500, {"ok": False, "error": f"Could not run clear: {e}"})
            return
        output = result.stdout or ""
        if result.returncode != 0:
            self._send_json(500, {"ok": False, "error": output.strip() or f"Clear failed (exit code {result.returncode})."})
            return
        self._send_json(200, {"ok": True, "output": output})

    def _handle_workspace_init(self) -> None:
        """"Select Working Folder" on the Home page: open a native folder picker, then
        run `tempa.py init <folder>` (same as the CLI) to stamp workspace.root into
        config.json and scaffold the default working folders under it."""
        root = _pick_folder_dialog()
        if root is None:
            self._send_json(200, {"ok": False, "cancelled": True})
            return
        tempa_py = Path(__file__).resolve().parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "init", root]
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            self._send_json(500, {"ok": False, "error": f"Could not initialize: {e}"})
            return
        output = result.stdout or ""
        if result.returncode != 0:
            self._send_json(500, {"ok": False, "error": output.strip() or f"Init failed (exit code {result.returncode})."})
            return
        # Re-derive prd_dir/clar_dir now that workspace.root is set, so the dashboard
        # reflects the new location immediately instead of requiring a restart.
        self.server.prd_dir = _resolve_source_dir("prd", "prd")
        self.server.clar_dir = _resolve_source_dir("clarifications", "clarifications")
        print(f"[workspace] root set to {root}")
        self._send_json(200, {"ok": True, "root": root, "output": output})

    def _handle_workspace_open(self) -> None:
        """Open workspace.root in Windows Explorer — used by the path label on the
        Home page's working-folder panel. Also tries to bring the resulting window to
        the foreground: since this request is served by a background HTTP server
        process rather than the user's active foreground app, Explorer's window would
        otherwise open silently behind the browser (see _bring_window_to_front)."""
        root = _workspace_root()
        if not root or not Path(root).is_dir():
            self._send_json(404, {"ok": False, "error": "Working folder not found on disk."})
            return
        try:
            os.startfile(root)  # noqa: S606 - local-only dashboard, opens the user's own configured folder
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not open folder: {e}"})
            return
        folder_name = Path(root).name or root
        for _ in range(20):
            time.sleep(0.15)
            hwnd = _find_explorer_window(folder_name)
            if hwnd:
                _bring_window_to_front(hwnd)
                break
        self._send_json(200, {"ok": True})

    def _handle_workspace_close(self) -> None:
        """Clear workspace.root — the "✕" icon next to the working-folder path,
        shown only once _workspace_can_close() is true. Shells out to
        `tempa.py close-folder` (same subprocess pattern as init/clear) so the
        precondition check and config.json write stay in one place."""
        if not _workspace_can_close():
            self._send_json(409, {
                "ok": False,
                "error": "Run Clear All first — the working folder can only be closed "
                         "once the epic array is empty and last_auto_answer is 0.",
            })
            return
        tempa_py = Path(__file__).resolve().parent / "tempa.py"
        cmd = [sys.executable, str(tempa_py), "close-folder"]
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            self._send_json(500, {"ok": False, "error": f"Could not close working folder: {e}"})
            return
        output = result.stdout or ""
        if result.returncode != 0:
            self._send_json(500, {"ok": False, "error": output.strip() or f"Close failed (exit code {result.returncode})."})
            return
        self.server.prd_dir = _resolve_source_dir("prd", "prd")
        self.server.clar_dir = _resolve_source_dir("clarifications", "clarifications")
        print("[workspace] root cleared")
        self._send_json(200, {"ok": True})


def run_dashboard(prd_dir: Path, clar_dir: Path, initial_view: str = "home") -> bool:
    """Serve the dashboard on a random 127.0.0.1 port, open it in the default
    browser, and block until interrupted with Ctrl+C. `initial_view` is one of
    "home" | "specification" | "clarification" and controls which sidebar section
    is expanded/shown on first paint. Returns True iff at least one clarification
    answer was saved during the session."""
    prd_dir = prd_dir.resolve() if prd_dir.exists() else prd_dir
    clar_dir = clar_dir.resolve() if clar_dir.exists() else clar_dir

    spec_tree = build_tree(prd_dir)
    clarify_unanswered, clarify_answered = _clarify_files_overview(clar_dir)
    page_html = _render_page(prd_dir, spec_tree, clarify_unanswered, clarify_answered, initial_view)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _DashboardHandler)
    server.prd_dir = prd_dir
    server.clar_dir = clar_dir
    server.page_html = page_html
    server.any_saved = False
    server.clarify_run = _new_clarify_run_state()
    server.implement_run = _new_implement_run_state()

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"Dashboard: {url}")
    print("Press Ctrl+C to stop.")
    if not webbrowser.open(url):
        print("Could not open a browser automatically -- open the URL above manually.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()

    return server.any_saved


def _render_page(prd_dir: Path, spec_tree: dict, clarify_unanswered: list[dict],
                  clarify_answered: list[dict], initial_view: str) -> str:
    tree_json = json.dumps(spec_tree, ensure_ascii=False)
    unanswered_json = json.dumps(clarify_unanswered, ensure_ascii=False)
    answered_json = json.dumps(clarify_answered, ensure_ascii=False)
    prd_name = json.dumps(prd_dir.name, ensure_ascii=False)
    view_json = json.dumps(initial_view if initial_view in ("home", "specification", "clarification") else "home")
    workspace_initialized_json = json.dumps(_workspace_initialized())
    workspace_root_json = json.dumps(_workspace_root())
    workspace_can_close_json = json.dumps(_workspace_can_close())
    live_findings = _live_clarification_findings(clarify_unanswered + clarify_answered)
    clarify_findings_json = json.dumps(live_findings, ensure_ascii=False)
    clarify_finalize_json = json.dumps(_clarify_finalize_status(live_findings), ensure_ascii=False)
    return (
        _PAGE_TEMPLATE
        .replace("/*__SPEC_TREE__*/null", tree_json)
        .replace("/*__CLARIFY_UNANSWERED__*/null", unanswered_json)
        .replace("/*__CLARIFY_ANSWERED__*/null", answered_json)
        .replace("/*__PRD_NAME__*/null", prd_name)
        .replace("/*__INITIAL_VIEW__*/null", view_json)
        .replace("/*__WORKSPACE_INITIALIZED__*/null", workspace_initialized_json)
        .replace("/*__WORKSPACE_ROOT__*/null", workspace_root_json)
        .replace("/*__WORKSPACE_CAN_CLOSE__*/null", workspace_can_close_json)
        .replace("/*__CLARIFY_FINDINGS__*/null", clarify_findings_json)
        .replace("/*__CLARIFY_FINALIZE__*/null", clarify_finalize_json)
    )


# The page is a single self-contained document: no external CSS/JS/fonts, and a
# small hand-written markdown renderer in JS (below) so it works fully offline.
_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tempa Dashboard</title>
<style>
  :root {
    --bg: #f3f3f3; --panel: #ffffff; --border: #e2e2e6; --border-strong: #cfcfd6;
    --text: #1f2328; --muted: #6b7280; --accent: #2563eb; --accent-soft: #e8f0fe;
    --hover: #eef1f5; --sel: #dbe7ff; --sel-text: #16418f;
    --code-bg: #f5f6f8; --danger: #b91c1c; --ok: #15803d;
    --critical: #dc2626; --major: #d97706; --minor: #2563eb;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1e1e22; --panel: #26262b; --border: #34343b; --border-strong: #43434c;
      --text: #e6e6ea; --muted: #9a9aa5; --accent: #6ea8fe; --accent-soft: #2b3550;
      --hover: #2f2f36; --sel: #33436b; --sel-text: #cfe0ff;
      --code-bg: #2c2c33; --danger: #f87171; --ok: #4ade80;
    }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    font: 14px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--text); background: var(--bg); overflow: hidden;
  }
  .app { display: flex; height: 100vh; }

  /* ---- sidebar (explorer) ---- */
  .sidebar { width: 300px; min-width: 200px; max-width: 70vw; display: flex;
    flex-direction: column; background: var(--panel); border-right: 1px solid var(--border); }
  .sidebar-head { display: flex; align-items: center; gap: 8px; padding: 10px 12px;
    border-bottom: 1px solid var(--border); font-weight: 600; }
  .sidebar-head .title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sidebar-head button { font-size: 12px; }
  .tree { flex: 1; overflow: auto; padding: 6px 4px 16px; }

  .row { display: flex; align-items: center; gap: 4px; padding: 5px 6px; border-radius: 5px;
    cursor: pointer; white-space: nowrap; user-select: none; }
  .row:hover { background: var(--hover); }
  .row.selected { background: var(--sel); color: var(--sel-text); }
  .row.top { font-weight: 600; }
  .row.disabled { color: var(--muted); cursor: default; opacity: 0.55; }
  .row.disabled:hover { background: none; }
  .row .twist { width: 14px; text-align: center; color: var(--muted); flex: none;
    font-size: 10px; transition: transform .12s ease; }
  .row .twist.hidden { visibility: hidden; }
  .row .icon { flex: none; width: 18px; text-align: center; }
  .row .label { overflow: hidden; text-overflow: ellipsis; flex: 1; }
  .row .badge-count { flex: none; font-size: 11px; font-weight: 700; color: #fff;
    background: var(--major); border-radius: 999px; padding: 1px 7px; }
  .row .file-status { flex: none; font-size: 11px; color: var(--muted); }
  .row .row-menu-btn { flex: none; border: none; background: transparent; color: var(--muted);
    padding: 1px 6px; border-radius: 5px; font-size: 15px; line-height: 1; }
  .row .row-menu-btn:hover { background: var(--border-strong); color: var(--text); }
  .children { display: none; }
  .node.open > .children { display: block; }
  .node.open > .row > .twist { transform: rotate(90deg); }
  .empty-note { padding: 6px 10px 6px 30px; color: var(--muted); font-size: 12.5px; }

  /* ---- row context menu (Specification file/folder rename/delete) ---- */
  .row-context-menu { position: fixed; z-index: 100; background: var(--panel);
    border: 1px solid var(--border-strong); border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.25);
    padding: 4px; min-width: 130px; }
  .row-context-menu button { display: block; width: 100%; text-align: left; border: none;
    background: none; color: var(--text); padding: 7px 10px; border-radius: 6px; font-size: 13px; }
  .row-context-menu button:hover { background: var(--hover); }
  .row-context-menu button.danger { color: var(--danger); }

  /* ---- splitter ---- */
  .splitter { width: 6px; cursor: col-resize; background: transparent; flex: none; }
  .splitter:hover, .splitter.dragging { background: var(--accent-soft); }

  /* ---- main pane ---- */
  .main { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--bg); }
  .toolbar { display: flex; align-items: center; gap: 10px; padding: 8px 12px;
    background: var(--panel); border-bottom: 1px solid var(--border); }
  .filepath { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; color: var(--muted); font-size: 13px; }
  .filepath .dirty { color: var(--danger); font-weight: 700; margin-left: 4px; }
  .seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: 7px; overflow: hidden; }
  .seg button { border: none; border-radius: 0; background: var(--panel); color: var(--text);
    padding: 5px 14px; }
  .seg button + button { border-left: 1px solid var(--border-strong); }
  .seg button.active { background: var(--accent); color: #fff; }
  button { font: inherit; cursor: pointer; background: var(--panel); color: var(--text);
    border: 1px solid var(--border-strong); border-radius: 7px; padding: 5px 12px; }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:disabled { opacity: .5; cursor: default; }
  button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }

  .content { flex: 1; position: relative; overflow: hidden; }
  .hidden { display: none !important; }
  .pane { position: absolute; inset: 0; overflow: auto; }
  .viewer { padding: 24px 32px; background: var(--bg); }
  .editor { width: 100%; height: 100%; border: 0; resize: none; padding: 18px 22px;
    font: 13px/1.6 "Cascadia Code", "Consolas", ui-monospace, monospace;
    color: var(--text); background: var(--panel); outline: none; tab-size: 4; }
  .placeholder-pane { display: flex; align-items: center;
    justify-content: center; color: var(--muted); text-align: center; padding: 40px; }

  /* ---- home page (step-by-step workflow) ---- */
  .home-pane { padding: 20px clamp(16px, 3vw, 36px) 60px; }
  .home-brand { font-size: 40px; font-weight: 800; letter-spacing: 2px;
    color: var(--border-strong); text-align: center; margin-bottom: 28px; }
  .home-init-prompt { display: flex; flex-direction: column; align-items: center; gap: 10px;
    padding: 60px 20px; color: var(--muted); text-align: center; }
  .home-init-text { max-width: 480px; line-height: 1.6; }
  .home-init-text code { background: var(--code-bg); padding: 1px 6px; border-radius: 4px; }
  .home-workspace-panel { display: flex; align-items: center; gap: 10px; max-width: 720px;
    margin: 0 auto 18px; padding: 12px 18px; border: 1px solid var(--border-strong);
    border-radius: 10px; background: var(--panel); }
  .home-workspace-icon { font-size: 1.2rem; flex: none; }
  .home-workspace-label { color: var(--muted); font-weight: 600; font-size: 0.85rem; flex: none; }
  .home-workspace-path { color: var(--accent); cursor: pointer; font-family: ui-monospace, Consolas, monospace;
    font-size: 0.85rem; overflow-wrap: anywhere; flex: 1 1 auto; }
  .home-workspace-path:hover { text-decoration: underline; }
  .home-workspace-close { flex: none; border: none; background: transparent; color: var(--muted);
    font-size: 1rem; line-height: 1; cursor: pointer; padding: 3px 8px; border-radius: 6px; }
  .home-workspace-close:hover { background: var(--danger); color: #fff; }
  .home-steps { display: flex; flex-direction: column; gap: 18px; max-width: 720px; margin: 0 auto; }
  .home-step { border: 1px solid var(--border-strong); border-radius: 10px; padding: 18px 22px;
    background: var(--panel); }
  .home-step.locked { opacity: 0.55; }
  .home-step-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .home-step-num { display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; border-radius: 50%; background: var(--accent); color: #fff;
    font-weight: 700; font-size: 0.85rem; flex: none; }
  .home-step-title { font-weight: 700; font-size: 1.02rem; }
  .home-step-desc { color: var(--muted); font-size: 0.9rem; margin: 0 0 12px; }
  .home-step-actions { display: flex; gap: 12px; flex-wrap: wrap; }
  .home-step-status { margin-top: 10px; font-size: 0.88rem; color: var(--text); }
  .home-danger-zone { max-width: 720px; margin: 32px auto 0; padding-top: 20px;
    border-top: 1px solid var(--border); text-align: center; }
  .home-clear-btn { background: var(--panel); color: var(--danger); border-color: var(--danger);
    font-weight: 600; padding: 8px 18px; }
  .home-clear-btn:hover:not(:disabled) { background: var(--danger); color: #fff; }
  .home-clear-btn:disabled { opacity: 0.5; cursor: default; }
  .home-danger-zone .home-step-desc { margin-top: 8px; }

  /* ---- implementation page (start/stop + status/log tabs) ---- */
  .impl-pane { padding: 20px clamp(16px, 3vw, 36px) 60px; display: flex; flex-direction: column;
    min-height: 100%; box-sizing: border-box; }
  .impl-header { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
  .impl-header-status { color: var(--muted); font-size: 0.85rem; }
  .impl-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border-strong); margin-bottom: 18px; }
  .impl-tab { background: transparent; border: none; border-radius: 0; padding: 8px 16px;
    font-weight: 600; color: var(--muted); border-bottom: 2px solid transparent; margin-bottom: -1px; }
  .impl-tab:hover:not(:disabled) { border-color: transparent; color: var(--text); }
  .impl-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .impl-tab-panel { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .impl-tab-panel.hidden { display: none; }
  #implLogBody { flex: 1; min-height: 0; max-height: none; }
  .impl-epic-card { border: 1px solid var(--border-strong); border-radius: 8px; padding: 12px 16px;
    margin-bottom: 12px; max-width: 860px; background: var(--panel); }
  .impl-epic-header { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 0.9rem; }
  .impl-epic-name { font-weight: 700; }
  .impl-epic-status { color: var(--muted); text-transform: capitalize; }
  .impl-epic-progress, .impl-epic-lastrun { color: var(--muted); font-size: 0.82rem; }
  .impl-qa-ok { color: var(--ok); font-weight: 600; font-size: 0.8rem; }
  .impl-qa-pending { color: var(--major); font-weight: 600; font-size: 0.8rem; }
  .impl-feature-list { margin-top: 8px; padding-left: 4px; }
  .impl-feature-row { display: flex; gap: 8px; font-size: 0.85rem; padding: 2px 0; color: var(--text); }

  /* ---- rendered markdown (spec viewer) ---- */
  .markdown-body { max-width: 860px; }
  .markdown-body h1, .markdown-body h2, .markdown-body h3,
  .markdown-body h4, .markdown-body h5, .markdown-body h6 { line-height: 1.25; margin: 1.4em 0 .5em; }
  .markdown-body h1 { font-size: 1.8em; border-bottom: 1px solid var(--border); padding-bottom: .25em; }
  .markdown-body h2 { font-size: 1.45em; border-bottom: 1px solid var(--border); padding-bottom: .2em; }
  .markdown-body h3 { font-size: 1.2em; }
  .markdown-body p { margin: .7em 0; }
  .markdown-body ul, .markdown-body ol { margin: .5em 0; padding-left: 1.7em; }
  .markdown-body li { margin: .2em 0; }
  .markdown-body code { background: var(--code-bg); border-radius: 4px; padding: .12em .35em;
    font-family: "Cascadia Code", "Consolas", ui-monospace, monospace; font-size: .9em; }
  .markdown-body pre { background: var(--code-bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 14px 16px; overflow: auto; }
  .markdown-body pre code { background: none; padding: 0; }
  .markdown-body blockquote { margin: .8em 0; padding: .2em 1em; color: var(--muted);
    border-left: 4px solid var(--border-strong); }
  .markdown-body table { border-collapse: collapse; margin: 1em 0; display: block; overflow: auto; }
  .markdown-body th, .markdown-body td { border: 1px solid var(--border-strong); padding: 6px 12px; }
  .markdown-body th { background: var(--hover); }
  .markdown-body img { max-width: 100%; }
  .markdown-body hr { border: none; border-top: 1px solid var(--border); margin: 1.6em 0; }
  .markdown-body a { color: var(--accent); }

  /* ---- clarification pane (findings cards) ---- */
  .clarify-pane { padding: 20px clamp(16px, 3vw, 36px) 60px; }
  .clarify-summary { color: var(--muted); font-size: 0.9rem; margin-bottom: 14px; }
  .doc-text { color: var(--text); margin: 8px 0 20px; max-width: 860px; }
  .doc-text p { margin: 0.6em 0; }
  .item {
    background: var(--panel); border: 1px solid var(--border); border-left: 4px solid var(--muted);
    border-radius: 8px; padding: 16px 18px; margin: 16px 0; max-width: 860px;
  }
  .item.sev-critical { border-left-color: var(--critical); }
  .item.sev-major { border-left-color: var(--major); }
  .item.sev-minor { border-left-color: var(--minor); }
  .item header { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  .item header h3 { margin: 0; font-size: 1.05rem; }
  .badge {
    font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
    padding: 2px 8px; border-radius: 999px; color: #fff; white-space: nowrap;
  }
  .badge.critical { background: var(--critical); }
  .badge.major { background: var(--major); }
  .badge.minor { background: var(--minor); }
  .field { margin: 10px 0; }
  .field h4 { margin: 0 0 2px; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
  .field .md p, .field .md ul { margin: 0.3em 0; }
  .field.recommendation { background: var(--accent-soft); border-radius: 6px; padding: 8px 10px; }
  .answer-block { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
  .selector { display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 8px; font-size: 0.92rem; }
  .selector label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
  .item textarea {
    width: 100%; min-height: 6.5em; font: inherit; padding: 8px 10px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--bg); color: var(--text); resize: vertical;
  }
  .item textarea:disabled { opacity: 0.55; resize: none; }

  /* ---- clarification overview (file lists per group) ---- */
  .clarify-overview-pane { padding: 20px clamp(16px, 3vw, 36px) 60px; }
  .clarify-overview-pane h3 { font-size: 0.95rem; margin: 0 0 10px; }
  .clarify-overview-pane .group + .group { margin-top: 28px; }
  .clarify-overview-pane table { width: 100%; max-width: 860px; border-collapse: collapse; }
  .clarify-overview-pane th, .clarify-overview-pane td {
    text-align: left; padding: 7px 12px; border-bottom: 1px solid var(--border); font-size: 0.9rem;
  }
  .clarify-overview-pane th { color: var(--muted); font-weight: 600; font-size: 0.75rem;
    text-transform: uppercase; letter-spacing: 0.03em; }
  .clarify-overview-pane tbody tr { cursor: pointer; }
  .clarify-overview-pane tbody tr:hover td { background: var(--hover); }
  .clarify-overview-pane .count-ok { color: var(--ok); }
  .clarify-overview-pane .count-pending { color: var(--major); font-weight: 600; }
  .clarify-overview-pane .status-complete { color: var(--ok); font-weight: 600; }
  .clarify-overview-pane .status-pending { color: var(--major); font-weight: 600; }

  /* ---- clarification run (Start / Finalized buttons + log panel) ---- */
  .clarify-run-actions { display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
  .clarify-run-btn { display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px;
    font-weight: 600; background: var(--accent); color: #fff; border-color: var(--accent); }
  .clarify-run-btn:hover:not(:disabled) { opacity: 0.9; border-color: var(--accent); }
  .clarify-run-btn:disabled { opacity: 0.5; cursor: default; }
  .clarify-run-btn.secondary { background: var(--panel); color: var(--text); border-color: var(--border-strong); }
  .clarify-run-btn.secondary:hover:not(:disabled) { opacity: 1; border-color: var(--accent); }
  .clarify-apply-btn { padding: 3px 10px; font-size: 0.78rem; font-weight: 600;
    background: var(--accent); color: #fff; border-color: var(--accent); }
  .clarify-apply-btn:hover:not(:disabled) { opacity: 0.9; }
  .clarify-apply-btn:disabled { opacity: 0.5; cursor: default; }
  .clarify-applied-badge { color: var(--ok); font-weight: 600; font-size: 0.82rem; white-space: nowrap; }
  .clarify-log { max-width: 860px; margin-bottom: 24px; border: 1px solid var(--border-strong);
    border-radius: 8px; background: var(--panel); }
  .clarify-log summary { cursor: pointer; padding: 9px 14px; font-weight: 600; font-size: 0.88rem;
    list-style: none; display: flex; align-items: center; gap: 8px; user-select: none; }
  .clarify-log summary::-webkit-details-marker { display: none; }
  .clarify-log summary::before { content: "▶"; font-size: 0.65rem; color: var(--muted);
    display: inline-block; transition: transform .15s; }
  .clarify-log[open] summary::before { transform: rotate(90deg); }
  .clarify-log-status { font-weight: 400; color: var(--muted); font-size: 0.8rem; }
  .clarify-log-body { max-height: 320px; overflow-y: auto; padding: 2px 14px 12px;
    border-top: 1px solid var(--border);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; }
  .clarify-log-line { padding: 4px 0; display: flex; gap: 8px; align-items: baseline;
    border-bottom: 1px dashed var(--border); }
  .clarify-log-line:last-child { border-bottom: none; }
  .clarify-log-time { color: var(--muted); flex: none; font-size: 0.75rem; }
  .clarify-log-icon { flex: none; }
  .clarify-log-msg { white-space: pre-wrap; word-break: break-word; }
  .clarify-log-line.banner .clarify-log-msg { font-weight: 700; }
  .clarify-log-line.ok .clarify-log-msg { color: var(--ok); }
  .clarify-log-line.warn .clarify-log-msg { color: var(--major); }
  .clarify-log-line.err .clarify-log-msg { color: var(--danger); }
  .clarify-log-line.progress .clarify-log-msg { color: var(--muted); font-style: italic; }
  .clarify-log-empty { color: var(--muted); font-size: 0.85rem; padding: 8px 0; }

  /* ---- readiness gate panels (Finalize Clarification / Start Implementation) ---- */
  .gate-panel { max-width: 860px; margin-bottom: 18px; border: 1px solid var(--border-strong);
    border-radius: 8px; background: var(--panel); padding: 14px 16px; }
  .gate-panel h3 { margin: 0 0 10px; font-size: 0.88rem; }
  .gate-checklist { list-style: none; margin: 0; padding: 0; display: flex;
    flex-direction: column; gap: 6px; }
  .gate-checklist + .clarify-run-btn { margin-top: 14px; }
  .gate-item { display: flex; align-items: baseline; gap: 8px; font-size: 0.85rem; }
  .gate-item .icon { flex: none; }
  .gate-item.ok { color: var(--ok); }
  .gate-item.pending { color: var(--muted); }

  .ready-banner { max-width: 860px; margin-bottom: 18px; border: 1px solid var(--ok);
    border-radius: 8px; background: var(--accent-soft); padding: 14px 16px;
    display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  .ready-banner-text { color: var(--text); font-size: 0.88rem; }
  .ready-banner-text strong { color: var(--ok); }

  /* ---- specification overview (file count + add file/folder) ---- */
  .spec-overview-pane { display: flex; flex-direction: column; align-items: center;
    justify-content: center; text-align: center; gap: 10px; padding: 40px; }
  .spec-overview-summary { font-size: 1.1rem; font-weight: 600; color: var(--text); }
  .spec-overview-hint { color: var(--muted); font-size: 0.95rem; }
  .spec-overview-actions { display: flex; gap: 22px; margin-top: 18px; flex-wrap: wrap;
    justify-content: center; }
  .big-action { display: flex; flex-direction: column; align-items: center; gap: 8px;
    padding: 22px 30px; min-width: 140px; border: 1px solid var(--border-strong);
    border-radius: 12px; background: var(--panel); color: var(--text); }
  .big-action:hover:not(:disabled) { border-color: var(--accent); background: var(--accent-soft); }
  .big-action-icon { font-size: 38px; line-height: 1; }
  .big-action-label { font-size: 0.9rem; font-weight: 600; }

  /* ---- toast ---- */
  .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%) translateY(20px);
    background: #1f2937; color: #fff; padding: 9px 18px; border-radius: 8px; font-size: 13px;
    opacity: 0; transition: opacity .2s, transform .2s; pointer-events: none; z-index: 50; }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  .toast.err { background: var(--danger); }

  /* ---- modal (confirm / prompt) ---- */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45);
    display: flex; align-items: center; justify-content: center; z-index: 200; }
  .modal-box { width: 420px; max-width: calc(100vw - 40px); background: var(--panel);
    border: 1px solid var(--border-strong); border-radius: 12px; padding: 22px 24px;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25); }
  .modal-title { font-size: 1.05rem; font-weight: 700; margin: 0 0 12px; }
  .modal-message { color: var(--text); font-size: 0.92rem; line-height: 1.6; }
  .modal-input { width: 100%; margin-top: 14px; padding: 8px 10px; font-size: 0.92rem;
    border: 1px solid var(--border-strong); border-radius: 6px; background: var(--bg);
    color: var(--text); }
  .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
  .modal-actions button { padding: 7px 16px; font-size: 0.9rem; border-radius: 6px;
    border: 1px solid var(--border-strong); background: var(--panel); color: var(--text); }
  .modal-actions button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .modal-actions button.primary.danger { background: var(--danger); border-color: var(--danger); }
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-head">
      <span class="icon">🛠️</span>
      <span class="title">Tempa Dashboard</span>
      <button id="refreshBtn" title="Rescan the folders">Refresh</button>
    </div>
    <div class="tree" id="tree"></div>
  </aside>
  <div class="splitter" id="splitter"></div>
  <main class="main">
    <div class="toolbar hidden" id="toolbar">
      <span class="filepath" id="filepath">Home</span>
      <div class="seg hidden" id="specSeg">
        <button id="viewBtn" class="active">View</button>
        <button id="editBtn">Edit</button>
      </div>
      <button id="followAllBtn" class="hidden">Follow all recommendations</button>
      <button id="saveBtn" class="primary hidden" disabled>Save</button>
    </div>
    <div class="content">
      <div id="homePane" class="pane home-pane">
        <div class="home-brand">TEMPA</div>
        <div id="homeNotInit" class="home-init-prompt hidden">
          <div class="home-init-text">
            No application repository folder has been set yet.
          </div>
          <button type="button" class="big-action" id="homeSelectFolderBtn">
            <span class="big-action-icon">📂</span>
            <span class="big-action-label">Select Working Folder</span>
          </button>
        </div>
        <div id="homeSteps" class="home-steps hidden">
          <div class="home-workspace-panel" id="homeWorkspacePanel">
            <span class="home-workspace-icon">📁</span>
            <span class="home-workspace-label">Working Folder:</span>
            <span class="home-workspace-path" id="homeWorkspacePath" title="Open in Explorer"></span>
            <button type="button" class="home-workspace-close hidden" id="homeWorkspaceCloseBtn" title="Close working folder">✕</button>
          </div>
          <div class="home-step" id="homeStep1">
            <div class="home-step-header">
              <span class="home-step-num">1</span>
              <span class="home-step-title">Upload Specification</span>
            </div>
            <p class="home-step-desc">Upload specification documents (PRD) as the basis for clarification and implementation.</p>
            <div class="home-step-actions">
              <button type="button" class="big-action" id="homeAddFileBtn">
                <span class="big-action-icon">📄</span>
                <span class="big-action-label">Add File</span>
              </button>
              <button type="button" class="big-action" id="homeAddFolderBtn">
                <span class="big-action-icon">📁</span>
                <span class="big-action-label">Add Folder</span>
              </button>
            </div>
            <div class="home-step-status" id="homeStep1Status"></div>
          </div>
          <div class="home-step" id="homeStep2">
            <div class="home-step-header">
              <span class="home-step-num">2</span>
              <span class="home-step-title">Clarification</span>
            </div>
            <p class="home-step-desc">Run clarification to find and resolve ambiguities/conflicts in the specification.</p>
            <div class="home-step-actions">
              <button type="button" class="clarify-run-btn" id="homeStartClarifyBtn">
                <span>▶️</span><span>Start Clarification</span>
              </button>
              <button type="button" class="clarify-run-btn secondary" id="homeFinalizeClarifyBtn">
                <span>🏁</span><span>Finalized Clarification</span>
              </button>
            </div>
            <div class="home-step-status" id="homeStep2Status"></div>
          </div>
          <div class="home-step" id="homeStep3">
            <div class="home-step-header">
              <span class="home-step-num">3</span>
              <span class="home-step-title">Start Implementation</span>
            </div>
            <p class="home-step-desc">Start the automated implementation process based on the clarification results.</p>
            <div class="home-step-actions">
              <button type="button" class="clarify-run-btn" id="homeStartImplementBtn">
                <span>🚀</span><span>Start Implementation</span>
              </button>
            </div>
            <div class="home-step-status" id="homeStep3Status"></div>
          </div>
          <div class="home-danger-zone">
            <button type="button" class="home-clear-btn" id="homeClearAllBtn">
              <span>🗑️</span><span>Clear All</span>
            </button>
            <p class="home-step-desc">Delete all plan, QA, log, and clarification results (specification files will not be deleted). This action cannot be undone.</p>
          </div>
        </div>
      </div>
      <div id="specPane" class="pane hidden">
        <div id="specViewer" class="viewer markdown-body"></div>
        <textarea id="specEditor" class="editor hidden" spellcheck="false"></textarea>
      </div>
      <div id="specOverviewPane" class="pane spec-overview-pane hidden">
        <div class="spec-overview-summary" id="specFileCount"></div>
        <div class="spec-overview-hint">Pick a file from Specification on the left to view it.</div>
        <div class="spec-overview-actions">
          <button type="button" class="big-action" id="addFileBtn">
            <span class="big-action-icon">📄</span>
            <span class="big-action-label">Add File</span>
          </button>
          <button type="button" class="big-action" id="addFolderBtn">
            <span class="big-action-icon">📁</span>
            <span class="big-action-label">Add Folder</span>
          </button>
        </div>
        <input type="file" id="addFileInput" class="hidden" multiple>
        <input type="file" id="addFolderInput" class="hidden" multiple webkitdirectory directory>
      </div>
      <div id="clarifyPane" class="pane clarify-pane hidden">
        <div class="clarify-summary" id="clarifySummary"></div>
        <div id="clarifyBody"></div>
      </div>
      <div id="clarifyOverviewPane" class="pane clarify-overview-pane hidden">
        <div class="clarify-run-actions">
          <button type="button" class="clarify-run-btn" id="startClarifyBtn">
            <span>▶️</span><span>Start Clarification</span>
          </button>
          <button type="button" class="clarify-run-btn secondary" id="applyAnswersBtn" disabled>
            <span>📤</span><span>Apply Answers</span>
          </button>
        </div>
        <div class="gate-panel" id="finalizeGate">
          <h3>Finalize readiness</h3>
          <ul class="gate-checklist" id="finalizeGateList"></ul>
          <button type="button" class="clarify-run-btn" id="finalizeClarifyBtn" disabled>
            <span>🏁</span><span>Finalized Clarification</span>
          </button>
        </div>
        <div class="ready-banner hidden" id="implementReadyBanner">
          <div class="ready-banner-text">
            <strong>✅ Ready for implementation.</strong> No critical or major findings remain —
            minor findings will be resolved during implementation.
          </div>
          <button type="button" class="clarify-run-btn" id="clarifyStartImplementBtn">
            <span>🚀</span><span>Start Implementation</span>
          </button>
        </div>
        <details class="clarify-log hidden" id="clarifyLogPanel">
          <summary>Clarification log <span class="clarify-log-status" id="clarifyLogStatus"></span></summary>
          <div class="clarify-log-body" id="clarifyLogBody"></div>
        </details>
        <div class="group">
          <h3>Unanswered</h3>
          <table>
            <thead><tr><th>File</th><th>Critical</th><th>Major</th><th>Minor</th><th>Status</th></tr></thead>
            <tbody id="clarifyUnansweredTbody"></tbody>
          </table>
        </div>
        <div class="group">
          <h3>Fully answered</h3>
          <table>
            <thead><tr><th>File</th><th>Critical</th><th>Major</th><th>Minor</th><th>Status</th><th>Applied</th></tr></thead>
            <tbody id="clarifyAnsweredTbody"></tbody>
          </table>
        </div>
      </div>
      <div id="implPane" class="pane impl-pane hidden">
        <div class="impl-header">
          <button type="button" class="clarify-run-btn" id="startImplementBtn">
            <span>🚀</span><span>Start Implementation</span>
          </button>
          <button type="button" class="clarify-run-btn secondary hidden" id="stopImplementBtn">
            <span>⏹️</span><span>Stop Implementation</span>
          </button>
          <span class="impl-header-status" id="implHeaderStatus"></span>
        </div>
        <div class="gate-panel" id="implGate">
          <h3>Implementation readiness</h3>
          <ul class="gate-checklist" id="implGateList"></ul>
        </div>
        <div class="impl-tabs">
          <button type="button" class="impl-tab active" id="implTabStatusBtn">Status</button>
          <button type="button" class="impl-tab" id="implTabLogBtn">Log</button>
        </div>
        <div class="impl-tab-panel" id="implStatusPanel">
          <div id="implStatusBody"></div>
        </div>
        <div class="impl-tab-panel hidden" id="implLogPanel">
          <div class="clarify-log-body" id="implLogBody"></div>
        </div>
      </div>
    </div>
  </main>
</div>
<div class="toast" id="toast"></div>
<div class="row-context-menu hidden" id="rowContextMenu">
  <button type="button" id="rowMenuRename">Rename</button>
  <button type="button" id="rowMenuDelete" class="danger">Delete</button>
</div>
<div class="modal-overlay hidden" id="modalOverlay">
  <div class="modal-box" role="dialog" aria-modal="true">
    <div class="modal-title" id="modalTitle"></div>
    <div class="modal-message" id="modalMessage"></div>
    <input type="text" class="modal-input hidden" id="modalInput">
    <div class="modal-actions">
      <button type="button" id="modalCancelBtn">Cancel</button>
      <button type="button" class="primary" id="modalOkBtn">OK</button>
    </div>
  </div>
</div>

<script>
"use strict";
const INITIAL_SPEC_TREE = /*__SPEC_TREE__*/null;
const INITIAL_CLARIFY_UNANSWERED = /*__CLARIFY_UNANSWERED__*/null;
const INITIAL_CLARIFY_ANSWERED = /*__CLARIFY_ANSWERED__*/null;
const PRD_NAME = /*__PRD_NAME__*/null;
const INITIAL_VIEW = /*__INITIAL_VIEW__*/null;
const INITIAL_WORKSPACE_INITIALIZED = /*__WORKSPACE_INITIALIZED__*/null;
const INITIAL_WORKSPACE_ROOT = /*__WORKSPACE_ROOT__*/null;
const INITIAL_WORKSPACE_CAN_CLOSE = /*__WORKSPACE_CAN_CLOSE__*/null;
const INITIAL_CLARIFY_FINDINGS = /*__CLARIFY_FINDINGS__*/null;
const INITIAL_CLARIFY_FINALIZE = /*__CLARIFY_FINALIZE__*/null;

// ---------------------------------------------------------------------------
// Minimal, dependency-free Markdown renderer for the Specification pane (offline-safe).
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inlineMd(src) {
  const codes = [];
  src = src.replace(/`([^`]+?)`/g, (m, c) => {
    codes.push("<code>" + escapeHtml(c) + "</code>");
    return "" + (codes.length - 1) + "";
  });
  src = escapeHtml(src);
  src = src.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)/g,
    (m, a, u, t) => `<img alt="${a}" src="${u}">`);
  src = src.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)/g,
    (m, txt, u) => `<a href="${u}" target="_blank" rel="noopener">${txt}</a>`);
  src = src.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
  src = src.replace(/__([^_]+?)__/g, "<strong>$1</strong>");
  src = src.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");
  src = src.replace(/(^|[^\w])_([^_\n]+?)_(?![\w])/g, "$1<em>$2</em>");
  src = src.replace(/~~([^~]+?)~~/g, "<del>$1</del>");
  src = src.replace(/(\d+)/g, (m, i) => codes[+i]);
  return src;
}
function isItem(line) { return line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/); }

function buildList(items) {
  let idx = 0;
  function buildLevel(indent) {
    let html = "";
    while (idx < items.length && items[idx].indent >= indent) {
      if (items[idx].indent > indent) { html += buildLevel(items[idx].indent); continue; }
      const ordered = items[idx].ordered;
      const tag = ordered ? "ol" : "ul";
      html += "<" + tag + ">";
      while (idx < items.length && items[idx].indent === indent && items[idx].ordered === ordered) {
        let li = "<li>" + inlineMd(items[idx].text);
        idx++;
        if (idx < items.length && items[idx].indent > indent) li += buildLevel(items[idx].indent);
        li += "</li>";
        html += li;
      }
      html += "</" + tag + ">";
    }
    return html;
  }
  return buildLevel(items[0].indent);
}

function renderMarkdown(src) {
  src = src.replace(/\r\n?/g, "\n").replace(/\t/g, "    ");
  const lines = src.split("\n");
  const out = [];
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i];
    const fm = line.match(/^(\s*)(`{3,}|~{3,})(.*)$/);
    if (fm) {
      const fence = fm[2][0], flen = fm[2].length, lang = fm[3].trim();
      i++;
      const buf = [];
      while (i < n) {
        const cm = lines[i].match(/^(\s*)(`{3,}|~{3,})\s*$/);
        if (cm && cm[2][0] === fence && cm[2].length >= flen) { i++; break; }
        buf.push(lines[i]); i++;
      }
      out.push('<pre><code' + (lang ? ` class="language-${lang}"` : "") +
        ">" + escapeHtml(buf.join("\n")) + "</code></pre>");
      continue;
    }
    if (/^\s*$/.test(line)) { i++; continue; }
    const hm = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (hm) { out.push(`<h${hm[1].length}>` + inlineMd(hm[2]) + `</h${hm[1].length}>`); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < n && /^\s*>/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push("<blockquote>" + renderMarkdown(buf.join("\n")) + "</blockquote>");
      continue;
    }
    if (line.indexOf("|") >= 0 && i + 1 < n &&
        /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$/.test(lines[i + 1])) {
      const cells = (l) => {
        let s = l.trim();
        if (s.startsWith("|")) s = s.slice(1);
        if (s.endsWith("|")) s = s.slice(0, -1);
        return s.split("|").map((c) => c.trim());
      };
      const heads = cells(lines[i]);
      const aligns = cells(lines[i + 1]).map((c) => {
        const l = c.startsWith(":"), r = c.endsWith(":");
        return l && r ? "center" : r ? "right" : l ? "left" : "";
      });
      i += 2;
      const rows = [];
      while (i < n && lines[i].indexOf("|") >= 0 && !/^\s*$/.test(lines[i])) { rows.push(cells(lines[i])); i++; }
      const sty = (k) => aligns[k] ? ` style="text-align:${aligns[k]}"` : "";
      let html = "<table><thead><tr>" +
        heads.map((h, k) => `<th${sty(k)}>` + inlineMd(h) + "</th>").join("") + "</tr></thead><tbody>";
      for (const r of rows) html += "<tr>" + r.map((c, k) => `<td${sty(k)}>` + inlineMd(c) + "</td>").join("") + "</tr>";
      out.push(html + "</tbody></table>");
      continue;
    }
    if (isItem(line)) {
      const items = [];
      while (i < n) {
        const m = isItem(lines[i]);
        if (m) { items.push({ indent: m[1].length, ordered: /\d/.test(m[2]), text: m[3] }); i++; continue; }
        if (/^\s*$/.test(lines[i])) {
          let j = i + 1;
          while (j < n && /^\s*$/.test(lines[j])) j++;
          if (j < n && isItem(lines[j])) { i = j; continue; }
        }
        break;
      }
      out.push(buildList(items));
      continue;
    }
    const buf = [];
    while (i < n && !/^\s*$/.test(lines[i]) && !/^(#{1,6})\s+/.test(lines[i]) &&
           !/^\s*([-*_])(\s*\1){2,}\s*$/.test(lines[i]) && !/^\s*>/.test(lines[i]) &&
           !/^(\s*)(`{3,}|~{3,})/.test(lines[i]) && !isItem(lines[i])) {
      buf.push(lines[i]); i++;
    }
    out.push("<p>" + inlineMd(buf.join("\n").trim()).replace(/\n/g, "<br>") + "</p>");
  }
  return out.join("\n");
}

// ---------------------------------------------------------------------------
// App state + DOM refs
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const treeEl = $("tree"), specViewer = $("specViewer"), specEditor = $("specEditor"),
  toolbarEl = $("toolbar"), filepathEl = $("filepath"), specSeg = $("specSeg"),
  viewBtn = $("viewBtn"), editBtn = $("editBtn"), saveBtn = $("saveBtn"), followAllBtn = $("followAllBtn"),
  clarifySummary = $("clarifySummary"), clarifyBody = $("clarifyBody"),
  clarifyUnansweredTbody = $("clarifyUnansweredTbody"), clarifyAnsweredTbody = $("clarifyAnsweredTbody"),
  specFileCountEl = $("specFileCount"),
  addFileBtn = $("addFileBtn"), addFolderBtn = $("addFolderBtn"),
  addFileInput = $("addFileInput"), addFolderInput = $("addFolderInput"),
  startClarifyBtn = $("startClarifyBtn"), finalizeClarifyBtn = $("finalizeClarifyBtn"),
  applyAnswersBtn = $("applyAnswersBtn"), finalizeGateList = $("finalizeGateList"),
  implementReadyBanner = $("implementReadyBanner"), clarifyStartImplementBtn = $("clarifyStartImplementBtn"),
  clarifyLogPanel = $("clarifyLogPanel"), clarifyLogBody = $("clarifyLogBody"),
  clarifyLogStatus = $("clarifyLogStatus"),
  homeNotInit = $("homeNotInit"), homeSteps = $("homeSteps"),
  homeSelectFolderBtn = $("homeSelectFolderBtn"), homeWorkspacePath = $("homeWorkspacePath"),
  homeWorkspaceCloseBtn = $("homeWorkspaceCloseBtn"),
  homeStep1 = $("homeStep1"), homeStep2 = $("homeStep2"), homeStep3 = $("homeStep3"),
  homeStep1Status = $("homeStep1Status"), homeStep2Status = $("homeStep2Status"), homeStep3Status = $("homeStep3Status"),
  homeAddFileBtn = $("homeAddFileBtn"), homeAddFolderBtn = $("homeAddFolderBtn"),
  homeStartClarifyBtn = $("homeStartClarifyBtn"), homeFinalizeClarifyBtn = $("homeFinalizeClarifyBtn"),
  homeStartImplementBtn = $("homeStartImplementBtn"), homeClearAllBtn = $("homeClearAllBtn"),
  startImplementBtn = $("startImplementBtn"), stopImplementBtn = $("stopImplementBtn"),
  implHeaderStatus = $("implHeaderStatus"), implGateList = $("implGateList"),
  implTabStatusBtn = $("implTabStatusBtn"), implTabLogBtn = $("implTabLogBtn"),
  implStatusPanel = $("implStatusPanel"), implLogPanel = $("implLogPanel"),
  implStatusBody = $("implStatusBody"), implLogBody = $("implLogBody"),
  modalOverlay = $("modalOverlay"), modalTitle = $("modalTitle"), modalMessage = $("modalMessage"),
  modalInput = $("modalInput"), modalCancelBtn = $("modalCancelBtn"), modalOkBtn = $("modalOkBtn");

const PANES = ["home", "spec", "specOverview", "clarify", "clarifyOverview", "impl"];

const state = {
  specTree: INITIAL_SPEC_TREE,
  clarifyUnanswered: INITIAL_CLARIFY_UNANSWERED || [],
  clarifyAnswered: INITIAL_CLARIFY_ANSWERED || [],
  expandedTop: { specification: INITIAL_VIEW === "specification", clarification: INITIAL_VIEW === "clarification" },
  expandedSpecDirs: new Set([""]),
  activeTop: INITIAL_VIEW,
  currentKind: null,          // null | "spec" | "clarify" — which file/toolbar is currently loaded
  selectedSpecPath: null,
  isMarkdown: false,
  isText: false,
  specMode: "view",
  specDirty: false,
  specShowingOverview: true,      // true = Specification pane shows the file-count/add-file overview
  selectedClarifyPath: null,
  clarifyDirty: false,
  clarifyShowingOverview: true,   // true = Clarification pane shows the file-list overview, not a single file
  clarifyRun: { running: false, mode: null, lines: [], progress: null, nextIndex: 0, pollTimer: null },
  workspaceInitialized: !!INITIAL_WORKSPACE_INITIALIZED,
  workspaceRoot: INITIAL_WORKSPACE_ROOT || "",
  workspaceCanClose: !!INITIAL_WORKSPACE_CAN_CLOSE,
  clarifyFindings: INITIAL_CLARIFY_FINDINGS || { critical: 0, major: 0, minor: 0 },
  clarifyFinalize: INITIAL_CLARIFY_FINALIZE || { hasRun: false, lastAction: null, critical: 0, ready: false },
  epics: [],
  implTab: "status",
  implementRun: { running: false, lines: [], progress: null, nextIndex: 0, pollTimer: null },
};

// ---------------------------------------------------------------------------
// Modal (confirm / prompt) — replaces window.confirm/window.prompt everywhere,
// since those render as an ugly, browser-chrome-branded "<host> says" dialog.
// ---------------------------------------------------------------------------
let modalResolve = null, modalIsPrompt = false;

function closeModal(result) {
  modalOverlay.classList.add("hidden");
  const resolve = modalResolve;
  modalResolve = null;
  if (resolve) resolve(result);
}

function showModal({ title = "Confirm", message = "", okLabel = "OK", danger = false, prompt = false, value = "" }) {
  return new Promise((resolve) => {
    modalResolve = resolve;
    modalIsPrompt = prompt;
    modalTitle.textContent = title;
    modalMessage.innerHTML = "";
    String(message).split("\n").forEach((line, i) => {
      if (i > 0) modalMessage.appendChild(document.createElement("br"));
      modalMessage.appendChild(document.createTextNode(line));
    });
    modalOkBtn.textContent = okLabel;
    modalOkBtn.classList.toggle("danger", danger);
    modalInput.classList.toggle("hidden", !prompt);
    modalInput.value = prompt ? value : "";
    modalOverlay.classList.remove("hidden");
    requestAnimationFrame(() => {
      if (prompt) { modalInput.focus(); modalInput.select(); } else modalOkBtn.focus();
    });
  });
}

// confirmModal resolves true/false; promptModal resolves the entered string, or null on cancel.
function confirmModal(message, opts) {
  return showModal({ message, prompt: false, ...opts });
}
function promptModal(message, value, opts) {
  return showModal({ message, prompt: true, value: value || "", ...opts }).then((v) => (v === false ? null : v));
}

modalCancelBtn.addEventListener("click", () => closeModal(modalIsPrompt ? null : false));
modalOkBtn.addEventListener("click", () => closeModal(modalIsPrompt ? modalInput.value : true));
modalOverlay.addEventListener("click", (e) => {
  if (e.target === modalOverlay) closeModal(modalIsPrompt ? null : false);
});
modalInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); closeModal(modalInput.value); }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !modalOverlay.classList.contains("hidden")) closeModal(modalIsPrompt ? null : false);
});

// ---------------------------------------------------------------------------
// Pane switching
// ---------------------------------------------------------------------------
function showPane(name) {
  PANES.forEach((n) => $(n + "Pane").classList.toggle("hidden", n !== name));
  updateToolbar();
}

function updateToolbar() {
  const kind = state.currentKind;
  // The toolbar (View/Edit/Save) only makes sense while an actual file is open; the
  // Home/Specification/Clarification/Implementation overview panes hide it entirely.
  toolbarEl.classList.toggle("hidden", kind === null);
  if (kind === null) return;
  specSeg.classList.toggle("hidden", kind !== "spec");
  followAllBtn.classList.toggle("hidden", kind !== "clarify");
  saveBtn.classList.remove("hidden");
  if (kind === "spec") {
    saveBtn.disabled = !state.specDirty || !state.isText;
    viewBtn.disabled = editBtn.disabled = !state.isText;
    filepathEl.textContent = "";
    filepathEl.appendChild(document.createTextNode(PRD_NAME + "/" + state.selectedSpecPath));
    if (state.specDirty) {
      const dot = document.createElement("span");
      dot.className = "dirty"; dot.textContent = "● unsaved";
      filepathEl.appendChild(dot);
    }
  } else if (kind === "clarify") {
    saveBtn.disabled = !state.clarifyDirty;
    filepathEl.textContent = "";
    filepathEl.appendChild(document.createTextNode("Clarification/" + state.selectedClarifyPath));
    if (state.clarifyDirty) {
      const dot = document.createElement("span");
      dot.className = "dirty"; dot.textContent = "● unsaved";
      filepathEl.appendChild(dot);
    }
  }
}

function confirmDiscardIfDirty() {
  const dirty = state.currentKind === "spec" ? state.specDirty
              : state.currentKind === "clarify" ? state.clarifyDirty : false;
  if (!dirty) return Promise.resolve(true);
  const label = state.currentKind === "spec" ? state.selectedSpecPath : state.selectedClarifyPath;
  return confirmModal(`You have unsaved changes in "${label}".\nDiscard them and continue?`,
    { title: "Unsaved changes", okLabel: "Discard" });
}

// ---------------------------------------------------------------------------
// Sidebar (top-level sections + nested trees)
// ---------------------------------------------------------------------------
function specIconFor(node) {
  if (node.type === "dir") return "📁";
  if (node.markdown) return "📝";
  if (node.text) return "📄";
  return "🔒";
}

function renderSidebar() {
  treeEl.innerHTML = "";
  treeEl.appendChild(renderLeafSection("home", "🏠", "Home"));
  treeEl.appendChild(renderSpecSection());
  treeEl.appendChild(renderClarifySection());
  treeEl.appendChild(renderLeafSection("implementation", "🛠️", "Implementation", !state.workspaceInitialized));
}

async function selectTop(key) {
  if (!(await confirmDiscardIfDirty())) return;
  state.activeTop = key;
  // Selecting a top-level section always exits "a file is open" mode, so the
  // View/Edit/Save toolbar (driven by currentKind) hides for every section overview.
  state.currentKind = null;
  if (key === "specification" || key === "clarification") state.expandedTop[key] = true;
  if (key === "home") {
    renderHomeWorkflow();
    showPane("home");
  } else if (key === "implementation") {
    refreshImplementRun();
    showPane("impl");
  } else if (key === "specification") {
    state.specShowingOverview = true;
    renderSpecOverview();
    showPane("specOverview");
  } else if (key === "clarification") {
    state.clarifyShowingOverview = true;
    renderClarifyOverview();
    showPane("clarifyOverview");
  }
  renderSidebar();
}

// ---------------------------------------------------------------------------
// Home page — step-by-step workflow (init check -> upload spec -> clarify -> implement)
// ---------------------------------------------------------------------------
function renderHomeWorkflow() {
  homeNotInit.classList.toggle("hidden", state.workspaceInitialized);
  homeSteps.classList.toggle("hidden", !state.workspaceInitialized);
  if (!state.workspaceInitialized) return;

  homeWorkspacePath.textContent = state.workspaceRoot;
  homeWorkspaceCloseBtn.classList.toggle("hidden", !state.workspaceCanClose);

  const specCount = countSpecFiles(state.specTree);
  const step1Done = specCount > 0;
  homeStep1Status.textContent = step1Done
    ? (specCount === 1 ? "1 specification file uploaded." : `${specCount} specification files uploaded.`)
    : "No specification files yet.";

  const step2Locked = !step1Done;
  homeStep2.classList.toggle("locked", step2Locked);
  homeStartClarifyBtn.disabled = step2Locked || state.clarifyRun.running;
  homeFinalizeClarifyBtn.disabled = step2Locked || state.clarifyRun.running || !state.clarifyFinalize.ready;
  const allClarifyFiles = state.clarifyUnanswered.concat(state.clarifyAnswered);
  const totalFindings = allClarifyFiles.reduce((sum, f) => sum + f.total, 0);
  const unansweredFindings = allClarifyFiles.reduce((sum, f) => sum + (f.total - f.answered), 0);
  const criticalCount = state.clarifyFindings.critical;
  homeStep2Status.textContent = step2Locked
    ? "Upload a specification first (step 1)."
    : totalFindings === 0
      ? "No clarification results yet — click Start Clarification to begin."
      : `${unansweredFindings} of ${totalFindings} finding(s) not yet answered (${criticalCount} critical).`;

  const findings = state.clarifyFindings;
  const findingsClean = findings.critical === 0 && findings.major === 0;
  const step3Locked = step2Locked || !findingsClean;
  homeStep3.classList.toggle("locked", step3Locked);
  homeStartImplementBtn.disabled = step3Locked || state.implementRun.running;
  homeStep3Status.textContent = step2Locked
    ? "Finish step 2 first."
    : findingsClean
      ? "No critical or major findings remain — ready to start implementation."
      : `Still ${findings.critical} critical and ${findings.major} major finding(s) that must be resolved.`;
}

homeSelectFolderBtn.addEventListener("click", async () => {
  homeSelectFolderBtn.disabled = true;
  try {
    const res = await fetch("/api/workspace/init", { method: "POST" });
    const data = await res.json();
    if (data.cancelled) return;
    if (!data.ok) { toast(data.error || "Could not set the working folder.", true); return; }
    toast("Working folder set: " + data.root);
    await refreshSpecTree();
  } catch (e) {
    toast("Could not set the working folder.", true);
  } finally {
    homeSelectFolderBtn.disabled = false;
  }
});

homeWorkspacePath.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/workspace/open", { method: "POST" });
    const data = await res.json();
    if (!data.ok) toast(data.error || "Could not open the folder.", true);
  } catch (e) { toast("Could not open the folder.", true); }
});

homeWorkspaceCloseBtn.addEventListener("click", async (e) => {
  e.stopPropagation();
  const ok = await confirmModal(
    `Close working folder "${state.workspaceRoot}"? This only clears the folder link in ` +
    "config.json — no files are deleted. The Home page will go back to Select Working Folder.",
    { title: "Close Working Folder", okLabel: "Close", danger: true });
  if (!ok) return;
  homeWorkspaceCloseBtn.disabled = true;
  try {
    const res = await fetch("/api/workspace/close", { method: "POST" });
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not close the working folder.", true); return; }
    toast("Working folder closed.");
    await refreshSpecTree();
  } catch (e) {
    toast("Network error while closing the working folder.", true);
  } finally {
    homeWorkspaceCloseBtn.disabled = false;
  }
});

homeAddFileBtn.addEventListener("click", () => { addFileInput.value = ""; addFileInput.click(); });
homeAddFolderBtn.addEventListener("click", () => { addFolderInput.value = ""; addFolderInput.click(); });

homeStartClarifyBtn.addEventListener("click", async () => {
  await selectTop("clarification");
  startClarifyRun("run");
});
homeFinalizeClarifyBtn.addEventListener("click", async () => {
  await selectTop("clarification");
  startClarifyRun("finalize");
});
homeStartImplementBtn.addEventListener("click", async () => {
  await selectTop("implementation");
  startImplementRun();
});

homeClearAllBtn.addEventListener("click", async () => {
  const ok = await confirmModal(
    "Are you sure you want to delete ALL data (plan, QA, log, and clarification results)?\n" +
    "Specification files will NOT be deleted.\n\nThis action CANNOT be undone.",
    { title: "Clear All Data", okLabel: "Clear All", danger: true });
  if (!ok) return;
  homeClearAllBtn.disabled = true;
  try {
    const res = await fetch("/api/clear", { method: "POST" });
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Clear failed.", true); return; }
    toast("All data cleared successfully.");
    await refreshClarifyList();
    state.epics = [];
    renderHomeWorkflow();
  } catch (e) {
    toast("Network error while clearing.", true);
  } finally {
    homeClearAllBtn.disabled = false;
  }
});

function renderLeafSection(key, icon, label, disabled) {
  const wrap = document.createElement("div");
  wrap.className = "node";
  const row = document.createElement("div");
  row.className = "row top" + (state.activeTop === key ? " selected" : "") + (disabled ? " disabled" : "");
  row.innerHTML = `<span class="twist hidden"></span><span class="icon">${icon}</span><span class="label">${label}</span>`;
  row.addEventListener("click", () => {
    if (disabled) { toast("Select a working folder first.", true); return; }
    selectTop(key);
  });
  wrap.appendChild(row);
  return wrap;
}

function renderSpecSection() {
  const disabled = !state.workspaceInitialized;
  const wrap = document.createElement("div");
  wrap.className = "node" + (state.expandedTop.specification ? " open" : "");
  const row = document.createElement("div");
  row.className = "row top" + (state.activeTop === "specification" && state.specShowingOverview ? " selected" : "") +
    (disabled ? " disabled" : "");
  row.innerHTML = `<span class="twist">▶</span><span class="icon">📁</span><span class="label">Specification</span>`;
  row.addEventListener("click", () => {
    if (disabled) { toast("Select a working folder first.", true); return; }
    selectTop("specification");
  });
  wrap.appendChild(row);

  const children = document.createElement("div");
  children.className = "children";
  const kids = (state.specTree && state.specTree.children) || [];
  if (!kids.length) {
    const note = document.createElement("div");
    note.className = "empty-note";
    note.textContent = "No PRD files found.";
    children.appendChild(note);
  } else {
    for (const child of kids) children.appendChild(renderSpecNode(child, 1));
  }
  wrap.appendChild(children);
  return wrap;
}

function renderSpecNode(node, depth) {
  const wrap = document.createElement("div");
  wrap.className = "node";
  const isDir = node.type === "dir";
  if (isDir && state.expandedSpecDirs.has(node.path)) wrap.classList.add("open");

  const row = document.createElement("div");
  row.className = "row";
  row.style.paddingLeft = (6 + depth * 15) + "px";
  if (!isDir && !state.specShowingOverview && node.path === state.selectedSpecPath) row.classList.add("selected");

  const twist = document.createElement("span");
  twist.className = "twist" + (isDir ? "" : " hidden");
  twist.textContent = "▶";
  row.appendChild(twist);

  const icon = document.createElement("span");
  icon.className = "icon";
  icon.textContent = specIconFor(node);
  row.appendChild(icon);

  const label = document.createElement("span");
  label.className = "label";
  label.textContent = node.name;
  row.appendChild(label);

  const menuBtn = document.createElement("button");
  menuBtn.type = "button";
  menuBtn.className = "row-menu-btn";
  menuBtn.title = "More";
  menuBtn.textContent = "⋯";
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    openRowContextMenu(menuBtn, node);
  });
  row.appendChild(menuBtn);
  wrap.appendChild(row);

  if (isDir) {
    const children = document.createElement("div");
    children.className = "children";
    for (const child of node.children || []) children.appendChild(renderSpecNode(child, depth + 1));
    wrap.appendChild(children);
    row.addEventListener("click", () => {
      if (state.expandedSpecDirs.has(node.path)) state.expandedSpecDirs.delete(node.path);
      else state.expandedSpecDirs.add(node.path);
      wrap.classList.toggle("open");
    });
  } else {
    row.addEventListener("click", () => openSpecFile(node));
  }
  return wrap;
}

function renderClarifySection() {
  const disabled = !state.workspaceInitialized;
  const wrap = document.createElement("div");
  wrap.className = "node" + (state.expandedTop.clarification ? " open" : "");
  const row = document.createElement("div");
  row.className = "row top" + (state.activeTop === "clarification" && state.clarifyShowingOverview ? " selected" : "") +
    (disabled ? " disabled" : "");
  const count = state.clarifyUnanswered.length;
  row.innerHTML = `<span class="twist">▶</span><span class="icon">❓</span><span class="label">Clarification</span>` +
    (count ? `<span class="badge-count">${count}</span>` : "");
  row.addEventListener("click", () => {
    if (disabled) { toast("Select a working folder first.", true); return; }
    selectTop("clarification");
  });
  wrap.appendChild(row);

  const children = document.createElement("div");
  children.className = "children";
  if (!state.clarifyUnanswered.length) {
    const note = document.createElement("div");
    note.className = "empty-note";
    note.textContent = "Nothing unanswered — all clarification findings are answered.";
    children.appendChild(note);
  } else {
    for (const file of state.clarifyUnanswered) children.appendChild(renderClarifyFileRow(file));
  }
  wrap.appendChild(children);
  return wrap;
}

function renderClarifyFileRow(file) {
  const wrap = document.createElement("div");
  wrap.className = "node";
  const row = document.createElement("div");
  row.className = "row" + (!state.clarifyShowingOverview && file.path === state.selectedClarifyPath ? " selected" : "");
  row.style.paddingLeft = "21px";
  row.innerHTML = `<span class="twist hidden"></span><span class="icon">📝</span>` +
    `<span class="label">${escapeHtml(file.name)}</span>` +
    `<span class="file-status">${file.answered}/${file.total}</span>`;
  row.addEventListener("click", () => openClarifyFile(file));
  wrap.appendChild(row);
  return wrap;
}

// ---------------------------------------------------------------------------
// Clarification overview (right panel shown when "Clarification" itself is selected)
// ---------------------------------------------------------------------------
function severityCell(counts) {
  if (!counts || !counts.total) return "–";
  const cls = counts.answered === counts.total ? "count-ok" : "count-pending";
  return `<span class="${cls}">${counts.answered}/${counts.total}</span>`;
}

function statusCell(file) {
  return file.answered === file.total
    ? '<span class="status-complete">✅ Complete</span>'
    : `<span class="status-pending">🔶 ${file.answered}/${file.total}</span>`;
}

function appliedCell(file) {
  return file.applied
    ? '<span class="clarify-applied-badge">✅ Applied</span>'
    : '<button type="button" class="clarify-apply-btn">Apply Answer</button>';
}

function renderClarifyOverviewRows(tbody, files, emptyMessage, showApplied) {
  tbody.innerHTML = "";
  const colspan = showApplied ? 6 : 5;
  if (!files.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="${colspan}" class="empty-note">${escapeHtml(emptyMessage)}</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const file of files) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(file.name)}</td>` +
      `<td>${severityCell(file.critical)}</td>` +
      `<td>${severityCell(file.major)}</td>` +
      `<td>${severityCell(file.minor)}</td>` +
      `<td>${statusCell(file)}</td>` +
      (showApplied ? `<td>${appliedCell(file)}</td>` : "");
    tr.addEventListener("click", () => openClarifyFile(file));
    if (showApplied && !file.applied) {
      // This file's own row also offers a one-off Apply — same underlying `tempa
      // clarify --apply` as the top Apply Answers button (it always applies every
      // answered file's current answers, there's no way to scope it to just this one).
      const applyBtn = tr.querySelector(".clarify-apply-btn");
      applyBtn.disabled = state.clarifyRun.running;
      applyBtn.addEventListener("click", (e) => { e.stopPropagation(); startClarifyRun("apply"); });
    }
    tbody.appendChild(tr);
  }
}

function renderClarifyOverview() {
  renderClarifyOverviewRows(clarifyUnansweredTbody, state.clarifyUnanswered,
    "No unanswered files.", false);
  renderClarifyOverviewRows(clarifyAnsweredTbody, state.clarifyAnswered,
    "No fully answered files yet.", true);
  setClarifyRunButtonsDisabled(state.clarifyRun.running);
  renderImplementReadyBanner();
}

// Mirrors the same "no critical/major findings left" gate as the Home page's step 3
// (see renderHomeWorkflow) — shown on the Clarification overview so the user doesn't
// have to go back to Home to notice they can move on to implementation.
function renderImplementReadyBanner() {
  const findings = state.clarifyFindings;
  const ready = findings.critical === 0 && findings.major === 0;
  implementReadyBanner.classList.toggle("hidden", !ready);
}

clarifyStartImplementBtn.addEventListener("click", async () => {
  await selectTop("implementation");
  startImplementRun();
});

// ---------------------------------------------------------------------------
// Clarification run (Start Clarification / Finalized Clarification / Apply Answers
// + log panel)
// ---------------------------------------------------------------------------
// Shared renderer for the readiness checklists (Finalize Clarification / Start
// Implementation): items is [{ok, label}], rendered as a ✅/⬜ list into listEl.
function renderGateChecklist(listEl, items) {
  listEl.innerHTML = items.map((it) =>
    `<li class="gate-item ${it.ok ? "ok" : "pending"}">` +
      `<span class="icon">${it.ok ? "✅" : "⬜"}</span><span>${escapeHtml(it.label)}</span></li>`
  ).join("");
}

// The 3 preconditions gating "Finalized Clarification" — see _clarify_finalize_status()
// in dashboard_ui.py for the server-side source of truth this mirrors:
//   1. clarification has been run at least once
//   2. the most recent result comes from a fresh evaluate (Start Clarification), not
//      just an apply — answering + applying criticals isn't enough on its own
//   3. that evaluate's findings show 0 critical
function renderFinalizeGate(runDisabled) {
  const st = state.clarifyFinalize;
  renderGateChecklist(finalizeGateList, [
    { ok: st.hasRun, label: "Clarification has been run at least once" },
    { ok: st.lastAction === "evaluate",
      label: "Most recent result comes from Start Clarification, not just Apply Answers" },
    { ok: st.critical === 0,
      label: st.critical === 0
        ? "Most recent evaluation shows 0 critical findings"
        : `Most recent evaluation still shows ${st.critical} critical finding(s)` },
  ]);
  finalizeClarifyBtn.disabled = runDisabled || !st.ready;
}

function setClarifyRunButtonsDisabled(disabled) {
  startClarifyBtn.disabled = disabled;
  applyAnswersBtn.disabled = disabled || !state.clarifyAnswered.some((f) => !f.applied);
  renderFinalizeGate(disabled);
  // Per-row "Apply Answer" buttons are (re)created by renderClarifyOverviewRows, which
  // already stamps them with the disabled state current at render time — but a run can
  // start/stop without the table re-rendering, so also sync any already-in-the-DOM ones.
  clarifyAnsweredTbody.querySelectorAll(".clarify-apply-btn").forEach((btn) => { btn.disabled = disabled; });
}

function clarifyRunStatusLabel(mode) {
  if (mode === "finalize") return "Finalizing…";
  if (mode === "apply") return "Applying…";
  return "Running…";
}

// Turns one raw console line from `tempa clarify` into a {cls, icon, time, msg} for
// user-friendly rendering — banners, [OK]/[!] markers, and the once-a-second
// progress tick each get their own look instead of showing as raw log text.
function formatClarifyLogLine(text) {
  if (/^\[\d{2}:\d{2}:\d{2}\].*\[\d+ rows\](\s*\[[^\]]*\])*\s*$/.test(text)) {
    const m = text.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$/);
    return { cls: "progress", icon: "⏳", time: m ? m[1] : "", msg: m ? m[2] : text };
  }
  const trimmed = text.trim();
  if (/^==.+==$/.test(trimmed)) {
    return { cls: "banner", icon: "📣", time: "", msg: trimmed.replace(/^=+\s*|\s*=+$/g, "") };
  }
  let time = "", msg = text;
  const tsMatch = text.match(/^\[\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2})\]\s?(.*)$/);
  if (tsMatch) { time = tsMatch[1]; msg = tsMatch[2]; }
  if (/^\[OK\]/i.test(msg)) return { cls: "ok", icon: "✅", time, msg: msg.replace(/^\[OK\]\s*/i, "") };
  if (/SUCCEEDED/.test(msg)) return { cls: "ok", icon: "✅", time, msg };
  if (/FAILED|ERROR|\[error\]|authentication failed/i.test(msg)) return { cls: "err", icon: "❌", time, msg };
  if (/^\[!\]/.test(msg)) return { cls: "warn", icon: "⚠️", time, msg: msg.replace(/^\[!\]\s*/, "") };
  if (/usage limit reached|reached the .* limit/i.test(msg)) return { cls: "warn", icon: "⚠️", time, msg };
  return { cls: "plain", icon: "•", time, msg };
}

function appendClarifyLogRow(text) {
  const f = formatClarifyLogLine(text);
  const row = document.createElement("div");
  row.className = "clarify-log-line " + f.cls;
  row.innerHTML =
    (f.time ? `<span class="clarify-log-time">${escapeHtml(f.time)}</span>` : "") +
    `<span class="clarify-log-icon">${f.icon}</span>` +
    `<span class="clarify-log-msg">${escapeHtml(f.msg)}</span>`;
  return row;
}

function renderClarifyLog() {
  clarifyLogBody.innerHTML = "";
  if (!state.clarifyRun.lines.length && !state.clarifyRun.progress) {
    clarifyLogBody.innerHTML = '<div class="clarify-log-empty">No log output yet.</div>';
    return;
  }
  for (const text of state.clarifyRun.lines) clarifyLogBody.appendChild(appendClarifyLogRow(text));
  // The live progress tick is rendered separately from `lines` (not appended to it) and
  // re-rendered fresh on every poll, so its elapsed time visibly keeps ticking instead of
  // freezing at whatever value happened to be present the first time it was fetched.
  if (state.clarifyRun.progress) clarifyLogBody.appendChild(appendClarifyLogRow(state.clarifyRun.progress));
  clarifyLogBody.scrollTop = clarifyLogBody.scrollHeight;
}

function stopClarifyPolling() {
  if (state.clarifyRun.pollTimer) {
    clearInterval(state.clarifyRun.pollTimer);
    state.clarifyRun.pollTimer = null;
  }
}

function returncodeMessage(code, mode) {
  const label = mode === "apply" ? "Apply" : mode === "finalize" ? "Finalize"
    : mode === "implement" ? "Implementation" : "Clarification";
  if (code === 0) return `${label} run finished.`;
  if (code === 2) return `${label} stopped — Claude usage limit reached.`;
  if (code === 3) return `${label} stopped — authentication error.`;
  return `${label} run exited with an error (code ${code}).`;
}

async function pollClarifyRun() {
  try {
    const res = await fetch("/api/clarify/run?since=" + state.clarifyRun.nextIndex);
    const data = await res.json();
    if (!data.ok) return;
    if (data.lines.length) {
      state.clarifyRun.lines.push(...data.lines);
      state.clarifyRun.nextIndex = data.next;
    }
    // Always re-render, even with no new finalized lines: `progress` (the live
    // elapsed-time tick) changes every second on its own and isn't part of `lines`.
    state.clarifyRun.progress = data.progress;
    renderClarifyLog();
    state.clarifyRun.running = data.running;
    clarifyLogStatus.textContent = data.running ? clarifyRunStatusLabel(data.mode) : "";
    setClarifyRunButtonsDisabled(data.running);
    if (!data.running) {
      stopClarifyPolling();
      if (data.returncode !== null) toast(returncodeMessage(data.returncode, data.mode), data.returncode !== 0);
      refreshClarifyList();
      // CLI parity: `tempa clarify --apply` asks "Run another clarification round now?"
      // via input() right after a successful apply, but only when stdin is a real TTY —
      // the dashboard's subprocess always runs with stdin=DEVNULL, so that prompt never
      // fires there. Ask the same question here instead, as a modal, since the web UI
      // has no terminal to type y/N into.
      if (data.mode === "apply" && data.returncode === 0) askContinueClarification();
    }
  } catch (e) { /* transient network hiccup — next tick retries */ }
}

async function askContinueClarification() {
  const ok = await confirmModal("Run another clarification round now?",
    { title: "Continue Clarification", okLabel: "Continue" });
  if (ok) startClarifyRun("run");
}

function startClarifyPolling() {
  stopClarifyPolling();
  state.clarifyRun.pollTimer = setInterval(pollClarifyRun, 1000);
  pollClarifyRun();
}

async function startClarifyRun(mode) {
  if (state.clarifyRun.running) return;
  setClarifyRunButtonsDisabled(true);
  clarifyLogPanel.classList.remove("hidden");
  clarifyLogPanel.open = true;
  state.clarifyRun.lines = [];
  state.clarifyRun.progress = null;
  state.clarifyRun.nextIndex = 0;
  state.clarifyRun.mode = mode;
  clarifyLogStatus.textContent = clarifyRunStatusLabel(mode);
  renderClarifyLog();
  try {
    const res = await fetch("/api/clarify/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    if (!data.ok) {
      toast(data.error || "Could not start clarification run.", true);
      clarifyLogStatus.textContent = "";
      setClarifyRunButtonsDisabled(false);
      return;
    }
    state.clarifyRun.running = true;
    startClarifyPolling();
  } catch (e) {
    toast("Network error starting clarification run.", true);
    clarifyLogStatus.textContent = "";
    setClarifyRunButtonsDisabled(false);
  }
}

async function checkClarifyRunOnLoad() {
  try {
    const res = await fetch("/api/clarify/run?since=0");
    const data = await res.json();
    if (!data.ok || (!data.running && !data.lines.length)) return;
    state.clarifyRun.lines = data.lines;
    state.clarifyRun.nextIndex = data.next;
    state.clarifyRun.mode = data.mode;
    state.clarifyRun.progress = data.progress;
    clarifyLogPanel.classList.remove("hidden");
    renderClarifyLog();
    clarifyLogStatus.textContent = data.running ? clarifyRunStatusLabel(data.mode) : "";
    setClarifyRunButtonsDisabled(data.running);
    if (data.running) { clarifyLogPanel.open = true; startClarifyPolling(); }
  } catch (e) { /* ignore — buttons stay enabled */ }
}

startClarifyBtn.addEventListener("click", () => startClarifyRun("run"));
finalizeClarifyBtn.addEventListener("click", () => startClarifyRun("finalize"));
applyAnswersBtn.addEventListener("click", () => startClarifyRun("apply"));

// ---------------------------------------------------------------------------
// Implementation run (Start/Stop Implementation + Status/Log tabs)
// ---------------------------------------------------------------------------
function setImplTab(tab) {
  state.implTab = tab;
  implTabStatusBtn.classList.toggle("active", tab === "status");
  implTabLogBtn.classList.toggle("active", tab === "log");
  implStatusPanel.classList.toggle("hidden", tab !== "status");
  implLogPanel.classList.toggle("hidden", tab !== "log");
}
implTabStatusBtn.addEventListener("click", () => setImplTab("status"));
implTabLogBtn.addEventListener("click", () => setImplTab("log"));

function epicStatusIcon(status) {
  return { done: "✅", on_progress: "🔄", pending: "⬜", failed: "❌", require_fixing: "🔧" }[status] || "❔";
}
function featureStatusIcon(status) {
  return { done: "✅", failed: "❌", require_fixing: "🔧" }[status] || "⬜";
}

function renderImplementStatus() {
  implStatusBody.innerHTML = "";
  if (!state.epics.length) {
    implStatusBody.innerHTML = '<div class="clarify-log-empty">No plan/epic yet. A plan will be generated automatically the first time implementation starts.</div>';
    return;
  }
  for (const epic of state.epics) {
    const card = document.createElement("div");
    card.className = "impl-epic-card";
    const qaTag = epic.status === "done"
      ? (epic.qa_passed ? '<span class="impl-qa-ok">QA ok</span>' : '<span class="impl-qa-pending">QA --</span>')
      : "";
    const lastRun = epic.last_run ? escapeHtml(epic.last_run.slice(0, 16).replace("T", " ")) : "-";
    const features = (epic.features || []).map((f) =>
      `<div class="impl-feature-row"><span>${featureStatusIcon(f.status)}</span><span>${escapeHtml(f.id)} — ${escapeHtml(f.name)}</span></div>`
    ).join("");
    card.innerHTML =
      `<div class="impl-epic-header">` +
        `<span class="impl-epic-icon">${epicStatusIcon(epic.status)}</span>` +
        `<span class="impl-epic-name">${escapeHtml(epic.epic_name || "?")}</span>` +
        `<span class="impl-epic-status">${escapeHtml(epic.status || "")}</span>` +
        `<span class="impl-epic-progress">${epic.completed_features || 0}/${epic.total_features || 0} features</span>` +
        `<span class="impl-epic-lastrun">last run: ${lastRun}</span>` +
        qaTag +
      `</div>` +
      `<div class="impl-feature-list">${features}</div>`;
    implStatusBody.appendChild(card);
  }
}

function renderImplementLog() {
  implLogBody.innerHTML = "";
  if (!state.implementRun.lines.length && !state.implementRun.progress) {
    implLogBody.innerHTML = '<div class="clarify-log-empty">No log output yet.</div>';
    return;
  }
  for (const text of state.implementRun.lines) implLogBody.appendChild(appendClarifyLogRow(text));
  if (state.implementRun.progress) implLogBody.appendChild(appendClarifyLogRow(state.implementRun.progress));
  implLogBody.scrollTop = implLogBody.scrollHeight;
}

// The 2 preconditions gating "Start Implementation": no critical and no major
// clarification findings remain (server-enforced too — see _handle_implement_run_start
// in dashboard_ui.py).
function renderImplementGate() {
  const findings = state.clarifyFindings;
  renderGateChecklist(implGateList, [
    { ok: findings.critical === 0,
      label: findings.critical === 0
        ? "No critical findings remain"
        : `${findings.critical} critical finding(s) remain` },
    { ok: findings.major === 0,
      label: findings.major === 0
        ? "No major findings remain"
        : `${findings.major} major finding(s) remain` },
  ]);
}

function updateImplementControls() {
  const findings = state.clarifyFindings;
  const clean = findings.critical === 0 && findings.major === 0;
  startImplementBtn.disabled = state.implementRun.running || !clean;
  stopImplementBtn.classList.toggle("hidden", !state.implementRun.running);
  implHeaderStatus.textContent = state.implementRun.running ? "Running…" : "";
  renderImplementGate();
}

function stopImplementPolling() {
  if (state.implementRun.pollTimer) {
    clearInterval(state.implementRun.pollTimer);
    state.implementRun.pollTimer = null;
  }
}

// Single fetch+render used both as the recurring 1s poll tick AND as a one-off
// refresh (page load, navigating into the Implementation section) — unlike
// clarify's two separate functions, implement only ever has one "mode", so there's
// no per-mode state to keep in sync between them.
async function refreshImplementRun() {
  try {
    const res = await fetch("/api/implement/run?since=" + state.implementRun.nextIndex);
    const data = await res.json();
    if (!data.ok) return;
    if (data.lines.length) {
      state.implementRun.lines.push(...data.lines);
      state.implementRun.nextIndex = data.next;
    }
    state.implementRun.progress = data.progress;
    state.epics = data.epics || [];
    renderImplementLog();
    renderImplementStatus();
    const wasRunning = state.implementRun.running;
    state.implementRun.running = data.running;
    updateImplementControls();
    homeStartImplementBtn.disabled = data.running || !(state.clarifyFindings.critical === 0 && state.clarifyFindings.major === 0);
    if (data.running && !state.implementRun.pollTimer) startImplementPolling();
    if (!data.running) {
      stopImplementPolling();
      if (wasRunning && data.returncode !== null) {
        toast(returncodeMessage(data.returncode, "implement"), data.returncode !== 0);
      }
    }
  } catch (e) { /* transient network hiccup — next tick retries */ }
}

function startImplementPolling() {
  stopImplementPolling();
  state.implementRun.pollTimer = setInterval(refreshImplementRun, 1000);
  refreshImplementRun();
}

async function startImplementRun() {
  if (state.implementRun.running) return;
  const findings = state.clarifyFindings;
  if (findings.critical > 0 || findings.major > 0) {
    toast("There are still critical/major findings — resolve clarification first.", true);
    return;
  }
  startImplementBtn.disabled = true;
  state.implementRun.lines = [];
  state.implementRun.progress = null;
  state.implementRun.nextIndex = 0;
  implHeaderStatus.textContent = "Running…";
  renderImplementLog();
  setImplTab("log");
  try {
    const res = await fetch("/api/implement/run", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      toast(data.error || "Could not start implementation.", true);
      updateImplementControls();
      return;
    }
    state.implementRun.running = true;
    updateImplementControls();
    startImplementPolling();
  } catch (e) {
    toast("Network error starting implementation.", true);
    updateImplementControls();
  }
}

async function stopImplementRun() {
  if (!state.implementRun.running) return;
  const ok = await confirmModal("Stop the implementation process that is currently running?",
    { title: "Stop Implementation", okLabel: "Stop", danger: true });
  if (!ok) return;
  stopImplementBtn.disabled = true;
  try {
    const res = await fetch("/api/implement/stop", { method: "POST" });
    const data = await res.json();
    if (!data.ok) toast(data.error || "Could not stop implementation.", true);
  } catch (e) {
    toast("Network error stopping implementation.", true);
  } finally {
    stopImplementBtn.disabled = false;
  }
}

startImplementBtn.addEventListener("click", startImplementRun);
stopImplementBtn.addEventListener("click", stopImplementRun);

// ---------------------------------------------------------------------------
// Specification: open / mode / save
// ---------------------------------------------------------------------------
async function openSpecFile(node) {
  if (!(await confirmDiscardIfDirty())) return;
  try {
    const res = await fetch("/api/spec/file?path=" + encodeURIComponent(node.path));
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not open file.", true); return; }
    state.activeTop = "specification";
    state.currentKind = "spec";
    state.selectedSpecPath = data.path;
    state.isMarkdown = data.markdown;
    state.isText = data.text;
    state.specDirty = false;
    state.specShowingOverview = false;
    specEditor.value = data.content || "";
    if (!data.text) {
      specViewer.innerHTML = "";
      specViewer.classList.remove("hidden");
      specEditor.classList.add("hidden");
      const p = document.createElement("div");
      p.className = "placeholder-pane";
      p.textContent = data.reason || "This file can't be shown as text.";
      specViewer.appendChild(p);
    } else {
      setSpecMode("view");
    }
    showPane("spec");
    renderSidebar();
  } catch (e) {
    toast("Network error opening file.", true);
  }
}

function renderSpecViewer() {
  const text = specEditor.value;
  specViewer.innerHTML = state.isMarkdown
    ? renderMarkdown(text)
    : "<pre><code>" + escapeHtml(text) + "</code></pre>";
}

function setSpecMode(mode) {
  if (!state.isText) return;
  state.specMode = mode;
  const viewing = mode === "view";
  viewBtn.classList.toggle("active", viewing);
  editBtn.classList.toggle("active", !viewing);
  if (viewing) {
    renderSpecViewer();
    specViewer.classList.remove("hidden");
    specEditor.classList.add("hidden");
  } else {
    specViewer.classList.add("hidden");
    specEditor.classList.remove("hidden");
    specEditor.focus();
  }
}

async function saveSpecFile() {
  if (!state.selectedSpecPath || !state.specDirty || !state.isText) return;
  saveBtn.disabled = true;
  try {
    const res = await fetch("/api/spec/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.selectedSpecPath, content: specEditor.value }),
    });
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Save failed.", true); updateToolbar(); return; }
    state.specDirty = false;
    updateToolbar();
    if (state.specMode === "view") renderSpecViewer();
    toast("Saved " + state.selectedSpecPath);
  } catch (e) {
    toast("Network error while saving.", true);
    updateToolbar();
  }
}

specEditor.addEventListener("input", () => {
  if (!state.specDirty) { state.specDirty = true; updateToolbar(); }
});
viewBtn.addEventListener("click", () => setSpecMode("view"));
editBtn.addEventListener("click", () => setSpecMode("edit"));

// ---------------------------------------------------------------------------
// Specification overview (right panel shown when "Specification" itself is selected)
// ---------------------------------------------------------------------------
function countSpecFiles(node) {
  if (!node) return 0;
  if (node.type === "file") return 1;
  return (node.children || []).reduce((sum, c) => sum + countSpecFiles(c), 0);
}

function renderSpecOverview() {
  const count = countSpecFiles(state.specTree);
  specFileCountEl.textContent = count === 1 ? "1 specification file" : `${count} specification files`;
}

async function refreshSpecTree() {
  try {
    const res = await fetch("/api/tree");
    const data = await res.json();
    if (data.ok) {
      state.specTree = data.spec.tree;
      state.workspaceInitialized = !!data.workspace.initialized;
      state.workspaceRoot = data.workspace.root || "";
      state.workspaceCanClose = !!data.workspace.canClose;
      state.clarifyFindings = data.clarify.findings;
      state.clarifyFinalize = data.clarify.finalize;
      renderSidebar();
      if (!$("specOverviewPane").classList.contains("hidden")) renderSpecOverview();
      if (!$("homePane").classList.contains("hidden")) renderHomeWorkflow();
    }
  } catch (e) { /* keep stale tree on network error */ }
}

async function uploadToSpec(entries) {
  if (!entries.length) return;
  const label = entries.length === 1 ? "1 file" : `${entries.length} files`;
  const ok = await confirmModal(
    `Add ${label} to Specification (${PRD_NAME})? Existing files with the same name will be overwritten.`,
    { title: "Add to Specification", okLabel: "Add" });
  if (!ok) return;
  let okCount = 0, failCount = 0;
  for (const { file, relPath } of entries) {
    try {
      const buf = await file.arrayBuffer();
      const res = await fetch("/api/spec/upload?path=" + encodeURIComponent(relPath), {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: buf,
      });
      const data = await res.json();
      if (data.ok) okCount++; else failCount++;
    } catch (e) { failCount++; }
  }
  toast(failCount ? `Added ${okCount} file(s), ${failCount} failed.` : `Added ${okCount} file(s).`, failCount > 0);
  await refreshSpecTree();
}

addFileBtn.addEventListener("click", () => { addFileInput.value = ""; addFileInput.click(); });
addFolderBtn.addEventListener("click", () => { addFolderInput.value = ""; addFolderInput.click(); });

addFileInput.addEventListener("change", () => {
  const entries = Array.from(addFileInput.files).map((f) => ({ file: f, relPath: f.name }));
  uploadToSpec(entries);
});
addFolderInput.addEventListener("change", () => {
  const entries = Array.from(addFolderInput.files).map((f) => ({ file: f, relPath: f.webkitRelativePath || f.name }));
  uploadToSpec(entries);
});

// ---------------------------------------------------------------------------
// Specification row context menu (rename / delete a file or folder)
// ---------------------------------------------------------------------------
const rowContextMenu = $("rowContextMenu"), rowMenuRename = $("rowMenuRename"), rowMenuDelete = $("rowMenuDelete");
let contextMenuNode = null;

function openRowContextMenu(anchorEl, node) {
  contextMenuNode = node;
  const rect = anchorEl.getBoundingClientRect();
  rowContextMenu.classList.remove("hidden");
  const menuWidth = rowContextMenu.offsetWidth || 130;
  rowContextMenu.style.top = rect.bottom + 4 + "px";
  rowContextMenu.style.left = Math.min(rect.left, window.innerWidth - menuWidth - 8) + "px";
}

function closeRowContextMenu() {
  rowContextMenu.classList.add("hidden");
  contextMenuNode = null;
}

document.addEventListener("click", (e) => {
  if (!rowContextMenu.classList.contains("hidden") && !rowContextMenu.contains(e.target)) closeRowContextMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeRowContextMenu();
});

rowMenuRename.addEventListener("click", async () => {
  const node = contextMenuNode;
  closeRowContextMenu();
  if (!node) return;
  const newName = await promptModal(`Rename "${node.name}" to:`, node.name, { title: "Rename", okLabel: "Rename" });
  if (!newName || newName === node.name) return;
  if (newName.includes("/") || newName.includes("\\")) {
    toast("Name cannot contain a path separator.", true);
    return;
  }
  try {
    const res = await fetch("/api/spec/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: node.path, new_name: newName }),
    });
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Rename failed.", true); return; }
    // Renaming the exact file currently open keeps it open under its new path; renaming
    // a folder that merely contains the open file is not tracked (rare edge case) — a
    // later Save would just fail gracefully with "File no longer exists".
    if (state.selectedSpecPath === node.path) state.selectedSpecPath = data.path;
    toast(`Renamed to "${newName}".`);
    await refreshSpecTree();
  } catch (e) {
    toast("Network error while renaming.", true);
  }
});

rowMenuDelete.addEventListener("click", async () => {
  const node = contextMenuNode;
  closeRowContextMenu();
  if (!node) return;
  const kind = node.type === "dir" ? "folder" : "file";
  const ok = await confirmModal(`Delete the ${kind} "${node.name}"? This cannot be undone.`,
    { title: "Delete", okLabel: "Delete", danger: true });
  if (!ok) return;
  try {
    const res = await fetch("/api/spec/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: node.path }),
    });
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Delete failed.", true); return; }
    const affectsOpenFile = state.selectedSpecPath === node.path ||
      (node.type === "dir" && state.selectedSpecPath && state.selectedSpecPath.startsWith(node.path + "/"));
    if (affectsOpenFile) {
      state.selectedSpecPath = null;
      state.currentKind = null;
      state.specDirty = false;
      state.specShowingOverview = true;
      showPane("specOverview");
    }
    toast(`Deleted "${node.name}".`);
    await refreshSpecTree();
  } catch (e) {
    toast("Network error while deleting.", true);
  }
});

// ---------------------------------------------------------------------------
// Clarification: open / answer / save
// ---------------------------------------------------------------------------
function onClarifyModeChange(radio) {
  const item = radio.closest(".item");
  const ta = item.querySelector("textarea");
  const own = item.querySelector('input[value="own"]').checked;
  ta.disabled = !own;
  if (own) ta.focus();
}

function wireClarifyBody() {
  clarifyBody.querySelectorAll('input[type=radio]').forEach((r) => {
    r.addEventListener("change", () => { onClarifyModeChange(r); markClarifyDirty(); });
  });
  clarifyBody.querySelectorAll("textarea").forEach((t) => {
    t.addEventListener("input", markClarifyDirty);
  });
}

// Bulk-selects "Follow the recommendation" for every item that has no radio picked
// yet (i.e. hasn't been answered in this session) — leaves items the user already
// answered, or already chose a mode for, untouched.
function followAllRecommendations() {
  let count = 0;
  clarifyBody.querySelectorAll(".item").forEach((sec) => {
    if (sec.querySelector('input[type=radio]:checked')) return;
    const recRadio = sec.querySelector('input[value="recommendation"]');
    if (!recRadio) return;
    recRadio.checked = true;
    onClarifyModeChange(recRadio);
    count++;
  });
  if (count) {
    markClarifyDirty();
    toast(`Set "Follow the recommendation" for ${count} finding(s).`);
  } else {
    toast("No unanswered findings to fill in.");
  }
}

followAllBtn.addEventListener("click", followAllRecommendations);

function markClarifyDirty() {
  if (!state.clarifyDirty) { state.clarifyDirty = true; updateToolbar(); }
}

async function openClarifyFile(file) {
  if (!(await confirmDiscardIfDirty())) return;
  try {
    const res = await fetch("/api/clarify/file?path=" + encodeURIComponent(file.path));
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not open file.", true); return; }
    state.activeTop = "clarification";
    state.currentKind = "clarify";
    state.selectedClarifyPath = data.path;
    state.clarifyDirty = false;
    state.clarifyShowingOverview = false;
    clarifySummary.textContent = data.summary || "";
    clarifyBody.innerHTML = data.html || "";
    wireClarifyBody();
    showPane("clarify");
    renderSidebar();
  } catch (e) {
    toast("Network error opening file.", true);
  }
}

function collectClarifyAnswers() {
  const items = [];
  clarifyBody.querySelectorAll(".item").forEach((sec) => {
    const key = sec.dataset.key;
    const checked = sec.querySelector("input[type=radio]:checked");
    const mode = checked ? checked.value : "own";
    const ta = sec.querySelector("textarea");
    items.push({ id: key, mode: mode, answer: ta.value });
  });
  return items;
}

async function saveClarifyFile() {
  if (!state.selectedClarifyPath || !state.clarifyDirty) return;
  const items = collectClarifyAnswers();
  const own = items.filter((i) => i.mode === "own");
  const missing = own.filter((i) => !i.answer.trim());
  if (missing.length) {
    alert('Please fill in your own answer for ' + missing.length +
      ' finding(s), or switch them back to "Follow the recommendation".');
    return;
  }
  saveBtn.disabled = true;
  try {
    const res = await fetch("/api/clarify/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.selectedClarifyPath, items: items }),
    });
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Save failed.", true); updateToolbar(); return; }
    state.clarifyDirty = false;
    updateToolbar();
    toast(`Saved ${state.selectedClarifyPath} (${data.answered}/${data.total} answered)`);
    await refreshClarifyList();
  } catch (e) {
    toast("Network error while saving.", true);
    updateToolbar();
  }
}

async function refreshClarifyList() {
  try {
    const res = await fetch("/api/tree");
    const data = await res.json();
    if (data.ok) {
      state.clarifyUnanswered = data.clarify.unanswered || [];
      state.clarifyAnswered = data.clarify.answered || [];
      state.workspaceInitialized = !!data.workspace.initialized;
      state.workspaceRoot = data.workspace.root || "";
      state.workspaceCanClose = !!data.workspace.canClose;
      state.clarifyFindings = data.clarify.findings;
      state.clarifyFinalize = data.clarify.finalize;
      renderSidebar();
      if (!$("clarifyOverviewPane").classList.contains("hidden")) renderClarifyOverview();
      if (!$("homePane").classList.contains("hidden")) renderHomeWorkflow();
    }
  } catch (e) { /* keep stale list on network error */ }
}

// ---------------------------------------------------------------------------
// Shared events
// ---------------------------------------------------------------------------
saveBtn.addEventListener("click", () => {
  if (state.currentKind === "spec") saveSpecFile();
  else if (state.currentKind === "clarify") saveClarifyFile();
});

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    if (state.currentKind === "spec") saveSpecFile();
    else if (state.currentKind === "clarify") saveClarifyFile();
  }
});
window.addEventListener("beforeunload", (e) => {
  if (state.specDirty || state.clarifyDirty) { e.preventDefault(); e.returnValue = ""; }
});

$("refreshBtn").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/tree");
    const data = await res.json();
    if (data.ok) {
      state.specTree = data.spec.tree;
      state.clarifyUnanswered = data.clarify.unanswered || [];
      state.clarifyAnswered = data.clarify.answered || [];
      state.workspaceInitialized = !!data.workspace.initialized;
      state.workspaceRoot = data.workspace.root || "";
      state.workspaceCanClose = !!data.workspace.canClose;
      state.clarifyFindings = data.clarify.findings;
      state.clarifyFinalize = data.clarify.finalize;
      renderSidebar();
      if (!$("specOverviewPane").classList.contains("hidden")) renderSpecOverview();
      if (!$("clarifyOverviewPane").classList.contains("hidden")) renderClarifyOverview();
      if (!$("homePane").classList.contains("hidden")) renderHomeWorkflow();
      toast("Rescanned.");
    }
  } catch (e) { toast("Could not refresh.", true); }
});

// splitter drag-to-resize
(function () {
  const splitter = $("splitter"), sidebar = $("sidebar");
  let dragging = false;
  splitter.addEventListener("mousedown", (e) => {
    dragging = true; splitter.classList.add("dragging");
    document.body.style.userSelect = "none"; e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const w = Math.max(200, Math.min(e.clientX, window.innerWidth * 0.7));
    sidebar.style.width = w + "px";
  });
  window.addEventListener("mouseup", () => {
    dragging = false; splitter.classList.remove("dragging"); document.body.style.userSelect = "";
  });
})();

let toastTimer = null;
function toast(msg, isErr) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.toggle("err", !!isErr);
  el.classList.add("show");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

// ---------------------------------------------------------------------------
// Initial paint
// ---------------------------------------------------------------------------
renderSidebar();
if (INITIAL_VIEW === "specification") {
  renderSpecOverview();
  showPane("specOverview");
} else if (INITIAL_VIEW === "clarification") {
  renderClarifyOverview();
  showPane("clarifyOverview");
} else {
  renderHomeWorkflow();
  showPane("home");
}
checkClarifyRunOnLoad();
refreshImplementRun();
</script>
</body>
</html>
"""
