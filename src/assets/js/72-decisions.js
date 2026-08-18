// ---------------------------------------------------------------------------
// Answering a blocked feature's decision — the ⛔ callout on a deferred epic's card.
//
// The form lives in a modal, not inline on the card, because renderImplementStatus rebuilds
// every card from scratch on the 1s poll: an inline textarea would be wiped mid-sentence.
// The modal sits outside implStatusBody, so it survives the rebuild.
//
// Before this existed the callout told the user to open config.json, find the right epic among
// dozens and type into a "blocked_answer" field — in a file a running agent is writing to at
// the same time. The endpoint behind Save records the decision in its own file first and only
// then edits config.json, under a lock and re-reading it, so neither this write nor the
// runner's can lose the other (see dashboard_api_decisions.py).
// ---------------------------------------------------------------------------
function decisionRadioRow(value, checked, label, desc) {
  return `<label class="settings-radio-row">` +
      `<input type="radio" name="decisionMode" value="${value}"${checked ? " checked" : ""}>` +
      `<span><span class="settings-radio-option-label">${escapeHtml(label)}</span>` +
      `<span class="settings-radio-option-desc">${escapeHtml(desc)}</span></span>` +
    `</label>`;
}

// Passed to showModal({html: true}), whose contract is that it is only ever handed strings the
// JS builds itself — everything interpolated from config.json goes through escapeHtml.
function decisionFormHtml(feature, mode, answer) {
  const question = String(feature.blocked_question || "").trim();
  const recommendation = String(feature.blocked_recommendation || "").trim();
  const block = (heading, body) =>
    `<div class="decision-modal-block"><h4>${escapeHtml(heading)}</h4><p>${escapeHtml(body)}</p></div>`;
  return (
    `<div class="decision-modal">` +
      `<div class="decision-modal-feature">${escapeHtml(feature.id || "?")} — ${escapeHtml(feature.name || "")}</div>` +
      (question ? block("The question", question) : "") +
      (recommendation ? block("What the session recommends", recommendation) : "") +
      `<div class="settings-radio-group decision-modal-modes">` +
        (recommendation
          ? decisionRadioRow("follow", mode === "follow", "Follow the recommendation",
              "Records the recommendation above as your decision, word for word.")
          : "") +
        decisionRadioRow("own", mode === "own", "Write my own answer",
          "The session is handed your text instead, and carries on with this feature.") +
        decisionRadioRow("drop", mode === "drop", "Drop this feature",
          "Marks it done without building it, with your reason kept on the record. " +
          "Use when the feature is no longer wanted, not when it is merely hard.") +
      `</div>` +
      `<textarea id="decisionAnswer" class="decision-answer" rows="6" ` +
        `placeholder="Write your decision here…"${mode === "follow" ? " disabled" : ""}>` +
        `${escapeHtml(answer)}</textarea>` +
    `</div>`
  );
}

// showModal fills the dialog synchronously inside its promise executor, so the controls exist
// by the time it returns and can be wired before the user ever sees them.
function wireDecisionForm() {
  const textarea = document.getElementById("decisionAnswer");
  if (!textarea) return;
  modalMessage.querySelectorAll('input[name="decisionMode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      const needsText = radio.value !== "follow";
      textarea.disabled = !needsText;
      if (needsText) textarea.focus();
    });
  });
}

function selectedDecisionMode() {
  const checked = modalMessage.querySelector('input[name="decisionMode"]:checked');
  return checked ? checked.value : "";
}

async function postDecision(epicName, featureId, mode, answer) {
  try {
    const res = await fetch("/api/implement/decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ epic: epicName, feature: featureId, mode, answer }),
    });
    const data = await res.json();
    if (!data.ok) {
      toast(data.error || "Could not save the decision.", true);
      return false;
    }
    return true;
  } catch (err) {
    toast("Network error saving the decision.", true);
    return false;
  }
}

// `mode`/`answer` are carried back in when the user has to be sent round again (they left the
// answer blank), so a rejected save never costs them what they had already typed.
async function openDecisionModal(epicName, featureId, mode, answer) {
  const epic = (state.epics || []).find((e) => e.epic_name === epicName);
  const feature = epic && (epic.features || []).find((f) => f.id === featureId);
  if (!feature) {
    toast("That feature is no longer waiting on a decision.", true);
    return;
  }
  const recommendation = String(feature.blocked_recommendation || "").trim();
  const startMode = mode || (recommendation ? "follow" : "own");
  const pending = showModal({
    title: "Answer the decision",
    html: true,
    message: decisionFormHtml(feature, startMode, answer || ""),
    okLabel: "Save answer",
  });
  wireDecisionForm();
  const confirmed = await pending;
  if (!confirmed) return;

  // Read the controls before anything else opens a modal — the confirm below reuses this same
  // dialog, and showing it replaces the markup these values live in.
  const chosen = selectedDecisionMode();
  const textarea = document.getElementById("decisionAnswer");
  const typed = textarea ? textarea.value.trim() : "";

  if (chosen !== "follow" && !typed) {
    await alertModal("Write an answer before saving.", { title: "Nothing to save" });
    await openDecisionModal(epicName, featureId, chosen, typed);
    return;
  }
  if (chosen === "drop") {
    const sure = await confirmModal(
      `Drop ${featureId} from ${epicName}? It is marked done without being built, and the epic's ` +
      "Definition of Done no longer covers it. Your reason is kept on the feature.",
      { title: "Drop Feature", okLabel: "Drop", danger: true });
    if (!sure) {
      await openDecisionModal(epicName, featureId, chosen, typed);
      return;
    }
  }

  if (!await postDecision(epicName, featureId, chosen, typed)) return;
  toast(chosen === "drop"
    ? `${featureId} dropped — ${epicName} goes back in the queue.`
    : `Answer saved — ${epicName} goes back in the queue on the next poll.`);
  refreshImplementRun();
}
