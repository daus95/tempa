"""Server-side rendering of clarification content into HTML.

A tiny markdown renderer plus the per-finding card markup (severity badge, where/question/
recommendation, answer selector + textarea) shown in the Clarification pane."""

from __future__ import annotations

import html as html_lib
import re

from dashboard_clarify_parse import ClarificationItem, SEVERITY_LABELS


def _md_inline(s: str) -> str:
    s = html_lib.escape(s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'`([^`]+?)`', r'<code>\1</code>', s)
    s = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'<em>\1</em>', s)
    return s


def render_markdown(text: str) -> str:
    text = text.strip("\n")
    if not text.strip():
        return ""
    parts: list[str] = []
    para: list[str] = []
    list_items: list[str] = []

    def flush_para() -> None:
        if para:
            parts.append(f"<p>{_md_inline(' '.join(para))}</p>")
            para.clear()

    def flush_list() -> None:
        if list_items:
            parts.append("<ul>" + "".join(f"<li>{_md_inline(li)}</li>" for li in list_items) + "</ul>")
            list_items.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_para()
            flush_list()
            continue
        heading_m = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        list_m = re.match(r'^[-*]\s+(.*)$', stripped)
        if heading_m:
            flush_para()
            flush_list()
            level = min(len(heading_m.group(1)) + 2, 6)
            parts.append(f"<h{level}>{_md_inline(heading_m.group(2))}</h{level}>")
        elif list_m:
            flush_para()
            list_items.append(list_m.group(1))
        else:
            flush_list()
            para.append(stripped)
    flush_para()
    flush_list()
    return "\n".join(parts)


def _attr(s: str) -> str:
    return html_lib.escape(s, quote=True)


def _render_item_html(item: ClarificationItem) -> str:
    key = _attr(item.key)
    has_recommendation = bool(item.recommendation)
    default_own = bool(item.existing_answer) or not has_recommendation

    # Unanswered items with a recommendation start with NEITHER radio checked, so the
    # user has to actively pick one — pre-selecting "recommendation" here meant a user
    # who wanted the default outcome never fired a `change` event, leaving clarifyDirty
    # false and the Save button stuck disabled (see followAllBtn for the bulk version).
    recommendation_radio = ""
    if has_recommendation:
        recommendation_radio = (
            f'<label><input type="radio" name="mode-{key}" value="recommendation"> '
            f"Follow the recommendation</label>"
        )
    own_checked = "checked" if default_own else ""
    own_radio = (
        f'<label><input type="radio" name="mode-{key}" value="own" {own_checked}> '
        f"I'll write my own answer</label>"
    )

    textarea_disabled = "" if default_own else "disabled"
    textarea_value = html_lib.escape(item.existing_answer) if default_own else ""

    recommendation_html = (
        f'<div class="field recommendation"><h4>Recommendation</h4>'
        f'<div class="md">{render_markdown(item.recommendation)}</div></div>'
        if has_recommendation else ""
    )

    return f"""
<section class="item sev-{item.severity}" data-key="{key}">
  <header>
    <span class="badge {item.severity}">{SEVERITY_LABELS.get(item.severity, item.severity)}</span>
    <h3>{_md_inline(item.title)}</h3>
  </header>
  <div class="field"><h4>Where</h4><div class="md">{render_markdown(item.where)}</div></div>
  <div class="field"><h4>Question</h4><div class="md">{render_markdown(item.question)}</div></div>
  {recommendation_html}
  <div class="answer-block">
    <div class="selector">
      {recommendation_radio}
      {own_radio}
    </div>
    <textarea rows="5" data-key="{key}" placeholder="Write your answer here..." {textarea_disabled}>{textarea_value}</textarea>
  </div>
</section>
""".strip()


def _render_blocks_html(blocks: list[tuple[str, object]]) -> str:
    parts: list[str] = []
    for kind, payload in blocks:
        if kind == "text":
            rendered = render_markdown(payload)  # type: ignore[arg-type]
            if rendered:
                parts.append(f'<div class="doc-text">{rendered}</div>')
        else:
            parts.append(_render_item_html(payload))  # type: ignore[arg-type]
    return "\n".join(parts)
