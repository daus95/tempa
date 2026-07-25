"""Local, single-use web UI for answering `clarify` findings.

Parses the `<!-- clarify:item ... -->` blocks written by prompt/clarification.md,
renders them as an interactive page (recommendation vs. own-answer per finding),
and on save/cancel rewrites the "Your answer:" section of the source file(s) in
place. Serves on 127.0.0.1 only, for the duration of a single answer session —
the HTTP server shuts itself down as soon as the user saves or cancels.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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


@dataclass
class ClarificationItem:
    key: str            # unique across the whole answer session (namespaced by file index)
    raw_id: str         # id as written in the markdown, e.g. "C1"
    severity: str        # critical | major | minor
    title: str
    where: str
    question: str
    recommendation: str
    existing_answer: str
    file: Path
    answer_start: int    # absolute offsets in the file's text, spanning what to replace on save
    answer_end: int
    has_markers: bool    # whether clarify:answer-start/end markers were present


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
            # Wrapped as a clarify:item but missing a "Your answer:" label — show it
            # read-only rather than silently dropping the finding from the page.
            blocks.append(("text", m.group(0)))
        pos = m.end()
    if pos < len(text):
        tail = text[pos:]
        if tail.strip():
            blocks.append(("text", tail))
    return items, blocks


# --- tiny markdown -> HTML (bold/code/italic/headings/bullet lists only) ---

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


SEVERITY_LABELS = {"critical": "Critical", "major": "Major", "minor": "Minor"}


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


PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TEMPA_TITLE__ — Tempa clarification answers</title>
<style>
:root {
  --bg: #f7f7f8; --fg: #1c1c1f; --card: #ffffff; --border: #e3e3e6; --muted: #6b6b74;
  --accent: #2563eb; --critical: #dc2626; --major: #d97706; --minor: #2563eb;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #16171a; --fg: #e8e8ea; --card: #1e1f23; --border: #313236; --muted: #97979f; }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 0 90px 0; background: var(--bg); color: var(--fg);
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
header.top {
  padding: 24px clamp(16px, 4vw, 40px) 12px; max-width: 860px; margin: 0 auto;
}
header.top h1 { font-size: 1.25rem; margin: 0 0 4px; }
header.top .summary { color: var(--muted); font-size: 0.9rem; }
main { max-width: 860px; margin: 0 auto; padding: 0 clamp(16px, 4vw, 40px); }
.doc-text { color: var(--fg); margin: 8px 0 20px; }
.doc-text p { margin: 0.6em 0; }
.item {
  background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--muted);
  border-radius: 8px; padding: 16px 18px; margin: 16px 0;
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
.field.recommendation { background: rgba(37, 99, 235, 0.07); border-radius: 6px; padding: 8px 10px; }
.answer-block { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
.selector { display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 8px; font-size: 0.92rem; }
.selector label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
textarea {
  width: 100%; min-height: 6.5em; font: inherit; padding: 8px 10px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--bg); color: var(--fg); resize: vertical;
}
textarea:disabled { opacity: 0.55; resize: none; }
.actions {
  position: fixed; left: 0; right: 0; bottom: 0; background: var(--card);
  border-top: 1px solid var(--border); padding: 14px clamp(16px, 4vw, 40px);
  display: flex; gap: 12px; justify-content: flex-end; align-items: center;
}
.actions .hint { margin-right: auto; color: var(--muted); font-size: 0.85rem; }
button {
  font: inherit; padding: 9px 18px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; background: var(--bg); color: var(--fg);
}
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button:disabled { opacity: 0.6; cursor: default; }
.done { max-width: 640px; margin: 20vh auto; text-align: center; font-size: 1.1rem; padding: 0 20px; }
</style>
</head>
<body>
<header class="top">
  <h1>__TEMPA_TITLE__</h1>
  <div class="summary">__TEMPA_SUMMARY__</div>
</header>
<main>
__TEMPA_BODY__
</main>
<div class="actions">
  <span class="hint">Choose recommendation or your own answer for each finding, then save.</span>
  <button id="cancel-btn">Cancel</button>
  <button id="save-btn" class="primary">Save answers</button>
</div>
<script>
function onModeChange(radio) {
  var item = radio.closest('.item');
  var ta = item.querySelector('textarea');
  var own = item.querySelector('input[value="own"]').checked;
  ta.disabled = !own;
  if (own) ta.focus();
}
document.querySelectorAll('.item input[type=radio]').forEach(function (r) {
  r.addEventListener('change', function () { onModeChange(r); });
});

function collect() {
  var items = [];
  document.querySelectorAll('.item').forEach(function (sec) {
    var key = sec.dataset.key;
    var checked = sec.querySelector('input[type=radio]:checked');
    var mode = checked ? checked.value : 'own';
    var ta = sec.querySelector('textarea');
    items.push({ id: key, mode: mode, answer: ta.value });
  });
  return items;
}

function setBusy(b) {
  document.getElementById('save-btn').disabled = b;
  document.getElementById('cancel-btn').disabled = b;
}

function showDone(msg) {
  document.body.innerHTML = '<div class="done">' + msg + '</div>';
  setTimeout(function () { try { window.close(); } catch (e) {} }, 600);
}

document.getElementById('save-btn').addEventListener('click', function () {
  var items = collect();
  var own = items.filter(function (i) { return i.mode === 'own'; });
  var missing = own.filter(function (i) { return !i.answer.trim(); });
  if (missing.length) {
    alert('Please fill in your own answer for ' + missing.length + ' finding(s), or switch them back to "Follow the recommendation".');
    return;
  }
  var recCount = items.length - own.length;
  var msg = 'Save answers for ' + items.length + ' finding(s)?\\n\\n' +
    '- ' + recCount + ' following the recommendation\\n' +
    '- ' + own.length + ' with your own answer\\n\\n' +
    'This will update the clarification file on disk.';
  if (!confirm(msg)) return;
  setBusy(true);
  fetch('/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(items) })
    .then(function (res) { if (!res.ok) throw new Error('save failed'); })
    .then(function () { showDone('Answers recorded. You can close this tab now \\u2014 the command-line tool has moved on.'); })
    .catch(function (e) { setBusy(false); alert('Failed to save: ' + e); });
});

document.getElementById('cancel-btn').addEventListener('click', function () {
  setBusy(true);
  fetch('/cancel', { method: 'POST' }).catch(function () {}).then(function () {
    showDone('Cancelled \\u2014 no changes were made. You can close this tab now.');
  });
});
</script>
</body>
</html>
"""


def _render_page(file_blocks: list[tuple[Path, list[tuple[str, object]]]], all_items: list[ClarificationItem]) -> str:
    counts = {"critical": 0, "major": 0, "minor": 0}
    for it in all_items:
        counts[it.severity] += 1
    summary = (
        f"{len(all_items)} finding(s) — {counts['critical']} critical · "
        f"{counts['major']} major · {counts['minor']} minor"
    )
    title = ", ".join(p.name for p, _ in file_blocks)

    body_parts: list[str] = []
    multi = len(file_blocks) > 1
    for path, blocks in file_blocks:
        if multi:
            body_parts.append(f'<h2 class="file-heading">{html_lib.escape(path.name)}</h2>')
        for kind, payload in blocks:
            if kind == "text":
                rendered = render_markdown(payload)  # type: ignore[arg-type]
                if rendered:
                    body_parts.append(f'<div class="doc-text">{rendered}</div>')
            else:
                body_parts.append(_render_item_html(payload))  # type: ignore[arg-type]

    html_out = PAGE_TEMPLATE
    html_out = html_out.replace("__TEMPA_TITLE__", html_lib.escape(title) if title else "Clarification answers")
    html_out = html_out.replace("__TEMPA_SUMMARY__", html_lib.escape(summary))
    html_out = html_out.replace("__TEMPA_BODY__", "\n".join(body_parts))
    return html_out


def _apply_answers(
    items_by_key: dict[str, ClarificationItem],
    files_text: dict[Path, str],
    payload: list[dict],
) -> list[Path]:
    edits: dict[Path, list[tuple[int, int, str]]] = {}
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
        edits.setdefault(item.file, []).append((item.answer_start, item.answer_end, replacement))

    changed: list[Path] = []
    for path, spans in edits.items():
        text = files_text[path]
        for start, end, replacement in sorted(spans, key=lambda s: s[0], reverse=True):
            text = text[:start] + replacement + text[end:]
        path.write_text(text, encoding="utf-8")
        changed.append(path)
    return changed


class _AnswerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # silence default per-request logging
        pass

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", ""):
            self._send(200, "text/html; charset=utf-8", self.server.page_html.encode("utf-8"))
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path == "/save":
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else []
            except json.JSONDecodeError:
                self._send(400, "application/json", b'{"ok": false}')
                return
            self.server.result = ("save", payload)
            self._send(200, "application/json", b'{"ok": true}')
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        elif self.path == "/cancel":
            self.server.result = ("cancel", None)
            self._send(200, "application/json", b'{"ok": true}')
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")


def run_answer_ui(paths: list[Path]) -> bool:
    """Parse the given clarification file(s), serve an interactive answer page on
    127.0.0.1, open it in the default browser, and block until the user saves or
    cancels (or interrupts with Ctrl+C). Writes answers back into the same file(s).
    Returns True iff the user saved and at least one file was actually updated —
    the caller uses this to decide whether to run `clarify --apply` next."""
    all_items: list[ClarificationItem] = []
    files_text: dict[Path, str] = {}
    file_blocks: list[tuple[Path, list[tuple[str, object]]]] = []

    for idx, path in enumerate(paths):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"Could not read {path}: {e}")
            continue
        files_text[path] = text
        items, blocks = parse_file(path, text, idx)
        all_items.extend(items)
        file_blocks.append((path, blocks))

    if not all_items:
        print("No recognized clarification items found -- nothing to show in the answer UI.")
        print('(Looking for "<!-- clarify:item id=... severity=... -->" markers. If this file')
        print(" predates the answer UI, or was hand-edited without them, re-run")
        print(" `py tempa.py clarify` to regenerate it, or answer/apply it manually.)")
        return False

    items_by_key = {item.key: item for item in all_items}
    page_html = _render_page(file_blocks, all_items)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _AnswerHandler)
    server.page_html = page_html
    server.result = None

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"Clarification answer UI: {url}")
    if not webbrowser.open(url):
        print("Could not open a browser automatically -- open the URL above manually.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nInterrupted -- no changes were made.")
        print(f"Re-open this file's answer UI anytime with: py tempa.py answer {paths[0]}")
        server.result = None
    finally:
        server.server_close()

    action, payload = server.result or (None, None)
    if action == "save":
        changed = _apply_answers(items_by_key, files_text, payload or [])
        if changed:
            print("[OK] Answers recorded:")
            for p in changed:
                print(f"  {p}")
            return True
        print("No answers were recorded (nothing matched).")
        return False
    elif action == "cancel":
        print("Cancelled -- no changes were made.")
    return False
