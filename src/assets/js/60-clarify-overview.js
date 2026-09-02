// ---------------------------------------------------------------------------
// Clarification overview (right panel shown when "Clarification" itself is selected)
// ---------------------------------------------------------------------------
const SEVERITY_ICON = { critical: "icon-severity-critical", major: "icon-severity-major", minor: "icon-severity-minor" };
const SEVERITY_LABEL = { critical: "Critical", major: "Major", minor: "Minor" };

// One icon per severity that actually has findings, each carrying a native-tooltip
// title (e.g. "Critical: 2/2") — replaces the old separate Critical/Major/Minor
// columns so the table has room for the Started column.
function findingsCell(file) {
  const parts = [];
  for (const sev of ["critical", "major", "minor"]) {
    const counts = file[sev];
    if (!counts || !counts.total) continue;
    const cls = counts.answered === counts.total ? "count-ok" : "count-pending";
    parts.push(
      `<span class="findings-icon" title="${SEVERITY_LABEL[sev]}: ${counts.answered}/${counts.total}">` +
        `<span class="findings-icon-glyph">${iconSvg("circle", "filled " + SEVERITY_ICON[sev])}</span>` +
        `<span class="${cls}">${counts.answered}/${counts.total}</span>` +
      `</span>`
    );
  }
  return parts.length ? parts.join(" ") : "–";
}

function formatClarifyStartedAt(startedAt) {
  return formatEpochShort(startedAt);
}

function formatClarifyDuration(seconds) {
  if (seconds == null) return "–";
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function statusCell(file) {
  if (file.answered === file.total) {
    return `<span class="clarify-status-stack">` +
      `<span class="status-complete">${iconSvg("circle-check")} Complete</span>` +
      (file.applied ? `<span class="clarify-applied-badge">${iconSvg("circle-check")} Applied</span>` : "") +
      `</span>`;
  }
  return `<span class="status-pending">${iconSvg("circle-dashed")} ${file.answered}/${file.total}</span>`;
}

function renderClarifyOverviewRows(tbody, files, emptyMessage) {
  tbody.innerHTML = "";
  if (!files.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="4" class="empty-note">${escapeHtml(emptyMessage)}</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const file of files) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(file.name)}</td>` +
      `<td>${formatClarifyStartedAt(file.started_at)}</td>` +
      `<td>${findingsCell(file)}</td>` +
      `<td>${statusCell(file)}</td>`;
    tr.addEventListener("click", () => openClarifyRowDetail(file));
    tbody.appendChild(tr);
  }
}

// Clicking an Unanswered/Fully answered row shows this info dialog (file, started time,
// per-severity findings, status, and the clarify/apply durations recorded for it —
// see clarify_file_timings in config.json) rather than jumping straight into the
// full answer-editing view; "Open File" inside the dialog still does that.
function clarifyRowDetailRow(label, value) {
  return `<div class="clarify-detail-row"><span class="clarify-detail-label">${escapeHtml(label)}</span>` +
    `<span>${value}</span></div>`;
}

function clarifyRowDetailHtml(file) {
  const sevRow = (label, counts) => clarifyRowDetailRow(
    label, counts && counts.total ? `${counts.answered}/${counts.total} answered` : "None"
  );
  let html = "" +
    clarifyRowDetailRow("Started", formatClarifyStartedAt(file.started_at)) +
    sevRow("Critical", file.critical) +
    sevRow("Major", file.major) +
    sevRow("Minor", file.minor) +
    clarifyRowDetailRow("Status", file.answered === file.total
      ? "Complete" : `${file.answered}/${file.total} answered`);
  if ("applied" in file) html += clarifyRowDetailRow("Applied", file.applied ? "Yes" : "No");
  html += clarifyRowDetailRow("Clarification duration", formatClarifyDuration(file.clarify_seconds));
  html += clarifyRowDetailRow("Apply duration", formatClarifyDuration(file.apply_seconds));
  return html;
}

async function openClarifyRowDetail(file) {
  const openFile = await confirmModal(clarifyRowDetailHtml(file), {
    title: file.name, okLabel: "Open File", html: true,
  });
  if (openFile) openClarifyFile(file);
}

function renderClarifyOverview() {
  skipMinorFindingsToggle.checked = !!state.skipMinorFindings;
  renderClarifyOverviewRows(clarifyUnansweredTbody, state.clarifyUnanswered,
    clarifyUnansweredEmptyMessage(state.clarifySettled));
  renderClarifyOverviewRows(clarifyAnsweredTbody, state.clarifyAnswered,
    "No fully answered files yet.");
  setClarifyRunButtonsDisabled(state.clarifyRun.running);
  renderPendingOverlayCard();
  renderImplementReadyBanner();
}

// "Pending resolutions" card: answered findings that aren't in the PRD yet. They don't
// block clarifying (they're carried into every round as already-decided resolutions), so
// this is informational — until the count passes clarify_overlay_warn_findings, at which
// point it also points out that every evaluation is now carrying that much extra text.
// Nothing is ever applied automatically; Apply Answers stays the user's call.
function renderPendingOverlayCard() {
  const overlay = state.clarifyPendingOverlay;
  const threshold = state.clarifyOverlayWarnThreshold;
  clarifyOverlayCard.classList.toggle("hidden", !overlay.findings);
  if (!overlay.findings) return;
  clarifyOverlayBadge.textContent = `${overlay.findings} pending`;
  clarifyOverlayBadge.classList.remove("hidden");
  const size = overlay.chars >= 1024 ? ` (~${Math.round(overlay.chars / 1024)} KB)` : "";
  let text = `${overlay.findings} answered finding(s) across ${overlay.files} clarification ` +
    `round(s)${size} aren't in the PRD yet. They're carried into every clarification round as ` +
    "already-decided resolutions, so you can keep clarifying without applying. Click Apply " +
    "Answers to write them in — required before Start Implementation.";
  const warn = overlay.findings >= threshold;
  if (warn) {
    text += " That's a lot to carry — applying now keeps each evaluation cheaper and the PRD " +
      "authoritative.";
  }
  clarifyOverlayCard.classList.toggle("warn", warn);
  clarifyOverlayHint.textContent = text;
}

// Shared copy for state.clarifySettled (see _clarification_settled_status in
// dashboard_clarify_parse.py), used by the Clarification overview, its Finalize readiness
// panel, and the Home page's step 2 — the same reason implementReadyMessage below is shared:
// three surfaces that must never word the same state differently.
function findingsPhrase(critical, major) {
  const parts = [];
  if (critical > 0) parts.push(`${critical} critical`);
  if (major > 0) parts.push(`${major} major`);
  return parts.length ? parts.join(" and ") + " finding(s)" : "no findings";
}

// Plain text only — renderClarifyOverviewRows puts this through escapeHtml.
function clarifyUnansweredEmptyMessage(cs) {
  switch (cs.reason) {
    case "settled":
      return "Nothing left to clarify — the latest clarification round found no open findings " +
        "in your specification.";
    case "spec_changed":
      return "The specification changed since the last clarification round — run Continue " +
        "Clarification to re-check it.";
    case "needs_recheck":
      return "Every finding has been answered, but the latest evaluation still lists " +
        `${findingsPhrase(cs.critical, cs.major)}. Answering doesn't update that count — run ` +
        "Continue Clarification to re-check them.";
    case "apply_only":
      return "Your answers were applied to the PRD, but nothing has been re-evaluated since. " +
        "Run Continue Clarification to confirm what's left.";
    case "sweep_pending":
      return "The last round only swept critical findings — majors haven't been checked yet. " +
        "Run Continue Clarification once more to sweep majors.";
    case "minors_open":
      return `The latest evaluation still lists ${cs.minor} minor finding(s). Turn on "Only ` +
        'evaluate critical & major findings" above to skip them, or run Continue Clarification ' +
        "to work through them.";
    case "never_run":
      return "No clarification has run yet — click Start Clarification to find the open " +
        "questions in your specification.";
    default:
      // "unanswered" — unreachable as an empty state (the table has rows), kept so the
      // switch stays total against _clarification_settled_status's reason enum.
      return "No unanswered files.";
  }
}

// Appended to the success toast of the four /api/spec/* mutation routes. The server sets
// "clarificationStale" only when there was a clarification result to invalidate (see
// _send_spec_mutation in dashboard_server.py), so a fresh workspace's first upload stays
// quiet. One toast rather than two — they auto-dismiss in 2.2s and would collide.
function clarifyStaleToastSuffix(data) {
  return data && data.clarificationStale
    ? " — the specification changed, so clarification needs another round."
    : "";
}

// The <button title> on the two buttons settled disables. Mentions Apply Answers when the
// overlay is the only thing left, since that button deliberately stays enabled.
function clarifySettledTitle(overlay) {
  const base = "Nothing left to clarify — the latest round found no open findings. " +
    "Edit the specification to re-open clarification.";
  return overlay.findings
    ? `${base} ${overlay.findings} answered finding(s) still need writing into the PRD — ` +
      "click Apply Answers."
    : base;
}

// The persistent explanation under "Finalize readiness", and the basis for Home step 2's
// status line.
function clarifySettledHint(overlay) {
  const base = "Clarification is settled: every finding is answered and the latest round " +
    "found nothing new. Start Clarification and Finalized Clarification stay disabled until " +
    "the specification changes.";
  return overlay.findings
    ? `${base} Click Apply Answers to write the remaining ${overlay.findings} answered ` +
      "finding(s) into the PRD before starting implementation."
    : base;
}

// Shared copy for state.implementReadiness (see _implement_readiness_status in
// dashboard_clarify_parse.py), used by the Home page's step 3, the Clarification
// overview's ready-for-implementation banner, and the toast shown if Start
// Implementation is somehow clicked while blocked (client-side disabling should
// normally prevent that, this is the fallback).
function implementReadyMessage(ir) {
  if (ir.requirement === "none") {
    return "No clarification-findings requirement is configured — ready to start implementation.";
  }
  if (ir.requirement === "no_critical") {
    return "No critical findings remain — ready to start implementation. Any open major/minor findings " +
      "will carry into implementation.";
  }
  return "No critical or major findings remain — ready to start implementation. Minor findings will be " +
    "resolved during implementation.";
}

// The pending-overlay branch comes first when it's the only thing blocking: reporting a
// findings count to someone whose latest round found none reads as "0 findings must be
// resolved", which is both wrong and unactionable. Mirrors the server-side wording in
// _handle_implement_run_start (dashboard_server.py).
function implementBlockedMessage(ir) {
  if (ir.pendingOverlay && ir.critical === 0 && ir.major === 0) {
    return `${ir.pendingOverlay} answered finding(s) still need writing into the PRD — click Apply Answers.`;
  }
  if (ir.specChanged) {
    return "The specification changed after the last clarification round, so it hasn't been " +
      "evaluated in its current form. Run Continue Clarification to re-check it.";
  }
  if (ir.requirement === "no_critical") {
    return `Still ${ir.critical} critical finding(s) that must be resolved.`;
  }
  if (ir.severitySweepPending && ir.critical === 0) {
    return "The last clarification round only checked for critical findings — majors haven't " +
      "been swept yet. Run Continue Clarification once more to sweep majors.";
  }
  return `Still ${ir.critical} critical and ${ir.major} major finding(s) that must be resolved.`;
}

function implementBlockedToast(ir) {
  if (!ir.hasRun) return "Run clarification first.";
  if (ir.pendingOverlay && ir.critical === 0 && ir.major === 0) {
    return "Apply your answers to the PRD first — implementation reads the PRD, not the clarification files.";
  }
  if (ir.specChanged) return "The specification changed — run Continue Clarification to re-check it.";
  if (ir.requirement === "no_critical") return "There are still critical findings — resolve clarification first.";
  if (ir.severitySweepPending && ir.critical === 0) {
    return "Majors haven't been swept yet — run Continue Clarification once more.";
  }
  return "There are still critical/major findings — resolve clarification first.";
}

// Mirrors the same gate as the Home page's step 3 (see renderHomeWorkflow) — shown on
// the Clarification overview so the user doesn't have to go back to Home to notice
// they can move on to implementation. Both are driven by the same server-computed
// state.implementReadiness, which already accounts for "hasRun" (a workspace where
// clarification was never run has zero findings simply from having no clarification
// files yet — without that check this would trivially look "ready").
function renderImplementReadyBanner() {
  const ir = state.implementReadiness;
  implementReadyBanner.classList.toggle("hidden", !ir.ready);
  if (ir.ready) implementReadyBannerText.innerHTML = `<strong>${iconSvg("circle-check")} Ready for implementation.</strong> ${implementReadyMessage(ir)}`;
}

clarifyStartImplementBtn.addEventListener("click", async () => {
  await selectTop("implementation");
  startImplementRun();
});

downloadPrdZipBtn.addEventListener("click", () =>
  downloadZip("/api/spec/download-zip", "prd-specs.zip",
    "Downloaded the full set of PRDs that have had answers applied."));

skipMinorFindingsToggle.addEventListener("change", async () => {
  const checked = skipMinorFindingsToggle.checked;
  state.skipMinorFindings = checked;
  try {
    const res = await fetch("/api/clarify/skip-minor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skip_minor_findings: checked }),
    });
    const data = await res.json();
    if (!data.ok) {
      state.skipMinorFindings = !checked;
      skipMinorFindingsToggle.checked = !checked;
      toast(data.error || "Could not save this setting.", true);
    }
  } catch (e) {
    state.skipMinorFindings = !checked;
    skipMinorFindingsToggle.checked = !checked;
    toast("Network error while saving.", true);
  }
});

// ---------------------------------------------------------------------------
// Clarification run (Start Clarification / Finalized Clarification / Apply Answers
// + log panel)
// ---------------------------------------------------------------------------
// Shared renderer for the readiness checklists (Finalize Clarification / Start
// Implementation): items is [{ok, label}], rendered as a checked/unchecked icon list into listEl.
function renderGateChecklist(listEl, items) {
  listEl.innerHTML = items.map((it) =>
    `<li class="gate-item ${it.ok ? "ok" : "pending"}">` +
      `<span class="icon">${it.ok ? iconSvg("circle-check") : iconSvg("square")}</span><span>${escapeHtml(it.label)}</span></li>`
  ).join("");
}

