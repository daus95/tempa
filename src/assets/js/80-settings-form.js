// ---------------------------------------------------------------------------
// Settings (AI backend + model + run limits, backed by config.json)
// ---------------------------------------------------------------------------
// Same Status/Log tab mechanic as the Implementation and Clarification pages, just driven
// off a table because there are five pairs rather than two. The active tab deliberately
// survives leaving and re-entering Settings, like implTab/clarifyTab do.
const SETTINGS_TABS = {
  models: [settingsTabModelsBtn, settingsTabModelsPanel],
  runs: [settingsTabRunsBtn, settingsTabRunsPanel],
  guardrails: [settingsTabGuardrailsBtn, settingsTabGuardrailsPanel],
  notifications: [settingsTabNotificationsBtn, settingsTabNotificationsPanel],
  maintenance: [settingsTabMaintenanceBtn, settingsTabMaintenancePanel],
};

function setSettingsTab(tab) {
  state.settingsTab = tab;
  for (const [name, [btn, panel]] of Object.entries(SETTINGS_TABS)) {
    btn.classList.toggle("active", name === tab);
    panel.classList.toggle("hidden", name !== tab);
  }
  updateSettingsSaveBar();
}

for (const [name, [btn]] of Object.entries(SETTINGS_TABS)) {
  btn.addEventListener("click", () => setSettingsTab(name));
}

// Inline "More…"/"Hide…" toggle for long field descriptions (see .settings-more-toggle in
// dashboard.css). One delegated listener covers every instance across all tabs, including
// any added later, so no per-field wiring is needed.
document.addEventListener("click", (e) => {
  const toggle = e.target.closest("[data-more-toggle]");
  if (!toggle) return;
  const wrap = toggle.closest(".settings-field-desc");
  const extra = wrap ? wrap.querySelector(".settings-more-extra") : null;
  const moreBtn = wrap ? wrap.querySelector('[data-more-toggle="show"]') : null;
  if (!extra || !moreBtn) return;
  if (toggle.dataset.moreToggle === "show") {
    extra.classList.remove("hidden");
    moreBtn.classList.add("hidden");
    extra.querySelector('[data-more-toggle="hide"]').focus();
  } else {
    extra.classList.add("hidden");
    moreBtn.classList.remove("hidden");
    moreBtn.focus();
  }
});

// Updates and Restart Server run immediately and aren't part of the save payload, so an
// idle Maintenance tab says so instead of offering a Save that would do nothing visible.
// Pending edits made on another tab bring the button back — Save posts every tab at once,
// so hiding the only way to commit them there would be a trap.
function updateSettingsSaveBar() {
  const nothingToSave = state.settingsTab === "maintenance" && !state.settingsDirty;
  settingsDirtyHint.classList.toggle("hidden", !state.settingsDirty);
  settingsSaveBtn.classList.toggle("hidden", nothingToSave);
  settingsNothingToSave.classList.toggle("hidden", !nothingToSave);
}

// What each backend's model picker offers, per stage. This used to feed a <datalist>
// behind a text input, which is technically a suggestion list but has no visible affordance
// at all — users could not tell there was anything to pick from, and had to already know an
// id in order to type one. It now populates a real <select>.
//
// That makes this list load-bearing in a way it was not before, so CUSTOM_MODEL_VALUE below
// is not a footnote: a model released after this list still has to be reachable, or a stale
// catalog turns into a wall rather than a missing hint. The list is a shortcut, never the
// set of ids Tempa accepts — the server takes any id whose vendor the backend can serve
// (see tempa_backend.model_backend_mismatch).
const MODEL_OPTIONS_BY_BACKEND = {
  claude: [
    { value: "claude-opus-5", label: "Opus 5" },
    { value: "claude-sonnet-5", label: "Sonnet 5" },
  ],
  codex: [
    { value: "gpt-5.6-sol", label: "GPT 5.6 Sol" },
    { value: "gpt-5.6-terra", label: "GPT 5.6 Terra" },
  ],
  copilot: [
    { value: "auto", label: "Auto" },
    { value: "claude-opus-5", label: "Opus 5" },
    { value: "claude-sonnet-5", label: "Sonnet 5" },
    { value: "gpt-5.6-sol", label: "GPT 5.6 Sol" },
    { value: "gpt-5.6-terra", label: "GPT 5.6 Terra" },
  ],
};

// Sentinel <option> value for "the id I want is not in this list". Deliberately shaped so
// no real model id could collide with it, since the same <select> holds both.
const CUSTOM_MODEL_VALUE = "__custom__";

// Shown under the model field only for backends whose model access can be restricted by
// an organization admin (Copilot's model list is governed by the org's Copilot policy).
const MODEL_AVAILABILITY_NOTES = {
  copilot: "Model availability depends on your organization's GitHub Copilot administrator/policy.",
};

const BACKEND_OPTIONS = [
  { value: "claude", label: "Claude Code" },
  { value: "copilot", label: "GitHub Copilot CLI" },
  { value: "codex", label: "OpenAI Codex CLI" },
];

// Reasoning-effort catalogs — mirrors tempa_backend.py exactly (CLAUDE_EFFORT_LEVELS /
// COPILOT_EFFORT_LEVELS / CODEX_MODEL_REASONING_LEVELS / CODEX_DEFAULT_EFFORT_LEVELS). The
// server is the authoritative validator (see dashboard_server._handle_config_save) — this
// copy only drives which options the dropdown offers, same duplication precedent already
// accepted for MODEL_OPTIONS_BY_BACKEND above.
const CLAUDE_EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"];
const COPILOT_EFFORT_LEVELS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
const CODEX_UNIVERSAL_LEVELS = ["none", "minimal"];
const CODEX_MODEL_REASONING_LEVELS = {
  "gpt-5.6-sol": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh", "max", "ultra"],
  "gpt-5.6-terra": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh", "max", "ultra"],
  "gpt-5.6-luna": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh", "max"],
  "codex-auto-review": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh", "max"],
  "gpt-5.5": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh"],
  "gpt-5.4": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh"],
  "gpt-5.4-mini": [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh"],
};
const CODEX_DEFAULT_EFFORT_LEVELS = [...CODEX_UNIVERSAL_LEVELS, "low", "medium", "high", "xhigh"];

// Vendor families — mirrors tempa_backend.py's MODEL_VENDOR_PREFIXES / MODEL_VENDOR_EXACT_IDS
// and each Backend.model_vendors. As with the effort catalogs above, the server is the
// authoritative validator (dashboard_api_settings._validate_stage_settings rejects the save);
// this copy only lets the form say so before the user gets that far.
//
// An id matching no family has no known vendor and is never flagged — the model field is
// free text and an unrecognized or future id has to keep working. Note copilot lists BOTH
// vendors: it proxies several providers, so claude-sonnet-5 on Copilot is valid.
const MODEL_VENDOR_PREFIXES = [
  ["anthropic", ["claude-"]],
  ["openai", ["gpt-", "o3-", "o4-", "codex-"]],
];
const MODEL_VENDOR_LABELS = { anthropic: "Anthropic", openai: "OpenAI" };
const MODEL_VENDOR_EXACT_IDS = {
  "opus": "anthropic", "opus-5": "anthropic",
  "sonnet": "anthropic", "sonnet-5": "anthropic",
  "haiku": "anthropic", "haiku-4.5": "anthropic",
  "fable": "anthropic", "fable-5": "anthropic",
};
const BACKEND_MODEL_VENDORS = {
  claude: ["anthropic"],
  codex: ["openai"],
  copilot: ["anthropic", "openai"],
};

function modelVendor(model) {
  const value = (model || "").trim().toLowerCase();
  if (!value) return null;
  if (MODEL_VENDOR_EXACT_IDS[value]) return MODEL_VENDOR_EXACT_IDS[value];
  for (const [vendor, prefixes] of MODEL_VENDOR_PREFIXES) {
    if (prefixes.some((prefix) => value.startsWith(prefix))) return vendor;
  }
  return null;
}

// The offending vendor when this backend cannot serve it, else null.
function modelBackendMismatch(backendName, model) {
  const vendor = modelVendor(model);
  if (!vendor) return null;
  const served = BACKEND_MODEL_VENDORS[backendName] || BACKEND_MODEL_VENDORS.claude;
  return served.includes(vendor) ? null : vendor;
}

const SMTP_PROVIDER_PRESETS = {
  gmail: {
    host: "smtp.gmail.com", port: 587, security: "starttls",
    guidance: "Use your Gmail address as the username. Enable 2-Step Verification, then <a href=\"https://myaccount.google.com/apppasswords\" target=\"_blank\" rel=\"noopener\">open Google App Passwords</a>, enter <strong>Tempa</strong> as the app name, and copy the generated 16-character password here. Your normal Google password will not work.",
  },
  office365: {
    host: "smtp.office365.com", port: 587, security: "starttls",
    guidance: "Use your Microsoft 365 email address as the username. <a href=\"https://mysignins.microsoft.com/security-info\" target=\"_blank\" rel=\"noopener\">Open Microsoft Security info</a>, choose <strong>Add method</strong> → <strong>App password</strong>, name it <strong>Tempa</strong>, then copy the generated password here. If App password is unavailable, ask your Microsoft 365 administrator to enable it and Authenticated SMTP for the mailbox.",
  },
  custom: {
    guidance: "Enter the SMTP host, port, security method, username, and password supplied by your email provider.",
  },
};

function updateSmtpProvider(applyPreset) {
  const preset = SMTP_PROVIDER_PRESETS[settingsEmailProvider.value] || SMTP_PROVIDER_PRESETS.custom;
  settingsEmailProviderGuidance.innerHTML = preset.guidance;
  if (applyPreset && preset.host) {
    settingsEmailHost.value = preset.host;
    settingsEmailPort.value = preset.port;
    settingsEmailSecurity.value = preset.security;
  }
}

// SMTP credentials, recipients and the alert-event grid are the bulk of this tab and mean
// nothing while alerts are off, so they collapse out of the way. They are still submitted
// and reloaded exactly as before — the save payload reads the elements directly, and
// display:none doesn't change that.
function updateEmailDetailsVisibility() {
  settingsEmailDetails.classList.toggle("hidden", !settingsEmailEnabled.checked);
}

settingsEmailEnabled.addEventListener("change", updateEmailDetailsVisibility);

const EMAIL_ALERT_EVENTS = [
  ["authentication_required", "Authentication required", "The configured AI CLI login or API key must be renewed."],
  ["implementation_failed", "Implementation failed", "An epic stopped on a non-retryable implementation failure."],
  ["implementation_auto_reordered", "Implementation auto-reordered", "An epic was stuck waiting on a not-yet-implemented epic and Tempa reordered the plan automatically — informational, no action needed unless it recurs."],
  ["implementation_decision_required", "Implementation decision required", "A feature cannot be finished without a decision only you can make (e.g. drop it, change the spec, accept a risk). The rest of the plan keeps running while it waits."],
  ["plan_failed", "Planning failed", "Tempa could not generate or review the implementation plan."],
  ["session_limit_reached", "Implementation run limit reached", "An epic reached the configured anti-loop session limit."],
  ["qa_limit_reached", "QA run limit reached", "An epic reached its configured QA run limit without ever passing and was marked failed; review is required."],
  ["qa_oscillation_detected", "QA loop detected", "An epic keeps failing QA in circles — each round's fix undoes an earlier one — so the run was stopped."],
  ["clarification_answers_required", "Clarification answers required", "Critical or major specification findings need a human decision."],
  ["clarification_limit_reached", "Clarification run limit reached", "Finalized clarification stopped with unresolved findings."],
  ["clarification_failed", "Clarification failed", "A non-retryable evaluation or apply pass stopped."],
  ["confirmation_required", "Confirmation required", "The terminal is waiting for your choice to run another clarification round."],
  ["verification_failed", "Verification failed", "An epic verification failed or did not produce its report."],
  ["backend_test_failed", "Backend test failed", "The configured AI CLI permission test did not complete."],
  ["backend_model_mismatch", "Backend/model mismatch", "A stage is configured with a model its CLI backend cannot run, so the session was stopped before the CLI was even started."],
  ["implementation_qa_state_repaired", "QA state repaired", "Tempa found an inconsistent QA record for an epic and repaired it — informational, no action needed unless it recurs."],
];

function renderEmailEventChoices(selectedEvents) {
  const selected = new Set(selectedEvents || []);
  settingsEmailEventList.innerHTML = "";
  for (const [value, name, description] of EMAIL_ALERT_EVENTS) {
    const row = document.createElement("label");
    row.className = "settings-checkbox-row";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "settingsEmailEvent";
    input.value = value;
    input.checked = selected.has(value);
    row.title = description;
    input.title = description;
    const copy = document.createElement("span");
    copy.className = "settings-checkbox-name";
    copy.textContent = name;
    row.append(input, copy);
    settingsEmailEventList.appendChild(row);
  }
}

function selectedEmailEvents() {
  return [...settingsEmailEventList.querySelectorAll('input[name="settingsEmailEvent"]:checked')].map(input => input.value);
}

function getReasoningEffortChoices(backendName, model) {
  if (backendName === "codex") {
    return CODEX_MODEL_REASONING_LEVELS[(model || "").trim().toLowerCase()] || CODEX_DEFAULT_EFFORT_LEVELS;
  }
  return backendName === "copilot" ? COPILOT_EFFORT_LEVELS : CLAUDE_EFFORT_LEVELS;
}

// Always offers "" ("(default)" — no override) plus the valid levels for backendName+model.
// If currentValue isn't among them (e.g. left over from before the model/backend changed),
// it's kept as a visibly-flagged extra option instead of being silently dropped, so the user
// notices before Save rejects it with the server's 400.
function populateEffortSelect(selectEl, backendName, model, currentValue) {
  if (!selectEl) return;
  selectEl.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = "(default)";
  selectEl.appendChild(defaultOpt);
  let found = !currentValue;
  for (const level of getReasoningEffortChoices(backendName, model)) {
    const el = document.createElement("option");
    el.value = level;
    el.textContent = level;
    if (level === currentValue) { el.selected = true; found = true; }
    selectEl.appendChild(el);
  }
  if (!found) {
    const el = document.createElement("option");
    el.value = currentValue;
    el.textContent = `⚠ ${currentValue} (not supported by this model)`;
    el.selected = true;
    selectEl.appendChild(el);
  }
}

// Fills a stage's model picker for `backendName` and selects whatever `currentModel` is.
// An id the catalog does not list — set from the CLI, hand-edited into config.json, saved by
// an older Tempa, or simply newer than this list — falls to the custom option ("Other model
// id…") and keeps its value in the text input rather than being silently dropped, so what
// the form shows is always what is configured. Same choice populateEffortSelect
// already makes for an effort level that stopped being valid: show what is actually
// configured, never quietly replace it.
function populateModelSelect(selectEl, modelInput, backendName, currentModel) {
  if (!selectEl) return;
  const options = MODEL_OPTIONS_BY_BACKEND[backendName] || MODEL_OPTIONS_BY_BACKEND.claude;
  const model = (currentModel || "").trim();
  const known = options.some((opt) => opt.value === model);

  selectEl.innerHTML = "";
  for (const opt of options) {
    const el = document.createElement("option");
    el.value = opt.value;
    el.textContent = opt.label + " (" + opt.value + ")";
    selectEl.appendChild(el);
  }
  const custom = document.createElement("option");
  custom.value = CUSTOM_MODEL_VALUE;
  // Spelled out rather than a bare "Custom…": this is the escape hatch that keeps a model
  // newer than the list above usable, so it has to read as an ordinary choice, not an
  // expert one someone would hesitate over. Kept short enough to survive the column width
  // too — a truncated "Custom — enter another model i" is exactly the wrong thing to
  // half-say about the option that stops a stale catalog becoming a wall.
  custom.textContent = "Other model id…";
  selectEl.appendChild(custom);

  selectEl.value = known ? model : CUSTOM_MODEL_VALUE;
  syncCustomModelInput(selectEl, modelInput);
}

// The text input stays the single source of truth for the saved value (buildSettingsPayload
// reads it, and always did), so picking from the list writes through to it. It is only
// visible in Custom mode — in list mode the <select> is already showing the same thing.
function syncCustomModelInput(selectEl, modelInput) {
  const isCustom = selectEl.value === CUSTOM_MODEL_VALUE;
  modelInput.classList.toggle("hidden", !isCustom);
  if (!isCustom) modelInput.value = selectEl.value;
}

// The mismatch warning takes precedence over the availability note, though the two can
// never actually collide: copilot is the only backend with a note, and it serves every
// vendor this table knows about, so it never mismatches.
function updateModelAvailabilityNote(noteEl, backendName, model) {
  if (!noteEl) return;
  const vendor = modelBackendMismatch(backendName, model);
  if (vendor) {
    const backendLabel = (BACKEND_OPTIONS.find((o) => o.value === backendName) || {}).label || backendName;
    noteEl.textContent = `${MODEL_VENDOR_LABELS[vendor] || vendor} model — ${backendLabel} cannot run it. `
      + "Pick a model this backend serves, or change the backend. Saving is blocked until you do.";
    noteEl.classList.add("warn");
    noteEl.classList.remove("hidden");
    return;
  }
  const note = MODEL_AVAILABILITY_NOTES[backendName];
  noteEl.textContent = note || "";
  noteEl.classList.remove("warn");
  noteEl.classList.toggle("hidden", !note);
}

function populateBackendSelect(selectEl, currentValue) {
  selectEl.innerHTML = "";
  let found = false;
  for (const opt of BACKEND_OPTIONS) {
    const el = document.createElement("option");
    el.value = opt.value;
    const info = state.backendsStatus[opt.value];
    el.textContent = info && !info.ready ? `${opt.label} (not ready)` : opt.label;
    if (opt.value === currentValue) { el.selected = true; found = true; }
    selectEl.appendChild(el);
  }
  // Keep an unrecognized/legacy backend value usable instead of silently swapping it out.
  if (!found && currentValue) {
    const el = document.createElement("option");
    el.value = currentValue;
    el.textContent = currentValue;
    el.selected = true;
    selectEl.appendChild(el);
  }
}

// Wires a stage's backend <select> + model picker + custom-model <input> so any of the
// three changing refreshes that stage's model options, availability/mismatch note, and
// reasoning-effort options.
//
// A backend change still never clobbers the configured model — it is free text and
// switching backends shouldn't discard it. What that means concretely now: a model absent
// from the new backend's list falls through to Custom, keeping its value visible, and the
// mismatch note (rather than a silent reset) is what tells the user the pair cannot run.
function wireBackendModelStage(backendSelect, modelSelect, modelInput, modelNote, effortSelect) {
  const refresh = () => {
    updateModelAvailabilityNote(modelNote, backendSelect.value, modelInput.value);
    populateEffortSelect(effortSelect, backendSelect.value, modelInput.value, effortSelect.value);
  };
  backendSelect.addEventListener("change", () => {
    populateModelSelect(modelSelect, modelInput, backendSelect.value, modelInput.value);
    refresh();
  });
  modelSelect.addEventListener("change", () => {
    syncCustomModelInput(modelSelect, modelInput);
    // Switching INTO Custom keeps whatever id was already there, so the user edits rather
    // than retypes — and lands the caret where they are about to work.
    if (modelSelect.value === CUSTOM_MODEL_VALUE) modelInput.focus();
    refresh();
  });
  modelInput.addEventListener("input", refresh);
}
wireBackendModelStage(settingsBackendClarify, settingsModelSelectClarify, settingsModelClarify, settingsModelNoteClarify, settingsEffortClarify);
wireBackendModelStage(settingsBackendClarifyApply, settingsModelSelectClarifyApply, settingsModelClarifyApply, settingsModelNoteClarifyApply, settingsEffortClarifyApply);
wireBackendModelStage(settingsBackendPlan, settingsModelSelectPlan, settingsModelPlan, settingsModelNotePlan, settingsEffortPlan);
wireBackendModelStage(settingsBackendImplement, settingsModelSelectImplement, settingsModelImplement, settingsModelNoteImplement, settingsEffortImplement);

function fillSettingsForm(config) {
  state.backendsStatus = config.backends_status || state.backendsStatus;
  renderBackendStatus();
  populateBackendSelect(settingsBackendClarify, config.backends.clarify);
  populateBackendSelect(settingsBackendClarifyApply, config.backends.clarify_apply);
  populateBackendSelect(settingsBackendPlan, config.backends.plan);
  populateBackendSelect(settingsBackendImplement, config.backends.implement);
  settingsModelClarify.value = config.models.clarify;
  settingsModelClarifyApply.value = config.models.clarify_apply;
  settingsModelPlan.value = config.models.plan;
  settingsModelImplement.value = config.models.implement;
  // Model inputs first: populateModelSelect reads the saved id to decide whether it can be
  // offered as a list entry or has to land on Custom, so filling the pickers before the
  // inputs would make every stage look Custom on load.
  populateModelSelect(settingsModelSelectClarify, settingsModelClarify, config.backends.clarify, config.models.clarify);
  populateModelSelect(settingsModelSelectClarifyApply, settingsModelClarifyApply, config.backends.clarify_apply, config.models.clarify_apply);
  populateModelSelect(settingsModelSelectPlan, settingsModelPlan, config.backends.plan, config.models.plan);
  populateModelSelect(settingsModelSelectImplement, settingsModelImplement, config.backends.implement, config.models.implement);
  // After the model inputs are populated, not before: the note now depends on the model as
  // well as the backend, so computing it first would hide a mismatch already saved in
  // config.json until the user happened to touch a field.
  updateModelAvailabilityNote(settingsModelNoteClarify, config.backends.clarify, config.models.clarify);
  updateModelAvailabilityNote(settingsModelNoteClarifyApply, config.backends.clarify_apply, config.models.clarify_apply);
  updateModelAvailabilityNote(settingsModelNotePlan, config.backends.plan, config.models.plan);
  updateModelAvailabilityNote(settingsModelNoteImplement, config.backends.implement, config.models.implement);
  populateEffortSelect(settingsEffortClarify, config.backends.clarify, config.models.clarify, config.reasoning_efforts.clarify);
  populateEffortSelect(settingsEffortClarifyApply, config.backends.clarify_apply, config.models.clarify_apply, config.reasoning_efforts.clarify_apply);
  populateEffortSelect(settingsEffortPlan, config.backends.plan, config.models.plan, config.reasoning_efforts.plan);
  populateEffortSelect(settingsEffortImplement, config.backends.implement, config.models.implement, config.reasoning_efforts.implement);
  settingsFeaturesPerSession.value = config.features_per_session == null ? "" : config.features_per_session;
  settingsMaxSessionRun.value = config.max_session_run == null ? "" : config.max_session_run;
  settingsMaxClarificationRun.value = config.max_clarification_run == null ? "" : config.max_clarification_run;
  settingsFinalizeNoProgressRounds.value = config.finalize_no_progress_rounds ?? 5;
  // null (checkpoints off) has to render as blank, not as the string "null" — same
  // nullable handling max_session_run gets above.
  settingsFinalizeCheckpointRounds.value =
    config.finalize_checkpoint_rounds == null ? "" : config.finalize_checkpoint_rounds;
  settingsFinalizeCheckpointCommit.checked = config.finalize_checkpoint_commit !== false;
  settingsQaLoopStrikes.value = config.qa_loop_strikes ?? 2;
  settingsMaxQaFailRounds.value = config.max_qa_fail_rounds ?? 6;
  settingsCommitAfterQaPass.checked = config.commit_after_qa_pass !== false;
  settingsTerminateLeftoverProcesses.checked = config.terminate_leftover_processes !== false;
  settingsAllowFinalizeWithCritical.checked = !!config.allow_finalize_with_critical;
  settingsAllowFinalizeWithCriticalWarning.classList.toggle("hidden", !config.allow_finalize_with_critical);
  const requirement = config.implementation_start_requirement || "no_critical_or_major";
  for (const input of settingsImplementRequirementInputs) input.checked = input.value === requirement;
  updateImplementRequirementWarning(requirement);
  lastImplementRequirement = requirement;
  const email = (config.notifications || {}).email || {};
  settingsEmailEnabled.checked = !!email.enabled;
  settingsEmailProvider.value = email.provider || "custom";
  settingsEmailHost.value = email.smtp_host || "";
  settingsEmailPort.value = email.smtp_port || 587;
  settingsEmailSecurity.value = email.security || "starttls";
  settingsEmailUsername.value = email.smtp_username || "";
  settingsEmailPassword.value = email.smtp_password || "";
  settingsEmailFrom.value = email.from || "";
  settingsEmailRecipients.value = (email.recipients || []).join(", ");
  renderEmailEventChoices(email.events || []);
  updateSmtpProvider(false);
  updateEmailDetailsVisibility();
  settingsUsageLimitRetryWaitMin.value = Math.round((config.usage_limit_retry_wait_sec ?? 1800) / 60);
  settingsUsageLimitHeartbeatMin.value = Math.round((config.usage_limit_heartbeat_sec ?? 300) / 60);
  settingsServerOverloadRetryWaitMin.value = Math.round((config.server_overloaded_retry_wait_sec ?? 300) / 60);
  settingsPollIntervalSec.value = config.poll_interval_sec ?? 60;
  // Last, so the snapshot covers every field this function just wrote.
  clearSettingsDirty();
}

