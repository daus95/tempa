// ---------------------------------------------------------------------------
// App state + DOM refs
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const appEl = document.querySelector(".app"),
  sidebarEl = $("sidebar"), sidebarToggleBtn = $("sidebarToggleBtn"),
  specPeekOverlay = $("specPeekOverlay"), specPeekBox = $("specPeekBox"),
  specPeekResize = $("specPeekResize"), specPeekBody = $("specPeekBody"),
  specPeekPath = $("specPeekPath"), specPeekOpenBtn = $("specPeekOpenBtn"),
  specPeekCloseBtn = $("specPeekCloseBtn");
const treeEl = $("tree"), treeBottomEl = $("treeBottom"), specViewer = $("specViewer"), specEditor = $("specEditor"),
  toolbarEl = $("toolbar"), toolbarBackBtn = $("toolbarBackBtn"), filepathEl = $("filepath"), specSeg = $("specSeg"),
  viewBtn = $("viewBtn"), editBtn = $("editBtn"), saveBtn = $("saveBtn"), followAllBtn = $("followAllBtn"),
  clarifySummary = $("clarifySummary"), clarifyBody = $("clarifyBody"),
  clarifyUnansweredTbody = $("clarifyUnansweredTbody"), clarifyAnsweredTbody = $("clarifyAnsweredTbody"),
  specFileCountEl = $("specFileCount"),
  addFileBtn = $("addFileBtn"), addFolderBtn = $("addFolderBtn"),
  addFileInput = $("addFileInput"), addFolderInput = $("addFolderInput"),
  startClarifyBtn = $("startClarifyBtn"), stopClarifyBtn = $("stopClarifyBtn"),
  stopClarifySplit = $("stopClarifySplit"), stopClarifyMenuBtn = $("stopClarifyMenuBtn"),
  finalizeClarifyBtn = $("finalizeClarifyBtn"),
  stopFinalizeClarifyBtn = $("stopFinalizeClarifyBtn"),
  stopFinalizeClarifySplit = $("stopFinalizeClarifySplit"),
  stopFinalizeClarifyMenuBtn = $("stopFinalizeClarifyMenuBtn"),
  applyAnswersBtn = $("applyAnswersBtn"), stopApplyAnswersBtn = $("stopApplyAnswersBtn"),
  stopApplyAnswersSplit = $("stopApplyAnswersSplit"),
  stopApplyAnswersMenuBtn = $("stopApplyAnswersMenuBtn"),
  finalizeGateList = $("finalizeGateList"),
  finalizeGateHint = $("finalizeGateHint"), clarifyRoundBadge = $("clarifyRoundBadge"),
  finalizeRoundProgress = $("finalizeRoundProgress"),
  skipMinorFindingsToggle = $("skipMinorFindingsToggle"),
  clarifyLanguageSelect = $("clarifyLanguageSelect"),
  clarifyOverlayCard = $("clarifyOverlayCard"), clarifyOverlayBadge = $("clarifyOverlayBadge"),
  clarifyOverlayHint = $("clarifyOverlayHint"),
  homeClarifyRoundBadge = $("homeClarifyRoundBadge"),
  implementReadyBanner = $("implementReadyBanner"), implementReadyBannerText = $("implementReadyBannerText"),
  clarifyStartImplementBtn = $("clarifyStartImplementBtn"),
  clarifyLogBody = $("clarifyLogBody"),
  clarifyTabOverviewBtn = $("clarifyTabOverviewBtn"), clarifyTabLogBtn = $("clarifyTabLogBtn"),
  clarifyOverviewTabPanel = $("clarifyOverviewTabPanel"), clarifyLogTabPanel = $("clarifyLogTabPanel"),
  clarifyRunStatus = $("clarifyRunStatus"),
  downloadPrdZipBtn = $("downloadPrdZipBtn"), downloadPlanZipBtn = $("downloadPlanZipBtn"),
  homeNotInit = $("homeNotInit"), homeSteps = $("homeSteps"),
  homeSelectFolderBtn = $("homeSelectFolderBtn"), homeWorkspacePath = $("homeWorkspacePath"),
  homeWorkspaceCloseBtn = $("homeWorkspaceCloseBtn"),
  homeCreateFolderBtn = $("homeCreateFolderBtn"), homeRecent = $("homeRecent"), homeRecentList = $("homeRecentList"),
  homeBackendStatusList = $("homeBackendStatusList"), settingsBackendStatusList = $("settingsBackendStatusList"),
  settingsDetectBackendsBtn = $("settingsDetectBackendsBtn"),
  homeStep1 = $("homeStep1"), homeStep2 = $("homeStep2"), homeStep3 = $("homeStep3"),
  homeStep1Status = $("homeStep1Status"), homeStep2Status = $("homeStep2Status"), homeStep3Status = $("homeStep3Status"),
  homeStep2FileList = $("homeStep2FileList"),
  homeAddFileBtn = $("homeAddFileBtn"), homeAddFolderBtn = $("homeAddFolderBtn"),
  homeStartClarifyBtn = $("homeStartClarifyBtn"), homeFinalizeClarifyBtn = $("homeFinalizeClarifyBtn"),
  homeApplyAnswersBtn = $("homeApplyAnswersBtn"),
  homeStartImplementBtn = $("homeStartImplementBtn"),
  startImplementBtn = $("startImplementBtn"), stopImplementBtn = $("stopImplementBtn"),
  stopImplementSplit = $("stopImplementSplit"), stopImplementMenuBtn = $("stopImplementMenuBtn"),
  stopOptionsMenu = $("stopOptionsMenu"), stopOptionsMenuItem = $("stopOptionsMenuItem"),
  implHeaderStatus = $("implHeaderStatus"), implGateList = $("implGateList"),
  implTabStatusBtn = $("implTabStatusBtn"), implTabLogBtn = $("implTabLogBtn"),
  implStatusPanel = $("implStatusPanel"), implLogPanel = $("implLogPanel"),
  implStatusBody = $("implStatusBody"), implLogBody = $("implLogBody"),
  verifyListBody = $("verifyListBody"), verifyListEmpty = $("verifyListEmpty"),
  verifyDetailBackLink = $("verifyDetailBackLink"), verifyDetailHeader = $("verifyDetailHeader"),
  verifyDetailStopBtn = $("verifyDetailStopBtn"), verifyDetailDeleteBtn = $("verifyDetailDeleteBtn"),
  verifyDetailStatus = $("verifyDetailStatus"), verifyDetailBody = $("verifyDetailBody"),
  modalOverlay = $("modalOverlay"), modalBox = $("modalBox"),
  modalTitle = $("modalTitle"), modalMessage = $("modalMessage"),
  modalInput = $("modalInput"), modalCancelBtn = $("modalCancelBtn"), modalOkBtn = $("modalOkBtn"),
  modalExtraBtn = $("modalExtraBtn"),
  logFileModalOverlay = $("logFileModalOverlay"), logFileModalBox = $("logFileModalBox"),
  logFileModalTitle = $("logFileModalTitle"), logFileModalBody = $("logFileModalBody"),
  logFileModalFullscreenBtn = $("logFileModalFullscreenBtn"), logFileModalCloseBtn = $("logFileModalCloseBtn"),
  settingsModelClarify = $("settingsModelClarify"), settingsModelClarifyApply = $("settingsModelClarifyApply"),
  settingsModelPlan = $("settingsModelPlan"),
  settingsModelImplement = $("settingsModelImplement"),
  settingsBackendClarify = $("settingsBackendClarify"), settingsBackendClarifyApply = $("settingsBackendClarifyApply"),
  settingsBackendPlan = $("settingsBackendPlan"),
  settingsBackendImplement = $("settingsBackendImplement"),
  settingsModelSelectClarify = $("settingsModelSelectClarify"), settingsModelSelectClarifyApply = $("settingsModelSelectClarifyApply"),
  settingsModelSelectPlan = $("settingsModelSelectPlan"),
  settingsModelSelectImplement = $("settingsModelSelectImplement"),
  settingsEffortClarify = $("settingsEffortClarify"), settingsEffortClarifyApply = $("settingsEffortClarifyApply"),
  settingsEffortPlan = $("settingsEffortPlan"),
  settingsEffortImplement = $("settingsEffortImplement"),
  settingsModelNoteClarify = $("settingsModelNoteClarify"), settingsModelNoteClarifyApply = $("settingsModelNoteClarifyApply"),
  settingsModelNotePlan = $("settingsModelNotePlan"),
  settingsModelNoteImplement = $("settingsModelNoteImplement"), settingsFeaturesPerSession = $("settingsFeaturesPerSession"),
  settingsMaxSessionRun = $("settingsMaxSessionRun"), settingsMaxClarificationRun = $("settingsMaxClarificationRun"),
  settingsFinalizeNoProgressRounds = $("settingsFinalizeNoProgressRounds"),
  settingsFinalizeCheckpointRounds = $("settingsFinalizeCheckpointRounds"),
  settingsFinalizeCheckpointCommit = $("settingsFinalizeCheckpointCommit"),
  settingsQaLoopStrikes = $("settingsQaLoopStrikes"), settingsMaxQaFailRounds = $("settingsMaxQaFailRounds"),
  settingsCommitAfterQaPass = $("settingsCommitAfterQaPass"),
  settingsTerminateLeftoverProcesses = $("settingsTerminateLeftoverProcesses"),
  settingsAllowFinalizeWithCritical = $("settingsAllowFinalizeWithCritical"),
  settingsAllowFinalizeWithCriticalWarning = $("settingsAllowFinalizeWithCriticalWarning"),
  settingsImplementRequirementInputs = document.getElementsByName("settingsImplementRequirement"),
  settingsImplementRequirementWarning = $("settingsImplementRequirementWarning"),
  settingsEmailEnabled = $("settingsEmailEnabled"), settingsEmailHost = $("settingsEmailHost"),
  settingsEmailPort = $("settingsEmailPort"), settingsEmailSecurity = $("settingsEmailSecurity"),
  settingsEmailProvider = $("settingsEmailProvider"), settingsEmailProviderGuidance = $("settingsEmailProviderGuidance"),
  settingsEmailUsername = $("settingsEmailUsername"), settingsEmailPassword = $("settingsEmailPassword"),
  settingsEmailFrom = $("settingsEmailFrom"), settingsEmailRecipients = $("settingsEmailRecipients"),
  settingsEmailEventList = $("settingsEmailEventList"), settingsEmailSelectAllBtn = $("settingsEmailSelectAllBtn"),
  settingsEmailClearAllBtn = $("settingsEmailClearAllBtn"), settingsTestEmailBtn = $("settingsTestEmailBtn"),
  settingsTestEmailStatus = $("settingsTestEmailStatus"),
  settingsUsageLimitRetryWaitMin = $("settingsUsageLimitRetryWaitMin"),
  settingsUsageLimitHeartbeatMin = $("settingsUsageLimitHeartbeatMin"),
  settingsServerOverloadRetryWaitMin = $("settingsServerOverloadRetryWaitMin"),
  settingsPollIntervalSec = $("settingsPollIntervalSec"),
  settingsSaveBtn = $("settingsSaveBtn"), settingsSaveStatus = $("settingsSaveStatus"),
  settingsUpdateCurrent = $("settingsUpdateCurrent"), settingsUpdateLatest = $("settingsUpdateLatest"),
  settingsWhatsNewBtn = $("settingsWhatsNewBtn"),
  settingsCheckUpdateBtn = $("settingsCheckUpdateBtn"), settingsUpdateBtn = $("settingsUpdateBtn"),
  settingsUpdateStatus = $("settingsUpdateStatus"),
  settingsRestartBtn = $("settingsRestartBtn"), settingsRestartStatus = $("settingsRestartStatus"),
  settingsClearAllBtn = $("settingsClearAllBtn"), settingsClearAllStatus = $("settingsClearAllStatus"),
  settingsPane = $("settingsPane"),
  settingsTabModelsBtn = $("settingsTabModelsBtn"), settingsTabRunsBtn = $("settingsTabRunsBtn"),
  settingsTabGuardrailsBtn = $("settingsTabGuardrailsBtn"),
  settingsTabNotificationsBtn = $("settingsTabNotificationsBtn"),
  settingsTabMaintenanceBtn = $("settingsTabMaintenanceBtn"),
  settingsTabModelsPanel = $("settingsTabModelsPanel"), settingsTabRunsPanel = $("settingsTabRunsPanel"),
  settingsTabGuardrailsPanel = $("settingsTabGuardrailsPanel"),
  settingsTabNotificationsPanel = $("settingsTabNotificationsPanel"),
  settingsTabMaintenancePanel = $("settingsTabMaintenancePanel"),
  settingsEmailDetails = $("settingsEmailDetails"),
  settingsDirtyHint = $("settingsDirtyHint"), settingsNothingToSave = $("settingsNothingToSave"),
  homePrinciplesBtn = $("homePrinciplesBtn"), homeStepPrinciplesStatus = $("homeStepPrinciplesStatus"),
  principlesEditor = $("principlesEditor"), principlesSaveBtn = $("principlesSaveBtn"),
  principlesSaveStatus = $("principlesSaveStatus");

const PANES = ["home", "spec", "specOverview", "clarify", "clarifyOverview", "impl", "settings",
  "principles", "verification", "verificationDetail"];

const state = {
  specTree: INITIAL_SPEC_TREE,
  clarifyUnanswered: INITIAL_CLARIFY_UNANSWERED || [],
  clarifyAnswered: INITIAL_CLARIFY_ANSWERED || [],
  expandedTop: { specification: INITIAL_VIEW === "specification", clarification: INITIAL_VIEW === "clarification" },
  expandedSpecDirs: new Set([""]),
  activeTop: INITIAL_VIEW,
  currentKind: null,          // null | "spec" | "clarify" — which file/toolbar is currently loaded
  selectedSpecPath: null,
  // Set when the open file is an epic's own spec (reached from its card on the
  // Implementation status list) rather than a file in the PRD tree. Those live in
  // sources.epics, a sibling of the PRD folder the Specification tree is rooted at, so
  // they are addressed by epic label and saved through their own endpoint.
  selectedEpicSpec: null,
  isMarkdown: false,
  isText: false,
  specMode: "view",
  specDirty: false,
  specShowingOverview: true,      // true = Specification pane shows the file-count/add-file overview
  selectedClarifyPath: null,
  clarifyDirty: false,
  clarifyShowingOverview: true,   // true = Clarification pane shows the file-list overview, not a single file
  clarifyRun: { running: false, mode: null, lines: [], progress: null, nextIndex: 0, pollTimer: null,
    gracefulStopRequested: false },
  clarifyTab: "overview",
  workspaceInitialized: !!INITIAL_WORKSPACE_INITIALIZED,
  workspaceRoot: INITIAL_WORKSPACE_ROOT || "",
  workspaceCanClose: !!INITIAL_WORKSPACE_CAN_CLOSE,
  recentWorkspaces: INITIAL_WORKSPACE_RECENT || [],
  clarifyFindings: INITIAL_CLARIFY_FINDINGS || { critical: 0, major: 0, minor: 0 },
  clarifyFinalize: INITIAL_CLARIFY_FINALIZE ||
    { hasRun: false, lastAction: null, critical: 0, ready: false, round: 0, maxRound: 0,
      finalizeRound: 0, allowFinalizeWithCritical: false },
  implementReadiness: INITIAL_IMPLEMENT_READINESS ||
    { hasRun: false, critical: 0, major: 0, requirement: "no_critical_or_major", ready: false,
      severitySweepPending: false, specChanged: false, pendingOverlay: 0 },
  // Is there anything left to clarify? (see _clarification_settled_status in
  // dashboard_clarify_parse.py). Advisory: when `settled`, Start/Continue Clarification and
  // Finalized Clarification are disabled because another round could only confirm what the
  // last one found — the server deliberately does NOT reject such a run. `reason` is a total
  // enum and is what the Unanswered table's empty state renders from. The fallback below
  // must stay in the server's shape; it is only ever seen if a payload arrives without it.
  clarifySettled: INITIAL_CLARIFY_SETTLED ||
    { settled: false, reason: "never_run", hasRun: false, lastAction: null, critical: 0,
      major: 0, minor: 0, unansweredFiles: 0, majorSweepPending: false,
      skipMinorFindings: true, specChanged: false },
  // Answered clarification findings not yet written into the PRD ({files, findings, chars}).
  // They're carried into every clarification round as already-decided resolutions, so they
  // don't block clarifying — but they DO block Start Implementation, which reads the PRD.
  // Must be re-assigned everywhere the sibling clarify.* fields are (see refreshSpecTree,
  // refreshClarifyList, and the refresh button) or the card below goes stale after a run.
  clarifyPendingOverlay: INITIAL_CLARIFY_PENDING_OVERLAY || { files: 0, findings: 0, chars: 0 },
  clarifyOverlayWarnThreshold: INITIAL_CLARIFY_OVERLAY_WARN_THRESHOLD || 25,
  sidebarWidth: uiPrefGet("sidebarWidth", 300),
  sidebarCollapsed: uiPrefGet("sidebarCollapsed", false),
  // The referenced-specification drawer. `open` is per-session on purpose: it is a dialog,
  // not part of the page layout, so a reload starts with it closed rather than refetching a
  // file nobody asked for. Only `width` is persisted.
  // `kind` is which of the two things the drawer is showing: a PRD file ("spec", keyed by
  // `path`) or one finding from another clarification round ("clarify", keyed by
  // `clarifyPath`). The header's open-elsewhere button dispatches on it.
  specPeek: { open: false, width: uiPrefGet("specPeekWidth", 480), path: null,
    kind: null, clarifyPath: null },
  principlesSet: !!INITIAL_PRINCIPLES_SET,
  backendsStatus: INITIAL_BACKENDS_STATUS || {},
  skipMinorFindings: INITIAL_SKIP_MINOR_FINDINGS ?? true,
  // Which language clarification findings are written in (config.json's
  // "clarification_language"). "en" is what every workspace that never touched the picker
  // has, and what the server falls back to for an unknown code.
  clarifyLanguage: INITIAL_CLARIFY_LANGUAGE || "en",
  epics: [],
  // Which epics' QA-history block is expanded, by epic_name. renderImplementStatus rebuilds
  // every card from scratch on each 1s poll tick, so — same reasoning as expandedSpecDirs —
  // the open/closed state has to live here rather than on the (thrown-away) DOM node, or a
  // <details> the user just opened would snap shut on the very next tick.
  expandedQaHistory: new Set(),
  // Server-computed (see _implementation_has_started): has any epic actually run yet?
  // Drives the Start -> Continue Implementation relabeling of all three buttons.
  implementStarted: false,
  implTab: "status",
  settingsTab: "models",
  updateAvailable: false,
  updateLatestVersion: null,
  // Whether the form currently differs from the config it was last filled from — one Save
  // writes every tab, so the user needs to know edits are pending on a tab they can't see.
  settingsDirty: false,
  implementRun: { running: false, lines: [], progress: null, nextIndex: 0, pollTimer: null,
    gracefulStopRequested: false },
  verifyRuns: [],
  verifyListPollTimer: null,
  // id of the verification run currently open on the detail pane, so its own poll tick
  // (while a run is still going) knows what to re-fetch — null while the list pane is shown.
  verifyDetailId: null,
  verifyDetailPollTimer: null,
};

// Every /api/tree consumer funnels through here: the Refresh button (99-events-init.js),
// refreshClarifyList (94-clarify-answers.js), refreshSpecTree (90-spec.js) and the initial
// load sync. They used to each unpack the payload themselves, which is how a field could be
// added in one place and go stale in the other two. Callers keep their own side effects
// (SPEC_PEEK_CACHE, toasts) and decide which panes to re-render; this only assigns state.
function applyTreePayload(data) {
  state.specTree = data.spec.tree;
  state.clarifyUnanswered = data.clarify.unanswered || [];
  state.clarifyAnswered = data.clarify.answered || [];
  state.workspaceInitialized = !!data.workspace.initialized;
  state.workspaceRoot = data.workspace.root || "";
  state.workspaceCanClose = !!data.workspace.canClose;
  state.recentWorkspaces = data.workspace.recent || [];
  state.clarifyFindings = data.clarify.findings;
  state.clarifyFinalize = data.clarify.finalize;
  state.implementReadiness = data.clarify.implementReadiness;
  if (data.clarify.settled) state.clarifySettled = data.clarify.settled;
  state.clarifyPendingOverlay = data.clarify.pendingOverlay || { files: 0, findings: 0, chars: 0 };
  state.clarifyOverlayWarnThreshold = data.clarify.overlayWarnThreshold || 25;
  state.skipMinorFindings = !!data.clarify.skipMinorFindings;
  state.clarifyLanguage = data.clarify.language || "en";
  state.principlesSet = !!(data.principles && data.principles.set);
  state.backendsStatus = data.backends || state.backendsStatus;
}
