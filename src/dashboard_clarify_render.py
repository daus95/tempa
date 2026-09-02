"""Server-side rendering of clarification content into HTML.

A tiny markdown renderer plus the per-finding card markup (severity badge, where/question/
recommendation, answer selector + textarea) shown in the Clarification pane."""

from __future__ import annotations

import html as html_lib
import re

from dashboard_clarify_overlap import DecidedElsewhere
from dashboard_clarify_parse import SEVERITY_LABELS, ClarificationItem


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


# A finding's prose is written in the workspace's chosen clarification language (see
# tempa_config.CLARIFICATION_LANGUAGES), so its direction is a property of the content rather
# than of the dashboard: `dir="auto"` lets the browser lay an Arabic finding out right-to-left
# while leaving every left-to-right language — English included — rendered exactly as before.
# The labels around it stay the page's own direction, because they are the dashboard's UI.
_DIR = ' dir="auto"'


def _attr(s: str) -> str:
    return html_lib.escape(s, quote=True)


def _identity(html: str) -> str:
    return html


def _render_overlap_html(sources: list[DecidedElsewhere]) -> str:
    """The "decided elsewhere" note, rendered between the recommendation and the answer
    controls — the last thing read before "Follow the recommendation" is clicked.

    Each line leads with the shared surfaces rather than the other finding's id, because that
    is what a reader scans for: the field, entity or message this answer is about to reword.
    Empty input renders nothing at all, so a file with no overlaps is byte-identical to what
    this module produced before the note existed. See dashboard_clarify_overlap."""
    if not sources:
        return ""
    lines = []
    for source in sources:
        surfaces = ", ".join(f"<code>{html_lib.escape(s)}</code>" for s in source.surfaces)
        if not source.decided:
            state = '<span class="pending">not yet answered</span>'
        elif not source.applied:
            state = '<span class="pending">decided, not yet in the PRD</span>'
        else:
            state = "already in the PRD"
        # The id+file is a link into the peek drawer (assets/js/96-spec-peek.js), the same one
        # spec references open. Comparing two decisions is the whole point of the note, and it
        # is worth nothing if reading the other one means leaving this page and losing the
        # unsaved answers on it.
        ref = (
            f'<a href="#" class="clarify-ref" data-clarify-path="{_attr(source.file_name)}" '
            f'data-clarify-id="{_attr(source.raw_id)}">'
            f"<strong>{html_lib.escape(source.raw_id)}</strong> in "
            f"{html_lib.escape(source.file_name)}</a>"
        )
        lines.append(f"<li>{surfaces} — {ref} <span class=\"state\">({state})</span></li>")
    return (
        '<div class="field overlap"><h4>Decided elsewhere</h4>'
        f"<ul>{''.join(lines)}</ul>"
        "<p>An earlier finding already names these. Check this recommendation against it "
        "before accepting — restating one of them differently is what a later round has to "
        "raise as a contradiction.</p></div>"
    )


def render_finding_peek_html(item: ClarificationItem, linkify=_identity) -> str:
    """One finding rendered read-only for the peek drawer — what a "decided elsewhere" link
    opens.

    Deliberately NOT _render_item_html: this is somebody else's finding, opened to be read
    against the one being answered, so it carries no radios and no textarea. What it adds
    instead is the **Decided** block, because the recorded answer — not the recommendation —
    is what the reader came to compare against. A finding answered with "follow the
    recommendation" stores an empty body on disk, so say so in words rather than rendering a
    blank block (see ClarificationItem.resolved_answer)."""
    if item.existing_answer:
        decided = f'<div class="md"{_DIR}>{linkify(render_markdown(item.existing_answer))}</div>'
    elif item.answer_mode == "recommendation":
        decided = "<p>Followed the recommendation above.</p>"
    else:
        decided = '<p class="pending">Not answered yet.</p>'

    recommendation_html = (
        f'<div class="field recommendation"><h4>Recommendation</h4>'
        f'<div class="md"{_DIR}>{linkify(render_markdown(item.recommendation))}</div></div>'
        if item.recommendation else ""
    )
    return f"""
<section class="item sev-{item.severity} peek-finding">
  <header>
    <span class="badge {item.severity}">{SEVERITY_LABELS.get(item.severity, item.severity)}</span>
    <h3{_DIR}>{linkify(_md_inline(item.title))}</h3>
  </header>
  <div class="field"><h4>Where</h4><div class="md"{_DIR}>{linkify(render_markdown(item.where))}</div></div>
  <div class="field"><h4>Question</h4><div class="md"{_DIR}>{linkify(render_markdown(item.question))}</div></div>
  {recommendation_html}
  <div class="field decided"><h4>Decided</h4>{decided}</div>
</section>
""".strip()


def _render_item_html(item: ClarificationItem, linkify=_identity,
                      overlaps: list[DecidedElsewhere] | None = None) -> str:
    key = _attr(item.key)
    has_recommendation = bool(item.recommendation)
    # "Follow the recommendation" round-trips as checked only for answers saved through
    # the marker'd mode="recommendation" path (see apply_answers_to_file /
    # _fill_unanswered_with_recommendations) — pre-existing files that duplicated the
    # recommendation text into "Your answer" with no mode marker are left exactly as
    # they rendered before this: shown as "own answer" (forward-only, deliberately not
    # reclassified by comparing existing_answer to recommendation).
    followed_recommendation = has_recommendation and item.answer_mode == "recommendation"
    default_own = (bool(item.existing_answer) or not has_recommendation) and not followed_recommendation

    # Unanswered items with a recommendation start with NEITHER radio checked, so the
    # user has to actively pick one — pre-selecting "recommendation" here meant a user
    # who wanted the default outcome never fired a `change` event, leaving clarifyDirty
    # false and the Save button stuck disabled (see followAllBtn for the bulk version).
    recommendation_radio = ""
    if has_recommendation:
        rec_checked = "checked" if followed_recommendation else ""
        recommendation_radio = (
            f'<label><input type="radio" name="mode-{key}" value="recommendation" {rec_checked}> '
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
        f'<div class="md"{_DIR}>{linkify(render_markdown(item.recommendation))}</div></div>'
        if has_recommendation else ""
    )

    overlap_html = _render_overlap_html(overlaps or [])

    return f"""
<section class="item sev-{item.severity}" data-key="{key}">
  <header>
    <span class="badge {item.severity}">{SEVERITY_LABELS.get(item.severity, item.severity)}</span>
    <h3{_DIR}>{linkify(_md_inline(item.title))}</h3>
  </header>
  <div class="field"><h4>Where</h4><div class="md"{_DIR}>{linkify(render_markdown(item.where))}</div></div>
  <div class="field"><h4>Question</h4><div class="md"{_DIR}>{linkify(render_markdown(item.question))}</div></div>
  {recommendation_html}
  {overlap_html}
  <div class="answer-block">
    <div class="selector">
      {recommendation_radio}
      {own_radio}
    </div>
    <textarea rows="5" data-key="{key}"{_DIR} placeholder="Write your answer here..." {textarea_disabled}>{textarea_value}</textarea>
  </div>
</section>
""".strip()


def _render_blocks_html(blocks: list[tuple[str, object]], linkify=_identity,
                        overlaps: dict[str, list[DecidedElsewhere]] | None = None) -> str:
    """`linkify` turns spec references in the finished HTML into links back to the PRD (see
    dashboard_spec_refs.make_linkifier). It defaults to a no-op so every caller that has no
    PRD folder to resolve against — and every existing test — gets byte-identical output.

    `overlaps` is dashboard_clarify_overlap.overlaps_for_file's output, keyed by finding id;
    it defaults to nothing for the same reason."""
    parts: list[str] = []
    for kind, payload in blocks:
        if kind == "text":
            rendered = linkify(render_markdown(payload))  # type: ignore[arg-type]
            if rendered:
                parts.append(f'<div class="doc-text"{_DIR}>{rendered}</div>')
        else:
            parts.append(_render_item_html(
                payload, linkify,
                (overlaps or {}).get(payload.raw_id),  # type: ignore[union-attr]
            ))  # type: ignore[arg-type]
    return "\n".join(parts)
