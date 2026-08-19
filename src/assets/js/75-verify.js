// ---------------------------------------------------------------------------
// Verification (tempa verify <epic>) — a read-only AI session that checks an epic's
// spec against the current code and writes a markdown report. Unlike Implementation/
// Clarification, more than one epic may verify at once (server.verify_runs is keyed per
// epic), so there's no single "is anything running" state here — each row in the list
// tracks its own epic independently.
// ---------------------------------------------------------------------------
function verifyStatusBadge(status) {
  if (status === "running") return `<span class="verify-status-badge running">${iconSvg("loader-circle", "icon-spin")} Running</span>`;
  if (status === "failed") return `<span class="verify-status-badge failed">${iconSvg("circle-x")} Failed</span>`;
  return `<span class="verify-status-badge completed">${iconSvg("circle-check")} Completed</span>`;
}

function verifyResultBadge(result) {
  if (result === "passed") return '<span class="verify-result-badge passed">Passed</span>';
  if (result === "issues") return '<span class="verify-result-badge issues">Issues Found</span>';
  return '<span class="verify-result-badge unknown">—</span>';
}

function renderVerifyList() {
  verifyListBody.innerHTML = "";
  verifyListEmpty.classList.toggle("hidden", state.verifyRuns.length > 0);
  for (const run of state.verifyRuns) {
    const row = document.createElement("tr");
    row.className = "verify-list-row";
    row.dataset.id = run.id;
    row.innerHTML =
      `<td>${escapeHtml(run.epic)}</td>` +
      `<td>${escapeHtml(run.timestamp || "—")}</td>` +
      `<td>${verifyStatusBadge(run.status)}</td>` +
      `<td>${verifyResultBadge(run.result)}</td>`;
    verifyListBody.appendChild(row);
  }
}

verifyListBody.addEventListener("click", (e) => {
  const row = e.target.closest(".verify-list-row");
  if (row) openVerificationDetail(row.dataset.id);
});

async function refreshVerifyList() {
  try {
    const res = await fetch("/api/verify/runs");
    const data = await res.json();
    if (!data.ok) return;
    state.verifyRuns = data.runs || [];
    renderVerifyList();
    const anyRunning = state.verifyRuns.some((r) => r.status === "running");
    if (anyRunning && !state.verifyListPollTimer) startVerifyListPolling();
    if (!anyRunning) stopVerifyListPolling();
  } catch (e) { /* transient network hiccup — next tick retries */ }
}

function startVerifyListPolling() {
  stopVerifyListPolling();
  state.verifyListPollTimer = setInterval(refreshVerifyList, 1000);
}

function stopVerifyListPolling() {
  if (state.verifyListPollTimer) {
    clearInterval(state.verifyListPollTimer);
    state.verifyListPollTimer = null;
  }
}

// Strips the leading tempa:verify-result marker (see verify.md / dashboard_verify.py) —
// it's metadata for the dashboard's own status/result badges, not part of the report a
// person is meant to read, so it's dropped before rendering rather than shown as a stray
// escaped HTML-comment line at the top of the report.
const VERIFY_RESULT_MARKER_RE = /^<!--\s*tempa:verify-result[^>]*-->\s*\n?/;

function renderVerifyDetail(data) {
  verifyDetailHeader.textContent = `${data.epic}${data.timestamp ? " — " + data.timestamp : ""}`;
  verifyDetailStopBtn.classList.toggle("hidden", data.status !== "running");
  verifyDetailDeleteBtn.classList.toggle("hidden", data.status !== "completed");
  if (data.status === "completed" && data.content) {
    verifyDetailStatus.classList.add("hidden");
    verifyDetailBody.classList.remove("hidden");
    verifyDetailBody.innerHTML = renderMarkdown(data.content.replace(VERIFY_RESULT_MARKER_RE, ""));
    renderMermaidDiagrams(verifyDetailBody);                 // async, not awaited (12-mermaid.js)
  } else {
    verifyDetailBody.classList.add("hidden");
    verifyDetailStatus.classList.remove("hidden");
    verifyDetailStatus.textContent = data.status === "running"
      ? `Verification for "${data.epic}" is still running...`
      : `Verification for "${data.epic}" failed — no report was produced. Check the dashboard's log output or .tempa/logs/ for details.`;
  }
}

async function refreshVerifyDetail() {
  if (!state.verifyDetailId) return;
  try {
    const res = await fetch("/api/verify/detail?id=" + encodeURIComponent(state.verifyDetailId));
    const data = await res.json();
    if (!data.ok) {
      toast(data.error || "Verification run not found.", true);
      selectTop("verification");
      return;
    }
    renderVerifyDetail(data);
    if (data.status !== "running") stopVerifyDetailPolling();
  } catch (e) { /* transient network hiccup — next tick retries */ }
}

function startVerifyDetailPolling() {
  stopVerifyDetailPolling();
  state.verifyDetailPollTimer = setInterval(refreshVerifyDetail, 1000);
}

function stopVerifyDetailPolling() {
  if (state.verifyDetailPollTimer) {
    clearInterval(state.verifyDetailPollTimer);
    state.verifyDetailPollTimer = null;
  }
}

async function openVerificationDetail(id) {
  state.verifyDetailId = id;
  showPane("verificationDetail");
  await refreshVerifyDetail();
  startVerifyDetailPolling();
}

verifyDetailBackLink.addEventListener("click", (e) => {
  e.preventDefault();
  selectTop("verification");
});

verifyDetailStopBtn.addEventListener("click", async () => {
  if (!state.verifyDetailId || !state.verifyDetailId.startsWith("live:")) return;
  const epic = state.verifyDetailId.slice("live:".length);
  const ok = await confirmModal(`Stop the verification currently running for "${epic}"?`,
    { title: "Stop Verification", okLabel: "Stop", danger: true });
  if (!ok) return;
  verifyDetailStopBtn.disabled = true;
  try {
    const res = await fetch("/api/verify/stop", { method: "POST", body: JSON.stringify({ epic }) });
    const data = await res.json();
    if (!data.ok) toast(data.error || "Could not stop verification.", true);
    else await refreshVerifyDetail();
  } catch (e) {
    toast("Network error stopping verification.", true);
  } finally {
    verifyDetailStopBtn.disabled = false;
  }
});

verifyDetailDeleteBtn.addEventListener("click", async () => {
  if (!state.verifyDetailId) return;
  const ok = await confirmModal("Delete this verification report? This cannot be undone.",
    { title: "Delete Verification Run", okLabel: "Delete", danger: true });
  if (!ok) return;
  verifyDetailDeleteBtn.disabled = true;
  try {
    const res = await fetch("/api/verify/delete", { method: "POST", body: JSON.stringify({ id: state.verifyDetailId }) });
    const data = await res.json();
    if (!data.ok) {
      toast(data.error || "Could not delete verification run.", true);
      return;
    }
    selectTop("verification");
  } catch (e) {
    toast("Network error deleting verification run.", true);
  } finally {
    verifyDetailDeleteBtn.disabled = false;
  }
});

