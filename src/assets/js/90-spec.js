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
    state.selectedEpicSpec = null;
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
  if (state.isMarkdown) renderMermaidDiagrams(specViewer);   // async, not awaited (12-mermaid.js)
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

// Opens one epic's own spec file in the same pane the Specification tree uses. Reached from
// the epic's card, because sources.epics is a sibling of the PRD folder that tree is rooted at
// — nothing under it can be browsed to. This is the file QA grades the epic against, so it is
// the one to read (and to correct) when QA rounds keep contradicting each other.
async function openEpicSpec(epicName) {
  if (!(await confirmDiscardIfDirty())) return;
  try {
    const res = await fetch("/api/epic/spec?epic=" + encodeURIComponent(epicName));
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not open the epic spec.", true); return; }
    state.currentKind = "spec";
    state.selectedEpicSpec = data.epic;
    state.selectedSpecPath = data.path;
    state.isMarkdown = data.markdown;
    state.isText = data.text;
    state.specDirty = false;
    state.specShowingOverview = false;
    specEditor.value = data.content || "";
    setSpecMode("view");
    showPane("spec");
    renderSidebar();
  } catch (e) {
    toast("Network error opening the epic spec.", true);
  }
}

async function saveSpecFile() {
  if (!state.selectedSpecPath || !state.specDirty || !state.isText) return;
  saveBtn.disabled = true;
  try {
    const epic = state.selectedEpicSpec;
    const res = await fetch(epic ? "/api/epic/spec/save" : "/api/spec/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(epic
        ? { epic, content: specEditor.value }
        : { path: state.selectedSpecPath, content: specEditor.value }),
    });
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Save failed.", true); updateToolbar(); return; }
    state.specDirty = false;
    // The spec drawer caches whole files; this one just changed underneath it.
    SPEC_PEEK_CACHE.delete(state.selectedSpecPath);
    updateToolbar();
    if (state.specMode === "view") renderSpecViewer();
    toast("Saved " + state.selectedSpecPath + clarifyStaleToastSuffix(data));
    // Editing a PRD file re-opens clarification and re-closes the Start Implementation gate
    // (see _spec_changed_since_evaluation), so every clarify-derived gate on screen is now
    // stale. Epic specs don't invalidate anything, but they go through the same refresh.
    await refreshSpecTree();
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
  // Runs after every clarify/implement run and after Refresh — i.e. after anything that can
  // have rewritten the PRD, which is exactly when the drawer's cached copies go stale.
  SPEC_PEEK_CACHE.clear();
  try {
    const res = await fetch("/api/tree");
    const data = await res.json();
    if (data.ok) {
      applyTreePayload(data);
      // The three Start Implementation buttons are driven by updateImplementControls,
      // whose own poll only ticks while a run is active — without this, a payload that
      // just changed implementReadiness would repaint the ready banner while leaving the
      // button inside it disabled (or vice versa).
      updateImplementControls();
      renderSidebar();
      renderBackendStatus();
      if (!$("specOverviewPane").classList.contains("hidden")) renderSpecOverview();
      // A spec change re-opens clarification (see _spec_changed_since_evaluation), so the
      // Clarification overview's buttons and Unanswered note have to repaint from here too.
      if (!$("clarifyOverviewPane").classList.contains("hidden")) renderClarifyOverview();
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
  let okCount = 0, failCount = 0, stale = false;
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
      if (clarifyStaleToastSuffix(data)) stale = true;
    } catch (e) { failCount++; }
  }
  toast(failCount
    ? `Added ${okCount} file(s), ${failCount} failed.`
    : `Added ${okCount} file(s)` + (stale ? clarifyStaleToastSuffix({ clarificationStale: true }) : "."),
    failCount > 0);
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

