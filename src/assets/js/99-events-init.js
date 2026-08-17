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
      state.clarifyPendingOverlay = data.clarify.pendingOverlay || { files: 0, findings: 0, chars: 0 };
      state.clarifyOverlayWarnThreshold = data.clarify.overlayWarnThreshold || 25;
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

async function downloadZip(url, filename, successMsg) {
  try {
    const res = await fetch(url);
    if (!res.ok) {
      let msg = "Could not download.";
      try { const data = await res.json(); if (data && data.error) msg = data.error; } catch (e) {}
      toast(msg, true);
      return;
    }
    const blob = await res.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    toast(successMsg);
  } catch (e) {
    toast("Network error while downloading.", true);
  }
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
checkForSidebarUpdate();
