// ---------------------------------------------------------------------------
// Start Implementation requirement (Settings) — 3-way radio: the default (safest)
// "no critical or major", a relaxed "no critical" (major findings allowed), or "none"
// (no clarification-findings condition at all). See _implement_readiness_status in
// dashboard_clarify_parse.py for the shared server-side definition every dashboard
// surface (Home step 3, the Clarification ready banner, the Implementation gate, and
// the server-side gate in _handle_implement_run_start) is driven by.
// ---------------------------------------------------------------------------
function selectedImplementRequirement() {
  for (const input of settingsImplementRequirementInputs) if (input.checked) return input.value;
  return "no_critical_or_major";
}

const IMPLEMENT_REQUIREMENT_RISK = {
  no_critical: "Major findings are still allowed to affect implementation correctness and completeness, " +
    "not just polish — starting with open major findings means the implementation may need rework once " +
    "they're eventually resolved. Double-check the open major findings before proceeding.",
  none: "Critical findings are the ones most likely to affect correctness. Starting implementation while " +
    "critical (or major) findings are still open means the implementation may be built on ambiguous or " +
    "conflicting requirements, and could require significant rework once those findings are resolved. Use " +
    "this only if you're confident the open findings don't matter for what you're about to implement.",
};

function updateImplementRequirementWarning(requirement) {
  const risk = IMPLEMENT_REQUIREMENT_RISK[requirement];
  settingsImplementRequirementWarning.classList.toggle("hidden", !risk);
  if (risk) settingsImplementRequirementWarning.innerHTML = iconSvg("triangle-alert") + " " + escapeHtml(risk);
}

// Explain the risk before letting the user actually select a relaxed option — reverts
// to the previous selection if they back out, same pattern as "Allow finalizing with
// critical findings" above. No confirmation needed when moving back to the default.
let lastImplementRequirement = null;
for (const input of settingsImplementRequirementInputs) {
  input.addEventListener("change", async () => {
    const value = input.value;
    const risk = IMPLEMENT_REQUIREMENT_RISK[value];
    if (!risk) {
      lastImplementRequirement = value;
      updateImplementRequirementWarning(value);
      return;
    }
    const ok = await confirmModal(
      risk + "\n\nUse this setting anyway? (Remember to click Save Settings to apply.)",
      { title: "Relax the Start Implementation Requirement?", okLabel: "Use Anyway", danger: true });
    if (!ok) {
      for (const i of settingsImplementRequirementInputs) {
        i.checked = i.value === (lastImplementRequirement || "no_critical_or_major");
      }
      // Same reason as the allow-finalize revert: restoring .checked fires no event.
      recomputeSettingsDirty();
      return;
    }
    lastImplementRequirement = value;
    updateImplementRequirementWarning(value);
  });
}

