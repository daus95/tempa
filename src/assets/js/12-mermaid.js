// ---------------------------------------------------------------------------
// Mermaid diagrams — a post-render pass over what renderMarkdown() produced.
//
// renderMarkdown() stays a pure string->string function (it is fed straight into innerHTML by
// four panes and recurses on itself for blockquotes), so it keeps emitting a ```mermaid
// fence as the plain <pre><code class="language-mermaid"> it always did. This part turns those
// nodes into SVG afterwards, in the DOM — which is also what lets the 3.5 MB mermaid bundle
// stay unfetched until a document that actually has a diagram is opened.
//
// Nothing here needs tearing down when a pane is navigated away from: no timers, no
// observers, no per-diagram listeners. The containers die with the innerHTML that replaces
// them, and the single document-level listener (the OS theme watcher) is process-lifetime by
// design.
// ---------------------------------------------------------------------------
// Same origin, served by dashboard_server._serve_mermaid — keep the ?v= in step with
// dashboard_assets.MERMAID_VERSION or the browser keeps handing back the old (immutable) copy.
const MERMAID_SRC = "/assets/mermaid.min.js?v=11.16.1";
let mermaidLoad = null;      // single-flight promise: many blocks, many renders, one <script>
let mermaidSeq = 0;          // unique element ids for mermaid.render
let mermaidPass = 0;         // bumped per pass so a slow one can't write into a replaced DOM
let mermaidThemeQuery = null;

function mermaidConfig() {
  return {
    startOnLoad: false,
    // Spec files are written by agents and edited by hand — treat their diagram text as
    // untrusted input: "strict" sanitizes HTML in labels and refuses click bindings.
    securityLevel: "strict",
    theme: mermaidThemeQuery && mermaidThemeQuery.matches ? "dark" : "default",
    fontFamily: '-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    themeVariables: {
      // Both mermaid themes paint edge labels on a near-white chip, which reads as a bright
      // sticker on a dark diagram. Take the value from the page's own palette instead, so it
      // matches the container the diagram sits in under either colour scheme.
      edgeLabelBackground:
        getComputedStyle(document.documentElement).getPropertyValue("--code-bg").trim() || "transparent",
    },
  };
}

function loadMermaid() {
  if (mermaidLoad) return mermaidLoad;
  mermaidThemeQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  mermaidLoad = new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = MERMAID_SRC;
    el.onload = () => (window.mermaid ? resolve(window.mermaid) : reject(new Error("mermaid did not register")));
    el.onerror = () => reject(new Error("could not load the diagram renderer"));
    document.head.appendChild(el);
  }).then((m) => { m.initialize(mermaidConfig()); watchMermaidTheme(); return m; });
  // A failed load must not poison every later document: drop the memo so the next render
  // that hits a diagram tries again.
  mermaidLoad.catch(() => { mermaidLoad = null; });
  return mermaidLoad;
}

// The original <pre> is never hidden or removed up front, so "failed" needs no restore step —
// the source is still on screen, it just gets a line above it saying why.
function mermaidNote(pre, message) {
  if (!pre || !pre.isConnected || pre.dataset.mermaidFailed) return;
  pre.dataset.mermaidFailed = "1";      // one note per block however many passes run over it
  const note = document.createElement("div");
  note.className = "mermaid-error";
  note.textContent = "Diagram could not be rendered — showing the source. " + message;
  pre.parentNode.insertBefore(note, pre);
}

function mermaidSourceToggle(source) {
  const details = document.createElement("details");
  details.className = "mermaid-source";
  const summary = document.createElement("summary");
  summary.textContent = "Show source";
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.className = "language-mermaid";
  code.textContent = source;             // textContent, never innerHTML
  pre.appendChild(code);
  details.appendChild(summary);
  details.appendChild(pre);
  return details;
}

// Turns every not-yet-rendered ```mermaid block under `root` into an SVG. Safe to call on the
// same node repeatedly — a rendered block no longer matches the selector and a failed one is
// marked — and deliberately fire-and-forget: callers set innerHTML synchronously and then call
// this, so nothing on screen waits for a diagram (or for the bundle behind it).
async function renderMermaidDiagrams(root) {
  const blocks = Array.from(root.querySelectorAll(
    "pre > code.language-mermaid:not([data-mermaid-done])"));
  if (!blocks.length) return;            // the whole point: no diagram, no 3.5 MB fetch
  const pass = ++mermaidPass;
  let mermaid;
  try {
    mermaid = await loadMermaid();
  } catch (e) {
    blocks.forEach((code) => mermaidNote(code.parentElement, e.message));
    return;
  }
  if (pass !== mermaidPass) return;      // the pane was re-rendered while the bundle loaded
  for (const code of blocks) {
    const pre = code.parentElement;
    if (!pre || !pre.isConnected) continue;
    const source = code.textContent;     // the DOM already decoded renderMarkdown's escaping
    code.dataset.mermaidDone = "1";
    const id = "mermaid-" + (++mermaidSeq);
    try {
      // parse() first: render() leaves a stray #d<id> node behind when it throws, and a
      // half-written diagram in a spec being edited is the common case, not the exception.
      if (!(await mermaid.parse(source, { suppressErrors: true }))) throw new Error("invalid diagram syntax");
      const rendered = await mermaid.render(id, source);
      if (pass !== mermaidPass || !pre.isConnected) return;
      const box = document.createElement("div");
      box.className = "mermaid-diagram";
      box.dataset.mermaidSource = source;          // kept so a theme flip can redraw it
      // srcLines mode (96-spec-peek.js) stamps the source line on the block; the diagram
      // takes the <pre>'s place in the flow, so it has to take its scroll anchor too.
      if (pre.dataset.srcLine) box.dataset.srcLine = pre.dataset.srcLine;
      box.innerHTML = rendered.svg;
      if (rendered.bindFunctions) rendered.bindFunctions(box);
      box.appendChild(mermaidSourceToggle(source));
      pre.replaceWith(box);
    } catch (e) {
      const orphan = document.getElementById("d" + id);
      if (orphan) orphan.remove();
      delete code.dataset.mermaidDone;
      mermaidNote(pre, (e && e.message) || String(e));
    }
  }
}

// Mermaid bakes its palette into the SVG at render time, so unlike the rest of the UI (pure
// CSS custom properties under prefers-color-scheme) a diagram cannot follow an OS theme flip
// on its own — it has to be drawn again. Cheap, because every container kept its own source:
// rehydrate them back into <pre> blocks and re-run the pass, with no help needed from
// whichever pane happens to be showing.
function watchMermaidTheme() {
  if (!mermaidThemeQuery || mermaidThemeQuery.tempaWatched) return;
  mermaidThemeQuery.tempaWatched = true;
  mermaidThemeQuery.addEventListener("change", () => {
    if (!mermaidLoad) return;
    mermaidLoad.then((m) => {
      m.initialize(mermaidConfig());
      document.querySelectorAll(".mermaid-diagram").forEach((box) => {
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.className = "language-mermaid";
        code.textContent = box.dataset.mermaidSource || "";
        pre.appendChild(code);
        box.replaceWith(pre);
      });
      renderMermaidDiagrams(document);
    });
  });
}
