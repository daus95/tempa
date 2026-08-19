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
  // Ctrl/Cmd+B — the binding VS Code uses for the same panel, which is the mental model this
  // explorer copies. preventDefault is required, not cosmetic: it is Firefox's bookmarks
  // sidebar. Ctrl+B is not a text-editing binding in a plain <textarea>, so no field guard.
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
    e.preventDefault();
    toggleSidebar();
  }
  // Guarded on its own overlay, exactly like the two modal Escape handlers in 30-modals.js,
  // so Escape inside a confirm dialog can't also close the drawer underneath it.
  if (e.key === "Escape" && !specPeekOverlay.classList.contains("hidden")) closeSpecPeek();
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
      state.recentWorkspaces = data.workspace.recent || [];
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
// The width is published as a custom property rather than an inline style, so
// `.sidebar.collapsed { width: 44px }` can beat it on specificity — an inline width could
// only be overridden with !important. It also means re-expanding restores the dragged width
// for free, since collapsing never overwrites the property.
(function () {
  const splitter = $("splitter");
  let dragging = false;
  splitter.addEventListener("mousedown", (e) => {
    dragging = true; splitter.classList.add("dragging");
    document.body.classList.add("resizing");
    document.body.style.userSelect = "none"; e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const w = Math.max(200, Math.min(e.clientX, window.innerWidth * 0.7));
    state.sidebarWidth = w;
    document.documentElement.style.setProperty("--sidebar-w", w + "px");
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false; splitter.classList.remove("dragging");
    document.body.classList.remove("resizing"); document.body.style.userSelect = "";
    uiPrefSet("sidebarWidth", state.sidebarWidth);
  });
})();

sidebarToggleBtn.addEventListener("click", toggleSidebar);

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
// Applied synchronously, before the first paint, so restoring a collapsed explorer doesn't
// animate its way in from 300px on load.
document.documentElement.style.setProperty("--sidebar-w", state.sidebarWidth + "px");
document.documentElement.style.setProperty("--peek-w", state.specPeek.width + "px");
setSidebarCollapsed(state.sidebarCollapsed);
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
