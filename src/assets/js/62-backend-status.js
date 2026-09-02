// ---------------------------------------------------------------------------
// CLI backend readiness (installed + workspace-writable) — shown on Home (workspace
// info area) and Settings (AI Backend & Model), sourced from state.backendsStatus
// (see refreshSpecTree / renderSettings, populated from /api/tree and /api/config).
// ---------------------------------------------------------------------------
function backendStatusLabel(info) {
  if (!state.workspaceInitialized) return `${info.label} — no workspace open yet`;
  if (!info.installed) return `${info.label} — CLI not found on PATH`;
  if (!info.writable) return `${info.label} — workspace folder is not writable`;
  return `${info.label} — ready`;
}

function renderBackendStatus() {
  const items = Object.values(state.backendsStatus).map((info) => ({
    ok: !!(info && info.ready),
    label: backendStatusLabel(info),
  }));
  if (homeBackendStatusList) renderGateChecklist(homeBackendStatusList, items);
  if (settingsBackendStatusList) renderGateChecklist(settingsBackendStatusList, items);
}

// Lets the user re-check CLI availability on demand (e.g. right after installing/logging
// into a CLI, or fixing workspace folder permissions) without reloading the whole Settings
// form — re-populating the backend <select>s below preserves whatever's currently chosen
// (including unsaved edits), it only refreshes each option's "(not ready)" annotation.
settingsDetectBackendsBtn.addEventListener("click", async () => {
  settingsDetectBackendsBtn.disabled = true;
  try {
    const res = await fetch("/api/backends/status");
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not check CLI backends.", true); return; }
    state.backendsStatus = data.backends || {};
    renderBackendStatus();
    populateBackendSelect(settingsBackendClarify, settingsBackendClarify.value);
    populateBackendSelect(settingsBackendClarifyApply, settingsBackendClarifyApply.value);
    populateBackendSelect(settingsBackendPlan, settingsBackendPlan.value);
    populateBackendSelect(settingsBackendImplement, settingsBackendImplement.value);
    toast("CLI backend availability refreshed.");
  } catch (e) {
    toast("Network error checking CLI backends.", true);
  } finally {
    settingsDetectBackendsBtn.disabled = false;
  }
});

// Status snapshot shown above "Finalized Clarification" — see _clarify_finalize_status()
// in dashboard_clarify_parse.py for the server-side source of truth this mirrors. The
// checklist below IS the precondition: the button stays disabled until every item here
// is satisfied (a fresh evaluate on record, zero critical findings — or the Settings
// override — see st.ready below and _handle_clarify_run_start in dashboard_server.py,
// which enforces the same thing server-side).
// The finalize checklist's last row: what Finalize would still have to do to the backlog
// before/within its loop. Unanswered findings get filled in with their own recommendation;
// everything already answered rides along as the pending overlay and is written into the
// PRD by Finalize's single compaction pass at the end (see run_clarify_finalize).
