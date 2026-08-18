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

// Finalized Clarification auto-answers findings itself (mechanical recommendation
// fill + agent-applied resolutions) — a hand save racing with that loop's own
// reads/writes to the same file would corrupt or lose an auto-answer, so answer
// editing is locked for as long as a finalize run is in flight. Mirrors the
// server-side guard in _handle_clarify_save (dashboard_server.py).
function isClarifyFinalizeLocked() {
  return state.clarifyRun.running && state.clarifyRun.mode === "finalize";
}

const CLARIFY_LOCKED_BANNER_HTML =
  '<div class="clarify-locked-banner">Finalized Clarification is running and auto-answering ' +
  "these findings — answers are read-only until it stops.</div>";

// openClarifyFile applies the lock at open time only; this keeps a file that is
// already on screen in sync when a finalize run starts or ends underneath it —
// without it, a file opened before the run stays editable-looking until save, and
// one opened during the run stays greyed out after it ends until the user closes
// and reopens the file. Called every poll tick; a no-op unless the state flipped.
function syncClarifyLockState() {
  if (!state.selectedClarifyPath || state.clarifyShowingOverview) return;
  const locked = isClarifyFinalizeLocked();
  const banner = clarifyBody.querySelector(".clarify-locked-banner");
  if (locked === !!banner) return;
  if (locked) clarifyBody.insertAdjacentHTML("afterbegin", CLARIFY_LOCKED_BANNER_HTML);
  else banner.remove();
  clarifyBody.querySelectorAll('input[type=radio], textarea').forEach((el) => { el.disabled = locked; });
}

// Bulk-selects "Follow the recommendation" for every item that has no radio picked
// yet (i.e. hasn't been answered in this session) — leaves items the user already
// answered, or already chose a mode for, untouched.
function followAllRecommendations() {
  if (isClarifyFinalizeLocked()) {
    toast("Answers are locked while Finalized Clarification is running.", true);
    return;
  }
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
    const locked = isClarifyFinalizeLocked();
    clarifyBody.innerHTML = (locked ? CLARIFY_LOCKED_BANNER_HTML : "") + (data.html || "");
    wireClarifyBody();
    if (locked) {
      clarifyBody.querySelectorAll('input[type=radio], textarea').forEach((el) => { el.disabled = true; });
    }
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
  if (isClarifyFinalizeLocked()) {
    toast("Answers are locked while Finalized Clarification is running.", true);
    return;
  }
  const items = collectClarifyAnswers();
  const own = items.filter((i) => i.mode === "own");
  const missing = own.filter((i) => !i.answer.trim());
  if (missing.length) {
    await alertModal('Please fill in your own answer for ' + missing.length +
      ' finding(s), or switch them back to "Follow the recommendation".', { title: "Answers incomplete" });
    return;
  }
  // Plain Save is the primary action: saved answers are carried into the next
  // clarification round as already-decided resolutions (see pending_resolutions in
  // dashboard_clarify_parse.py). Save & Clarify does that same save and then jumps
  // straight into the next Continue Clarification run, for anyone who wants to keep
  // moving without an extra trip back to the overview. Cancel aborts the save
  // entirely (the textarea edits stay in place, dirty).
  const choice = await threeWayModal(
    "Save these answers now? They'll be carried into the next clarification round even " +
    "before they're written into the PRD. Save & Clarify also starts that next round right away.",
    { title: "Save Answers", extraLabel: "Save & Clarify", okLabel: "Save" }
  );
  if (choice === "cancel") return;
  const continueAfterSave = choice === "extra";
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
    if (continueAfterSave) {
      await selectTop("clarification");
      startClarifyRun("run");
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
      state.recentWorkspaces = data.workspace.recent || [];
      state.clarifyFindings = data.clarify.findings;
      state.clarifyFinalize = data.clarify.finalize;
      state.implementReadiness = data.clarify.implementReadiness;
      state.clarifyPendingOverlay = data.clarify.pendingOverlay || { files: 0, findings: 0, chars: 0 };
      state.clarifyOverlayWarnThreshold = data.clarify.overlayWarnThreshold || 25;
      state.skipMinorFindings = !!data.clarify.skipMinorFindings;
      state.principlesSet = !!(data.principles && data.principles.set);
      renderSidebar();
      if (!$("clarifyOverviewPane").classList.contains("hidden")) renderClarifyOverview();
      if (!$("homePane").classList.contains("hidden")) renderHomeWorkflow();
    }
  } catch (e) { /* keep stale list on network error */ }
}

