function finalizeBacklogLabel(hasUnanswered) {
  const pending = state.clarifyPendingOverlay.findings;
  if (hasUnanswered) {
    return "Unanswered findings above will be filled in with their own recommendation " +
      "before Finalize's loop starts";
  }
  if (pending > 0) {
    return `${pending} already-decided resolution(s) will be carried into every evaluation and ` +
      "written into the PRD in one pass at the end";
  }
  return "No pending backlog — the PRD already contains every recorded answer";
}

function renderFinalizeGate(runDisabled, hasUnanswered, hasUnapplied) {
  const st = state.clarifyFinalize;
  // Just the round count so far — manual clarification isn't bounded by Max Finalize
  // Clarification Round, so pairing it with that max here would misleadingly suggest a
  // cap on rounds run outside of Finalize's own loop. The count against that max is
  // shown separately, next to the Finalized Clarification button (finalizeRoundProgress).
  if (st.round > 0) {
    clarifyRoundBadge.textContent = `Round ${st.round}`;
    clarifyRoundBadge.classList.remove("hidden");
  } else {
    clarifyRoundBadge.classList.add("hidden");
  }
  // finalizeRound is the separate counter that restarts from 0 every time a
  // Finalized Clarification run starts (see run_clarify_finalize in
  // tempa_clarify.py) — while a run is actually in progress this gets overridden
  // with fresher, per-second numbers by pollClarifyRun below; this render is only
  // what's left showing once a run finishes (or on initial page load).
  if (st.maxRound > 0) {
    finalizeRoundProgress.textContent = `${st.finalizeRound} / ${st.maxRound}`;
    finalizeRoundProgress.classList.remove("hidden");
  } else {
    finalizeRoundProgress.classList.add("hidden");
  }
  // Nothing left for another round to find (see _clarification_settled_status in
  // dashboard_clarify_parse.py). Advisory only — the server still accepts the run.
  const settled = state.clarifySettled.settled;
  const criticalOk = st.critical === 0 || st.allowFinalizeWithCritical;
  // The Settings override waives every requirement below, not just the critical-findings
  // one — see _clarify_finalize_status in dashboard_clarify_parse.py, where `ready` is
  // just `allowFinalizeWithCritical` once it's on. Reflect that here too: with it on,
  // show every row as satisfied (bypassed) rather than leaving stale unchecked boxes next
  // to an enabled button.
  renderGateChecklist(finalizeGateList, [
    { ok: st.hasRun || st.allowFinalizeWithCritical,
      label: st.hasRun
        ? "Clarification has been run at least once"
        : st.allowFinalizeWithCritical
          ? "Clarification hasn't been run yet — allowed via the Settings override"
          : "Clarification has been run at least once" },
    { ok: st.lastAction === "evaluate" || st.allowFinalizeWithCritical,
      label: st.lastAction === "evaluate"
        ? "Most recent result comes from Start Clarification, not just Apply Answers"
        : st.allowFinalizeWithCritical
          ? "Most recent result isn't from Start Clarification — allowed via the Settings override"
          : "Most recent result comes from Start Clarification, not just Apply Answers" },
    { ok: criticalOk,
      label: st.critical === 0
        ? "Most recent evaluation shows 0 critical findings"
        : st.allowFinalizeWithCritical
          ? `${st.critical} critical finding(s) from your last evaluation haven't been re-checked ` +
            "yet — allowed via the Settings override"
          : `${st.critical} critical finding(s) from your last evaluation haven't been re-checked ` +
            "yet — answering them doesn't update this count" },
    { ok: true, label: finalizeBacklogLabel(hasUnanswered) },
    // Only when settled: four green ticks above a disabled button would read as a bug, so
    // the checklist has to name the thing that is actually holding it shut.
    ...(settled
      ? [{ ok: false, label: "There are open findings for Finalized Clarification to resolve" }]
      : []),
  ]);
  // Disabled while a run is in progress OR the checklist above isn't fully satisfied yet
  // (st.ready — see _clarify_finalize_status in dashboard_clarify_parse.py). While a
  // finalize run specifically is in progress, swap it for Stop Finalize entirely
  // (same Start/Stop toggle Implementation already has) rather than just disabling it.
  const finalizeRunning = runDisabled && state.clarifyRun.mode === "finalize";
  finalizeClarifyBtn.disabled = runDisabled || !st.ready || state.implementRun.running || settled;
  finalizeClarifyBtn.title = state.implementRun.running
    ? "Implementation is running."
    : settled ? clarifySettledTitle(state.clarifyPendingOverlay) : "";
  finalizeClarifyBtn.classList.toggle("hidden", finalizeRunning);
  // The wrapper, not the button: Stop Now and its chevron have to appear together.
  stopFinalizeClarifySplit.classList.toggle("hidden", !finalizeRunning);

  // Once clarification has run at least once but isn't finalize-ready yet, relabel
  // Start Clarification -> Continue Clarification and explain why in plain language,
  // so users who just finished answering/applying don't get stuck wondering why
  // Finalize/Implement are still blocked.
  // Left as "Start Clarification" when settled: needsContinue is false there (settled
  // implies st.ready), and there is nothing to continue anyway.
  const needsContinue = st.hasRun && !st.ready;
  startClarifyBtn.querySelector("span:last-child").textContent =
    needsContinue ? "Continue Clarification" : "Start Clarification";
  // Settled is checked FIRST: it always implies st.ready, so the !needsContinue branch
  // below would otherwise hide the hint in exactly the state that most needs explaining.
  if (settled) {
    finalizeGateHint.textContent = clarifySettledHint(state.clarifyPendingOverlay);
    finalizeGateHint.classList.remove("hidden");
  } else if (!needsContinue) {
    finalizeGateHint.classList.add("hidden");
  } else if (hasUnanswered) {
    // Deliberately NOT `|| hasUnapplied`: saved-but-unapplied answers are carried into the
    // next round as already-decided resolutions, so they're no reason to stop clarifying.
    finalizeGateHint.textContent =
      "Answer the remaining findings before continuing clarification.";
    finalizeGateHint.classList.remove("hidden");
  } else if (st.critical > 0) {
    finalizeGateHint.textContent =
      `You still need to run Continue Clarification — the last evaluation found ${st.critical} ` +
      "critical finding(s), and your answers haven't been re-evaluated yet.";
    finalizeGateHint.classList.remove("hidden");
  } else {
    finalizeGateHint.textContent =
      "Run Continue Clarification once more to confirm the latest status before finalizing.";
    finalizeGateHint.classList.remove("hidden");
  }
}

function setClarifyRunButtonsDisabled(disabled) {
  const st = state.clarifyFinalize;
  const hasUnanswered = state.clarifyUnanswered.some((f) => f.total > f.answered);
  const hasUnapplied = state.clarifyAnswered.some((f) => !f.applied);
  const needsContinue = st.hasRun && !st.ready;
  // Only UNANSWERED findings block another round. Answered-but-unapplied ones don't:
  // they're carried into the next evaluation as already-decided resolutions (see
  // pending_resolutions in dashboard_clarify_parse.py), so requiring an apply pass first
  // would just be a full PRD rewrite standing between the user and the next round.
  // hasUnapplied is still computed — Apply Answers and the finalize checklist need it.
  const blockedByAnswers = needsContinue && hasUnanswered;
  // Clarification and implementation are two independent background runs that both
  // touch the spec/PRD — never let the user start one while the other is in progress.
  const implementRunning = state.implementRun.running;
  // settled and blockedByAnswers are mutually exclusive (settled requires
  // unansweredFiles === 0), so their order below only decides which loses to a live run.
  const settled = state.clarifySettled.settled;
  startClarifyBtn.disabled = disabled || blockedByAnswers || implementRunning || settled;
  startClarifyBtn.title = implementRunning
    ? "Implementation is running."
    : settled ? clarifySettledTitle(state.clarifyPendingOverlay)
    : blockedByAnswers ? "Answer the remaining findings first." : "";
  applyAnswersBtn.disabled = disabled || !hasUnapplied || implementRunning;
  applyAnswersBtn.title = implementRunning ? "Implementation is running." : "";
  // While "run"/"apply" specifically is in progress, swap the Start/Apply button for its
  // Stop counterpart entirely (same Start/Stop toggle Implementation and Finalize already
  // use) rather than just disabling it.
  const runRunning = disabled && state.clarifyRun.mode === "run";
  const applyRunning = disabled && state.clarifyRun.mode === "apply";
  startClarifyBtn.classList.toggle("hidden", runRunning);
  stopClarifySplit.classList.toggle("hidden", !runRunning);
  applyAnswersBtn.classList.toggle("hidden", applyRunning);
  stopApplyAnswersSplit.classList.toggle("hidden", !applyRunning);
  renderFinalizeGate(disabled, hasUnanswered, hasUnapplied);
}

function clarifyRunStatusLabel(mode) {
  if (mode === "finalize") return "Finalizing…";
  if (mode === "apply") return "Applying…";
  return "Running…";
}

// Persistent "a run is in progress" badge shown above the Overview/Log tabs — unlike the
// log itself, it stays visible regardless of which tab is active, so switching to the
// Overview tab during a run doesn't leave the user wondering if anything is happening.
function renderClarifyRunStatus() {
  const run = state.clarifyRun;
  if (!run.running) {
    clarifyRunStatus.classList.add("hidden");
    clarifyRunStatus.innerHTML = "";
    return;
  }
  const tick = run.progress ? formatClarifyLogLine(run.progress).msg : "";
  const target = GRACEFUL_STOP_TARGETS[run.mode];
  // Appended rather than replacing the label: the run genuinely is still running, and the
  // live progress tick is exactly what tells the user how much longer that will be.
  const stopping = run.gracefulStopRequested && target ? ` — ${target.pendingStatus}` : "";
  clarifyRunStatus.innerHTML = iconSvg("loader-circle", "icon-spin") +
    `<span>${escapeHtml(clarifyRunStatusLabel(run.mode))}${tick ? " — " + escapeHtml(tick) : ""}` +
    `${escapeHtml(stopping)}</span>`;
  clarifyRunStatus.classList.remove("hidden");
}

// Turns one raw console line from `tempa clarify` into a {cls, icon, time, msg} for
// user-friendly rendering — banners, [OK]/[!] markers, and the once-a-second
// progress tick each get their own look instead of showing as raw log text.
function formatClarifyLogLine(text) {
  if (/^\[\d{2}:\d{2}:\d{2}\].*\[\d+ rows\](\s*\[[^\]]*\])*\s*$/.test(text)) {
    const m = text.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$/);
    return { cls: "progress", icon: "loader-circle", time: m ? m[1] : "", msg: m ? m[2] : text };
  }
  const trimmed = text.trim();
  if (/^==.+==$/.test(trimmed)) {
    return { cls: "banner", icon: "megaphone", time: "", msg: trimmed.replace(/^=+\s*|\s*=+$/g, "") };
  }
  let time = "", msg = text;
  const tsMatch = text.match(/^\[\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2})\]\s?(.*)$/);
  if (tsMatch) { time = tsMatch[1]; msg = tsMatch[2]; }
  if (/^\[OK\]/i.test(msg)) return { cls: "ok", icon: "circle-check", time, msg: msg.replace(/^\[OK\]\s*/i, "") };
  if (/SUCCEEDED/.test(msg)) return { cls: "ok", icon: "circle-check", time, msg };
  if (/FAILED|ERROR|\[error\]|authentication failed/i.test(msg)) return { cls: "err", icon: "circle-x", time, msg };
  if (/^\[!\]/.test(msg)) return { cls: "warn", icon: "triangle-alert", time, msg: msg.replace(/^\[!\]\s*/, "") };
  if (/usage limit reached|reached the .* limit|overloaded/i.test(msg)) return { cls: "warn", icon: "triangle-alert", time, msg };
  return { cls: "plain", icon: "circle-dashed", time, msg };
}

// Matches the bare filenames tempa_session._run_backend_session's own banner line always
// names (e.g. "... | log: session_EPIC-17_20260809_044718.txt"). Anchored on the
// _<YYYYMMDD>_<HHMMSS>.txt suffix every log Tempa writes ends with rather than on a list of
// known prefixes, so clarification_/apply_clarification_/verify_/plan_epics_* logs linkify
// too — and any prefix added later does as well, without another edit here. No path
// separators are allowed in the match, so it only ever yields the bare filename
// /api/log-file expects (which serves flat files out of .tempa/logs/).
const LOG_FILENAME_RE = /\b([\w.-]+_\d{8}_\d{6}\.txt)\b/g;

function linkifyLogFilenames(escapedMsg) {
  return escapedMsg.replace(
    LOG_FILENAME_RE,
    (name) => `<a href="#" class="log-file-link" data-log-file="${name}">${name}</a>`,
  );
}

// Whether a log panel is currently scrolled to (or near) its bottom — used to decide
// whether a re-render should follow new content or leave the user's scroll-up position
// alone (a 1s poll rebuilds the whole panel, so without this a user reading earlier
// lines gets yanked back to the bottom on every tick).
function isScrolledNearBottom(el, threshold = 4) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}

function appendClarifyLogRow(text) {
  const f = formatClarifyLogLine(text);
  const row = document.createElement("div");
  row.className = "clarify-log-line " + f.cls;
  row.innerHTML =
    (f.time ? `<span class="clarify-log-time">${escapeHtml(f.time)}</span>` : "") +
    `<span class="clarify-log-icon">${iconSvg(f.icon, f.cls === "progress" ? "icon-spin" : "")}</span>` +
    `<span class="clarify-log-msg">${linkifyLogFilenames(escapeHtml(f.msg))}</span>`;
  return row;
}

function renderClarifyLog() {
  const stickToBottom = isScrolledNearBottom(clarifyLogBody);
  clarifyLogBody.innerHTML = "";
  if (!state.clarifyRun.lines.length && !state.clarifyRun.progress) {
    clarifyLogBody.innerHTML = '<div class="clarify-log-empty">No log output yet.</div>';
    return;
  }
  for (const text of state.clarifyRun.lines) clarifyLogBody.appendChild(appendClarifyLogRow(text));
  // The live progress tick is rendered separately from `lines` (not appended to it) and
  // re-rendered fresh on every poll, so its elapsed time visibly keeps ticking instead of
  // freezing at whatever value happened to be present the first time it was fetched.
  if (state.clarifyRun.progress) clarifyLogBody.appendChild(appendClarifyLogRow(state.clarifyRun.progress));
  // Only follow new content to the bottom if the user was already there (or hadn't
  // scrolled) — otherwise a poll tick mid-read would yank them back down.
  if (stickToBottom) clarifyLogBody.scrollTop = clarifyLogBody.scrollHeight;
}

function stopClarifyPolling() {
  if (state.clarifyRun.pollTimer) {
    clearInterval(state.clarifyRun.pollTimer);
    state.clarifyRun.pollTimer = null;
  }
}

function returncodeMessage(code, mode) {
  const label = mode === "apply" ? "Apply" : mode === "finalize" ? "Finalize"
    : mode === "implement" ? "Implementation" : mode === "verify" ? "Verification" : "Clarification";
  if (code === 0) return `${label} run finished.`;
  if (code === 2) return `${label} stopped — usage limit reached.`;
  if (code === 3) return `${label} stopped — authentication error.`;
  return `${label} run exited with an error (code ${code}).`;
}

// A graceful stop exits 0, which returncodeMessage would report as "run finished" — true
// of the process, misleading about the work: epics or rounds are still outstanding. Only
// the success case is reworded; a non-zero exit means something else ended the run and
// that reason is the one worth showing.
function stopAwareReturncodeMessage(code, mode, gracefulStopRequested) {
  if (code === 0 && gracefulStopRequested) {
    const label = returncodeMessage(0, mode).replace(/ run finished\.$/, "");
    return `${label} stopped after the session in progress finished.`;
  }
  return returncodeMessage(code, mode);
}

async function pollClarifyRun() {
  try {
    const res = await fetch("/api/clarify/run?since=" + state.clarifyRun.nextIndex);
    const data = await res.json();
    if (!data.ok) return;
    if (data.lines.length) {
      state.clarifyRun.lines.push(...data.lines);
      state.clarifyRun.nextIndex = data.next;
      // Each evaluate/apply session ends with a "... SUCCEEDED (exit code N)" line
      // (_log_session_result) — refresh Unanswered/Fully answered right then instead
      // of waiting for the whole (possibly multi-round) finalize run to exit, so the
      // panels track each round's answers as they land on disk.
      if (data.lines.some((l) => l.includes("SUCCEEDED (exit code"))) {
        refreshClarifyList();
      }
    }
    // Always re-render, even with no new finalized lines: `progress` (the live
    // elapsed-time tick) changes every second on its own and isn't part of `lines`.
    state.clarifyRun.progress = data.progress;
    state.clarifyRun.mode = data.mode;
    // Server-computed, so a graceful stop asked for from a terminal
    // (`tempa clarify --stop-graceful`) shows up here as well as one asked for here.
    state.clarifyRun.gracefulStopRequested = !!data.gracefulStopRequested;
    renderClarifyLog();
    const wasRunning = state.clarifyRun.running;
    state.clarifyRun.running = data.running;
    renderClarifyRunStatus();
    syncClarifyLockState();
    setClarifyRunButtonsDisabled(data.running);
    if (wasRunning !== data.running) {
      // Push the clarify-running state to the implement side right away too, mirroring
      // updateImplementControls' equivalent push in the other direction — the 1s poll
      // driving this runs regardless of which page is active.
      updateImplementControls();
      renderSidebar();
    }
    // Finalize's round counter, read fresh from config.json every poll (see
    // _handle_clarify_run_status) — ticks up live, round by round, instead of
    // waiting for the run to finish and /api/tree to pick it up.
    if (data.mode === "finalize" && data.maxRound > 0) {
      finalizeRoundProgress.textContent = `${data.finalizeRound} / ${data.maxRound}`;
      finalizeRoundProgress.classList.remove("hidden");
    }
    if (!data.running) {
      stopClarifyPolling();
      if (data.returncode !== null) {
        toast(stopAwareReturncodeMessage(data.returncode, data.mode, data.gracefulStopRequested),
          data.returncode !== 0);
      }
      refreshClarifyList();
    }
  } catch (e) { /* transient network hiccup — next tick retries */ }
}

function startClarifyPolling() {
  stopClarifyPolling();
  state.clarifyRun.pollTimer = setInterval(pollClarifyRun, 1000);
  pollClarifyRun();
}

async function startClarifyRun(mode) {
  if (state.clarifyRun.running) return;
  // Set before the disabled-state render below so it can already tell this is a
  // finalize run and show Stop Finalize instead of waiting for the next poll.
  state.clarifyRun.mode = mode;
  setClarifyRunButtonsDisabled(true);
  state.clarifyRun.lines = [];
  state.clarifyRun.progress = null;
  state.clarifyRun.nextIndex = 0;
  state.clarifyRun.gracefulStopRequested = false;
  // Set optimistically, before the POST resolves, so the running badge appears the
  // instant the button is clicked instead of waiting on a network round trip.
  state.clarifyRun.running = true;
  renderClarifyRunStatus();
  renderClarifyLog();
  renderSidebar();
  updateImplementControls();
  try {
    const res = await fetch("/api/clarify/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    if (!data.ok) {
      toast(data.error || "Could not start clarification run.", true);
      state.clarifyRun.running = false;
      renderClarifyRunStatus();
      setClarifyRunButtonsDisabled(false);
      renderSidebar();
      updateImplementControls();
      return;
    }
    startClarifyPolling();
  } catch (e) {
    toast("Network error starting clarification run.", true);
    state.clarifyRun.running = false;
    renderClarifyRunStatus();
    setClarifyRunButtonsDisabled(false);
    renderSidebar();
    updateImplementControls();
  }
}

async function checkClarifyRunOnLoad() {
  try {
    const res = await fetch("/api/clarify/run?since=0");
    const data = await res.json();
    if (!data.ok || (!data.running && !data.lines.length)) return;
    state.clarifyRun.lines = data.lines;
    state.clarifyRun.nextIndex = data.next;
    state.clarifyRun.mode = data.mode;
    state.clarifyRun.progress = data.progress;
    state.clarifyRun.running = data.running;
    // A graceful stop asked for before this page was opened (or before it was reloaded)
    // is still pending — the button has to come back showing that, not "Running…".
    state.clarifyRun.gracefulStopRequested = !!data.gracefulStopRequested;
    renderClarifyLog();
    renderClarifyRunStatus();
    setClarifyRunButtonsDisabled(data.running);
    updateImplementControls();
    renderSidebar();
    if (data.mode === "finalize" && data.maxRound > 0) {
      finalizeRoundProgress.textContent = `${data.finalizeRound} / ${data.maxRound}`;
      finalizeRoundProgress.classList.remove("hidden");
    }
    if (data.running) startClarifyPolling();
  } catch (e) { /* ignore — buttons stay enabled */ }
}

