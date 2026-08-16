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
  treeEl.appendChild(renderLeafSection("implementation", iconSvg("wrench"), "Implementation", !state.workspaceInitialized, state.implementRun.running));
  treeEl.appendChild(renderLeafSection("verification", iconSvg("circle-check"), "Verification", !state.workspaceInitialized));
  treeBottomEl.innerHTML = "";
  if (state.updateAvailable) treeBottomEl.appendChild(renderUpdateAvailableItem());
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
  } else if (key === "verification") {
    state.verifyDetailId = null;
    stopVerifyDetailPolling();
    refreshVerifyList();
    showPane("verification");
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

