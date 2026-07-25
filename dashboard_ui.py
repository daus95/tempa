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

import html as html_lib
import json
import re
import shutil
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

    recommendation_radio = ""
    if has_recommendation:
        checked = "" if default_own else "checked"
        recommendation_radio = (
            f'<label><input type="radio" name="mode-{key}" value="recommendation" {checked}> '
            f"Follow the recommendation</label>"
        )
    own_checked = "checked" if default_own or not has_recommendation else ""
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
    }


def _clarify_files_overview(clar_dir: Path) -> tuple[list[dict], list[dict]]:
    """Every clarification result file (flat, excluding claude.md) with recognized
    findings, split into (unanswered, fully_answered), each sorted by name."""
    unanswered: list[dict] = []
    answered: list[dict] = []
    if not clar_dir.exists():
        return unanswered, answered
    for p in sorted(clar_dir.glob("*.md")):
        if p.name.lower() == "claude.md":
            continue
        stats = _file_severity_stats(p)
        if stats is None:
            continue
        (answered if stats["answered"] == stats["total"] else unanswered).append(stats)
    return unanswered, answered


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
            self._send_json(200, {
                "ok": True,
                "spec": {"tree": build_tree(self.server.prd_dir)},
                "clarify": {"unanswered": unanswered, "answered": answered},
            })
        elif route == "/api/spec/file":
            self._handle_spec_file(parse_qs(parsed.query))
        elif route == "/api/clarify/file":
            self._handle_clarify_file(parse_qs(parsed.query))
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
    return (
        _PAGE_TEMPLATE
        .replace("/*__SPEC_TREE__*/null", tree_json)
        .replace("/*__CLARIFY_UNANSWERED__*/null", unanswered_json)
        .replace("/*__CLARIFY_ANSWERED__*/null", answered_json)
        .replace("/*__PRD_NAME__*/null", prd_name)
        .replace("/*__INITIAL_VIEW__*/null", view_json)
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
  .placeholder-pane, .home-pane, .impl-pane { display: flex; align-items: center;
    justify-content: center; color: var(--muted); text-align: center; padding: 40px; }

  /* ---- watermark / coming-soon ---- */
  .watermark { font-size: 15px; opacity: .8; }
  .watermark .brand { font-size: 46px; font-weight: 800; letter-spacing: 2px;
    color: var(--border-strong); margin-bottom: 10px; }
  .impl-pane .brand { font-size: 40px; margin-bottom: 10px; }

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
    <div class="toolbar">
      <span class="filepath" id="filepath">Home</span>
      <div class="seg hidden" id="specSeg">
        <button id="viewBtn" class="active">View</button>
        <button id="editBtn">Edit</button>
      </div>
      <button id="saveBtn" class="primary hidden" disabled>Save</button>
    </div>
    <div class="content">
      <div id="homePane" class="pane home-pane">
        <div class="watermark">
          <div class="brand">TEMPA</div>
          <div>Select Specification or Clarification on the left to get started.</div>
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
            <thead><tr><th>File</th><th>Critical</th><th>Major</th><th>Minor</th><th>Status</th></tr></thead>
            <tbody id="clarifyAnsweredTbody"></tbody>
          </table>
        </div>
      </div>
      <div id="implPane" class="pane impl-pane hidden">
        <div>
          <div class="brand">🚧</div>
          <div>Implementation view is coming soon.</div>
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

<script>
"use strict";
const INITIAL_SPEC_TREE = /*__SPEC_TREE__*/null;
const INITIAL_CLARIFY_UNANSWERED = /*__CLARIFY_UNANSWERED__*/null;
const INITIAL_CLARIFY_ANSWERED = /*__CLARIFY_ANSWERED__*/null;
const PRD_NAME = /*__PRD_NAME__*/null;
const INITIAL_VIEW = /*__INITIAL_VIEW__*/null;

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
  filepathEl = $("filepath"), specSeg = $("specSeg"),
  viewBtn = $("viewBtn"), editBtn = $("editBtn"), saveBtn = $("saveBtn"),
  clarifySummary = $("clarifySummary"), clarifyBody = $("clarifyBody"),
  clarifyUnansweredTbody = $("clarifyUnansweredTbody"), clarifyAnsweredTbody = $("clarifyAnsweredTbody"),
  specFileCountEl = $("specFileCount"),
  addFileBtn = $("addFileBtn"), addFolderBtn = $("addFolderBtn"),
  addFileInput = $("addFileInput"), addFolderInput = $("addFolderInput");

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
};

// ---------------------------------------------------------------------------
// Pane switching
// ---------------------------------------------------------------------------
function showPane(name) {
  PANES.forEach((n) => $(n + "Pane").classList.toggle("hidden", n !== name));
  updateToolbar();
}

function updateToolbar() {
  const kind = state.currentKind;
  specSeg.classList.toggle("hidden", kind !== "spec");
  saveBtn.classList.toggle("hidden", kind === null);
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
  } else if (state.activeTop === "specification") {
    filepathEl.textContent = "Specification";
  } else if (state.activeTop === "clarification") {
    filepathEl.textContent = "Clarification";
  } else if (state.activeTop === "implementation") {
    filepathEl.textContent = "Implementation";
  } else {
    filepathEl.textContent = "Home";
  }
}

function confirmDiscardIfDirty() {
  const dirty = state.currentKind === "spec" ? state.specDirty
              : state.currentKind === "clarify" ? state.clarifyDirty : false;
  if (!dirty) return Promise.resolve(true);
  const label = state.currentKind === "spec" ? state.selectedSpecPath : state.selectedClarifyPath;
  return Promise.resolve(window.confirm(
    "You have unsaved changes in \"" + label + "\".\nDiscard them and continue?"));
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
  treeEl.appendChild(renderLeafSection("implementation", "🛠️", "Implementation"));
}

async function selectTop(key) {
  if (!(await confirmDiscardIfDirty())) return;
  state.activeTop = key;
  if (key === "specification" || key === "clarification") state.expandedTop[key] = true;
  if (key === "home") showPane("home");
  else if (key === "implementation") showPane("impl");
  else if (key === "specification") {
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

function renderLeafSection(key, icon, label) {
  const wrap = document.createElement("div");
  wrap.className = "node";
  const row = document.createElement("div");
  row.className = "row top" + (state.activeTop === key ? " selected" : "");
  row.innerHTML = `<span class="twist hidden"></span><span class="icon">${icon}</span><span class="label">${label}</span>`;
  row.addEventListener("click", () => selectTop(key));
  wrap.appendChild(row);
  return wrap;
}

function renderSpecSection() {
  const wrap = document.createElement("div");
  wrap.className = "node" + (state.expandedTop.specification ? " open" : "");
  const row = document.createElement("div");
  row.className = "row top" + (state.activeTop === "specification" && state.specShowingOverview ? " selected" : "");
  row.innerHTML = `<span class="twist">▶</span><span class="icon">📁</span><span class="label">Specification</span>`;
  row.addEventListener("click", () => selectTop("specification"));
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
  const wrap = document.createElement("div");
  wrap.className = "node" + (state.expandedTop.clarification ? " open" : "");
  const row = document.createElement("div");
  row.className = "row top" + (state.activeTop === "clarification" && state.clarifyShowingOverview ? " selected" : "");
  const count = state.clarifyUnanswered.length;
  row.innerHTML = `<span class="twist">▶</span><span class="icon">❓</span><span class="label">Clarification</span>` +
    (count ? `<span class="badge-count">${count}</span>` : "");
  row.addEventListener("click", () => selectTop("clarification"));
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

function renderClarifyOverviewRows(tbody, files, emptyMessage) {
  tbody.innerHTML = "";
  if (!files.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="5" class="empty-note">${escapeHtml(emptyMessage)}</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const file of files) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(file.name)}</td>` +
      `<td>${severityCell(file.critical)}</td>` +
      `<td>${severityCell(file.major)}</td>` +
      `<td>${severityCell(file.minor)}</td>` +
      `<td>${statusCell(file)}</td>`;
    tr.addEventListener("click", () => openClarifyFile(file));
    tbody.appendChild(tr);
  }
}

function renderClarifyOverview() {
  renderClarifyOverviewRows(clarifyUnansweredTbody, state.clarifyUnanswered,
    "No unanswered files.");
  renderClarifyOverviewRows(clarifyAnsweredTbody, state.clarifyAnswered,
    "No fully answered files yet.");
}

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
      renderSidebar();
      if (!$("specOverviewPane").classList.contains("hidden")) renderSpecOverview();
    }
  } catch (e) { /* keep stale tree on network error */ }
}

async function uploadToSpec(entries) {
  if (!entries.length) return;
  const label = entries.length === 1 ? "1 file" : `${entries.length} files`;
  if (!window.confirm(`Add ${label} to Specification (${PRD_NAME})? Existing files with the same name will be overwritten.`)) return;
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
  const newName = window.prompt(`Rename "${node.name}" to:`, node.name);
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
  if (!window.confirm(`Delete the ${kind} "${node.name}"? This cannot be undone.`)) return;
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
      renderSidebar();
      if (!$("clarifyOverviewPane").classList.contains("hidden")) renderClarifyOverview();
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
      renderSidebar();
      if (!$("clarifyOverviewPane").classList.contains("hidden")) renderClarifyOverview();
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
  showPane("home");
}
</script>
</body>
</html>
"""
