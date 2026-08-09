
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
const INITIAL_IMPLEMENT_READINESS = /*__IMPLEMENT_READINESS__*/null;
const INITIAL_PRINCIPLES_SET = /*__PRINCIPLES_SET__*/null;
const INITIAL_BACKENDS_STATUS = /*__BACKENDS_STATUS__*/null;
const INITIAL_SKIP_MINOR_FINDINGS = /*__SKIP_MINOR_FINDINGS__*/null;

// ---------------------------------------------------------------------------
// Minimal, dependency-free Markdown renderer for the Specification pane (offline-safe).
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
// Renders one of the <symbol id="i-*"> icons defined in the sprite at the top of
// dashboard.html as an inline <svg><use> — the Lucide-icon equivalent of the emoji
// strings this app used to interpolate directly into innerHTML/textContent.
function iconSvg(name, extraClass) {
  return `<svg class="icon-svg${extraClass ? " " + extraClass : ""}"><use href="#i-${name}"></use></svg>`;
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
const treeEl = $("tree"), treeBottomEl = $("treeBottom"), specViewer = $("specViewer"), specEditor = $("specEditor"),
  toolbarEl = $("toolbar"), filepathEl = $("filepath"), specSeg = $("specSeg"),
  viewBtn = $("viewBtn"), editBtn = $("editBtn"), saveBtn = $("saveBtn"), followAllBtn = $("followAllBtn"),
  clarifySummary = $("clarifySummary"), clarifyBody = $("clarifyBody"),
  clarifyUnansweredTbody = $("clarifyUnansweredTbody"), clarifyAnsweredTbody = $("clarifyAnsweredTbody"),
  specFileCountEl = $("specFileCount"),
  addFileBtn = $("addFileBtn"), addFolderBtn = $("addFolderBtn"),
  addFileInput = $("addFileInput"), addFolderInput = $("addFolderInput"),
  startClarifyBtn = $("startClarifyBtn"), finalizeClarifyBtn = $("finalizeClarifyBtn"),
  stopFinalizeClarifyBtn = $("stopFinalizeClarifyBtn"),
  openUnansweredBtn = $("openUnansweredBtn"),
  applyAnswersBtn = $("applyAnswersBtn"), finalizeGateList = $("finalizeGateList"),
  finalizeGateHint = $("finalizeGateHint"), clarifyRoundBadge = $("clarifyRoundBadge"),
  finalizeRoundProgress = $("finalizeRoundProgress"),
  skipMinorFindingsToggle = $("skipMinorFindingsToggle"),
  homeClarifyRoundBadge = $("homeClarifyRoundBadge"),
  implementReadyBanner = $("implementReadyBanner"), implementReadyBannerText = $("implementReadyBannerText"),
  clarifyStartImplementBtn = $("clarifyStartImplementBtn"),
  clarifyLogPanel = $("clarifyLogPanel"), clarifyLogBody = $("clarifyLogBody"),
  clarifyLogStatus = $("clarifyLogStatus"),
  homeNotInit = $("homeNotInit"), homeSteps = $("homeSteps"),
  homeSelectFolderBtn = $("homeSelectFolderBtn"), homeWorkspacePath = $("homeWorkspacePath"),
  homeWorkspaceCloseBtn = $("homeWorkspaceCloseBtn"),
  homeBackendStatusList = $("homeBackendStatusList"), settingsBackendStatusList = $("settingsBackendStatusList"),
  settingsDetectBackendsBtn = $("settingsDetectBackendsBtn"),
  homeStep1 = $("homeStep1"), homeStep2 = $("homeStep2"), homeStep3 = $("homeStep3"),
  homeStep1Status = $("homeStep1Status"), homeStep2Status = $("homeStep2Status"), homeStep3Status = $("homeStep3Status"),
  homeStep2FileList = $("homeStep2FileList"),
  homeAddFileBtn = $("homeAddFileBtn"), homeAddFolderBtn = $("homeAddFolderBtn"),
  homeStartClarifyBtn = $("homeStartClarifyBtn"), homeFinalizeClarifyBtn = $("homeFinalizeClarifyBtn"),
  homeOpenUnansweredBtn = $("homeOpenUnansweredBtn"), homeApplyAnswersBtn = $("homeApplyAnswersBtn"),
  homeStartImplementBtn = $("homeStartImplementBtn"), homeClearAllBtn = $("homeClearAllBtn"),
  startImplementBtn = $("startImplementBtn"), stopImplementBtn = $("stopImplementBtn"),
  implHeaderStatus = $("implHeaderStatus"), implGateList = $("implGateList"),
  implTabStatusBtn = $("implTabStatusBtn"), implTabLogBtn = $("implTabLogBtn"),
  implStatusPanel = $("implStatusPanel"), implLogPanel = $("implLogPanel"),
  implStatusBody = $("implStatusBody"), implLogBody = $("implLogBody"),
  modalOverlay = $("modalOverlay"), modalBox = $("modalBox"),
  modalTitle = $("modalTitle"), modalMessage = $("modalMessage"),
  modalInput = $("modalInput"), modalCancelBtn = $("modalCancelBtn"), modalOkBtn = $("modalOkBtn"),
  modalExtraBtn = $("modalExtraBtn"),
  logFileModalOverlay = $("logFileModalOverlay"), logFileModalBox = $("logFileModalBox"),
  logFileModalTitle = $("logFileModalTitle"), logFileModalBody = $("logFileModalBody"),
  logFileModalFullscreenBtn = $("logFileModalFullscreenBtn"), logFileModalCloseBtn = $("logFileModalCloseBtn"),
  settingsModelClarify = $("settingsModelClarify"), settingsModelClarifyApply = $("settingsModelClarifyApply"),
  settingsModelPlan = $("settingsModelPlan"),
  settingsModelImplement = $("settingsModelImplement"),
  settingsBackendClarify = $("settingsBackendClarify"), settingsBackendClarifyApply = $("settingsBackendClarifyApply"),
  settingsBackendPlan = $("settingsBackendPlan"),
  settingsBackendImplement = $("settingsBackendImplement"),
  modelSuggestionsClarify = $("modelSuggestionsClarify"), modelSuggestionsClarifyApply = $("modelSuggestionsClarifyApply"),
  modelSuggestionsPlan = $("modelSuggestionsPlan"),
  modelSuggestionsImplement = $("modelSuggestionsImplement"),
  settingsEffortClarify = $("settingsEffortClarify"), settingsEffortClarifyApply = $("settingsEffortClarifyApply"),
  settingsEffortPlan = $("settingsEffortPlan"),
  settingsEffortImplement = $("settingsEffortImplement"),
  settingsModelNoteClarify = $("settingsModelNoteClarify"), settingsModelNoteClarifyApply = $("settingsModelNoteClarifyApply"),
  settingsModelNotePlan = $("settingsModelNotePlan"),
  settingsModelNoteImplement = $("settingsModelNoteImplement"), settingsFeaturesPerSession = $("settingsFeaturesPerSession"),
  settingsMaxSessionRun = $("settingsMaxSessionRun"), settingsMaxClarificationRun = $("settingsMaxClarificationRun"),
  settingsAllowFinalizeWithCritical = $("settingsAllowFinalizeWithCritical"),
  settingsAllowFinalizeWithCriticalWarning = $("settingsAllowFinalizeWithCriticalWarning"),
  settingsImplementRequirementInputs = document.getElementsByName("settingsImplementRequirement"),
  settingsImplementRequirementWarning = $("settingsImplementRequirementWarning"),
  settingsEmailEnabled = $("settingsEmailEnabled"), settingsEmailHost = $("settingsEmailHost"),
  settingsEmailPort = $("settingsEmailPort"), settingsEmailSecurity = $("settingsEmailSecurity"),
  settingsEmailProvider = $("settingsEmailProvider"), settingsEmailProviderGuidance = $("settingsEmailProviderGuidance"),
  settingsEmailUsername = $("settingsEmailUsername"), settingsEmailPassword = $("settingsEmailPassword"),
  settingsEmailFrom = $("settingsEmailFrom"), settingsEmailRecipients = $("settingsEmailRecipients"),
  settingsEmailEventList = $("settingsEmailEventList"), settingsEmailSelectAllBtn = $("settingsEmailSelectAllBtn"),
  settingsEmailClearAllBtn = $("settingsEmailClearAllBtn"), settingsTestEmailBtn = $("settingsTestEmailBtn"),
  settingsTestEmailStatus = $("settingsTestEmailStatus"),
  settingsUsageLimitRetryWaitMin = $("settingsUsageLimitRetryWaitMin"),
  settingsUsageLimitHeartbeatMin = $("settingsUsageLimitHeartbeatMin"),
  settingsServerOverloadRetryWaitMin = $("settingsServerOverloadRetryWaitMin"),
  settingsPollIntervalSec = $("settingsPollIntervalSec"),
  settingsSaveBtn = $("settingsSaveBtn"), settingsSaveStatus = $("settingsSaveStatus"),
  settingsUpdateCurrent = $("settingsUpdateCurrent"), settingsUpdateLatest = $("settingsUpdateLatest"),
  settingsCheckUpdateBtn = $("settingsCheckUpdateBtn"), settingsUpdateBtn = $("settingsUpdateBtn"),
  settingsUpdateStatus = $("settingsUpdateStatus"),
  homePrinciplesBtn = $("homePrinciplesBtn"), homeStepPrinciplesStatus = $("homeStepPrinciplesStatus"),
  principlesEditor = $("principlesEditor"), principlesSaveBtn = $("principlesSaveBtn"),
  principlesSaveStatus = $("principlesSaveStatus");

const PANES = ["home", "spec", "specOverview", "clarify", "clarifyOverview", "impl", "settings",
  "principles"];

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
  clarifyFinalize: INITIAL_CLARIFY_FINALIZE ||
    { hasRun: false, lastAction: null, critical: 0, ready: false, round: 0, maxRound: 0,
      finalizeRound: 0, allowFinalizeWithCritical: false },
  implementReadiness: INITIAL_IMPLEMENT_READINESS ||
    { hasRun: false, critical: 0, major: 0, requirement: "no_critical_or_major", ready: false },
  principlesSet: !!INITIAL_PRINCIPLES_SET,
  backendsStatus: INITIAL_BACKENDS_STATUS || {},
  skipMinorFindings: INITIAL_SKIP_MINOR_FINDINGS ?? true,
  epics: [],
  // Server-computed (see _implementation_has_started): has any epic actually run yet?
  // Drives the Start -> Continue Implementation relabeling of all three buttons.
  implementStarted: false,
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

function showModal({ title = "Confirm", message = "", okLabel = "OK", danger = false, prompt = false,
    value = "", showCancel = true, extraLabel = "", html = false }) {
  return new Promise((resolve) => {
    modalResolve = resolve;
    modalIsPrompt = prompt;
    modalTitle.textContent = title;
    modalBox.classList.toggle("wide", html);
    if (html) {
      // Only ever called with strings this file builds itself (see
      // clarifyRowDetailHtml) — never with unescaped user/observed input.
      modalMessage.innerHTML = message;
    } else {
      modalMessage.innerHTML = "";
      String(message).split("\n").forEach((line, i) => {
        if (i > 0) modalMessage.appendChild(document.createElement("br"));
        modalMessage.appendChild(document.createTextNode(line));
      });
    }
    modalOkBtn.textContent = okLabel;
    modalOkBtn.classList.toggle("danger", danger);
    modalCancelBtn.classList.toggle("hidden", !showCancel);
    modalExtraBtn.textContent = extraLabel;
    modalExtraBtn.classList.toggle("hidden", !extraLabel);
    modalInput.classList.toggle("hidden", !prompt);
    modalInput.value = prompt ? value : "";
    modalOverlay.classList.remove("hidden");
    requestAnimationFrame(() => {
      if (prompt) { modalInput.focus(); modalInput.select(); } else modalOkBtn.focus();
    });
  });
}

// confirmModal resolves true/false; promptModal resolves the entered string, or null on
// cancel; alertModal is a single-button (no Cancel) notice, resolved once acknowledged.
// threeWayModal is for a Cancel/middle-choice/main-choice prompt (e.g. Cancel / Save /
// Save & Apply): resolves "cancel" (Cancel, Escape, or overlay click), extraLabel's
// choice as the string "extra", or okLabel's choice as "ok".
function confirmModal(message, opts) {
  return showModal({ message, prompt: false, ...opts });
}
function promptModal(message, value, opts) {
  return showModal({ message, prompt: true, value: value || "", ...opts }).then((v) => (v === false ? null : v));
}
function alertModal(message, opts) {
  return showModal({ message, prompt: false, showCancel: false, ...opts });
}
function threeWayModal(message, opts) {
  return showModal({ message, prompt: false, ...opts })
    .then((v) => (v === "extra" ? "extra" : v === true ? "ok" : "cancel"));
}

modalCancelBtn.addEventListener("click", () => closeModal(modalIsPrompt ? null : false));
modalExtraBtn.addEventListener("click", () => closeModal("extra"));
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
// Log file viewer modal — opened from a "log: <filename>" link in the Log tab (clarify or
// implement run) via linkifyLogFilenames/appendClarifyLogRow above. Large by design (a
// session log can run past 400KB) and toggleable to fullscreen, unlike the small
// confirm/prompt modal above.
// ---------------------------------------------------------------------------
function closeLogFileModal() {
  logFileModalOverlay.classList.add("hidden");
  logFileModalBox.classList.remove("fullscreen");
  logFileModalFullscreenBtn.innerHTML = iconSvg("maximize-2");
}

async function openLogFileModal(name) {
  logFileModalTitle.textContent = name;
  logFileModalBody.innerHTML = '<div class="log-file-modal-status">Loading…</div>';
  logFileModalOverlay.classList.remove("hidden");
  try {
    const res = await fetch("/api/log-file?name=" + encodeURIComponent(name));
    const data = await res.json();
    if (!data.ok) {
      logFileModalBody.innerHTML = `<div class="log-file-modal-status">${escapeHtml(data.error || "Could not open file.")}</div>`;
      return;
    }
    const truncatedNote = data.truncated
      ? '<div class="log-file-modal-truncated">This file is large — showing only the most recent portion.</div>'
      : "";
    logFileModalBody.innerHTML = truncatedNote + `<pre>${escapeHtml(data.content)}</pre>`;
  } catch (e) {
    logFileModalBody.innerHTML = '<div class="log-file-modal-status">Network error opening file.</div>';
  }
}

document.addEventListener("click", (e) => {
  const link = e.target.closest(".log-file-link");
  if (link) { e.preventDefault(); openLogFileModal(link.dataset.logFile); }
});
logFileModalCloseBtn.addEventListener("click", closeLogFileModal);
logFileModalFullscreenBtn.addEventListener("click", () => {
  const isFullscreen = logFileModalBox.classList.toggle("fullscreen");
  logFileModalFullscreenBtn.innerHTML = iconSvg(isFullscreen ? "minimize-2" : "maximize-2");
});
logFileModalOverlay.addEventListener("click", (e) => {
  if (e.target === logFileModalOverlay) closeLogFileModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !logFileModalOverlay.classList.contains("hidden")) closeLogFileModal();
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
      dot.className = "dirty"; dot.innerHTML = iconSvg("circle", "filled") + " unsaved";
      filepathEl.appendChild(dot);
    }
  } else if (kind === "clarify") {
    saveBtn.disabled = !state.clarifyDirty;
    filepathEl.textContent = "";
    filepathEl.appendChild(document.createTextNode("Clarification/" + state.selectedClarifyPath));
    if (state.clarifyDirty) {
      const dot = document.createElement("span");
      dot.className = "dirty"; dot.innerHTML = iconSvg("circle", "filled") + " unsaved";
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
  if (node.type === "dir") return iconSvg("folder");
  if (node.markdown) return iconSvg("file-pen-line");
  if (node.text) return iconSvg("file-text");
  return iconSvg("lock");
}

function renderSidebar() {
  treeEl.innerHTML = "";
  treeEl.appendChild(renderLeafSection("home", iconSvg("house"), "Home"));
  treeEl.appendChild(renderSpecSection());
  treeEl.appendChild(renderClarifySection());
  treeEl.appendChild(renderLeafSection("implementation", iconSvg("wrench"), "Implementation", !state.workspaceInitialized));
  treeBottomEl.innerHTML = "";
  treeBottomEl.appendChild(renderLeafSection("principles", iconSvg("ruler"), "Architecture Principles", !state.workspaceInitialized));
  treeBottomEl.appendChild(renderLeafSection("settings", iconSvg("settings"), "Settings", !state.workspaceInitialized));
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
  } else if (key === "settings") {
    renderSettings();
    showPane("settings");
  } else if (key === "principles") {
    renderPrinciples();
    showPane("principles");
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

  homeStepPrinciplesStatus.textContent = state.principlesSet
    ? "Set — applied to every clarification, plan, implementation, and QA prompt."
    : "Not set — Tempa runs without project-wide principles.";

  const specCount = countSpecFiles(state.specTree);
  const step1Done = specCount > 0;
  homeStep1Status.textContent = step1Done
    ? (specCount === 1 ? "1 specification file uploaded." : `${specCount} specification files uploaded.`)
    : "No specification files yet.";

  const step2Locked = !step1Done;
  homeStep2.classList.toggle("locked", step2Locked);
  // Just the round count so far, same as the Clarification page's clarifyRoundBadge —
  // manual clarification isn't bounded by Max Finalize Clarification Round, so pairing it
  // with that max here would misleadingly suggest a cap on rounds run outside of Finalize's
  // own loop.
  const homeFinalize = state.clarifyFinalize;
  if (homeFinalize.round > 0) {
    homeClarifyRoundBadge.textContent = `Round ${homeFinalize.round}`;
    homeClarifyRoundBadge.classList.remove("hidden");
  } else {
    homeClarifyRoundBadge.classList.add("hidden");
  }
  // Mirrors the Clarification page's own Start/Continue Clarification + Answer Findings
  // behavior (see setClarifyRunButtonsDisabled) so the two pages never disagree.
  const homeHasUnanswered = state.clarifyUnanswered.some((f) => f.total > f.answered);
  const homeHasUnapplied = state.clarifyAnswered.some((f) => !f.applied);
  const homeNeedsContinue = state.clarifyFinalize.hasRun && !state.clarifyFinalize.ready;
  const homeBlockedByAnswers = homeNeedsContinue && (homeHasUnanswered || homeHasUnapplied);
  homeStartClarifyBtn.querySelector("span:last-child").textContent =
    homeNeedsContinue ? "Continue Clarification" : "Start Clarification";
  homeStartClarifyBtn.disabled = step2Locked || state.clarifyRun.running || homeBlockedByAnswers;
  homeStartClarifyBtn.title = homeBlockedByAnswers
    ? "Answer the remaining findings or apply your saved answers first." : "";
  homeOpenUnansweredBtn.disabled = step2Locked || state.clarifyRun.running || !homeHasUnanswered;
  homeApplyAnswersBtn.disabled = step2Locked || state.clarifyRun.running || !homeHasUnapplied;
  // Not gated on state.clarifyFinalize.ready — `clarify --finalize` now resolves its
  // own pre-existing backlog (unanswered findings filled in with their recommendation,
  // then applied) before its loop starts, so it's safe to start regardless of the most
  // recent evaluate's outcome. See the matching change in renderFinalizeGate above.
  homeFinalizeClarifyBtn.disabled = step2Locked || state.clarifyRun.running;
  const allClarifyFiles = state.clarifyUnanswered.concat(state.clarifyAnswered);
  const totalFindings = allClarifyFiles.reduce((sum, f) => sum + f.total, 0);
  const unansweredFindings = allClarifyFiles.reduce((sum, f) => sum + (f.total - f.answered), 0);
  const criticalCount = state.clarifyFindings.critical;
  const criticalOverrideNote = criticalCount > 0 && state.clarifyFinalize.allowFinalizeWithCritical
    ? " Finalizing is allowed anyway via the Settings override." : "";
  homeStep2Status.textContent = step2Locked
    ? "Upload a specification first (step 1)."
    : totalFindings === 0
      ? "No clarification results yet — click Start Clarification to begin."
      : `${unansweredFindings} of ${totalFindings} finding(s) not yet answered (${criticalCount} critical).` +
        criticalOverrideNote;

  const findings = state.clarifyFindings;
  const needsClarification = !step2Locked && (findings.critical > 0 || findings.major > 0);
  const filesToAnswer = state.clarifyUnanswered.filter((f) => f.total > f.answered);
  homeStep2FileList.classList.toggle("hidden", !needsClarification || !filesToAnswer.length);
  if (needsClarification && filesToAnswer.length) {
    homeStep2FileList.innerHTML = "";
    for (const file of filesToAnswer) {
      const li = document.createElement("li");
      li.className = "home-step2-file-item";
      li.innerHTML = `<span class="home-step2-file-name">${escapeHtml(file.name)}</span>` +
        `<span class="file-status">${file.answered}/${file.total}</span>`;
      li.addEventListener("click", () => openClarifyFile(file));
      homeStep2FileList.appendChild(li);
    }
  }

  // See implementReadyMessage/implementBlockedMessage — mirrors the Clarification
  // page's ready banner and the Implementation page's own gate, all driven by the same
  // server-computed state.implementReadiness (see _implement_readiness_status in
  // dashboard_clarify_parse.py) so the three surfaces never disagree.
  const ir = state.implementReadiness;
  const step3Locked = step2Locked || !ir.ready;
  homeStep3.classList.toggle("locked", step3Locked);
  homeStartImplementBtn.disabled = step3Locked || state.implementRun.running;
  updateImplementButtonLabels();
  homeStep3Status.textContent = step2Locked
    ? "Finish step 2 first."
    : !ir.hasRun
      ? "Run clarification first (step 2)."
      : ir.ready
        ? implementReadyMessage(ir)
        : implementBlockedMessage(ir);
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

homePrinciplesBtn.addEventListener("click", () => selectTop("principles"));

homeAddFileBtn.addEventListener("click", () => { addFileInput.value = ""; addFileInput.click(); });
homeAddFolderBtn.addEventListener("click", () => { addFolderInput.value = ""; addFolderInput.click(); });

homeStartClarifyBtn.addEventListener("click", async () => {
  await selectTop("clarification");
  startClarifyRun("run");
});
homeOpenUnansweredBtn.addEventListener("click", () => {
  if (state.clarifyUnanswered.length) openClarifyFile(state.clarifyUnanswered[0]);
});
homeApplyAnswersBtn.addEventListener("click", async () => {
  await selectTop("clarification");
  startClarifyRun("apply");
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
    state.implementStarted = false;
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
  row.innerHTML = `<span class="twist">${iconSvg("chevron-right")}</span><span class="icon">${iconSvg("folder")}</span><span class="label">Specification</span>`;
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
  twist.innerHTML = iconSvg("chevron-right");
  row.appendChild(twist);

  const icon = document.createElement("span");
  icon.className = "icon";
  icon.innerHTML = specIconFor(node);
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
  row.innerHTML = `<span class="twist">${iconSvg("chevron-right")}</span><span class="icon">${iconSvg("circle-help")}</span><span class="label">Clarification</span>` +
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
  row.innerHTML = `<span class="twist hidden"></span><span class="icon">${iconSvg("file-pen-line")}</span>` +
    `<span class="label">${escapeHtml(file.name)}</span>` +
    `<span class="file-status">${file.answered}/${file.total}</span>`;
  row.addEventListener("click", () => openClarifyFile(file));
  wrap.appendChild(row);
  return wrap;
}

// ---------------------------------------------------------------------------
// Clarification overview (right panel shown when "Clarification" itself is selected)
// ---------------------------------------------------------------------------
const SEVERITY_ICON = { critical: "icon-severity-critical", major: "icon-severity-major", minor: "icon-severity-minor" };
const SEVERITY_LABEL = { critical: "Critical", major: "Major", minor: "Minor" };

// One icon per severity that actually has findings, each carrying a native-tooltip
// title (e.g. "Critical: 2/2") — replaces the old separate Critical/Major/Minor
// columns so the table has room for the Started column.
function findingsCell(file) {
  const parts = [];
  for (const sev of ["critical", "major", "minor"]) {
    const counts = file[sev];
    if (!counts || !counts.total) continue;
    const cls = counts.answered === counts.total ? "count-ok" : "count-pending";
    parts.push(
      `<span class="findings-icon" title="${SEVERITY_LABEL[sev]}: ${counts.answered}/${counts.total}">` +
        `<span class="findings-icon-glyph">${iconSvg("circle", "filled " + SEVERITY_ICON[sev])}</span>` +
        `<span class="${cls}">${counts.answered}/${counts.total}</span>` +
      `</span>`
    );
  }
  return parts.length ? parts.join(" ") : "–";
}

// dd/MM HH:mm, local time — matches the wall-clock timestamps used everywhere else in
// the app (banners, log lines). `startedAt` is epoch seconds, falsy/missing -> "–".
function formatClarifyStartedAt(startedAt) {
  if (!startedAt) return "–";
  const d = new Date(startedAt * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatClarifyDuration(seconds) {
  if (seconds == null) return "–";
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function statusCell(file) {
  return file.answered === file.total
    ? `<span class="status-complete">${iconSvg("circle-check")} Complete</span>`
    : `<span class="status-pending">${iconSvg("circle-dashed")} ${file.answered}/${file.total}</span>`;
}

function appliedCell(file) {
  return file.applied
    ? `<span class="clarify-applied-badge">${iconSvg("circle-check")} Applied</span>`
    : '<button type="button" class="clarify-apply-btn">Apply Answer</button>';
}

function renderClarifyOverviewRows(tbody, files, emptyMessage, showApplied) {
  tbody.innerHTML = "";
  const colspan = showApplied ? 5 : 4;
  if (!files.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="${colspan}" class="empty-note">${escapeHtml(emptyMessage)}</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const file of files) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(file.name)}</td>` +
      `<td>${formatClarifyStartedAt(file.started_at)}</td>` +
      `<td>${findingsCell(file)}</td>` +
      `<td>${statusCell(file)}</td>` +
      (showApplied ? `<td>${appliedCell(file)}</td>` : "");
    tr.addEventListener("click", () => openClarifyRowDetail(file));
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

// Clicking an Unanswered/Fully answered row shows this info dialog (file, started time,
// per-severity findings, status, and the clarify/apply durations recorded for it —
// see clarify_file_timings in config.json) rather than jumping straight into the
// full answer-editing view; "Open File" inside the dialog still does that.
function clarifyRowDetailRow(label, value) {
  return `<div class="clarify-detail-row"><span class="clarify-detail-label">${escapeHtml(label)}</span>` +
    `<span>${value}</span></div>`;
}

function clarifyRowDetailHtml(file) {
  const sevRow = (label, counts) => clarifyRowDetailRow(
    label, counts && counts.total ? `${counts.answered}/${counts.total} answered` : "None"
  );
  let html = "" +
    clarifyRowDetailRow("Started", formatClarifyStartedAt(file.started_at)) +
    sevRow("Critical", file.critical) +
    sevRow("Major", file.major) +
    sevRow("Minor", file.minor) +
    clarifyRowDetailRow("Status", file.answered === file.total
      ? "Complete" : `${file.answered}/${file.total} answered`);
  if ("applied" in file) html += clarifyRowDetailRow("Applied", file.applied ? "Yes" : "No");
  html += clarifyRowDetailRow("Clarification duration", formatClarifyDuration(file.clarify_seconds));
  html += clarifyRowDetailRow("Apply duration", formatClarifyDuration(file.apply_seconds));
  return html;
}

async function openClarifyRowDetail(file) {
  const openFile = await confirmModal(clarifyRowDetailHtml(file), {
    title: file.name, okLabel: "Open File", html: true,
  });
  if (openFile) openClarifyFile(file);
}

function renderClarifyOverview() {
  skipMinorFindingsToggle.checked = !!state.skipMinorFindings;
  renderClarifyOverviewRows(clarifyUnansweredTbody, state.clarifyUnanswered,
    "No unanswered files.", false);
  renderClarifyOverviewRows(clarifyAnsweredTbody, state.clarifyAnswered,
    "No fully answered files yet.", true);
  setClarifyRunButtonsDisabled(state.clarifyRun.running);
  renderImplementReadyBanner();
}

// Shared copy for state.implementReadiness (see _implement_readiness_status in
// dashboard_clarify_parse.py), used by the Home page's step 3, the Clarification
// overview's ready-for-implementation banner, and the toast shown if Start
// Implementation is somehow clicked while blocked (client-side disabling should
// normally prevent that, this is the fallback).
function implementReadyMessage(ir) {
  if (ir.requirement === "none") {
    return "No clarification-findings requirement is configured — ready to start implementation.";
  }
  if (ir.requirement === "no_critical") {
    return "No critical findings remain — ready to start implementation. Any open major/minor findings " +
      "will carry into implementation.";
  }
  return "No critical or major findings remain — ready to start implementation. Minor findings will be " +
    "resolved during implementation.";
}

function implementBlockedMessage(ir) {
  if (ir.requirement === "no_critical") {
    return `Still ${ir.critical} critical finding(s) that must be resolved.`;
  }
  return `Still ${ir.critical} critical and ${ir.major} major finding(s) that must be resolved.`;
}

function implementBlockedToast(ir) {
  if (!ir.hasRun) return "Run clarification first.";
  if (ir.requirement === "no_critical") return "There are still critical findings — resolve clarification first.";
  return "There are still critical/major findings — resolve clarification first.";
}

// Mirrors the same gate as the Home page's step 3 (see renderHomeWorkflow) — shown on
// the Clarification overview so the user doesn't have to go back to Home to notice
// they can move on to implementation. Both are driven by the same server-computed
// state.implementReadiness, which already accounts for "hasRun" (a workspace where
// clarification was never run has zero findings simply from having no clarification
// files yet — without that check this would trivially look "ready").
function renderImplementReadyBanner() {
  const ir = state.implementReadiness;
  implementReadyBanner.classList.toggle("hidden", !ir.ready);
  if (ir.ready) implementReadyBannerText.innerHTML = `<strong>${iconSvg("circle-check")} Ready for implementation.</strong> ${implementReadyMessage(ir)}`;
}

clarifyStartImplementBtn.addEventListener("click", async () => {
  await selectTop("implementation");
  startImplementRun();
});

skipMinorFindingsToggle.addEventListener("change", async () => {
  const checked = skipMinorFindingsToggle.checked;
  state.skipMinorFindings = checked;
  try {
    const res = await fetch("/api/clarify/skip-minor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skip_minor_findings: checked }),
    });
    const data = await res.json();
    if (!data.ok) {
      state.skipMinorFindings = !checked;
      skipMinorFindingsToggle.checked = !checked;
      toast(data.error || "Could not save this setting.", true);
    }
  } catch (e) {
    state.skipMinorFindings = !checked;
    skipMinorFindingsToggle.checked = !checked;
    toast("Network error while saving.", true);
  }
});

// ---------------------------------------------------------------------------
// Clarification run (Start Clarification / Finalized Clarification / Apply Answers
// + log panel)
// ---------------------------------------------------------------------------
// Shared renderer for the readiness checklists (Finalize Clarification / Start
// Implementation): items is [{ok, label}], rendered as a checked/unchecked icon list into listEl.
function renderGateChecklist(listEl, items) {
  listEl.innerHTML = items.map((it) =>
    `<li class="gate-item ${it.ok ? "ok" : "pending"}">` +
      `<span class="icon">${it.ok ? iconSvg("circle-check") : iconSvg("square")}</span><span>${escapeHtml(it.label)}</span></li>`
  ).join("");
}

// ---------------------------------------------------------------------------
// CLI backend readiness (installed + workspace-writable) — shown on Home (workspace
// info area) and Settings (AI Backend & Model), sourced from state.backendsStatus
// (see refreshSpecTree / renderSettings, populated from /api/tree and /api/config).
// ---------------------------------------------------------------------------
function backendStatusLabel(info) {
  if (!state.workspaceInitialized) return `${info.label} — no workspace open yet`;
  if (!info.installed) return `${info.label} — CLI not found on PATH`;
  if (!info.writable) return `${info.label} — workspace folder is not writable`;
  return `${info.label} — ready`;
}

function renderBackendStatus() {
  const items = Object.values(state.backendsStatus).map((info) => ({
    ok: !!(info && info.ready),
    label: backendStatusLabel(info),
  }));
  if (homeBackendStatusList) renderGateChecklist(homeBackendStatusList, items);
  if (settingsBackendStatusList) renderGateChecklist(settingsBackendStatusList, items);
}

// Lets the user re-check CLI availability on demand (e.g. right after installing/logging
// into a CLI, or fixing workspace folder permissions) without reloading the whole Settings
// form — re-populating the backend <select>s below preserves whatever's currently chosen
// (including unsaved edits), it only refreshes each option's "(not ready)" annotation.
settingsDetectBackendsBtn.addEventListener("click", async () => {
  settingsDetectBackendsBtn.disabled = true;
  try {
    const res = await fetch("/api/backends/status");
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not check CLI backends.", true); return; }
    state.backendsStatus = data.backends || {};
    renderBackendStatus();
    populateBackendSelect(settingsBackendClarify, settingsBackendClarify.value);
    populateBackendSelect(settingsBackendPlan, settingsBackendPlan.value);
    populateBackendSelect(settingsBackendImplement, settingsBackendImplement.value);
    toast("CLI backend availability refreshed.");
  } catch (e) {
    toast("Network error checking CLI backends.", true);
  } finally {
    settingsDetectBackendsBtn.disabled = false;
  }
});

// Status snapshot shown above "Finalized Clarification" — see _clarify_finalize_status()
// in dashboard_clarify_parse.py for the server-side source of truth this mirrors. This
// used to also gate whether the button could be clicked at all (requiring a fresh
// zero-critical evaluate on record); it no longer does — `clarify --finalize` now
// resolves its own pre-existing backlog first (files answered-but-not-applied get
// applied; files with unanswered findings get each one filled in with its own
// recommendation, then applied) before its evaluate/apply loop starts, so it's safe
// to start regardless of what state clarification is currently in. The checklist below
// is purely informational now: it shows what the most recent evaluate pass found, not
// a precondition.
function renderFinalizeGate(runDisabled, hasUnanswered, hasUnapplied) {
  const st = state.clarifyFinalize;
  // Just the round count so far — manual clarification isn't bounded by Max Finalize
  // Clarification Round, so pairing it with that max here would misleadingly suggest a
  // cap on rounds run outside of Finalize's own loop. The count against that max is
  // shown separately, next to the Finalized Clarification button (finalizeRoundProgress).
  if (st.round > 0) {
    clarifyRoundBadge.textContent = `Round ${st.round}`;
    clarifyRoundBadge.classList.remove("hidden");
  } else {
    clarifyRoundBadge.classList.add("hidden");
  }
  // finalizeRound is the separate counter that restarts from 0 every time a
  // Finalized Clarification run starts (see run_clarify_finalize in
  // tempa_clarify.py) — while a run is actually in progress this gets overridden
  // with fresher, per-second numbers by pollClarifyRun below; this render is only
  // what's left showing once a run finishes (or on initial page load).
  if (st.maxRound > 0) {
    finalizeRoundProgress.textContent = `${st.finalizeRound} / ${st.maxRound}`;
    finalizeRoundProgress.classList.remove("hidden");
  } else {
    finalizeRoundProgress.classList.add("hidden");
  }
  const criticalOk = st.critical === 0 || st.allowFinalizeWithCritical;
  renderGateChecklist(finalizeGateList, [
    { ok: st.hasRun, label: "Clarification has been run at least once" },
    { ok: st.lastAction === "evaluate",
      label: "Most recent result comes from Start Clarification, not just Apply Answers" },
    { ok: criticalOk,
      label: st.critical === 0
        ? "Most recent evaluation shows 0 critical findings"
        : st.allowFinalizeWithCritical
          ? `Most recent evaluation still shows ${st.critical} critical finding(s) — allowed via ` +
            "the Settings override"
          : `Most recent evaluation still shows ${st.critical} critical finding(s)` },
    { ok: true,
      label: (hasUnanswered || hasUnapplied)
        ? "Any unanswered/unapplied backlog above will be resolved automatically " +
          "(unanswered findings filled in with their own recommendation) before Finalize's loop starts"
        : "No unanswered/unapplied backlog — Finalize's loop can start right away" },
  ]);
  // Only actually-in-progress runs disable this button now — the checklist above is
  // informational, not a precondition (see the comment above this function). While a
  // finalize run specifically is in progress, swap it for Stop Finalize entirely
  // (same Start/Stop toggle Implementation already has) rather than just disabling it.
  const finalizeRunning = runDisabled && state.clarifyRun.mode === "finalize";
  finalizeClarifyBtn.disabled = runDisabled;
  finalizeClarifyBtn.classList.toggle("hidden", finalizeRunning);
  stopFinalizeClarifyBtn.classList.toggle("hidden", !finalizeRunning);

  // Once clarification has run at least once but isn't finalize-ready yet, relabel
  // Start Clarification -> Continue Clarification and explain why in plain language,
  // so users who just finished answering/applying don't get stuck wondering why
  // Finalize/Implement are still blocked.
  const needsContinue = st.hasRun && !st.ready;
  startClarifyBtn.querySelector("span:last-child").textContent =
    needsContinue ? "Continue Clarification" : "Start Clarification";
  if (!needsContinue) {
    finalizeGateHint.classList.add("hidden");
  } else if (hasUnanswered || hasUnapplied) {
    finalizeGateHint.textContent =
      "Answer the remaining findings or apply your saved answers before continuing clarification.";
    finalizeGateHint.classList.remove("hidden");
  } else if (st.critical > 0) {
    finalizeGateHint.textContent =
      `You still need to run Continue Clarification — the last evaluation showed ${st.critical} ` +
      "critical finding(s).";
    finalizeGateHint.classList.remove("hidden");
  } else {
    finalizeGateHint.textContent =
      "Run Continue Clarification once more to confirm the latest status before finalizing.";
    finalizeGateHint.classList.remove("hidden");
  }
}

function setClarifyRunButtonsDisabled(disabled) {
  const st = state.clarifyFinalize;
  const hasUnanswered = state.clarifyUnanswered.some((f) => f.total > f.answered);
  const hasUnapplied = state.clarifyAnswered.some((f) => !f.applied);
  const needsContinue = st.hasRun && !st.ready;
  const blockedByAnswers = needsContinue && (hasUnanswered || hasUnapplied);
  startClarifyBtn.disabled = disabled || blockedByAnswers;
  startClarifyBtn.title = blockedByAnswers
    ? "Answer the remaining findings or apply your saved answers first." : "";
  applyAnswersBtn.disabled = disabled || !hasUnapplied;
  openUnansweredBtn.disabled = disabled || !hasUnanswered;
  renderFinalizeGate(disabled, hasUnanswered, hasUnapplied);
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
    return { cls: "progress", icon: "loader-circle", time: m ? m[1] : "", msg: m ? m[2] : text };
  }
  const trimmed = text.trim();
  if (/^==.+==$/.test(trimmed)) {
    return { cls: "banner", icon: "megaphone", time: "", msg: trimmed.replace(/^=+\s*|\s*=+$/g, "") };
  }
  let time = "", msg = text;
  const tsMatch = text.match(/^\[\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2})\]\s?(.*)$/);
  if (tsMatch) { time = tsMatch[1]; msg = tsMatch[2]; }
  if (/^\[OK\]/i.test(msg)) return { cls: "ok", icon: "circle-check", time, msg: msg.replace(/^\[OK\]\s*/i, "") };
  if (/SUCCEEDED/.test(msg)) return { cls: "ok", icon: "circle-check", time, msg };
  if (/FAILED|ERROR|\[error\]|authentication failed/i.test(msg)) return { cls: "err", icon: "circle-x", time, msg };
  if (/^\[!\]/.test(msg)) return { cls: "warn", icon: "triangle-alert", time, msg: msg.replace(/^\[!\]\s*/, "") };
  if (/usage limit reached|reached the .* limit|overloaded/i.test(msg)) return { cls: "warn", icon: "triangle-alert", time, msg };
  return { cls: "plain", icon: "circle-dashed", time, msg };
}

// Matches the bare filenames tempa_session._run_backend_session's own banner line always
// names (e.g. "... | log: session_EPIC-17_20260809_044718.txt") — every session/QA/process
// log Tempa writes follows this <prefix>_<name>_<timestamp>.txt shape with no path
// separators, so this alone is enough to find them inside an already-escaped log message
// without matching anything else.
const LOG_FILENAME_RE = /\b((?:session|qa|process)_[\w-]+\.txt)\b/g;

function linkifyLogFilenames(escapedMsg) {
  return escapedMsg.replace(
    LOG_FILENAME_RE,
    (name) => `<a href="#" class="log-file-link" data-log-file="${name}">${name}</a>`,
  );
}

// Whether a log panel is currently scrolled to (or near) its bottom — used to decide
// whether a re-render should follow new content or leave the user's scroll-up position
// alone (a 1s poll rebuilds the whole panel, so without this a user reading earlier
// lines gets yanked back to the bottom on every tick).
function isScrolledNearBottom(el, threshold = 4) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}

function appendClarifyLogRow(text) {
  const f = formatClarifyLogLine(text);
  const row = document.createElement("div");
  row.className = "clarify-log-line " + f.cls;
  row.innerHTML =
    (f.time ? `<span class="clarify-log-time">${escapeHtml(f.time)}</span>` : "") +
    `<span class="clarify-log-icon">${iconSvg(f.icon, f.cls === "progress" ? "icon-spin" : "")}</span>` +
    `<span class="clarify-log-msg">${linkifyLogFilenames(escapeHtml(f.msg))}</span>`;
  return row;
}

function renderClarifyLog() {
  const stickToBottom = isScrolledNearBottom(clarifyLogBody);
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
  // Only follow new content to the bottom if the user was already there (or hadn't
  // scrolled) — otherwise a poll tick mid-read would yank them back down.
  if (stickToBottom) clarifyLogBody.scrollTop = clarifyLogBody.scrollHeight;
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
  if (code === 2) return `${label} stopped — usage limit reached.`;
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
    // Finalize's round counter, read fresh from config.json every poll (see
    // _handle_clarify_run_status) — ticks up live, round by round, instead of
    // waiting for the run to finish and /api/tree to pick it up.
    if (data.mode === "finalize" && data.maxRound > 0) {
      finalizeRoundProgress.textContent = `${data.finalizeRound} / ${data.maxRound}`;
      finalizeRoundProgress.classList.remove("hidden");
    }
    if (!data.running) {
      stopClarifyPolling();
      if (data.returncode !== null) toast(returncodeMessage(data.returncode, data.mode), data.returncode !== 0);
      refreshClarifyList();
    }
  } catch (e) { /* transient network hiccup — next tick retries */ }
}

function startClarifyPolling() {
  stopClarifyPolling();
  state.clarifyRun.pollTimer = setInterval(pollClarifyRun, 1000);
  pollClarifyRun();
}

async function startClarifyRun(mode) {
  if (state.clarifyRun.running) return;
  // Set before the disabled-state render below so it can already tell this is a
  // finalize run and show Stop Finalize instead of waiting for the next poll.
  state.clarifyRun.mode = mode;
  setClarifyRunButtonsDisabled(true);
  clarifyLogPanel.classList.remove("hidden");
  clarifyLogPanel.open = true;
  state.clarifyRun.lines = [];
  state.clarifyRun.progress = null;
  state.clarifyRun.nextIndex = 0;
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
    if (data.mode === "finalize" && data.maxRound > 0) {
      finalizeRoundProgress.textContent = `${data.finalizeRound} / ${data.maxRound}`;
      finalizeRoundProgress.classList.remove("hidden");
    }
    if (data.running) { clarifyLogPanel.open = true; startClarifyPolling(); }
  } catch (e) { /* ignore — buttons stay enabled */ }
}

async function stopFinalizeClarifyRun() {
  if (!(state.clarifyRun.running && state.clarifyRun.mode === "finalize")) return;
  const ok = await confirmModal("Stop the Finalized Clarification run that is currently in progress?",
    { title: "Stop Finalize", okLabel: "Stop", danger: true });
  if (!ok) return;
  stopFinalizeClarifyBtn.disabled = true;
  try {
    const res = await fetch("/api/clarify/stop", { method: "POST" });
    const data = await res.json();
    if (!data.ok) toast(data.error || "Could not stop Finalized Clarification.", true);
  } catch (e) {
    toast("Network error stopping Finalized Clarification.", true);
  } finally {
    stopFinalizeClarifyBtn.disabled = false;
  }
}

startClarifyBtn.addEventListener("click", () => startClarifyRun("run"));
finalizeClarifyBtn.addEventListener("click", () => startClarifyRun("finalize"));
stopFinalizeClarifyBtn.addEventListener("click", stopFinalizeClarifyRun);
applyAnswersBtn.addEventListener("click", () => startClarifyRun("apply"));
// With multiple unanswered files, just jump into the first one — same as clicking a
// row in the "Unanswered" table below, this is only meant to get the user started.
openUnansweredBtn.addEventListener("click", () => {
  if (state.clarifyUnanswered.length) openClarifyFile(state.clarifyUnanswered[0]);
});

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
  const name = { done: "circle-check", on_progress: "refresh-cw", pending: "square", failed: "circle-x", require_fixing: "wrench" }[status] || "circle-help";
  return iconSvg(name, status === "on_progress" ? "icon-spin" : "");
}
function featureStatusIcon(status) {
  const name = { done: "circle-check", failed: "circle-x", require_fixing: "wrench" }[status] || "square";
  return iconSvg(name);
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
    // A "failed" epic that still carries blocked_reason means the no-progress guard gave up
    // trying to fix it automatically (see _try_reorder_for_dependency in tempa_session.py) —
    // surface its own explanation right on the card instead of leaving the user to go dig
    // through the log, since that's the one thing they actually need to act on.
    const blockedReason = epic.status === "failed" && epic.blocked_reason
      ? `<div class="impl-epic-blocked-reason">⚠ Blocked — no progress across resumed sessions:<br>${escapeHtml(epic.blocked_reason).replace(/\n/g, "<br>")}</div>`
      : "";
    card.innerHTML =
      `<div class="impl-epic-header">` +
        `<span class="impl-epic-icon">${epicStatusIcon(epic.status)}</span>` +
        `<span class="impl-epic-name">${escapeHtml(epic.epic_name || "?")}</span>` +
        `<span class="impl-epic-status">${escapeHtml(epic.status || "")}</span>` +
        `<span class="impl-epic-progress">${epic.completed_features || 0}/${epic.total_features || 0} features</span>` +
        `<span class="impl-epic-lastrun">last run: ${lastRun}</span>` +
        qaTag +
      `</div>` +
      blockedReason +
      `<div class="impl-feature-list">${features}</div>`;
    implStatusBody.appendChild(card);
  }
}

function renderImplementLog() {
  const stickToBottom = isScrolledNearBottom(implLogBody);
  implLogBody.innerHTML = "";
  if (!state.implementRun.lines.length && !state.implementRun.progress) {
    implLogBody.innerHTML = '<div class="clarify-log-empty">No log output yet.</div>';
    return;
  }
  for (const text of state.implementRun.lines) implLogBody.appendChild(appendClarifyLogRow(text));
  if (state.implementRun.progress) implLogBody.appendChild(appendClarifyLogRow(state.implementRun.progress));
  // Only follow new content to the bottom if the user was already there (or hadn't
  // scrolled) — otherwise a poll tick mid-read would yank them back down.
  if (stickToBottom) implLogBody.scrollTop = implLogBody.scrollHeight;
}

// The preconditions gating "Start Implementation": clarification has run at least
// once, plus whatever the configured requirement demands of the most recent
// evaluation's critical/major findings (server-enforced too — see
// _handle_implement_run_start in dashboard_server.py, and _implement_readiness_status
// in dashboard_clarify_parse.py for the shared source of truth). A finding row is
// shown as satisfied ("ok") both when it's actually clean and when the current
// requirement doesn't care about that severity at all, so the checklist always
// reflects what's actually gating the button.
function renderImplementGate() {
  const ir = state.implementReadiness;
  const requiresCritical = ir.requirement !== "none";
  const requiresMajor = ir.requirement === "no_critical_or_major";
  renderGateChecklist(implGateList, [
    { ok: ir.hasRun, label: "Clarification has been run at least once" },
    { ok: !requiresCritical || ir.critical === 0,
      label: !requiresCritical
        ? `${ir.critical} critical finding(s) — allowed by the current requirement`
        : ir.critical === 0
          ? "No critical findings remain"
          : `${ir.critical} critical finding(s) remain` },
    { ok: !requiresMajor || ir.major === 0,
      label: !requiresMajor
        ? `${ir.major} major finding(s) — allowed by the current requirement`
        : ir.major === 0
          ? "No major findings remain"
          : `${ir.major} major finding(s) remain` },
  ]);
}

// Start -> Continue Implementation, the same relabeling the clarification buttons
// already get (see renderFinalizeGate). Once any epic has run, "Start" is misleading:
// the run resumes the existing plan where it left off rather than beginning anything.
// Applied to all three buttons that trigger the same run (Home step 3, the
// Clarification ready banner, the Implementation header) so they never disagree.
// Continuing also resets any `failed` epic back to pending first — server-side, in
// _start_implement_run — which is what the tooltip promises here.
function updateImplementButtonLabels() {
  const started = state.implementStarted;
  const label = started ? "Continue Implementation" : "Start Implementation";
  const tip = started
    ? "Resumes the existing plan. Any epic left in the failed state is reset back to " +
      "pending first (same as `tempa implement --reset-failed`)."
    : "";
  for (const btn of [homeStartImplementBtn, clarifyStartImplementBtn, startImplementBtn]) {
    btn.querySelector("span:last-child").textContent = label;
    btn.title = tip;
  }
}

function updateImplementControls() {
  startImplementBtn.disabled = state.implementRun.running || !state.implementReadiness.ready;
  stopImplementBtn.classList.toggle("hidden", !state.implementRun.running);
  implHeaderStatus.textContent = state.implementRun.running ? "Running…" : "";
  updateImplementButtonLabels();
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
    state.implementStarted = !!data.started;
    renderImplementLog();
    renderImplementStatus();
    const wasRunning = state.implementRun.running;
    state.implementRun.running = data.running;
    updateImplementControls();
    homeStartImplementBtn.disabled = data.running || !state.implementReadiness.ready;
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
  if (!state.implementReadiness.ready) {
    toast(implementBlockedToast(state.implementReadiness), true);
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
// Settings (AI backend + model + run limits, backed by config.json)
// ---------------------------------------------------------------------------
// Each backend's model field stays free text (typing any id always works), but which
// suggestions the <datalist> offers depends on the backend picked for that stage — see
// populateModelDatalist / the "change" listener wired in wireBackendModelStage below.
const MODEL_OPTIONS_BY_BACKEND = {
  claude: [
    { value: "claude-opus-5", label: "Opus 5" },
    { value: "claude-sonnet-5", label: "Sonnet 5" },
  ],
  codex: [
    { value: "gpt-5.6-sol", label: "GPT 5.6 Sol" },
    { value: "gpt-5.6-terra", label: "GPT 5.6 Terra" },
  ],
  copilot: [
    { value: "auto", label: "Auto" },
    { value: "claude-opus-5", label: "Opus 5" },
    { value: "claude-sonnet-5", label: "Sonnet 5" },
    { value: "gpt-5.6-sol", label: "GPT 5.6 Sol" },
    { value: "gpt-5.6-terra", label: "GPT 5.6 Terra" },
  ],
};

// Shown under the model field only for backends whose model access can be restricted by
// an organization admin (Copilot's model list is governed by the org's Copilot policy).
const MODEL_AVAILABILITY_NOTES = {
  copilot: "Model availability depends on your organization's GitHub Copilot administrator/policy.",
};

const BACKEND_OPTIONS = [
  { value: "claude", label: "Claude Code" },
  { value: "copilot", label: "GitHub Copilot CLI" },
  { value: "codex", label: "OpenAI Codex CLI" },
];

// Reasoning-effort catalogs — mirrors tempa_backend.py exactly (CLAUDE_EFFORT_LEVELS /
// COPILOT_EFFORT_LEVELS / CODEX_MODEL_REASONING_LEVELS / CODEX_DEFAULT_EFFORT_LEVELS). The
// server is the authoritative validator (see dashboard_server._handle_config_save) — this
// copy only drives which options the dropdown offers, same duplication precedent already
// accepted for MODEL_OPTIONS_BY_BACKEND above.
const CLAUDE_EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"];
const COPILOT_EFFORT_LEVELS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
const CODEX_UNIVERSAL_LEVELS = ["none", "minimal"];
const CODEX_MODEL_REASONING_LEVELS = {
  "gpt-5.6-sol": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh", "max", "ultra"],
  "gpt-5.6-terra": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh", "max", "ultra"],
  "gpt-5.6-luna": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh", "max"],
  "codex-auto-review": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh", "max"],
  "gpt-5.5": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh"],
  "gpt-5.4": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh"],
  "gpt-5.4-mini": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh"],
};
const CODEX_DEFAULT_EFFORT_LEVELS = [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh"];

const SMTP_PROVIDER_PRESETS = {
  gmail: {
    host: "smtp.gmail.com", port: 587, security: "starttls",
    guidance: "Use your Gmail address as the username. Enable 2-Step Verification, then <a href=\"https://myaccount.google.com/apppasswords\" target=\"_blank\" rel=\"noopener\">open Google App Passwords</a>, enter <strong>Tempa</strong> as the app name, and copy the generated 16-character password here. Your normal Google password will not work.",
  },
  office365: {
    host: "smtp.office365.com", port: 587, security: "starttls",
    guidance: "Use your Microsoft 365 email address as the username. <a href=\"https://mysignins.microsoft.com/security-info\" target=\"_blank\" rel=\"noopener\">Open Microsoft Security info</a>, choose <strong>Add method</strong> → <strong>App password</strong>, name it <strong>Tempa</strong>, then copy the generated password here. If App password is unavailable, ask your Microsoft 365 administrator to enable it and Authenticated SMTP for the mailbox.",
  },
  custom: {
    guidance: "Enter the SMTP host, port, security method, username, and password supplied by your email provider.",
  },
};

function updateSmtpProvider(applyPreset) {
  const preset = SMTP_PROVIDER_PRESETS[settingsEmailProvider.value] || SMTP_PROVIDER_PRESETS.custom;
  settingsEmailProviderGuidance.innerHTML = preset.guidance;
  if (applyPreset && preset.host) {
    settingsEmailHost.value = preset.host;
    settingsEmailPort.value = preset.port;
    settingsEmailSecurity.value = preset.security;
  }
}

const EMAIL_ALERT_EVENTS = [
  ["authentication_required", "Authentication required", "The configured AI CLI login or API key must be renewed."],
  ["implementation_failed", "Implementation failed", "An epic stopped on a non-retryable implementation failure."],
  ["implementation_auto_reordered", "Implementation auto-reordered", "An epic was stuck waiting on a not-yet-implemented epic and Tempa reordered the plan automatically — informational, no action needed unless it recurs."],
  ["plan_failed", "Planning failed", "Tempa could not generate or review the implementation plan."],
  ["session_limit_reached", "Implementation run limit reached", "An epic reached the configured anti-loop session limit."],
  ["qa_limit_reached", "QA run limit reached", "QA reached its configured limit and was skipped; review is required."],
  ["clarification_answers_required", "Clarification answers required", "Critical or major specification findings need a human decision."],
  ["clarification_limit_reached", "Clarification run limit reached", "Finalized clarification stopped with unresolved findings."],
  ["clarification_failed", "Clarification failed", "A non-retryable evaluation or apply pass stopped."],
  ["confirmation_required", "Confirmation required", "The terminal is waiting for your choice to run another clarification round."],
  ["verification_failed", "Verification failed", "An epic verification failed or did not produce its report."],
  ["backend_test_failed", "Backend test failed", "The configured AI CLI permission test did not complete."],
];

function renderEmailEventChoices(selectedEvents) {
  const selected = new Set(selectedEvents || []);
  settingsEmailEventList.innerHTML = "";
  for (const [value, name, description] of EMAIL_ALERT_EVENTS) {
    const row = document.createElement("label");
    row.className = "settings-checkbox-row";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "settingsEmailEvent";
    input.value = value;
    input.checked = selected.has(value);
    row.title = description;
    input.title = description;
    const copy = document.createElement("span");
    copy.className = "settings-checkbox-name";
    copy.textContent = name;
    row.append(input, copy);
    settingsEmailEventList.appendChild(row);
  }
}

function selectedEmailEvents() {
  return [...settingsEmailEventList.querySelectorAll('input[name="settingsEmailEvent"]:checked')].map(input => input.value);
}

function getReasoningEffortChoices(backendName, model) {
  if (backendName === "codex") {
    return CODEX_MODEL_REASONING_LEVELS[(model || "").trim().toLowerCase()] || CODEX_DEFAULT_EFFORT_LEVELS;
  }
  return backendName === "copilot" ? COPILOT_EFFORT_LEVELS : CLAUDE_EFFORT_LEVELS;
}

// Always offers "" ("(default)" — no override) plus the valid levels for backendName+model.
// If currentValue isn't among them (e.g. left over from before the model/backend changed),
// it's kept as a visibly-flagged extra option instead of being silently dropped, so the user
// notices before Save rejects it with the server's 400.
function populateEffortSelect(selectEl, backendName, model, currentValue) {
  if (!selectEl) return;
  selectEl.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = "(default)";
  selectEl.appendChild(defaultOpt);
  let found = !currentValue;
  for (const level of getReasoningEffortChoices(backendName, model)) {
    const el = document.createElement("option");
    el.value = level;
    el.textContent = level;
    if (level === currentValue) { el.selected = true; found = true; }
    selectEl.appendChild(el);
  }
  if (!found) {
    const el = document.createElement("option");
    el.value = currentValue;
    el.textContent = `⚠ ${currentValue} (not supported by this model)`;
    el.selected = true;
    selectEl.appendChild(el);
  }
}

function populateModelDatalist(datalistEl, backendName) {
  if (!datalistEl) return;
  datalistEl.innerHTML = "";
  const options = MODEL_OPTIONS_BY_BACKEND[backendName] || MODEL_OPTIONS_BY_BACKEND.claude;
  for (const opt of options) {
    const el = document.createElement("option");
    el.value = opt.value;
    el.label = opt.label;
    datalistEl.appendChild(el);
  }
}

function updateModelAvailabilityNote(noteEl, backendName) {
  if (!noteEl) return;
  const note = MODEL_AVAILABILITY_NOTES[backendName];
  noteEl.textContent = note || "";
  noteEl.classList.toggle("hidden", !note);
}

function populateBackendSelect(selectEl, currentValue) {
  selectEl.innerHTML = "";
  let found = false;
  for (const opt of BACKEND_OPTIONS) {
    const el = document.createElement("option");
    el.value = opt.value;
    const info = state.backendsStatus[opt.value];
    el.textContent = info && !info.ready ? `${opt.label} (not ready)` : opt.label;
    if (opt.value === currentValue) { el.selected = true; found = true; }
    selectEl.appendChild(el);
  }
  // Keep an unrecognized/legacy backend value usable instead of silently swapping it out.
  if (!found && currentValue) {
    const el = document.createElement("option");
    el.value = currentValue;
    el.textContent = currentValue;
    el.selected = true;
    selectEl.appendChild(el);
  }
}

// Wires a stage's backend <select> + model <input> so either one changing refreshes that
// same stage's model <datalist> suggestions/availability note and reasoning-effort options.
// Never touches the model input's current value on a backend change — it's free text,
// switching backends shouldn't clobber it — only the effort select's value can get flagged
// (by populateEffortSelect) if it's no longer valid for the new backend/model.
function wireBackendModelStage(backendSelect, modelInput, modelDatalist, modelNote, effortSelect) {
  backendSelect.addEventListener("change", () => {
    populateModelDatalist(modelDatalist, backendSelect.value);
    updateModelAvailabilityNote(modelNote, backendSelect.value);
    populateEffortSelect(effortSelect, backendSelect.value, modelInput.value, effortSelect.value);
  });
  modelInput.addEventListener("input", () => {
    populateEffortSelect(effortSelect, backendSelect.value, modelInput.value, effortSelect.value);
  });
}
wireBackendModelStage(settingsBackendClarify, settingsModelClarify, modelSuggestionsClarify, settingsModelNoteClarify, settingsEffortClarify);
wireBackendModelStage(settingsBackendClarifyApply, settingsModelClarifyApply, modelSuggestionsClarifyApply, settingsModelNoteClarifyApply, settingsEffortClarifyApply);
wireBackendModelStage(settingsBackendPlan, settingsModelPlan, modelSuggestionsPlan, settingsModelNotePlan, settingsEffortPlan);
wireBackendModelStage(settingsBackendImplement, settingsModelImplement, modelSuggestionsImplement, settingsModelNoteImplement, settingsEffortImplement);

function fillSettingsForm(config) {
  state.backendsStatus = config.backends_status || state.backendsStatus;
  renderBackendStatus();
  populateBackendSelect(settingsBackendClarify, config.backends.clarify);
  populateBackendSelect(settingsBackendClarifyApply, config.backends.clarify_apply);
  populateBackendSelect(settingsBackendPlan, config.backends.plan);
  populateBackendSelect(settingsBackendImplement, config.backends.implement);
  populateModelDatalist(modelSuggestionsClarify, config.backends.clarify);
  populateModelDatalist(modelSuggestionsClarifyApply, config.backends.clarify_apply);
  populateModelDatalist(modelSuggestionsPlan, config.backends.plan);
  populateModelDatalist(modelSuggestionsImplement, config.backends.implement);
  updateModelAvailabilityNote(settingsModelNoteClarify, config.backends.clarify);
  updateModelAvailabilityNote(settingsModelNoteClarifyApply, config.backends.clarify_apply);
  updateModelAvailabilityNote(settingsModelNotePlan, config.backends.plan);
  updateModelAvailabilityNote(settingsModelNoteImplement, config.backends.implement);
  settingsModelClarify.value = config.models.clarify;
  settingsModelClarifyApply.value = config.models.clarify_apply;
  settingsModelPlan.value = config.models.plan;
  settingsModelImplement.value = config.models.implement;
  populateEffortSelect(settingsEffortClarify, config.backends.clarify, config.models.clarify, config.reasoning_efforts.clarify);
  populateEffortSelect(settingsEffortClarifyApply, config.backends.clarify_apply, config.models.clarify_apply, config.reasoning_efforts.clarify_apply);
  populateEffortSelect(settingsEffortPlan, config.backends.plan, config.models.plan, config.reasoning_efforts.plan);
  populateEffortSelect(settingsEffortImplement, config.backends.implement, config.models.implement, config.reasoning_efforts.implement);
  settingsFeaturesPerSession.value = config.features_per_session == null ? "" : config.features_per_session;
  settingsMaxSessionRun.value = config.max_session_run == null ? "" : config.max_session_run;
  settingsMaxClarificationRun.value = config.max_clarification_run == null ? "" : config.max_clarification_run;
  settingsAllowFinalizeWithCritical.checked = !!config.allow_finalize_with_critical;
  settingsAllowFinalizeWithCriticalWarning.classList.toggle("hidden", !config.allow_finalize_with_critical);
  const requirement = config.implementation_start_requirement || "no_critical_or_major";
  for (const input of settingsImplementRequirementInputs) input.checked = input.value === requirement;
  updateImplementRequirementWarning(requirement);
  lastImplementRequirement = requirement;
  const email = (config.notifications || {}).email || {};
  settingsEmailEnabled.checked = !!email.enabled;
  settingsEmailProvider.value = email.provider || "custom";
  settingsEmailHost.value = email.smtp_host || "";
  settingsEmailPort.value = email.smtp_port || 587;
  settingsEmailSecurity.value = email.security || "starttls";
  settingsEmailUsername.value = email.smtp_username || "";
  settingsEmailPassword.value = email.smtp_password || "";
  settingsEmailFrom.value = email.from || "";
  settingsEmailRecipients.value = (email.recipients || []).join(", ");
  renderEmailEventChoices(email.events || []);
  updateSmtpProvider(false);
  settingsUsageLimitRetryWaitMin.value = Math.round((config.usage_limit_retry_wait_sec ?? 1800) / 60);
  settingsUsageLimitHeartbeatMin.value = Math.round((config.usage_limit_heartbeat_sec ?? 300) / 60);
  settingsServerOverloadRetryWaitMin.value = Math.round((config.server_overloaded_retry_wait_sec ?? 300) / 60);
  settingsPollIntervalSec.value = config.poll_interval_sec ?? 60;
}

async function renderSettings() {
  settingsSaveStatus.textContent = "";
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not load settings.", true); return; }
    fillSettingsForm(data.config);
  } catch (e) {
    toast("Network error loading settings.", true);
  }
  renderUpdateStatus();
}

async function renderUpdateStatus() {
  settingsUpdateStatus.textContent = "";
  settingsUpdateStatus.classList.remove("err");
  settingsUpdateCurrent.textContent = "—";
  settingsUpdateLatest.textContent = "Checking…";
  settingsUpdateBtn.classList.add("hidden");
  try {
    const res = await fetch("/api/update/status");
    const data = await res.json();
    if (!data.ok) {
      settingsUpdateLatest.textContent = "";
      settingsUpdateStatus.textContent = data.error || "Could not check for updates.";
      settingsUpdateStatus.classList.add("err");
      return;
    }
    settingsUpdateCurrent.textContent = data.current;
    if (data.latest == null) {
      settingsUpdateLatest.textContent = "";
      settingsUpdateStatus.textContent = data.error || "Could not reach GitHub to check the latest release.";
      settingsUpdateStatus.classList.add("err");
      return;
    }
    if (data.updateAvailable) {
      settingsUpdateLatest.textContent = `Update available: ${data.latest}`;
      settingsUpdateBtn.classList.remove("hidden");
    } else {
      settingsUpdateLatest.textContent = "Up to date.";
    }
  } catch (e) {
    settingsUpdateLatest.textContent = "";
    settingsUpdateStatus.textContent = "Network error checking for updates.";
    settingsUpdateStatus.classList.add("err");
  }
}

settingsCheckUpdateBtn.addEventListener("click", async () => {
  settingsCheckUpdateBtn.disabled = true;
  try {
    await renderUpdateStatus();
  } finally {
    settingsCheckUpdateBtn.disabled = false;
  }
});

settingsUpdateBtn.addEventListener("click", async () => {
  const latestLabel = settingsUpdateLatest.textContent.replace("Update available: ", "");
  const ok = await confirmModal(
    `This will download and install Tempa ${latestLabel} over this installation.\n` +
    "You will need to restart the Tempa application afterward — reloading this page is not enough.",
    { title: "Update Tempa", okLabel: "Update Now" });
  if (!ok) return;
  settingsUpdateBtn.disabled = true;
  settingsCheckUpdateBtn.disabled = true;
  settingsUpdateStatus.textContent = "Downloading and applying update…";
  settingsUpdateStatus.classList.remove("err");
  try {
    const res = await fetch("/api/update/run", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      settingsUpdateStatus.textContent = data.error || "Update failed.";
      settingsUpdateStatus.classList.add("err");
      toast(data.error || "Update failed.", true);
      return;
    }
    settingsUpdateStatus.textContent = `Updated to ${data.version}.`;
    settingsUpdateCurrent.textContent = data.version;
    settingsUpdateLatest.textContent = "Up to date.";
    settingsUpdateBtn.classList.add("hidden");
    await alertModal(
      `Tempa has been updated to version ${data.version}. Restart the Tempa application ` +
      "(close this dashboard and run \"tempa dashboard\" again, or restart any running " +
      "\"tempa implement\" session) so it picks up the new code. Reloading this page is " +
      "NOT enough — the running process still has the old code loaded in memory.",
      { title: "Restart Required" });
  } catch (e) {
    settingsUpdateStatus.textContent = "Network error while updating.";
    settingsUpdateStatus.classList.add("err");
    toast("Network error while updating.", true);
  } finally {
    settingsUpdateBtn.disabled = false;
    settingsCheckUpdateBtn.disabled = false;
  }
});

settingsSaveBtn.addEventListener("click", async () => {
  settingsSaveBtn.disabled = true;
  settingsSaveStatus.textContent = "";
  settingsSaveStatus.classList.remove("err");
  try {
    const res = await fetch("/api/config/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        models: {
          clarify: settingsModelClarify.value,
          clarify_apply: settingsModelClarifyApply.value,
          plan: settingsModelPlan.value,
          implement: settingsModelImplement.value,
        },
        backends: {
          clarify: settingsBackendClarify.value,
          clarify_apply: settingsBackendClarifyApply.value,
          plan: settingsBackendPlan.value,
          implement: settingsBackendImplement.value,
        },
        reasoning_efforts: {
          clarify: settingsEffortClarify.value,
          clarify_apply: settingsEffortClarifyApply.value,
          plan: settingsEffortPlan.value,
          implement: settingsEffortImplement.value,
        },
        features_per_session: settingsFeaturesPerSession.value,
        max_session_run: settingsMaxSessionRun.value,
        max_clarification_run: settingsMaxClarificationRun.value,
        allow_finalize_with_critical: settingsAllowFinalizeWithCritical.checked,
        implementation_start_requirement: selectedImplementRequirement(),
        notifications: { email: {
          enabled: settingsEmailEnabled.checked, provider: settingsEmailProvider.value,
          smtp_host: settingsEmailHost.value,
          smtp_port: settingsEmailPort.value, security: settingsEmailSecurity.value,
          smtp_username: settingsEmailUsername.value, smtp_password: settingsEmailPassword.value,
          from: settingsEmailFrom.value,
          recipients: settingsEmailRecipients.value.split(",").map(v => v.trim()).filter(Boolean),
          events: selectedEmailEvents(),
        } },
        usage_limit_retry_wait_sec: Number(settingsUsageLimitRetryWaitMin.value) * 60,
        usage_limit_heartbeat_sec: Number(settingsUsageLimitHeartbeatMin.value) * 60,
        server_overloaded_retry_wait_sec: Number(settingsServerOverloadRetryWaitMin.value) * 60,
        poll_interval_sec: Number(settingsPollIntervalSec.value),
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      settingsSaveStatus.textContent = data.error || "Could not save settings.";
      settingsSaveStatus.classList.add("err");
      return;
    }
    fillSettingsForm(data.config);
    settingsSaveStatus.textContent = "Saved.";
    toast("Settings saved.");
    // Saved fine, but a run already in flight can't pick the new value up — surfaced as a
    // modal rather than a toast because it contradicts what the user is about to watch
    // happen in the log (see _max_clarification_run_change_warning in dashboard_runs.py).
    if (data.warning) {
      settingsSaveStatus.textContent = "Saved — applies from the next Finalized Clarification run.";
      await alertModal(data.warning, { title: "Finalized Clarification Is Already Running" });
    }
  } catch (e) {
    settingsSaveStatus.textContent = "Network error while saving.";
    settingsSaveStatus.classList.add("err");
  } finally {
    settingsSaveBtn.disabled = false;
  }
});

settingsTestEmailBtn.addEventListener("click", async () => {
  settingsTestEmailBtn.disabled = true;
  settingsTestEmailStatus.textContent = "Sending…";
  settingsTestEmailStatus.classList.remove("err");
  try {
    const res = await fetch("/api/notifications/test-email", { method: "POST" });
    const data = await res.json();
    settingsTestEmailStatus.textContent = data.message || (data.ok ? "Test email sent." : "Test email failed.");
    settingsTestEmailStatus.classList.toggle("err", !data.ok);
  } catch (e) {
    settingsTestEmailStatus.textContent = "Network error sending test email.";
    settingsTestEmailStatus.classList.add("err");
  } finally {
    settingsTestEmailBtn.disabled = false;
  }
});

settingsEmailProvider.addEventListener("change", () => updateSmtpProvider(true));
settingsEmailSelectAllBtn.addEventListener("click", () => renderEmailEventChoices(EMAIL_ALERT_EVENTS.map(([value]) => value)));
settingsEmailClearAllBtn.addEventListener("click", () => renderEmailEventChoices([]));

// Explain the meaning and consequences before letting the user actually turn this on —
// reverts the switch if they back out. The warning banner stays visible below the field
// afterward as a standing reminder (see fillSettingsForm), since this is a real change in
// what Finalized Clarification is allowed to do, not a cosmetic preference.
settingsAllowFinalizeWithCritical.addEventListener("change", async () => {
  if (!settingsAllowFinalizeWithCritical.checked) {
    settingsAllowFinalizeWithCriticalWarning.classList.add("hidden");
    return;
  }
  const ok = await confirmModal(
    "Turning this on lets Finalized Clarification start even while critical findings are still " +
    "open, so its automated evaluate → apply loop will attempt to resolve those critical " +
    "issues on its own instead of requiring you to answer them by hand first. Critical findings " +
    "are the ones most likely to affect correctness, so letting automation resolve them " +
    "unsupervised carries real risk of a wrong or incomplete answer being applied to the PRD/spec " +
    "— review its results carefully afterward. This does not relax the separate Start " +
    "Implementation requirement, which is configured independently under \"Start Implementation\" " +
    "below.\n\nEnable anyway? (Remember to click Save Settings to apply.)",
    { title: "Allow Finalizing With Critical Findings?", okLabel: "Enable", danger: true });
  if (!ok) {
    settingsAllowFinalizeWithCritical.checked = false;
    return;
  }
  settingsAllowFinalizeWithCriticalWarning.classList.remove("hidden");
});

// ---------------------------------------------------------------------------
// Start Implementation requirement (Settings) — 3-way radio: the default (safest)
// "no critical or major", a relaxed "no critical" (major findings allowed), or "none"
// (no clarification-findings condition at all). See _implement_readiness_status in
// dashboard_clarify_parse.py for the shared server-side definition every dashboard
// surface (Home step 3, the Clarification ready banner, the Implementation gate, and
// the server-side gate in _handle_implement_run_start) is driven by.
// ---------------------------------------------------------------------------
function selectedImplementRequirement() {
  for (const input of settingsImplementRequirementInputs) if (input.checked) return input.value;
  return "no_critical_or_major";
}

const IMPLEMENT_REQUIREMENT_RISK = {
  no_critical: "Major findings are still allowed to affect implementation correctness and completeness, " +
    "not just polish — starting with open major findings means the implementation may need rework once " +
    "they're eventually resolved. Double-check the open major findings before proceeding.",
  none: "Critical findings are the ones most likely to affect correctness. Starting implementation while " +
    "critical (or major) findings are still open means the implementation may be built on ambiguous or " +
    "conflicting requirements, and could require significant rework once those findings are resolved. Use " +
    "this only if you're confident the open findings don't matter for what you're about to implement.",
};

function updateImplementRequirementWarning(requirement) {
  const risk = IMPLEMENT_REQUIREMENT_RISK[requirement];
  settingsImplementRequirementWarning.classList.toggle("hidden", !risk);
  if (risk) settingsImplementRequirementWarning.innerHTML = iconSvg("triangle-alert") + " " + escapeHtml(risk);
}

// Explain the risk before letting the user actually select a relaxed option — reverts
// to the previous selection if they back out, same pattern as "Allow finalizing with
// critical findings" above. No confirmation needed when moving back to the default.
let lastImplementRequirement = null;
for (const input of settingsImplementRequirementInputs) {
  input.addEventListener("change", async () => {
    const value = input.value;
    const risk = IMPLEMENT_REQUIREMENT_RISK[value];
    if (!risk) {
      lastImplementRequirement = value;
      updateImplementRequirementWarning(value);
      return;
    }
    const ok = await confirmModal(
      risk + "\n\nUse this setting anyway? (Remember to click Save Settings to apply.)",
      { title: "Relax the Start Implementation Requirement?", okLabel: "Use Anyway", danger: true });
    if (!ok) {
      for (const i of settingsImplementRequirementInputs) {
        i.checked = i.value === (lastImplementRequirement || "no_critical_or_major");
      }
      return;
    }
    lastImplementRequirement = value;
    updateImplementRequirementWarning(value);
  });
}

// ---------------------------------------------------------------------------
// Architecture Principles
// ---------------------------------------------------------------------------
async function renderPrinciples() {
  principlesSaveStatus.textContent = "";
  principlesSaveStatus.classList.remove("err");
  try {
    const res = await fetch("/api/principles");
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not load the principles.", true); return; }
    principlesEditor.value = data.content;
    state.principlesSet = !!data.content;
  } catch (e) {
    toast("Network error loading the principles.", true);
  }
}

principlesSaveBtn.addEventListener("click", async () => {
  principlesSaveBtn.disabled = true;
  principlesSaveStatus.textContent = "";
  principlesSaveStatus.classList.remove("err");
  try {
    const res = await fetch("/api/principles/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: principlesEditor.value }),
    });
    const data = await res.json();
    if (!data.ok) {
      principlesSaveStatus.textContent = data.error || "Could not save the principles.";
      principlesSaveStatus.classList.add("err");
      return;
    }
    principlesEditor.value = data.content;
    state.principlesSet = !!data.content;
    toast(data.content ? "Architecture principles saved." : "Architecture principles cleared.");
    selectTop("home");
  } catch (e) {
    principlesSaveStatus.textContent = "Network error while saving.";
    principlesSaveStatus.classList.add("err");
  } finally {
    principlesSaveBtn.disabled = false;
  }
});

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
      state.implementReadiness = data.clarify.implementReadiness;
      state.skipMinorFindings = !!data.clarify.skipMinorFindings;
      state.principlesSet = !!(data.principles && data.principles.set);
      state.backendsStatus = data.backends || {};
      renderSidebar();
      renderBackendStatus();
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
    await alertModal('Please fill in your own answer for ' + missing.length +
      ' finding(s), or switch them back to "Follow the recommendation".', { title: "Answers incomplete" });
    return;
  }
  // Ask before saving, not after: applying re-runs an evaluate afterward (see
  // _start_clarify_run's auto-chain in dashboard_runs.py), so it's worth knowing up
  // front whether that longer round-trip is about to start. Cancel aborts the save
  // entirely (the textarea edits stay in place, dirty), unlike "Save" which saves but
  // skips applying.
  const choice = await threeWayModal(
    "Apply these answers to the PRD right after saving?",
    { title: "Apply Answers", extraLabel: "Save", okLabel: "Save & Apply" }
  );
  if (choice === "cancel") return;
  const applyAfterSave = choice === "ok";
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
    if (applyAfterSave) {
      await selectTop("clarification");
      startClarifyRun("apply");
    }
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
      state.implementReadiness = data.clarify.implementReadiness;
      state.skipMinorFindings = !!data.clarify.skipMinorFindings;
      state.principlesSet = !!(data.principles && data.principles.set);
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
      state.implementReadiness = data.clarify.implementReadiness;
      state.skipMinorFindings = !!data.clarify.skipMinorFindings;
      state.principlesSet = !!(data.principles && data.principles.set);
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
renderBackendStatus();
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
