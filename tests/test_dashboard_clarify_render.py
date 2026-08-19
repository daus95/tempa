"""Tests for dashboard_clarify_render.py's per-finding radio/textarea state — which
answer mode is shown as selected when a clarification file is (re)opened.

Covers the "follow the recommendation" round-trip: an item saved with
answer_mode == "recommendation" must render with that radio checked and the textarea
left disabled+empty (not prefilled with the recommendation text, which is already shown
once in the Recommendation block above — see ClarificationItem.resolved_answer for why
the raw existing_answer stays empty on disk for this case)."""

from __future__ import annotations

from pathlib import Path

import dashboard_clarify_render as dcr
from dashboard_clarify_parse import ClarificationItem


def _item(existing_answer="", answer_mode="", recommendation="do the thing"):
    return ClarificationItem(
        key="f0-1",
        raw_id="1",
        severity="major",
        title="Title",
        where="here",
        question="what?",
        recommendation=recommendation,
        existing_answer=existing_answer,
        answer_mode=answer_mode,
        file=Path("f.md"),
        answer_start=0,
        answer_end=0,
        has_markers=True,
    )


def test_unanswered_item_neither_radio_checked_and_textarea_disabled():
    html = dcr._render_item_html(_item())
    assert 'value="recommendation" checked' not in html
    assert 'value="own" checked' not in html
    assert "disabled" in html


def test_typed_own_answer_checks_own_and_prefills_textarea():
    html = dcr._render_item_html(_item(existing_answer="my own text"))
    assert 'value="own" checked' in html
    assert 'value="recommendation" checked' not in html
    assert ">my own text<" in html


def test_followed_recommendation_checks_recommendation_radio_not_own():
    html = dcr._render_item_html(_item(answer_mode="recommendation"))
    assert 'value="recommendation" checked' in html
    assert 'value="own" checked' not in html


def test_followed_recommendation_textarea_stays_disabled_and_empty():
    # The recommendation text must NOT be duplicated into the textarea — it's already
    # shown once in the Recommendation block right above.
    html = dcr._render_item_html(_item(answer_mode="recommendation", recommendation="do the thing"))
    assert "<textarea" in html
    textarea_start = html.index("<textarea")
    textarea_tag_end = html.index(">", textarea_start)
    assert "disabled" in html[textarea_start:textarea_tag_end]
    closing = html.index("</textarea>")
    assert html[textarea_tag_end + 1:closing] == ""


def test_no_recommendation_at_all_forces_own_answer():
    html = dcr._render_item_html(_item(recommendation=""))
    assert 'value="recommendation"' not in html  # no radio rendered at all
    assert 'value="own" checked' in html


def test_forward_only_legacy_duplicated_text_without_mode_renders_as_own_answer():
    # Old file: recommendation text duplicated into existing_answer, but no mode marker.
    # Must render as "own answer" — not reclassified as "followed recommendation".
    html = dcr._render_item_html(_item(existing_answer="do the thing", answer_mode=""))
    assert 'value="own" checked' in html
    assert 'value="recommendation" checked' not in html
    assert ">do the thing<" in html


# ---------------------------------------------------------------------------
# The `linkify` hook (see dashboard_spec_refs.make_linkifier)
# ---------------------------------------------------------------------------

def _shout(html):
    """A stand-in linkifier — deliberately not the real one, so these tests pin the wiring
    (which fields get passed through the hook) rather than the resolution rules, which
    test_dashboard_spec_refs.py owns."""
    return html + "<!--L-->"


def test_default_output_is_byte_identical_without_a_linkifier():
    """The no-regression guard for every clarification file already on disk: the hook
    defaults to identity, so rendering must be unchanged for callers that pass nothing."""
    item = _item(existing_answer="typed", recommendation="do the thing")
    assert dcr._render_item_html(item) == dcr._render_item_html(item, dcr._identity)
    blocks = [("text", "intro"), ("item", item)]
    assert dcr._render_blocks_html(blocks) == dcr._render_blocks_html(blocks, dcr._identity)


def test_every_finding_field_passes_through_the_linkifier():
    """Where/Question/Recommendation and the title all cite the spec, so all four are hooked."""
    html = dcr._render_item_html(_item(), _shout)
    assert html.count("<!--L-->") == 4


def test_prose_between_findings_is_linkified_too():
    html = dcr._render_blocks_html([("text", "intro prose")], _shout)
    assert "<!--L-->" in html
