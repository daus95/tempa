// ---------------------------------------------------------------------------
// Graceful stop — the chevron half of every Stop split-button.
//
// "Stop Now" kills the process tree, which throws away whatever the agent session in
// flight had done but not yet written. The chevron offers the other choice: let that
// session finish and record its work, then don't start the next one. Where "the next
// one" is differs per run, which is the only thing this table holds — everything else
// (menu element, positioning, outside-click) is shared.
// ---------------------------------------------------------------------------
const GRACEFUL_STOP_TARGETS = {
  implement: {
    what: "Implementation",
    // Named after where the stop actually lands, rather than a vaguer "Graceful Stop" —
    // the whole question a user has here is *when* it will stop.
    label: "Stop After Current Session",
    requestedToast: "Implementation will stop once the session in progress finishes.",
    pendingStatus: "Stopping after current session…",
    request: "/api/implement/stop-graceful",
    cancel: "/api/implement/stop-graceful/cancel",
    isPending: () => state.implementRun.gracefulStopRequested,
    setPending(value) {
      state.implementRun.gracefulStopRequested = value;
      updateImplementControls();
    },
  },
  run: {
    what: "Clarification",
    label: "Stop After Current Session",
    requestedToast: "Clarification will stop once the session in progress finishes.",
    pendingStatus: "stopping after current session",
    request: "/api/clarify/stop-graceful",
    cancel: "/api/clarify/stop-graceful/cancel",
    isPending: () => state.clarifyRun.gracefulStopRequested,
    setPending: setClarifyGracefulStopPending,
  },
  apply: {
    what: "Apply Answers",
    label: "Stop After Current Session",
    requestedToast: "Apply Answers will stop once the session in progress finishes.",
    pendingStatus: "stopping after current session",
    request: "/api/clarify/stop-graceful",
    cancel: "/api/clarify/stop-graceful/cancel",
    isPending: () => state.clarifyRun.gracefulStopRequested,
    setPending: setClarifyGracefulStopPending,
  },
  finalize: {
    what: "Finalized Clarification",
    // Finalize's unit of work is a whole evaluate/answer round, not a single session.
    label: "Stop After Current Round",
    requestedToast: "Finalized Clarification will stop once the round in progress finishes.",
    pendingStatus: "stopping after current round",
    request: "/api/clarify/stop-graceful",
    cancel: "/api/clarify/stop-graceful/cancel",
    isPending: () => state.clarifyRun.gracefulStopRequested,
    setPending: setClarifyGracefulStopPending,
  },
};

function setClarifyGracefulStopPending(value) {
  state.clarifyRun.gracefulStopRequested = value;
  renderClarifyRunStatus();
  setClarifyRunButtonsDisabled(state.clarifyRun.running);
}

// The active clarify mode doubles as its key here; implementation has only one run.
function clarifyStopTargetKey() {
  const mode = state.clarifyRun.mode;
  return state.clarifyRun.running && GRACEFUL_STOP_TARGETS[mode] ? mode : null;
}

// What the menu was opened for, captured at open time: the item's meaning flips between
// "request" and "cancel" depending on whether one is already pending, and a poll tick
// landing between opening and clicking must not silently change which one fires.
let stopOptionsSelection = null;

function openStopOptionsMenu(anchorEl, key) {
  const target = GRACEFUL_STOP_TARGETS[key];
  if (!target) return;
  const pending = !!target.isPending();
  stopOptionsSelection = { key, pending };
  stopOptionsMenuItem.textContent = pending ? "Cancel Graceful Stop" : target.label;
  openAnchoredMenu(anchorEl, stopOptionsMenu);
}

// Neither action goes through a confirm modal: asking to stop later is not destructive
// (the immediate Stop next to it still is, and keeps its confirmation), and reaching the
// item already took two clicks.
stopOptionsMenuItem.addEventListener("click", async () => {
  const selection = stopOptionsSelection;
  closeAnchoredMenu(stopOptionsMenu);
  if (!selection) return;
  const target = GRACEFUL_STOP_TARGETS[selection.key];
  const url = selection.pending ? target.cancel : target.request;
  try {
    const res = await fetch(url, { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      toast(data.error || `Could not change the stop request for ${target.what}.`, true);
      return;
    }
    // Applied locally rather than waiting for the next 1s poll to report it back, so the
    // button reacts to the click immediately; the poll then simply confirms it.
    target.setPending(!selection.pending);
    toast(selection.pending
      ? `Graceful stop cancelled — ${target.what} will continue.`
      : target.requestedToast);
  } catch (e) {
    toast(`Network error changing the stop request for ${target.what}.`, true);
  }
});

// stopPropagation, or the document-level outside-click listener that closes the menu
// (see closeAnchoredMenu's registration) fires on this very click and closes it again.
for (const [btn, key] of [
  [stopClarifyMenuBtn, "run"],
  [stopApplyAnswersMenuBtn, "apply"],
  [stopFinalizeClarifyMenuBtn, "finalize"],
]) {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const active = clarifyStopTargetKey();
    if (active === key) openStopOptionsMenu(btn, key);
  });
}
stopImplementMenuBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  if (state.implementRun.running) openStopOptionsMenu(stopImplementMenuBtn, "implement");
});

// One handler for all three Stop buttons — /api/clarify/stop is already mode-agnostic
// (it just kills whatever's running), so the only per-mode thing left is copy + which
// button to disable while the request is in flight.
const CLARIFY_STOP_CONFIRM = {
  run: { title: "Stop Clarification",
    text: "Stop the Clarification run that is currently in progress?" },
  apply: { title: "Stop Apply Answers",
    text: "Stop the Apply Answers run that is currently in progress?" },
  finalize: { title: "Stop Finalize",
    text: "Stop the Finalized Clarification run that is currently in progress?" },
};
const CLARIFY_STOP_ERROR_LABEL = { run: "Clarification", apply: "Apply Answers", finalize: "Finalized Clarification" };
const CLARIFY_STOP_BUTTONS = { run: stopClarifyBtn, apply: stopApplyAnswersBtn, finalize: stopFinalizeClarifyBtn };

async function stopClarifyRun() {
  const mode = state.clarifyRun.mode;
  if (!(state.clarifyRun.running && mode && CLARIFY_STOP_CONFIRM[mode])) return;
  const { title, text } = CLARIFY_STOP_CONFIRM[mode];
  const ok = await confirmModal(text, { title, okLabel: "Stop", danger: true });
  if (!ok) return;
  const btn = CLARIFY_STOP_BUTTONS[mode];
  btn.disabled = true;
  try {
    const res = await fetch("/api/clarify/stop", { method: "POST" });
    const data = await res.json();
    if (!data.ok) toast(data.error || `Could not stop ${CLARIFY_STOP_ERROR_LABEL[mode]}.`, true);
  } catch (e) {
    toast(`Network error stopping ${CLARIFY_STOP_ERROR_LABEL[mode]}.`, true);
  } finally {
    btn.disabled = false;
  }
}

startClarifyBtn.addEventListener("click", () => startClarifyRun("run"));
stopClarifyBtn.addEventListener("click", stopClarifyRun);
finalizeClarifyBtn.addEventListener("click", () => startClarifyRun("finalize"));
stopFinalizeClarifyBtn.addEventListener("click", stopClarifyRun);
applyAnswersBtn.addEventListener("click", () => startClarifyRun("apply"));
stopApplyAnswersBtn.addEventListener("click", stopClarifyRun);

function setClarifyTab(tab) {
  state.clarifyTab = tab;
  clarifyTabOverviewBtn.classList.toggle("active", tab === "overview");
  clarifyTabLogBtn.classList.toggle("active", tab === "log");
  clarifyOverviewTabPanel.classList.toggle("hidden", tab !== "overview");
  clarifyLogTabPanel.classList.toggle("hidden", tab !== "log");
}
clarifyTabOverviewBtn.addEventListener("click", () => setClarifyTab("overview"));
clarifyTabLogBtn.addEventListener("click", () => setClarifyTab("log"));

