// ---------------------------------------------------------------------------
// Specification row context menu (rename / delete a file or folder)
// ---------------------------------------------------------------------------
const rowContextMenu = $("rowContextMenu"), rowMenuRename = $("rowMenuRename"), rowMenuDelete = $("rowMenuDelete");
let contextMenuNode = null;

// Position a .row-context-menu under the element that opened it. Shared by the row menu
// here and the Stop split-buttons' menu (see openStopOptionsMenu) — same look, same
// outside-click/Escape behaviour, one implementation.
function openAnchoredMenu(anchorEl, menuEl) {
  const rect = anchorEl.getBoundingClientRect();
  menuEl.classList.remove("hidden");
  const menuWidth = menuEl.offsetWidth || 130;
  menuEl.style.top = rect.bottom + 4 + "px";
  menuEl.style.left = Math.min(rect.left, window.innerWidth - menuWidth - 8) + "px";
}

function closeAnchoredMenu(menuEl) {
  menuEl.classList.add("hidden");
}

function openRowContextMenu(anchorEl, node) {
  contextMenuNode = node;
  openAnchoredMenu(anchorEl, rowContextMenu);
}

function closeRowContextMenu() {
  closeAnchoredMenu(rowContextMenu);
  contextMenuNode = null;
}

document.addEventListener("click", (e) => {
  if (!rowContextMenu.classList.contains("hidden") && !rowContextMenu.contains(e.target)) closeRowContextMenu();
  if (!stopOptionsMenu.classList.contains("hidden") && !stopOptionsMenu.contains(e.target)) {
    closeAnchoredMenu(stopOptionsMenu);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeRowContextMenu();
    closeAnchoredMenu(stopOptionsMenu);
  }
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
    toast(`Renamed to "${newName}"` + (clarifyStaleToastSuffix(data) || "."));
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
    toast(`Deleted "${node.name}"` + (clarifyStaleToastSuffix(data) || "."));
    await refreshSpecTree();
  } catch (e) {
    toast("Network error while deleting.", true);
  }
});

