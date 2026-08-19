async function renderSettings() {
  settingsSaveStatus.textContent = "";
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not load settings.", true); return; }
    fillSettingsForm(data.config);
  } catch (e) {
    toast("Network error loading settings.", true);
  }
  renderUpdateStatus();
}

// Set by renderUpdateStatus whenever an update is available, so the "What's New" click
// handler below knows which release's changelog to ask for without re-parsing the
// "Update available: X" label text.
let settingsUpdateLatestVersion = null;

async function renderUpdateStatus() {
  settingsUpdateStatus.textContent = "";
  settingsUpdateStatus.classList.remove("err");
  settingsUpdateCurrent.textContent = "—";
  settingsUpdateLatest.textContent = "Checking…";
  settingsUpdateBtn.classList.add("hidden");
  settingsWhatsNewBtn.classList.add("hidden");
  settingsUpdateLatestVersion = null;
  try {
    const res = await fetch("/api/update/status");
    const data = await res.json();
    if (!data.ok) {
      settingsUpdateLatest.textContent = "";
      settingsUpdateStatus.textContent = data.error || "Could not check for updates.";
      settingsUpdateStatus.classList.add("err");
      return;
    }
    settingsUpdateCurrent.textContent = data.current;
    if (data.latest == null) {
      settingsUpdateLatest.textContent = "";
      settingsUpdateStatus.textContent = data.error || "Could not reach GitHub to check the latest release.";
      settingsUpdateStatus.classList.add("err");
      return;
    }
    if (data.updateAvailable) {
      settingsUpdateLatest.textContent = `Update available: ${data.latest}`;
      settingsUpdateBtn.classList.remove("hidden");
      settingsUpdateLatestVersion = data.latest;
      settingsWhatsNewBtn.classList.remove("hidden");
    } else {
      settingsUpdateLatest.textContent = "Up to date.";
    }
  } catch (e) {
    settingsUpdateLatest.textContent = "";
    settingsUpdateStatus.textContent = "Network error checking for updates.";
    settingsUpdateStatus.classList.add("err");
  }
}

settingsWhatsNewBtn.addEventListener("click", () => {
  if (!settingsUpdateLatestVersion) return;
  openFileViewerModal(
    "/api/update/changelog?latest=" + encodeURIComponent(settingsUpdateLatestVersion),
    `What's New in ${settingsUpdateLatestVersion}`,
    { markdown: true });
});

// Passive, silent counterpart to renderUpdateStatus() above: checked once on page load so
// the sidebar can flag a new release without the user having to open Settings first.
async function checkForSidebarUpdate() {
  try {
    const res = await fetch("/api/update/status");
    const data = await res.json();
    if (data.ok && data.updateAvailable && data.latest != null) {
      state.updateAvailable = true;
      state.updateLatestVersion = data.latest;
      renderSidebar();
    }
  } catch (e) {
    // Silent -- this is a passive notice, not a user-initiated action.
  }
}

settingsCheckUpdateBtn.addEventListener("click", async () => {
  settingsCheckUpdateBtn.disabled = true;
  try {
    await renderUpdateStatus();
  } finally {
    settingsCheckUpdateBtn.disabled = false;
  }
});

settingsUpdateBtn.addEventListener("click", async () => {
  const latestLabel = settingsUpdateLatest.textContent.replace("Update available: ", "");
  const ok = await confirmModal(
    `This will download and install Tempa ${latestLabel} over this installation.\n` +
    "You will need to restart the Tempa application afterward — reloading this page is not enough.",
    { title: "Update Tempa", okLabel: "Update Now" });
  if (!ok) return;
  settingsUpdateBtn.disabled = true;
  settingsCheckUpdateBtn.disabled = true;
  settingsUpdateStatus.textContent = "Downloading and applying update…";
  settingsUpdateStatus.classList.remove("err");
  try {
    const res = await fetch("/api/update/run", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      settingsUpdateStatus.textContent = data.error || "Update failed.";
      settingsUpdateStatus.classList.add("err");
      toast(data.error || "Update failed.", true);
      return;
    }
    settingsUpdateStatus.textContent = `Updated to ${data.version}.`;
    settingsUpdateCurrent.textContent = data.version;
    settingsUpdateLatest.textContent = "Up to date.";
    settingsUpdateBtn.classList.add("hidden");
    await alertModal(
      `Tempa has been updated to version ${data.version}. Restart the Tempa application ` +
      "(close this dashboard and run \"tempa dashboard\" again, or restart any running " +
      "\"tempa implement\" session) so it picks up the new code. Reloading this page is " +
      "NOT enough — the running process still has the old code loaded in memory.",
      { title: "Restart Required" });
  } catch (e) {
    settingsUpdateStatus.textContent = "Network error while updating.";
    settingsUpdateStatus.classList.add("err");
    toast("Network error while updating.", true);
  } finally {
    settingsUpdateBtn.disabled = false;
    settingsCheckUpdateBtn.disabled = false;
  }
});

async function waitForServerAndReload() {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 500));
    try {
      const res = await fetch("/", { cache: "no-store" });
      if (res.ok) { window.location.reload(); return; }
    } catch (e) {
      // Still down (old process gone, new one not listening yet) -- keep polling.
    }
  }
  settingsRestartStatus.textContent =
    "The server did not come back on this address. It may be on a different port -- check the console/terminal.";
  settingsRestartStatus.classList.add("err");
  settingsRestartBtn.disabled = false;
}

settingsRestartBtn.addEventListener("click", async () => {
  const ok = await confirmModal(
    "This will stop and relaunch the dashboard server on the same port. " +
    "Any in-progress page state will be lost and the page will reload automatically once the server is back.",
    { title: "Restart Server", okLabel: "Restart", danger: true });
  if (!ok) return;
  settingsRestartBtn.disabled = true;
  settingsRestartStatus.textContent = "Restarting…";
  settingsRestartStatus.classList.remove("err");
  try {
    const res = await fetch("/api/server/restart", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      settingsRestartStatus.textContent = data.error || "Restart failed.";
      settingsRestartStatus.classList.add("err");
      toast(data.error || "Restart failed.", true);
      settingsRestartBtn.disabled = false;
      return;
    }
    settingsRestartStatus.textContent = "Waiting for the server to come back…";
    await waitForServerAndReload();
  } catch (e) {
    settingsRestartStatus.textContent = "Network error while restarting.";
    settingsRestartStatus.classList.add("err");
    toast("Network error while restarting.", true);
    settingsRestartBtn.disabled = false;
  }
});

settingsClearAllBtn.addEventListener("click", async () => {
  const ok = await confirmModal(
    "Are you sure you want to delete ALL data (plan, QA, log, and clarification results)?\n" +
    "Specification files will NOT be deleted.\n\nThis action CANNOT be undone.",
    { title: "Clear All Data", okLabel: "Clear All", danger: true });
  if (!ok) return;
  settingsClearAllBtn.disabled = true;
  settingsClearAllStatus.textContent = "Clearing…";
  settingsClearAllStatus.classList.remove("err");
  try {
    const res = await fetch("/api/clear", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      settingsClearAllStatus.textContent = data.error || "Clear failed.";
      settingsClearAllStatus.classList.add("err");
      toast(data.error || "Clear failed.", true);
      return;
    }
    settingsClearAllStatus.textContent = "All data cleared successfully.";
    toast("All data cleared successfully.");
    await refreshClarifyList();
    state.epics = [];
    state.implementStarted = false;
    renderHomeWorkflow();
  } catch (e) {
    settingsClearAllStatus.textContent = "Network error while clearing.";
    settingsClearAllStatus.classList.add("err");
    toast("Network error while clearing.", true);
  } finally {
    settingsClearAllBtn.disabled = false;
  }
});

// Doubles as the dirty-check snapshot (see recomputeSettingsDirty): this object *is* the
// definition of "what a Save would write", so comparing it against the config the form was
// last filled from is exactly the right test for whether anything is pending.
function buildSettingsPayload() {
  return {
    models: {
      clarify: settingsModelClarify.value,
      clarify_apply: settingsModelClarifyApply.value,
      plan: settingsModelPlan.value,
      implement: settingsModelImplement.value,
    },
    backends: {
      clarify: settingsBackendClarify.value,
      clarify_apply: settingsBackendClarifyApply.value,
      plan: settingsBackendPlan.value,
      implement: settingsBackendImplement.value,
    },
    reasoning_efforts: {
      clarify: settingsEffortClarify.value,
      clarify_apply: settingsEffortClarifyApply.value,
      plan: settingsEffortPlan.value,
      implement: settingsEffortImplement.value,
    },
    features_per_session: settingsFeaturesPerSession.value,
    max_session_run: settingsMaxSessionRun.value,
    max_clarification_run: settingsMaxClarificationRun.value,
    finalize_no_progress_rounds: settingsFinalizeNoProgressRounds.value,
    qa_loop_strikes: settingsQaLoopStrikes.value,
    max_qa_fail_rounds: settingsMaxQaFailRounds.value,
    commit_after_qa_pass: settingsCommitAfterQaPass.checked,
    terminate_leftover_processes: settingsTerminateLeftoverProcesses.checked,
    allow_finalize_with_critical: settingsAllowFinalizeWithCritical.checked,
    implementation_start_requirement: selectedImplementRequirement(),
    notifications: { email: {
      enabled: settingsEmailEnabled.checked, provider: settingsEmailProvider.value,
      smtp_host: settingsEmailHost.value,
      smtp_port: settingsEmailPort.value, security: settingsEmailSecurity.value,
      smtp_username: settingsEmailUsername.value, smtp_password: settingsEmailPassword.value,
      from: settingsEmailFrom.value,
      recipients: settingsEmailRecipients.value.split(",").map(v => v.trim()).filter(Boolean),
      events: selectedEmailEvents(),
    } },
    usage_limit_retry_wait_sec: Number(settingsUsageLimitRetryWaitMin.value) * 60,
    usage_limit_heartbeat_sec: Number(settingsUsageLimitHeartbeatMin.value) * 60,
    server_overloaded_retry_wait_sec: Number(settingsServerOverloadRetryWaitMin.value) * 60,
    poll_interval_sec: Number(settingsPollIntervalSec.value),
  };
}

// Snapshot of the payload as of the last fill, rather than a sticky "something changed"
// flag: the two confirm-gated controls below revert themselves when the user backs out of
// the modal, and only a value comparison notices that the form is unmodified again.
let settingsBaseline = null;

function recomputeSettingsDirty() {
  const dirty = settingsBaseline !== null && JSON.stringify(buildSettingsPayload()) !== settingsBaseline;
  if (dirty === state.settingsDirty) return;
  state.settingsDirty = dirty;
  // "Saved." sitting next to "Unsaved changes" reads as a contradiction.
  if (dirty) { settingsSaveStatus.textContent = ""; settingsSaveStatus.classList.remove("err"); }
  updateSettingsSaveBar();
}

function clearSettingsDirty() {
  settingsBaseline = JSON.stringify(buildSettingsPayload());
  state.settingsDirty = false;
  updateSettingsSaveBar();
}

// Both events bubble, so one pair of listeners covers every control on every tab —
// including the alert-event checkboxes, which are rebuilt from scratch on each fill.
// fillSettingsForm assigns .value/.checked programmatically, which fires neither event,
// so repopulating the form can't trip this.
settingsPane.addEventListener("input", recomputeSettingsDirty);
settingsPane.addEventListener("change", recomputeSettingsDirty);

settingsSaveBtn.addEventListener("click", async () => {
  settingsSaveBtn.disabled = true;
  settingsSaveStatus.textContent = "";
  settingsSaveStatus.classList.remove("err");
  try {
    const res = await fetch("/api/config/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildSettingsPayload()),
    });
    const data = await res.json();
    if (!data.ok) {
      settingsSaveStatus.textContent = data.error || "Could not save settings.";
      settingsSaveStatus.classList.add("err");
      return;
    }
    fillSettingsForm(data.config);
    settingsSaveStatus.textContent = "Saved.";
    toast("Settings saved.");
    // Saved fine, but a run already in flight can't pick the new value up — surfaced as a
    // modal rather than a toast because it contradicts what the user is about to watch
    // happen in the log (see _finalize_limit_change_warning in dashboard_runs.py).
    if (data.warning) {
      settingsSaveStatus.textContent = "Saved — applies from the next Finalized Clarification run.";
      await alertModal(data.warning, { title: "Finalized Clarification Is Already Running" });
    }
  } catch (e) {
    settingsSaveStatus.textContent = "Network error while saving.";
    settingsSaveStatus.classList.add("err");
  } finally {
    settingsSaveBtn.disabled = false;
  }
});

settingsTestEmailBtn.addEventListener("click", async () => {
  settingsTestEmailBtn.disabled = true;
  settingsTestEmailStatus.textContent = "Sending…";
  settingsTestEmailStatus.classList.remove("err");
  try {
    const res = await fetch("/api/notifications/test-email", { method: "POST" });
    const data = await res.json();
    settingsTestEmailStatus.textContent = data.message || (data.ok ? "Test email sent." : "Test email failed.");
    settingsTestEmailStatus.classList.toggle("err", !data.ok);
  } catch (e) {
    settingsTestEmailStatus.textContent = "Network error sending test email.";
    settingsTestEmailStatus.classList.add("err");
  } finally {
    settingsTestEmailBtn.disabled = false;
  }
});

settingsEmailProvider.addEventListener("change", () => updateSmtpProvider(true));
// These rebuild the checkbox list wholesale rather than ticking boxes, so no "change"
// reaches the delegated listener — the dirty state has to be recomputed by hand.
settingsEmailSelectAllBtn.addEventListener("click", () => {
  renderEmailEventChoices(EMAIL_ALERT_EVENTS.map(([value]) => value));
  recomputeSettingsDirty();
});
settingsEmailClearAllBtn.addEventListener("click", () => {
  renderEmailEventChoices([]);
  recomputeSettingsDirty();
});

// Explain the meaning and consequences before letting the user actually turn this on —
// reverts the switch if they back out. The warning banner stays visible below the field
// afterward as a standing reminder (see fillSettingsForm), since this is a real change in
// what Finalized Clarification is allowed to do, not a cosmetic preference.
settingsAllowFinalizeWithCritical.addEventListener("change", async () => {
  if (!settingsAllowFinalizeWithCritical.checked) {
    settingsAllowFinalizeWithCriticalWarning.classList.add("hidden");
    return;
  }
  const ok = await confirmModal(
    "Turning this on lets Finalized Clarification start even while critical findings are still " +
    "open, so its automated evaluate → apply loop will attempt to resolve those critical " +
    "issues on its own instead of requiring you to answer them by hand first. Critical findings " +
    "are the ones most likely to affect correctness, so letting automation resolve them " +
    "unsupervised carries real risk of a wrong or incomplete answer being applied to the PRD/spec " +
    "— review its results carefully afterward. This does not relax the separate Start " +
    "Implementation requirement, which is configured independently under \"Start Implementation\" " +
    "below.\n\nEnable anyway? (Remember to click Save Settings to apply.)",
    { title: "Allow Finalizing With Critical Findings?", okLabel: "Enable", danger: true });
  if (!ok) {
    // Reverting programmatically fires no "change", so the delegated listener never sees
    // the switch go back — without this the "Unsaved changes" hint would stick forever.
    settingsAllowFinalizeWithCritical.checked = false;
    recomputeSettingsDirty();
    return;
  }
  settingsAllowFinalizeWithCriticalWarning.classList.remove("hidden");
});

