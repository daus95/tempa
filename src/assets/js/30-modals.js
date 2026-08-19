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
// Save & Clarify): resolves "cancel" (Cancel, Escape, or overlay click), extraLabel's
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

// Shared by both file kinds this modal serves — a raw agent/session log (fetched via
// /api/log-file, rendered verbatim in a <pre>) and a QA report (fetched via /api/qa-report,
// rendered as markdown like the Verification page's report viewer). Same overlay/fullscreen
// chrome either way; only the fetch URL and the rendering differ.
async function openFileViewerModal(url, title, { markdown = false } = {}) {
  logFileModalTitle.textContent = title;
  logFileModalBody.innerHTML = '<div class="log-file-modal-status">Loading…</div>';
  logFileModalOverlay.classList.remove("hidden");
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) {
      logFileModalBody.innerHTML = `<div class="log-file-modal-status">${escapeHtml(data.error || "Could not open file.")}</div>`;
      return;
    }
    const truncatedNote = data.truncated
      ? '<div class="log-file-modal-truncated">This file is large — showing only the most recent portion.</div>'
      : "";
    logFileModalBody.innerHTML = truncatedNote + (markdown
      ? `<div class="markdown-body">${renderMarkdown(data.content)}</div>`
      : `<pre>${escapeHtml(data.content)}</pre>`);
    if (markdown) renderMermaidDiagrams(logFileModalBody);   // async, not awaited (12-mermaid.js)
  } catch (e) {
    logFileModalBody.innerHTML = '<div class="log-file-modal-status">Network error opening file.</div>';
  }
}

function openLogFileModal(name) {
  return openFileViewerModal("/api/log-file?name=" + encodeURIComponent(name), name);
}

// Opened from a QA-history round's "report" link (see qaHistoryHtml) — the report
// tempa_qa_history.record_qa_round attached to that round, served by _handle_qa_report the
// same way session logs are, confined to get_qa_dir() instead of get_logs_dir().
function openQaReportModal(name) {
  return openFileViewerModal("/api/qa-report?name=" + encodeURIComponent(name), name, { markdown: true });
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

