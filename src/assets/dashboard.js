
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
