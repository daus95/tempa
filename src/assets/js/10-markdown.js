// ---------------------------------------------------------------------------
// Minimal, dependency-free Markdown renderer for the Specification pane (offline-safe).
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  // Escapes quotes too (not just &/</>) so this is also safe to interpolate into a
  // double-quoted HTML attribute (e.g. data-epic="${escapeHtml(name)}") — text-node usages
  // are unaffected since browsers render &quot;/&#39; back to "/' there regardless.
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
// Layout preferences (panel widths, explorer collapsed). NOTE the reach of this:
// run_dashboard() binds an EPHEMERAL port by default and localStorage is scoped to the
// origin, so these survive F5 and the Settings > Restart Server round-trip, but a fresh
// `tempa dashboard` is a new origin and starts from the defaults. That is acceptable
// precisely because nothing here is user data — two pixel widths and a boolean. Anything
// that must outlive a launch belongs in config.json, not here.
const UI_PREF_NS = "tempa.ui.";
function uiPrefGet(key, fallback) {
  try {
    const raw = localStorage.getItem(UI_PREF_NS + key);
    if (raw === null) return fallback;
    const val = JSON.parse(raw);
    return typeof val === typeof fallback ? val : fallback;   // reject a hand-edited value
  } catch (e) { return fallback; }      // storage disabled or full — prefs are optional
}
function uiPrefSet(key, val) {
  try { localStorage.setItem(UI_PREF_NS + key, JSON.stringify(val)); } catch (e) {}
}
// Renders one of the <symbol id="i-*"> icons defined in the sprite at the top of
// dashboard.html as an inline <svg><use> — the Lucide-icon equivalent of the emoji
// strings this app used to interpolate directly into innerHTML/textContent.
function iconSvg(name, extraClass) {
  return `<svg class="icon-svg${extraClass ? " " + extraClass : ""}"><use href="#i-${name}"></use></svg>`;
}
// dd/MM HH:mm, local time — the wall-clock timestamp format used everywhere in the app
// (banners, log lines, home page). `epochSeconds` is epoch seconds, falsy/missing -> "–".
function formatEpochShort(epochSeconds) {
  if (!epochSeconds) return "–";
  const d = new Date(epochSeconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function inlineMd(src) {
  const codes = [];
  src = src.replace(/`([^`]+?)`/g, (m, c) => {
    codes.push("<code>" + escapeHtml(c) + "</code>");
    return "" + (codes.length - 1) + "";
  });
  src = escapeHtml(src);
  src = src.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)/g,
    (m, a, u, t) => `<img alt="${a}" src="${u}">`);
  src = src.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)/g,
    (m, txt, u) => `<a href="${u}" target="_blank" rel="noopener">${txt}</a>`);
  src = src.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
  src = src.replace(/__([^_]+?)__/g, "<strong>$1</strong>");
  src = src.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");
  src = src.replace(/(^|[^\w])_([^_\n]+?)_(?![\w])/g, "$1<em>$2</em>");
  src = src.replace(/~~([^~]+?)~~/g, "<del>$1</del>");
  src = src.replace(/(\d+)/g, (m, i) => codes[+i]);
  return src;
}
function isItem(line) { return line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/); }

// `srcLines`/`base` are threaded through so each <li> can carry the source line of the
// bullet that produced it. A requirement written as `- **BR-07.1** ...` is a list ITEM,
// not a list, so stamping only the enclosing <ul> would point a reference at the top of
// a forty-bullet block.
function buildList(items, srcLines, base) {
  let idx = 0;
  function buildLevel(indent) {
    let html = "";
    while (idx < items.length && items[idx].indent >= indent) {
      if (items[idx].indent > indent) { html += buildLevel(items[idx].indent); continue; }
      const ordered = items[idx].ordered;
      const tag = ordered ? "ol" : "ul";
      html += "<" + tag + ">";
      while (idx < items.length && items[idx].indent === indent && items[idx].ordered === ordered) {
        const liAttr = srcLines ? ` data-src-line="${base + items[idx].line + 1}"` : "";
        let li = "<li" + liAttr + ">" + inlineMd(items[idx].text);
        idx++;
        if (idx < items.length && items[idx].indent > indent) li += buildLevel(items[idx].indent);
        li += "</li>";
        html += li;
      }
      html += "</" + tag + ">";
    }
    return html;
  }
  return buildLevel(items[0].indent);
}

// `opts.srcLines` is opt-in, and off by default so the Specification pane (90-spec.js), the
// verification detail (75-verify.js) and the log-file modal (30-modals.js) keep producing
// byte-identical output. With it, every top-level block carries data-src-line="<1-based line
// in src>" — the anchor the clarification pane's spec drawer scrolls to when a finding's
// reference is clicked. `opts.lineOffset` is how the blockquote recursion below keeps those
// numbers absolute rather than relative to the quoted fragment.
function renderMarkdown(src, opts) {
  const srcLines = !!(opts && opts.srcLines);
  const base = (opts && opts.lineOffset) || 0;
  // Neither replacement changes the line COUNT, so line indices stay faithful to the source.
  src = src.replace(/\r\n?/g, "\n").replace(/\t/g, "    ");
  const lines = src.split("\n");
  const out = [];
  let i = 0;
  const n = lines.length;
  // Every branch below emits a string that opens with exactly one tag, so stamping the
  // attribute onto that first tag is a single edit point instead of seven.
  const push = (html, start) => out.push(srcLines
    ? html.replace(/^<([a-zA-Z][^\s>/]*)/, `<$1 data-src-line="${base + start + 1}"`)
    : html);
  while (i < n) {
    const start = i;      // the source line this block begins on
    const line = lines[i];
    const fm = line.match(/^(\s*)(`{3,}|~{3,})(.*)$/);
    if (fm) {
      const fence = fm[2][0], flen = fm[2].length, lang = fm[3].trim();
      i++;
      const buf = [];
      while (i < n) {
        const cm = lines[i].match(/^(\s*)(`{3,}|~{3,})\s*$/);
        if (cm && cm[2][0] === fence && cm[2].length >= flen) { i++; break; }
        buf.push(lines[i]); i++;
      }
      push('<pre><code' + (lang ? ` class="language-${lang}"` : "") +
        ">" + escapeHtml(buf.join("\n")) + "</code></pre>", start);
      continue;
    }
    if (/^\s*$/.test(line)) { i++; continue; }
    const hm = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (hm) { push(`<h${hm[1].length}>` + inlineMd(hm[2]) + `</h${hm[1].length}>`, start); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { push("<hr>", start); i++; continue; }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < n && /^\s*>/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      // The nested render is told where the quote STARTS, or every block inside it would
      // report the OUTER offset. Stripping the quote marker keeps one buffer entry
      // per source line, so the mapping stays 1:1.
      push("<blockquote>" + renderMarkdown(buf.join("\n"),
        srcLines ? { srcLines: true, lineOffset: base + start } : undefined) + "</blockquote>", start);
      continue;
    }
    if (line.indexOf("|") >= 0 && i + 1 < n &&
        /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$/.test(lines[i + 1])) {
      const cells = (l) => {
        let s = l.trim();
        if (s.startsWith("|")) s = s.slice(1);
        if (s.endsWith("|")) s = s.slice(0, -1);
        return s.split("|").map((c) => c.trim());
      };
      const heads = cells(lines[i]);
      const aligns = cells(lines[i + 1]).map((c) => {
        const l = c.startsWith(":"), r = c.endsWith(":");
        return l && r ? "center" : r ? "right" : l ? "left" : "";
      });
      i += 2;
      const rows = [];
      // Keep each row's own source line: in this spec shape a requirement IS a table row, so
      // a reference to one must land on the row, not on the head of the whole table.
      while (i < n && lines[i].indexOf("|") >= 0 && !/^\s*$/.test(lines[i])) { rows.push({ cells: cells(lines[i]), line: i }); i++; }
      const sty = (k) => aligns[k] ? ` style="text-align:${aligns[k]}"` : "";
      let html = "<table><thead><tr>" +
        heads.map((h, k) => `<th${sty(k)}>` + inlineMd(h) + "</th>").join("") + "</tr></thead><tbody>";
      for (const r of rows) {
        const trAttr = srcLines ? ` data-src-line="${base + r.line + 1}"` : "";
        html += "<tr" + trAttr + ">" +
          r.cells.map((c, k) => `<td${sty(k)}>` + inlineMd(c) + "</td>").join("") + "</tr>";
      }
      push(html + "</tbody></table>", start);
      continue;
    }
    if (isItem(line)) {
      const items = [];
      while (i < n) {
        const m = isItem(lines[i]);
        if (m) { items.push({ indent: m[1].length, ordered: /\d/.test(m[2]), text: m[3], line: i }); i++; continue; }
        if (/^\s*$/.test(lines[i])) {
          let j = i + 1;
          while (j < n && /^\s*$/.test(lines[j])) j++;
          if (j < n && isItem(lines[j])) { i = j; continue; }
        }
        break;
      }
      push(buildList(items, srcLines, base), start);
      continue;
    }
    const buf = [];
    while (i < n && !/^\s*$/.test(lines[i]) && !/^(#{1,6})\s+/.test(lines[i]) &&
           !/^\s*([-*_])(\s*\1){2,}\s*$/.test(lines[i]) && !/^\s*>/.test(lines[i]) &&
           !/^(\s*)(`{3,}|~{3,})/.test(lines[i]) && !isItem(lines[i])) {
      buf.push(lines[i]); i++;
    }
    push("<p>" + inlineMd(buf.join("\n").trim()).replace(/\n/g, "<br>") + "</p>", start);
  }
  return out.join("\n");
}

