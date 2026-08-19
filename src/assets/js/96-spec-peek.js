// ---------------------------------------------------------------------------
// Clarification: the referenced-specification drawer
// ---------------------------------------------------------------------------
// A finding cites requirements by id ("M07-FR-03 Group A aggregation rule; BR-07.2"), and
// deciding it means reading what those say. dashboard_spec_refs.py resolves each id to a
// file and line and emits it as `a.spec-ref`; clicking one opens the spec right here instead
// of sending the reader off to the Specification pane and back.
//
// The drawer is modal — a fixed overlay swallows every pointer event outside it, and `.app`
// is marked inert so Tab can't reach the findings behind it either. Closing it restores the
// Clarification page exactly as it was; nothing about #clarifyPane is touched.

// Whole files, keyed by PRD-relative path. Small because the drawer is for reading one
// requirement at a time, not for browsing.
const SPEC_PEEK_CACHE = new Map();
const SPEC_PEEK_CACHE_MAX = 8;
// Guards against a slow fetch landing after a fast one when refs are clicked in quick
// succession — without it the wrong file can win and the drawer lies about what it shows.
let specPeekSeq = 0;
let specPeekReturnFocus = null;

function setSpecPeekOpen(on) {
  state.specPeek.open = on;
  specPeekOverlay.classList.toggle("hidden", !on);
  // `inert` is what makes this a real modal for the keyboard as well as the mouse. A browser
  // without it still gets the pointer block from the overlay, which is what the app's other
  // modals rely on anyway.
  if (appEl) appEl.inert = on;
  if (on) {
    specPeekCloseBtn.focus();
    return;
  }
  state.specPeek.path = null;
  specPeekBody.innerHTML = "";
  if (specPeekReturnFocus && document.contains(specPeekReturnFocus)) specPeekReturnFocus.focus();
  specPeekReturnFocus = null;
}

function closeSpecPeek() {
  if (!state.specPeek.open) return;
  setSpecPeekOpen(false);
}

// Scrolls to the block owning 1-based source `line` and flashes it. Blocks carry ascending
// data-src-line in document order, so the last one at or before `line` is the tightest match.
// A UNIQUE `token` match is preferred over the line: the line is where the id sat when the
// server read the file, whereas the id itself has not moved even if the spec was edited
// since — and it is the id the reader clicked, so landing on it is what they asked for.
function scrollToSrcLine(container, line, token) {
  const blocks = container.querySelectorAll("[data-src-line]");
  if (!blocks.length) return;
  let hit = null;
  if (token) {
    const named = Array.prototype.filter.call(blocks, (el) => el.textContent.indexOf(token) >= 0);
    if (named.length === 1) hit = named[0];
  }
  if (!hit && line) {
    for (const el of blocks) {
      if (+el.dataset.srcLine <= line) hit = el; else break;
    }
  }
  if (!hit) hit = blocks[0];
  container.scrollTop = Math.max(0, hit.offsetTop - 16);
  const prev = container.querySelector(".src-line-hit");
  if (prev) prev.classList.remove("src-line-hit");
  void hit.offsetWidth;          // restarts the flash when it is the same block again
  hit.classList.add("src-line-hit");
}

function specPeekPlaceholder(message) {
  return '<div class="placeholder-pane">' + escapeHtml(message) + "</div>";
}

async function openSpecPeek(path, line, token) {
  if (!state.specPeek.open) specPeekReturnFocus = document.activeElement;
  setSpecPeekOpen(true);
  // Same file already on screen: re-scroll only. Re-fetching would blank and reflow the
  // drawer for no reason, which reads as a flicker when following two refs into one file.
  if (state.specPeek.path === path) {
    // `data` isn't in scope here — it's declared below, on the fetch/cache path this branch
    // is skipping. The cache is what already holds the payload for the file on screen.
    const cached = SPEC_PEEK_CACHE.get(path);
    if (cached && cached.text && cached.markdown) renderMermaidDiagrams(specPeekBody);  // async (12-mermaid.js)
    scrollToSrcLine(specPeekBody, line, token);
    return;
  }
  specPeekPath.textContent = path;
  specPeekPath.title = path;
  let data = SPEC_PEEK_CACHE.get(path);
  if (!data) {
    const seq = ++specPeekSeq;
    specPeekBody.innerHTML = specPeekPlaceholder("Loading…");
    try {
      const res = await fetch("/api/spec/file?path=" + encodeURIComponent(path));
      data = await res.json();
    } catch (e) {
      if (seq === specPeekSeq) {
        specPeekBody.innerHTML = specPeekPlaceholder("Network error opening this file.");
      }
      return;
    }
    if (seq !== specPeekSeq) return;         // a later click already owns the drawer
    if (!data.ok) {
      state.specPeek.path = null;
      specPeekBody.innerHTML = specPeekPlaceholder(
        data.error || "Could not open this specification file.");
      return;
    }
    if (SPEC_PEEK_CACHE.size >= SPEC_PEEK_CACHE_MAX) {
      SPEC_PEEK_CACHE.delete(SPEC_PEEK_CACHE.keys().next().value);
    }
    SPEC_PEEK_CACHE.set(path, data);
  }
  state.specPeek.path = path;
  specPeekBody.innerHTML = !data.text
    ? specPeekPlaceholder(data.reason || "This file can't be shown as text.")
    : data.markdown
      ? renderMarkdown(data.content, { srcLines: true })
      : "<pre><code>" + escapeHtml(data.content) + "</code></pre>";
  scrollToSrcLine(specPeekBody, line, token);
}

// Delegated, and registered once: #clarifyBody itself survives every innerHTML rebuild in
// openClarifyFile/syncClarifyLockState, its children do not.
clarifyBody.addEventListener("click", (e) => {
  const ref = e.target.closest("a.spec-ref");
  if (!ref || !ref.dataset.specPath) return;
  e.preventDefault();                       // never a navigation, despite the href="#"
  openSpecPeek(ref.dataset.specPath, parseInt(ref.dataset.specLine, 10) || 0,
    (ref.textContent || "").trim());
});

specPeekCloseBtn.addEventListener("click", closeSpecPeek);
specPeekOverlay.addEventListener("click", (e) => {
  if (e.target === specPeekOverlay) closeSpecPeek();
});
// Reuses openSpecFile as-is, including its confirmDiscardIfDirty() prompt — the same one
// selectTop() already shows when leaving a clarification file with unsaved answers.
specPeekOpenBtn.addEventListener("click", () => {
  const path = state.specPeek.path;
  if (!path) return;
  closeSpecPeek();
  openSpecFile({ path: path });
});

// Drag the drawer's left edge. Its own small handler rather than a shared abstraction: the
// explorer splitter resizes the opposite edge of a different element, and two short
// independent handlers are clearer than one parameterised over both.
(function () {
  let dragging = false, width = state.specPeek.width;
  specPeekResize.addEventListener("mousedown", (e) => {
    dragging = true;
    specPeekResize.classList.add("dragging");
    document.body.classList.add("resizing");   // stops the slide-in replaying every frame
    document.body.style.userSelect = "none";
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    width = Math.max(320, Math.min(window.innerWidth - e.clientX, window.innerWidth * 0.8));
    state.specPeek.width = width;
    document.documentElement.style.setProperty("--peek-w", width + "px");
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    specPeekResize.classList.remove("dragging");
    document.body.classList.remove("resizing");
    document.body.style.userSelect = "";
    uiPrefSet("specPeekWidth", width);
  });
})();
