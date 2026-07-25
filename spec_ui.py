"""Local web UI for browsing and editing the specification folder.

Serves a Windows-Explorer-style page on 127.0.0.1: a collapsible file/folder tree
of the specs directory (recursed into subfolders) on the left, and a markdown
viewer/editor on the right. Clicking a file loads it; the user can switch between
a rendered-markdown *view* mode and a raw-text *edit* mode, and save changes back
to disk. Unlike clarify_ui (a one-shot form), this server stays up until the user
stops it with Ctrl+C, because browsing is an open-ended activity.

All file access is confined to the specs directory: every requested path is
resolved and checked to be inside it before any read/write, so the browser cannot
escape the folder via `..` or absolute paths.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Files that can be opened as text. Anything else is treated as binary and shown
# with a "not viewable" placeholder rather than mangled into the editor.
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
    front-end sends back on read/save). Unreadable directories are skipped."""

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
    tree["name"] = root.name  # top node shows the specs folder's own name
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


class _SpecHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Injected on the server instance by run_spec_ui:
    #   server.root_dir  -> Path of the specs folder
    #   server.page_html -> str of the index page

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
            self._send_json(200, {"ok": True, "tree": build_tree(self.server.root_dir)})
        elif route == "/api/file":
            self._handle_file(parse_qs(parsed.query))
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _handle_file(self, query: dict) -> None:
        rel = (query.get("path", [""])[0])
        target = _resolve_within(self.server.root_dir, rel)
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

    # -- POST ---------------------------------------------------------------
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/save":
            self._send(404, "text/plain; charset=utf-8", b"Not found")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"ok": False, "error": "Malformed request."})
            return
        rel = payload.get("path", "")
        content = payload.get("content", "")
        if not isinstance(content, str):
            self._send_json(400, {"ok": False, "error": "Content must be text."})
            return
        target = _resolve_within(self.server.root_dir, rel)
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
            # Normalize to '\n' so re-saving from the browser (which uses '\n' in
            # the textarea) doesn't sprinkle stray '\r' into the file.
            target.write_text(content.replace("\r\n", "\n").replace("\r", "\n"),
                              encoding="utf-8", newline="\n")
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"Could not save file: {e}"})
            return
        print(f"[saved] {rel}")
        self._send_json(200, {"ok": True, "path": rel})


def run_spec_ui(specs_dir: Path) -> None:
    """Serve the spec explorer for `specs_dir` on a random 127.0.0.1 port, open it
    in the default browser, and block until interrupted with Ctrl+C."""
    specs_dir = specs_dir.resolve()
    tree = build_tree(specs_dir)
    page_html = _render_page(specs_dir, tree)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _SpecHandler)
    server.root_dir = specs_dir
    server.page_html = page_html

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"Spec explorer: {url}")
    print(f"Serving folder: {specs_dir}")
    print("Press Ctrl+C to stop.")
    if not webbrowser.open(url):
        print("Could not open a browser automatically -- open the URL above manually.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


def _render_page(specs_dir: Path, tree: dict) -> str:
    """Build the single-page app. The tree is embedded as JSON for an instant first
    paint; the client can re-fetch it from /api/tree via the Refresh button."""
    tree_json = json.dumps(tree, ensure_ascii=False)
    folder_name = json.dumps(specs_dir.name, ensure_ascii=False)
    return _PAGE_TEMPLATE.replace("/*__TREE__*/null", tree_json) \
                         .replace("/*__ROOT_NAME__*/null", folder_name)


# The page is a single self-contained document: no external CSS/JS/fonts, and a
# small hand-written markdown renderer in JS (below) so it works fully offline.
_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spec Explorer</title>
<style>
  :root {
    --bg: #f3f3f3; --panel: #ffffff; --border: #e2e2e6; --border-strong: #cfcfd6;
    --text: #1f2328; --muted: #6b7280; --accent: #2563eb; --accent-soft: #e8f0fe;
    --hover: #eef1f5; --sel: #dbe7ff; --sel-text: #16418f;
    --code-bg: #f5f6f8; --danger: #b91c1c; --ok: #15803d;
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
  .sidebar { width: 300px; min-width: 160px; max-width: 70vw; display: flex;
    flex-direction: column; background: var(--panel); border-right: 1px solid var(--border); }
  .sidebar-head { display: flex; align-items: center; gap: 8px; padding: 10px 12px;
    border-bottom: 1px solid var(--border); font-weight: 600; }
  .sidebar-head .folder { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sidebar-head button { font-size: 12px; }
  .tree { flex: 1; overflow: auto; padding: 6px 4px 16px; }

  .row { display: flex; align-items: center; gap: 4px; padding: 3px 6px; border-radius: 5px;
    cursor: pointer; white-space: nowrap; user-select: none; }
  .row:hover { background: var(--hover); }
  .row.selected { background: var(--sel); color: var(--sel-text); }
  .row .twist { width: 14px; text-align: center; color: var(--muted); flex: none;
    font-size: 10px; transition: transform .12s ease; }
  .row .twist.hidden { visibility: hidden; }
  .row .icon { flex: none; width: 18px; text-align: center; }
  .row .label { overflow: hidden; text-overflow: ellipsis; }
  .children { display: none; }
  .node.open > .children { display: block; }
  .node.open > .row > .twist { transform: rotate(90deg); }

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
  .viewer, .editor, .placeholder { position: absolute; inset: 0; overflow: auto; }
  .viewer { padding: 24px 32px; background: var(--bg); }
  .editor { width: 100%; height: 100%; border: 0; resize: none; padding: 18px 22px;
    font: 13px/1.6 "Cascadia Code", "Consolas", ui-monospace, monospace;
    color: var(--text); background: var(--panel); outline: none; tab-size: 4; }
  .placeholder { display: flex; align-items: center; justify-content: center;
    color: var(--muted); text-align: center; padding: 40px; }
  .hidden { display: none !important; }

  /* ---- rendered markdown ---- */
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
      <span class="icon">📁</span>
      <span class="folder" id="rootName">specs</span>
      <button id="refreshBtn" title="Rescan the folder">Refresh</button>
    </div>
    <div class="tree" id="tree"></div>
  </aside>
  <div class="splitter" id="splitter"></div>
  <main class="main">
    <div class="toolbar">
      <span class="filepath" id="filepath">Select a file from the left to open it.</span>
      <div class="seg">
        <button id="viewBtn" class="active" disabled>View</button>
        <button id="editBtn" disabled>Edit</button>
      </div>
      <button id="saveBtn" class="primary" disabled>Save</button>
    </div>
    <div class="content">
      <div id="viewer" class="viewer markdown-body"></div>
      <textarea id="editor" class="editor hidden" spellcheck="false"></textarea>
      <div id="placeholder" class="placeholder">Nothing open yet — pick a file on the left.</div>
    </div>
  </main>
</div>
<div class="toast" id="toast"></div>

<script>
"use strict";
const INITIAL_TREE = /*__TREE__*/null;
const ROOT_NAME = /*__ROOT_NAME__*/null;

// ---------------------------------------------------------------------------
// Minimal, dependency-free Markdown renderer (offline-safe).
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inlineMd(src) {
  const codes = [];
  // Pull code spans out first so their contents aren't touched by other rules.
  src = src.replace(/`([^`]+?)`/g, (m, c) => {
    codes.push("<code>" + escapeHtml(c) + "</code>");
    return "\uE000" + (codes.length - 1) + "\uE001";
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
  src = src.replace(/\uE000(\d+)\uE001/g, (m, i) => codes[+i]);
  return src;
}
function isItem(line) { return line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/); }

function buildList(items) {
  let idx = 0;
  // Emit every sibling list at `indent`. Consecutive items of the same marker
  // kind share one <ul>/<ol>; a switch between bullet and number (or a deeper
  // block returning) starts a fresh list, so "- a / 1. b" render as two lists.
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
    // fenced code block
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
    // heading
    const hm = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (hm) { out.push(`<h${hm[1].length}>` + inlineMd(hm[2]) + `</h${hm[1].length}>`); i++; continue; }
    // horizontal rule
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    // blockquote
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < n && /^\s*>/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push("<blockquote>" + renderMarkdown(buf.join("\n")) + "</blockquote>");
      continue;
    }
    // table (header row + separator row)
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
    // list
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
    // paragraph
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
const treeEl = $("tree"), viewer = $("viewer"), editor = $("editor"),
  placeholder = $("placeholder"), filepathEl = $("filepath"),
  viewBtn = $("viewBtn"), editBtn = $("editBtn"), saveBtn = $("saveBtn");

const state = {
  tree: INITIAL_TREE,
  current: null,        // relative path of open file
  isMarkdown: false,
  isText: false,
  mode: "view",         // "view" | "edit"
  dirty: false,
  expanded: new Set([""]),  // relative paths of open folders (root always open)
};

// ---------------------------------------------------------------------------
// Tree rendering
// ---------------------------------------------------------------------------
function iconFor(node) {
  if (node.type === "dir") return "📁";
  if (node.markdown) return "📝";
  if (node.text) return "📄";
  return "🔒";
}
function renderTree() {
  $("rootName").textContent = ROOT_NAME || state.tree.name || "specs";
  treeEl.innerHTML = "";
  // Render the root's children directly (the root folder itself is the header).
  for (const child of state.tree.children || []) treeEl.appendChild(renderNode(child, 0));
  if (!(state.tree.children || []).length) {
    const empty = document.createElement("div");
    empty.className = "placeholder";
    empty.style.padding = "20px";
    empty.textContent = "This specs folder is empty.";
    treeEl.appendChild(empty);
  }
}
function renderNode(node, depth) {
  const wrap = document.createElement("div");
  wrap.className = "node";
  const isDir = node.type === "dir";
  if (isDir && state.expanded.has(node.path)) wrap.classList.add("open");

  const row = document.createElement("div");
  row.className = "row";
  row.style.paddingLeft = (6 + depth * 15) + "px";
  if (!isDir && node.path === state.current) row.classList.add("selected");

  const twist = document.createElement("span");
  twist.className = "twist" + (isDir ? "" : " hidden");
  twist.textContent = "▶";
  row.appendChild(twist);

  const icon = document.createElement("span");
  icon.className = "icon";
  icon.textContent = iconFor(node);
  row.appendChild(icon);

  const label = document.createElement("span");
  label.className = "label";
  label.textContent = node.name;
  row.appendChild(label);
  wrap.appendChild(row);

  if (isDir) {
    const children = document.createElement("div");
    children.className = "children";
    for (const child of node.children || []) children.appendChild(renderNode(child, depth + 1));
    wrap.appendChild(children);
    row.addEventListener("click", () => {
      if (state.expanded.has(node.path)) state.expanded.delete(node.path);
      else state.expanded.add(node.path);
      wrap.classList.toggle("open");
    });
  } else {
    row.addEventListener("click", () => openFile(node));
  }
  return wrap;
}

// ---------------------------------------------------------------------------
// File open / mode / save
// ---------------------------------------------------------------------------
async function openFile(node) {
  if (!(await confirmDiscardIfDirty())) return;
  try {
    const res = await fetch("/api/file?path=" + encodeURIComponent(node.path));
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not open file.", true); return; }
    state.current = data.path;
    state.isMarkdown = data.markdown;
    state.isText = data.text;
    state.dirty = false;
    editor.value = data.content || "";
    placeholder.classList.add("hidden");
    if (!data.text) {
      // Non-text file: show the reason, disable editing.
      viewer.innerHTML = "";
      placeholder.textContent = data.reason || "This file can't be shown as text.";
      placeholder.classList.remove("hidden");
      viewer.classList.add("hidden");
      editor.classList.add("hidden");
      viewBtn.disabled = editBtn.disabled = saveBtn.disabled = true;
    } else {
      viewBtn.disabled = editBtn.disabled = false;
      setMode("view");
    }
    updateHeader();
    // Reflect selection highlight without a full rescan.
    renderTree();
  } catch (e) {
    toast("Network error opening file.", true);
  }
}

function renderViewer() {
  const text = editor.value;
  viewer.innerHTML = state.isMarkdown
    ? renderMarkdown(text)
    : "<pre><code>" + escapeHtml(text) + "</code></pre>";
}

function setMode(mode) {
  if (!state.isText) return;
  state.mode = mode;
  const viewing = mode === "view";
  viewBtn.classList.toggle("active", viewing);
  editBtn.classList.toggle("active", !viewing);
  if (viewing) {
    renderViewer();
    viewer.classList.remove("hidden");
    editor.classList.add("hidden");
  } else {
    viewer.classList.add("hidden");
    editor.classList.remove("hidden");
    editor.focus();
  }
}

function updateHeader() {
  saveBtn.disabled = !state.dirty || !state.isText;
  if (!state.current) { filepathEl.textContent = "Select a file from the left to open it."; return; }
  filepathEl.textContent = "";
  const label = document.createTextNode(ROOT_NAME + "/" + state.current);
  filepathEl.appendChild(label);
  if (state.dirty) {
    const dot = document.createElement("span");
    dot.className = "dirty";
    dot.textContent = "● unsaved";
    filepathEl.appendChild(dot);
  }
}

async function saveFile() {
  if (!state.current || !state.dirty || !state.isText) return;
  saveBtn.disabled = true;
  try {
    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.current, content: editor.value }),
    });
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Save failed.", true); updateHeader(); return; }
    state.dirty = false;
    updateHeader();
    if (state.mode === "view") renderViewer();
    toast("Saved " + state.current);
  } catch (e) {
    toast("Network error while saving.", true);
    updateHeader();
  }
}

function confirmDiscardIfDirty() {
  if (!state.dirty) return Promise.resolve(true);
  return Promise.resolve(window.confirm(
    "You have unsaved changes in \"" + state.current + "\".\nDiscard them and continue?"));
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
editor.addEventListener("input", () => {
  if (!state.dirty) { state.dirty = true; updateHeader(); }
});
viewBtn.addEventListener("click", () => setMode("view"));
editBtn.addEventListener("click", () => setMode("edit"));
saveBtn.addEventListener("click", saveFile);

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    saveFile();
  }
});
window.addEventListener("beforeunload", (e) => {
  if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
});

$("refreshBtn").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/tree");
    const data = await res.json();
    if (data.ok) { state.tree = data.tree; renderTree(); toast("Folder rescanned."); }
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
    const w = Math.max(160, Math.min(e.clientX, window.innerWidth * 0.7));
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
renderTree();
</script>
</body>
</html>
"""
