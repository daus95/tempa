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

// A qa_history entry's "report" field is the full path tempa_implement.py wrote it under
// (e.g. ".tempa/qa/EPIC-03-qa-20260815_101500.md", or a Windows-style backslash path on that
// platform) — /api/qa-report only accepts a bare filename (see _handle_qa_report, confined to
// get_qa_dir() the same way log files are confined to get_logs_dir()), so this strips
// whichever separator the path was built with down to the last component.
function basenameOf(path) {
  return String(path || "").replace(/\\/g, "/").split("/").pop();
}

// Collapsible per-epic QA round history (epic.qa_history / epic.qa_loop_strikes — see
// tempa_qa_history.py) rendered inside the epic card. The strike badge stays visible whether
// the history is expanded or not: seeing "1 strike" while implementation is still running is
// the whole point — it's the warning that a round is repeating BEFORE the loop guard gives up
// and stops the run over it, not just an explanation after the fact.
function qaHistoryHtml(epic) {
  const history = epic.qa_history || [];
  if (!history.length) return "";
  const strikes = epic.qa_loop_strikes || 0;
  const strikeBadge = strikes > 0
    ? `<span class="qa-history-strikes">⚠ ${strikes} strike${strikes === 1 ? "" : "s"}</span>`
    : "";
  const isOpen = state.expandedQaHistory.has(epic.epic_name);
  const rows = history.map((entry) => {
    if (entry.verdict === "reset") {
      return `<div class="qa-history-row qa-history-reset-row">round ${entry.round} — reset by hand, counting restarts here</div>`;
    }
    const passed = entry.verdict === "pass";
    const when = entry.at ? escapeHtml(entry.at.slice(0, 16).replace("T", " ")) : "";
    const detail = passed
      ? "passed"
      : ((entry.failed || []).length ? escapeHtml(entry.failed.join(", ")) : "failed, no feature flagged");
    // The QA prompt only writes a report file on a fail verdict (see src/prompt/qa.md) — a
    // pass round's "report" field still carries the filename QA would have written to if it
    // had found anything, so linking it there would 404.
    const reportName = !passed && entry.report ? basenameOf(entry.report) : "";
    const reportLink = reportName
      ? ` <a href="#" class="qa-round-link" data-qa-report="${escapeHtml(reportName)}">report</a>`
      : "";
    return `<div class="qa-history-row">` +
      `<span class="qa-history-icon">${passed ? "✅" : "❌"}</span>` +
      `<span class="qa-history-label">round ${entry.round}</span>` +
      `<span class="qa-history-detail">${detail}</span>` +
      `<span class="qa-history-when">${when}</span>${reportLink}` +
    `</div>`;
  }).join("");
  return (
    `<div class="qa-history">` +
      `<button type="button" class="qa-history-toggle${isOpen ? " open" : ""}" data-epic="${escapeHtml(epic.epic_name || "")}">` +
        `<span class="twist">${iconSvg("chevron-right")}</span>` +
        `<span>QA history (${history.length} round${history.length === 1 ? "" : "s"})</span>${strikeBadge}` +
      `</button>` +
      `<div class="qa-history-body${isOpen ? "" : " hidden"}">${rows}</div>` +
    `</div>`
  );
}

function renderImplementStatus() {
  // The 1s poll rebuilds every card from scratch, and emptying the container collapses its
  // height — which makes the browser clamp the panel's scrollTop to 0. Without capturing and
  // restoring it, a user scrolled down the epic list gets snapped back to the top every tick.
  const prevScroll = implStatusPanel.scrollTop;
  implStatusBody.innerHTML = "";
  if (!state.epics.length) {
    implStatusBody.innerHTML = '<div class="clarify-log-empty">No plan/epic yet. A plan will be generated automatically the first time implementation starts.</div>';
    return;
  }
  for (const epic of state.epics) {
    const card = document.createElement("div");
    card.className = "impl-epic-card";
    const qaTag = epic.status === "done"
      ? (epic.qa_status === "ongoing"
          ? `<span class="impl-qa-running">${iconSvg("loader-circle", "icon-spin")} QA running</span>`
          : epic.qa_passed
            ? '<span class="impl-qa-ok">QA ok</span>'
            : '<span class="impl-qa-pending">QA --</span>')
      : "";
    const lastRun = epic.last_run ? escapeHtml(epic.last_run.slice(0, 16).replace("T", " ")) : "-";
    const features = (epic.features || []).map((f) =>
      `<div class="impl-feature-row"><span>${featureStatusIcon(f.status)}</span><span>${escapeHtml(f.id)} — ${escapeHtml(f.name)}</span></div>`
    ).join("");
    // A "failed" epic that still carries blocked_reason means one of the automatic guards gave
    // up on it: no forward progress across resumed sessions (see _try_reorder_for_dependency in
    // tempa_session.py), or a QA loop it never converged out of (tempa_qa_history.py). Each
    // writes its own explanation, so the label stays neutral. Surface it right on the card
    // instead of leaving the user to go dig through the log — it's the one thing they need to
    // act on.
    const blockedReason = epic.status === "failed" && epic.blocked_reason
      ? `<div class="impl-epic-blocked-reason">⚠ Halted:<br>${escapeHtml(epic.blocked_reason).replace(/\n/g, "<br>")}</div>`
      : "";
    card.innerHTML =
      `<div class="impl-epic-header">` +
        `<span class="impl-epic-icon">${epicStatusIcon(epic.status)}</span>` +
        `<span class="impl-epic-name">${escapeHtml(epic.epic_name || "?")}</span>` +
        `<span class="impl-epic-status">${escapeHtml(epic.status || "")}</span>` +
        `<span class="impl-epic-progress">${epic.completed_features || 0}/${epic.total_features || 0} features</span>` +
        `<span class="impl-epic-lastrun">last run: ${lastRun}</span>` +
        qaTag +
        `<button type="button" class="impl-epic-verify-btn" data-epic="${escapeHtml(epic.epic_name || "")}">Verify</button>` +
      `</div>` +
      blockedReason +
      qaHistoryHtml(epic) +
      `<div class="impl-feature-list">${features}</div>`;
    implStatusBody.appendChild(card);
  }
  implStatusPanel.scrollTop = prevScroll;
}

// Event delegation (not a per-card listener) because renderImplementStatus rebuilds every
// card from scratch on each 1s poll tick — re-attaching per card would leak/duplicate
// listeners over time.
implStatusBody.addEventListener("click", async (e) => {
  const historyToggle = e.target.closest(".qa-history-toggle");
  if (historyToggle) {
    const epicName = historyToggle.dataset.epic;
    if (state.expandedQaHistory.has(epicName)) state.expandedQaHistory.delete(epicName);
    else state.expandedQaHistory.add(epicName);
    renderImplementStatus();
    return;
  }
  const reportLink = e.target.closest(".qa-round-link");
  if (reportLink) {
    e.preventDefault();
    openQaReportModal(reportLink.dataset.qaReport);
    return;
  }
  const btn = e.target.closest(".impl-epic-verify-btn");
  if (!btn) return;
  const epic = btn.dataset.epic;
  if (!epic) return;
  const ok = await confirmModal(
    `Run verification for "${epic}"? This starts a new AI session that checks the current spec against the current code — it makes no changes.`,
    { title: "Verify Epic", okLabel: "Verify" });
  if (!ok) return;
  try {
    const res = await fetch("/api/verify/run", { method: "POST", body: JSON.stringify({ epic }) });
    const data = await res.json();
    if (!data.ok) {
      toast(data.error || "Could not start verification.", true);
      return;
    }
  } catch (err) {
    toast("Network error starting verification.", true);
    return;
  }
  selectTop("verification");
});

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
  clarifyStartImplementBtn.disabled = state.implementRun.running || !state.implementReadiness.ready;
  // The wrapper, not the button: Stop Now and its chevron have to appear together.
  stopImplementSplit.classList.toggle("hidden", !state.implementRun.running);
  implHeaderStatus.textContent = !state.implementRun.running ? ""
    : state.implementRun.gracefulStopRequested
      ? GRACEFUL_STOP_TARGETS.implement.pendingStatus
      : "Running…";
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
    // Server-computed, so a `tempa implement --stop-graceful` typed in a terminal is
    // reflected here too, not just a request made from this dashboard.
    state.implementRun.gracefulStopRequested = !!data.gracefulStopRequested;
    state.epics = data.epics || [];
    state.implementStarted = !!data.started;
    renderImplementLog();
    renderImplementStatus();
    const wasRunning = state.implementRun.running;
    state.implementRun.running = data.running;
    updateImplementControls();
    if (wasRunning !== data.running) renderSidebar();
    homeStartImplementBtn.disabled = data.running || !state.implementReadiness.ready;
    // Keep polling while any epic is actively running/QA'ing even if this dashboard
    // session isn't the one that started it (e.g. `tempa implement` in a terminal) —
    // otherwise the spinner freezes stale until the user re-navigates to the tab.
    const qaActive = (state.epics || []).some((e) => e.qa_status === "ongoing" || e.status === "on_progress");
    if ((data.running || qaActive) && !state.implementRun.pollTimer) startImplementPolling();
    if (!data.running) {
      if (!qaActive) stopImplementPolling();
      if (wasRunning && data.returncode !== null) {
        toast(stopAwareReturncodeMessage(data.returncode, "implement", data.gracefulStopRequested),
          data.returncode !== 0);
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
  state.implementRun.gracefulStopRequested = false;
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
    renderSidebar();
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

