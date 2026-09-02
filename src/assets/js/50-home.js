// ---------------------------------------------------------------------------
// Home page — step-by-step workflow (init check -> upload spec -> clarify -> implement)
// ---------------------------------------------------------------------------
function renderHomeWorkflow() {
  homeNotInit.classList.toggle("hidden", state.workspaceInitialized);
  homeSteps.classList.toggle("hidden", !state.workspaceInitialized);
  if (!state.workspaceInitialized) { renderRecentWorkspaces(); return; }

  homeWorkspacePath.textContent = state.workspaceRoot;
  homeWorkspaceCloseBtn.classList.toggle("hidden", !state.workspaceCanClose);

  homeStepPrinciplesStatus.textContent = state.principlesSet
    ? "Set — applied to every clarification, plan, implementation, and QA prompt."
    : "Not set — Tempa runs without project-wide principles.";

  const specCount = countSpecFiles(state.specTree);
  const step1Done = specCount > 0;
  homeStep1Status.textContent = step1Done
    ? (specCount === 1 ? "1 specification file uploaded." : `${specCount} specification files uploaded.`)
    : "No specification files yet.";

  const step2Locked = !step1Done;
  homeStep2.classList.toggle("locked", step2Locked);
  // Just the round count so far, same as the Clarification page's clarifyRoundBadge —
  // manual clarification isn't bounded by Max Finalize Clarification Round, so pairing it
  // with that max here would misleadingly suggest a cap on rounds run outside of Finalize's
  // own loop.
  const homeFinalize = state.clarifyFinalize;
  if (homeFinalize.round > 0) {
    homeClarifyRoundBadge.textContent = `Round ${homeFinalize.round}`;
    homeClarifyRoundBadge.classList.remove("hidden");
  } else {
    homeClarifyRoundBadge.classList.add("hidden");
  }
  // Mirrors the Clarification page's own Start/Continue Clarification behavior (see
  // setClarifyRunButtonsDisabled) so the two pages never disagree.
  const homeHasUnanswered = state.clarifyUnanswered.some((f) => f.total > f.answered);
  const homeHasUnapplied = state.clarifyAnswered.some((f) => !f.applied);
  const homeNeedsContinue = state.clarifyFinalize.hasRun && !state.clarifyFinalize.ready;
  // Only unanswered findings block — answered-but-unapplied ones ride into the next round
  // as already-decided resolutions. Same rule as setClarifyRunButtonsDisabled.
  const homeBlockedByAnswers = homeNeedsContinue && homeHasUnanswered;
  // Clarification and implementation are two independent background runs that both
  // touch the spec/PRD — never let the user start one while the other is in progress
  // (mirrors setClarifyRunButtonsDisabled/renderFinalizeGate on the Clarification page).
  const homeImplementRunning = state.implementRun.running;
  // Nothing left for another round to find — the same advisory gate the Clarification page
  // applies (see setClarifyRunButtonsDisabled / _clarification_settled_status).
  const homeSettled = state.clarifySettled.settled;
  homeStartClarifyBtn.querySelector("span:last-child").textContent =
    homeNeedsContinue ? "Continue Clarification" : "Start Clarification";
  homeStartClarifyBtn.disabled = step2Locked || state.clarifyRun.running || homeBlockedByAnswers ||
    homeImplementRunning || homeSettled;
  homeStartClarifyBtn.title = homeImplementRunning
    ? "Implementation is running."
    : homeSettled ? clarifySettledTitle(state.clarifyPendingOverlay)
    : homeBlockedByAnswers ? "Answer the remaining findings first." : "";
  homeApplyAnswersBtn.disabled = step2Locked || state.clarifyRun.running || !homeHasUnapplied ||
    homeImplementRunning;
  homeApplyAnswersBtn.title = homeImplementRunning ? "Implementation is running." : "";
  // Mirrors renderFinalizeGate's gate above: disabled until clarification has run, the
  // latest result is a fresh evaluate, and it shows zero critical findings (or the
  // Settings override is on) — state.clarifyFinalize.ready, computed server-side by
  // _clarify_finalize_status in dashboard_clarify_parse.py.
  homeFinalizeClarifyBtn.disabled = step2Locked || state.clarifyRun.running || !state.clarifyFinalize.ready ||
    homeImplementRunning || homeSettled;
  homeFinalizeClarifyBtn.title = homeImplementRunning
    ? "Implementation is running."
    : homeSettled ? clarifySettledTitle(state.clarifyPendingOverlay) : "";
  const allClarifyFiles = state.clarifyUnanswered.concat(state.clarifyAnswered);
  const totalFindings = allClarifyFiles.reduce((sum, f) => sum + f.total, 0);
  const unansweredFindings = allClarifyFiles.reduce((sum, f) => sum + (f.total - f.answered), 0);
  const criticalCount = state.clarifyFindings.critical;
  const criticalOverrideNote = criticalCount > 0 && state.clarifyFinalize.allowFinalizeWithCritical
    ? " Finalizing is allowed anyway via the Settings override." : "";
  // The settled branch has to come BEFORE the totalFindings check: a workspace whose clean
  // round wrote no clarification file at all has zero findings, and would otherwise read
  // "No clarification results yet — click Start Clarification" next to a disabled Start
  // Clarification button.
  homeStep2Status.textContent = step2Locked
    ? "Upload a specification first (step 1)."
    : homeSettled
      ? clarifySettledHint(state.clarifyPendingOverlay)
      : totalFindings === 0
        ? "No clarification results yet — click Start Clarification to begin."
        : `${unansweredFindings} of ${totalFindings} finding(s) not yet answered (${criticalCount} critical).` +
          criticalOverrideNote;

  const findings = state.clarifyFindings;
  const needsClarification = !step2Locked && (findings.critical > 0 || findings.major > 0);
  const filesToAnswer = state.clarifyUnanswered.filter((f) => f.total > f.answered);
  homeStep2FileList.classList.toggle("hidden", !needsClarification || !filesToAnswer.length);
  if (needsClarification && filesToAnswer.length) {
    homeStep2FileList.innerHTML = "";
    for (const file of filesToAnswer) {
      const li = document.createElement("li");
      li.className = "home-step2-file-item";
      li.innerHTML = `<span class="home-step2-file-name">${escapeHtml(file.name)}</span>` +
        `<span class="file-status">${file.answered}/${file.total}</span>`;
      li.addEventListener("click", () => openClarifyFile(file));
      homeStep2FileList.appendChild(li);
    }
  }

  // See implementReadyMessage/implementBlockedMessage — mirrors the Clarification
  // page's ready banner and the Implementation page's own gate, all driven by the same
  // server-computed state.implementReadiness (see _implement_readiness_status in
  // dashboard_clarify_parse.py) so the three surfaces never disagree.
  const ir = state.implementReadiness;
  const step3Locked = step2Locked || !ir.ready;
  homeStep3.classList.toggle("locked", step3Locked);
  homeStartImplementBtn.disabled = step3Locked || state.implementRun.running || state.clarifyRun.running;
  updateImplementButtonLabels();
  homeStep3Status.textContent = step2Locked
    ? "Finish step 2 first."
    : !ir.hasRun
      ? "Run clarification first (step 2)."
      : ir.ready
        ? implementReadyMessage(ir)
        : implementBlockedMessage(ir);
}

homeSelectFolderBtn.addEventListener("click", async () => {
  homeSelectFolderBtn.disabled = true;
  try {
    const res = await fetch("/api/workspace/init", { method: "POST" });
    const data = await res.json();
    if (data.cancelled) return;
    if (!data.ok) { toast(data.error || "Could not set the working folder.", true); return; }
    toast("Working folder set: " + data.root);
    await refreshSpecTree();
  } catch (e) {
    toast("Could not set the working folder.", true);
  } finally {
    homeSelectFolderBtn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Recent working folders (empty-state only) — reopen a folder Tempa has seen before
// with one click, or create a brand-new one via a native "pick location" + "type a
// name" pair of steps.
// ---------------------------------------------------------------------------
function renderRecentWorkspaces() {
  const entries = state.recentWorkspaces || [];
  homeRecent.classList.toggle("hidden", !entries.length);
  homeRecentList.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");
    li.className = "home-recent-item" + (entry.exists ? "" : " missing");

    const icon = document.createElement("span");
    icon.className = "home-recent-icon";
    icon.innerHTML = iconSvg("folder");
    li.appendChild(icon);

    const info = document.createElement("span");
    info.className = "home-recent-info";
    info.innerHTML = `<span class="home-recent-name">${escapeHtml(entry.name)}</span>` +
      `<span class="home-recent-path">${escapeHtml(entry.root)}</span>` +
      (entry.exists ? "" : '<span class="home-recent-missing-note">Folder not found</span>');
    li.appendChild(info);

    const time = document.createElement("span");
    time.className = "home-recent-time";
    time.textContent = formatEpochShort(entry.openedAt);
    li.appendChild(time);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "home-recent-remove";
    removeBtn.title = "Remove from recent list";
    removeBtn.innerHTML = iconSvg("x");
    removeBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        const res = await fetch("/api/workspace/recent/remove", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: entry.root }),
        });
        const data = await res.json();
        if (!data.ok) { toast(data.error || "Could not remove the folder.", true); return; }
        state.recentWorkspaces = data.recent || [];
        renderRecentWorkspaces();
      } catch (e) {
        toast("Network error while removing the folder.", true);
      }
    });
    li.appendChild(removeBtn);

    if (entry.exists) {
      li.addEventListener("click", async () => {
        li.classList.add("loading");
        try {
          const res = await fetch("/api/workspace/open-recent", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: entry.root }),
          });
          const data = await res.json();
          if (!data.ok) { toast(data.error || "Could not open the working folder.", true); return; }
          toast("Working folder set: " + data.root);
          await refreshSpecTree();
        } catch (e) {
          toast("Could not open the working folder.", true);
        } finally {
          li.classList.remove("loading");
        }
      });
    }
    homeRecentList.appendChild(li);
  }
}

homeCreateFolderBtn.addEventListener("click", async () => {
  homeCreateFolderBtn.disabled = true;
  try {
    const pickRes = await fetch("/api/workspace/pick-parent", { method: "POST" });
    const pickData = await pickRes.json();
    if (pickData.cancelled) return;
    if (!pickData.ok) { toast(pickData.error || "Could not choose a location.", true); return; }

    const name = await promptModal("New working folder name", "", {
      title: "Create New Working Folder", okLabel: "Create",
    });
    if (!name || !name.trim()) return;

    const createRes = await fetch("/api/workspace/create", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent: pickData.path, name: name.trim() }),
    });
    const createData = await createRes.json();
    if (!createData.ok) { toast(createData.error || "Could not create the working folder.", true); return; }
    toast("Working folder created: " + createData.root);
    await refreshSpecTree();
  } catch (e) {
    toast("Could not create the working folder.", true);
  } finally {
    homeCreateFolderBtn.disabled = false;
  }
});

homeWorkspacePath.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/workspace/open", { method: "POST" });
    const data = await res.json();
    if (!data.ok) toast(data.error || "Could not open the folder.", true);
  } catch (e) { toast("Could not open the folder.", true); }
});

homeWorkspaceCloseBtn.addEventListener("click", async (e) => {
  e.stopPropagation();
  const ok = await confirmModal(
    `Close working folder "${state.workspaceRoot}"? This only clears the folder link in ` +
    "config.json — no files are deleted. The Home page will go back to Select Working Folder.",
    { title: "Close Working Folder", okLabel: "Close", danger: true });
  if (!ok) return;
  homeWorkspaceCloseBtn.disabled = true;
  try {
    const res = await fetch("/api/workspace/close", { method: "POST" });
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not close the working folder.", true); return; }
    toast("Working folder closed.");
    await refreshSpecTree();
  } catch (e) {
    toast("Network error while closing the working folder.", true);
  } finally {
    homeWorkspaceCloseBtn.disabled = false;
  }
});

homePrinciplesBtn.addEventListener("click", () => selectTop("principles"));

homeAddFileBtn.addEventListener("click", () => { addFileInput.value = ""; addFileInput.click(); });
homeAddFolderBtn.addEventListener("click", () => { addFolderInput.value = ""; addFolderInput.click(); });

homeStartClarifyBtn.addEventListener("click", async () => {
  await selectTop("clarification");
  startClarifyRun("run");
});
homeApplyAnswersBtn.addEventListener("click", async () => {
  await selectTop("clarification");
  startClarifyRun("apply");
});
homeFinalizeClarifyBtn.addEventListener("click", async () => {
  await selectTop("clarification");
  startClarifyRun("finalize");
});
homeStartImplementBtn.addEventListener("click", async () => {
  await selectTop("implementation");
  startImplementRun();
});

function renderLeafSection(key, icon, label, disabled, running) {
  const wrap = document.createElement("div");
  wrap.className = "node";
  const row = document.createElement("div");
  row.className = "row top" + (state.activeTop === key ? " selected" : "") + (disabled ? " disabled" : "");
  row.innerHTML = `<span class="twist hidden"></span><span class="icon">${icon}</span><span class="label">${label}</span>` +
    (running ? `<span class="row-status-spin">${iconSvg("loader-circle", "icon-spin")}</span>` : "");
  row.title = label;              // the label is hidden in the collapsed icon rail
  row.addEventListener("click", () => {
    if (disabled) { toast("Select a working folder first.", true); return; }
    selectTop(key);
  });
  wrap.appendChild(row);
  return wrap;
}

function renderUpdateAvailableItem() {
  const wrap = document.createElement("div");
  wrap.className = "node";
  const row = document.createElement("div");
  row.className = "row top update-available";
  row.innerHTML = `<span class="twist hidden"></span><span class="icon">${iconSvg("arrow-up")}</span>` +
    `<span class="label">Update available (${state.updateLatestVersion})</span>`;
  row.addEventListener("click", async () => {
    await selectTop("settings");
    setSettingsTab("maintenance");
  });
  wrap.appendChild(row);
  return wrap;
}

function renderSpecSection() {
  const disabled = !state.workspaceInitialized;
  const wrap = document.createElement("div");
  wrap.className = "node" + (state.expandedTop.specification ? " open" : "");
  const row = document.createElement("div");
  row.className = "row top" + (state.activeTop === "specification" && state.specShowingOverview ? " selected" : "") +
    (disabled ? " disabled" : "");
  row.innerHTML = `<span class="twist">${iconSvg("chevron-right")}</span><span class="icon">${iconSvg("folder")}</span><span class="label">Specification</span>`;
  row.title = "Specification";    // the label is hidden in the collapsed icon rail
  row.addEventListener("click", () => {
    if (disabled) { toast("Select a working folder first.", true); return; }
    selectTop("specification");
  });
  wrap.appendChild(row);

  const children = document.createElement("div");
  children.className = "children";
  const kids = (state.specTree && state.specTree.children) || [];
  if (!kids.length) {
    const note = document.createElement("div");
    note.className = "empty-note";
    note.textContent = "No PRD files found.";
    children.appendChild(note);
  } else {
    for (const child of kids) children.appendChild(renderSpecNode(child, 1));
  }
  wrap.appendChild(children);
  return wrap;
}

function renderSpecNode(node, depth) {
  const wrap = document.createElement("div");
  wrap.className = "node";
  const isDir = node.type === "dir";
  if (isDir && state.expandedSpecDirs.has(node.path)) wrap.classList.add("open");

  const row = document.createElement("div");
  row.className = "row";
  row.style.paddingLeft = (6 + depth * 15) + "px";
  // ...and not while an epic spec is open: it isn't a row in this tree, so a PRD file that
  // happens to share its name would otherwise light up as though it were the open one.
  if (!isDir && !state.specShowingOverview && !state.selectedEpicSpec
      && node.path === state.selectedSpecPath) row.classList.add("selected");

  const twist = document.createElement("span");
  twist.className = "twist" + (isDir ? "" : " hidden");
  twist.innerHTML = iconSvg("chevron-right");
  row.appendChild(twist);

  const icon = document.createElement("span");
  icon.className = "icon";
  icon.innerHTML = specIconFor(node);
  row.appendChild(icon);

  const label = document.createElement("span");
  label.className = "label";
  label.textContent = node.name;
  row.appendChild(label);

  const menuBtn = document.createElement("button");
  menuBtn.type = "button";
  menuBtn.className = "row-menu-btn";
  menuBtn.title = "More";
  menuBtn.textContent = "⋯";
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    openRowContextMenu(menuBtn, node);
  });
  row.appendChild(menuBtn);
  wrap.appendChild(row);

  if (isDir) {
    const children = document.createElement("div");
    children.className = "children";
    for (const child of node.children || []) children.appendChild(renderSpecNode(child, depth + 1));
    wrap.appendChild(children);
    row.addEventListener("click", () => {
      if (state.expandedSpecDirs.has(node.path)) state.expandedSpecDirs.delete(node.path);
      else state.expandedSpecDirs.add(node.path);
      wrap.classList.toggle("open");
    });
  } else {
    row.addEventListener("click", () => openSpecFile(node));
  }
  return wrap;
}

function renderClarifySection() {
  const disabled = !state.workspaceInitialized;
  const wrap = document.createElement("div");
  wrap.className = "node" + (state.expandedTop.clarification ? " open" : "");
  const row = document.createElement("div");
  row.className = "row top" + (state.activeTop === "clarification" && state.clarifyShowingOverview ? " selected" : "") +
    (disabled ? " disabled" : "");
  const count = state.clarifyUnanswered.length;
  row.innerHTML = `<span class="twist">${iconSvg("chevron-right")}</span><span class="icon">${iconSvg("circle-help")}</span><span class="label">Clarification</span>` +
    (state.clarifyRun.running ? `<span class="row-status-spin">${iconSvg("loader-circle", "icon-spin")}</span>` : "") +
    (count ? `<span class="badge-count">${count}</span>` : "");
  row.title = count ? `Clarification (${count} unanswered)` : "Clarification";
  row.addEventListener("click", () => {
    if (disabled) { toast("Select a working folder first.", true); return; }
    selectTop("clarification");
  });
  wrap.appendChild(row);

  const children = document.createElement("div");
  children.className = "children";
  if (!state.clarifyUnanswered.length) {
    const note = document.createElement("div");
    note.className = "empty-note";
    note.textContent = "Nothing unanswered — all clarification findings are answered.";
    children.appendChild(note);
  } else {
    for (const file of state.clarifyUnanswered) children.appendChild(renderClarifyFileRow(file));
  }
  wrap.appendChild(children);
  return wrap;
}

function renderClarifyFileRow(file) {
  const wrap = document.createElement("div");
  wrap.className = "node";
  const row = document.createElement("div");
  row.className = "row" + (!state.clarifyShowingOverview && file.path === state.selectedClarifyPath ? " selected" : "");
  row.style.paddingLeft = "21px";
  row.innerHTML = `<span class="twist hidden"></span><span class="icon">${iconSvg("file-pen-line")}</span>` +
    `<span class="label">${escapeHtml(file.name)}</span>` +
    `<span class="file-status">${file.answered}/${file.total}</span>`;
  row.addEventListener("click", () => openClarifyFile(file));
  wrap.appendChild(row);
  return wrap;
}

