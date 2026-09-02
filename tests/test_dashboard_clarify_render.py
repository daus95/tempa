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
from dashboard_clarify_overlap import DecidedElsewhere
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


# ---------------------------------------------------------------------------
# The "decided elsewhere" note (dashboard_clarify_overlap feeds it)
# ---------------------------------------------------------------------------

def _source(raw_id="C3", decided=True, applied=True, surfaces=("is_active",)):
    return DecidedElsewhere(
        file_name="clarification-20260826-023338.md", raw_id=raw_id,
        title="Voiding a sale pushes stock onto an archived product",
        decided=decided, applied=applied, surfaces=surfaces,
    )


def test_no_overlaps_renders_byte_identically_to_before_the_note_existed():
    """Most findings share no surface with an earlier round, and their card must be
    untouched — the note is an exception, not a new permanent field."""
    item = _item()
    assert dcr._render_item_html(item) == dcr._render_item_html(item, dcr._identity, [])
    assert "field overlap" not in dcr._render_item_html(item, dcr._identity, None)


def test_the_note_names_the_shared_surface_and_the_finding_that_decided_it():
    html = dcr._render_item_html(_item(), dcr._identity, [_source()])
    assert '<div class="field overlap">' in html
    assert "<code>is_active</code>" in html
    assert "<strong>C3</strong>" in html
    assert "clarification-20260826-023338.md" in html


def test_the_note_sits_between_the_recommendation_and_the_answer_controls():
    """It is a prompt to re-read the recommendation just above it before choosing below it;
    anywhere else in the card and it is read before there is anything to check."""
    html = dcr._render_item_html(_item(), dcr._identity, [_source()])
    assert html.index("field recommendation") < html.index("field overlap") < html.index("answer-block")


def test_each_source_is_labelled_with_what_state_it_is_in():
    """The three states carry different risks, so the note may not blur them: an unapplied
    decision contradicts something no document shows yet, an applied one is already in the PRD
    this round was evaluated against, and an unanswered one is not a decision at all."""
    def note(**kw):
        return dcr._render_item_html(_item(), dcr._identity, [_source(**kw)])

    assert "not yet answered" in note(decided=False)
    assert "decided, not yet in the PRD" in note(decided=True, applied=False)
    assert "already in the PRD" in note(decided=True, applied=True)
    assert "not yet answered" not in note(decided=True, applied=True)


def test_overlaps_reach_the_right_finding_by_id():
    item = _item()  # raw_id "1"
    blocks = [("item", item)]
    assert "field overlap" in dcr._render_blocks_html(blocks, dcr._identity, {"1": [_source()]})
    assert "field overlap" not in dcr._render_blocks_html(blocks, dcr._identity, {"9": [_source()]})


def test_a_source_id_from_the_file_is_escaped():
    html = dcr._render_item_html(_item(), dcr._identity, [_source(raw_id="<img>")])
    assert "<img>" not in html
    assert "&lt;img&gt;" in html


def test_the_source_is_a_link_the_peek_drawer_can_open():
    """assets/js/96-spec-peek.js dispatches on these two data attributes; without them the
    note names a finding the reader then has to go and find by hand."""
    html = dcr._render_item_html(_item(), dcr._identity, [_source()])
    assert 'class="clarify-ref"' in html
    assert 'data-clarify-path="clarification-20260826-023338.md"' in html
    assert 'data-clarify-id="C3"' in html


# ---------------------------------------------------------------------------
# render_finding_peek_html — one finding, read-only, in the drawer
# ---------------------------------------------------------------------------

def test_the_peeked_finding_carries_no_answer_controls():
    """It is somebody else's finding, opened to be read against the one being answered — a
    radio or textarea here would look editable and would be collected by nothing."""
    html = dcr.render_finding_peek_html(_item(existing_answer="typed"))
    assert "<textarea" not in html
    assert "type=\"radio\"" not in html


def test_the_peeked_finding_shows_what_was_decided():
    html = dcr.render_finding_peek_html(_item(existing_answer="Reactivate the product."))
    assert "Decided" in html
    assert "Reactivate the product." in html


def test_a_followed_recommendation_says_so_rather_than_rendering_blank():
    """"Follow the recommendation" stores an empty body on disk, so the Decided block has no
    text of its own to show — an empty box would read as "nobody answered this"."""
    html = dcr.render_finding_peek_html(_item(answer_mode="recommendation"))
    assert "Followed the recommendation above." in html


def test_an_unanswered_peeked_finding_says_so():
    assert "Not answered yet." in dcr.render_finding_peek_html(_item())


# ---------------------------------------------------------------------------
# Direction — the finding's prose is in the workspace's clarification language
# ---------------------------------------------------------------------------

def test_finding_prose_carries_dir_auto_so_rtl_languages_lay_out_correctly():
    """A finding is written in whichever language the Evaluation card picks, so its direction
    belongs to the content, not to the page: without this an Arabic finding renders
    left-to-right. Every left-to-right language, English included, is unaffected."""
    html = dcr._render_item_html(_item(existing_answer="typed"))
    # title, where, question, recommendation, and the answer box the reader types into
    assert html.count('dir="auto"') == 5
    peek = dcr.render_finding_peek_html(_item(existing_answer="typed"))
    assert peek.count('dir="auto"') == 5   # ...and the Decided block, minus the textarea


def test_the_dashboards_own_labels_keep_the_pages_direction():
    """Only the agent-written prose is direction-agnostic. Where/Question/Recommendation are
    the dashboard's own English chrome, so they must NOT pick up the content's direction."""
    html = dcr._render_item_html(_item())
    for label in ("<h4>Where</h4>", "<h4>Question</h4>", "<h4>Recommendation</h4>"):
        assert label in html
