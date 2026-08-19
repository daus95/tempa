"""Tests for dashboard_spec_refs.py — resolving the spec references a finding cites.

Two things are being pinned down here, and they pull in opposite directions:

- a requirement id a finding mentions must resolve to the line that DEFINES it, and
- a token that only looks like one (`R8`/`M2` — a reference to an earlier finding, or a
  field name a finding quotes) must resolve to nothing at all.

The linkifier half is tested for what it must NOT corrupt: attribute values, HTML entities,
and the inside of a code span are all rendered output that a naive regex would happily eat.
"""

from __future__ import annotations

import os

import dashboard_spec_refs as refs
from dashboard_clarify_render import render_markdown


def _prd(tmp_path, files: dict[str, str]):
    """Write a fake PRD folder and return its path, with the index cache cleared so each
    test builds its own."""
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    refs._build_index.cache_clear()
    return tmp_path


def _links(html: str) -> list[tuple[str, str, str]]:
    """(label, path, line) for every spec-ref anchor in `html`."""
    import re
    return [
        (m.group(4), m.group(1), m.group(2))
        for m in re.finditer(
            r'data-spec-path="([^"]+)" data-spec-line="(\d+)" data-spec-kind="(\w+)"'
            r'[^>]*>(.*?)</a>', html)
    ]


# ---------------------------------------------------------------------------
# Where a definition is recognised
# ---------------------------------------------------------------------------

def test_id_defined_in_a_heading_resolves_to_that_line(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "# Intro\n\n## M07-FR-03 Journals\n\ntext\n"})
    assert refs.get_index(root).ids["M07-FR-03"][0].line == 3


def test_id_defined_in_a_table_row_resolves_to_the_row(tmp_path):
    root = _prd(tmp_path, {
        "requirements/a.md": "# T\n\n| ID | Prio | Note |\n|---|---|---|\n"
                             "| M07-FR-01 | P0 | first |\n| M07-FR-03 | P0 | third |\n",
    })
    assert refs.get_index(root).ids["M07-FR-03"][0].line == 6


def test_id_defined_in_a_bold_lead_bullet_resolves_to_that_bullet(tmp_path):
    root = _prd(tmp_path, {
        "requirements/a.md": "# T\n\n- **BR-07.1** — must balance\n- **BR-07.2** — no deletes\n",
    })
    assert refs.get_index(root).ids["BR-07.2"][0].line == 4


def test_only_the_first_table_cell_defines_ids(tmp_path):
    """A requirement table's prose cells cite other requirements constantly. Counting those
    as definitions made one cross-referenced row claim ids from all over the spec, which then
    looked like "defined in three files" and got suppressed as ambiguous."""
    root = _prd(tmp_path, {
        "requirements/a.md": "| ID | Note |\n|---|---|\n| M04-FR-09 | see M07-FR-03 and BR-07.2 |\n",
    })
    index = refs.get_index(root)
    assert "M04-FR-09" in index.ids
    assert "M07-FR-03" not in index.ids
    assert "BR-07.2" not in index.ids


def test_a_code_quoted_first_cell_defines_nothing(tmp_path):
    """`| `m07.coa.chart` | ... M07-FR-03 ... |` is a config-key row, not a requirement row.
    Blanking the code span must not let the scanner fall through to the prose beside it."""
    root = _prd(tmp_path, {
        "requirements/a.md": "| Key | Note |\n|---|---|\n| `m07.coa.chart` | read by M07-FR-03 |\n",
    })
    assert "M07-FR-03" not in refs.get_index(root).ids


def test_a_mention_in_prose_is_never_a_definition(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "# T\n\nThis paragraph mentions M07-FR-03.\n"})
    assert refs.get_index(root).ids == {}


def test_ids_inside_fenced_and_inline_code_are_not_definitions(tmp_path):
    root = _prd(tmp_path, {
        "requirements/a.md": "# T\n\n```\n## M07-FR-03 fake heading\n```\n\n## `BR-09.9` quoted\n",
    })
    index = refs.get_index(root)
    assert "M07-FR-03" not in index.ids
    assert "BR-09.9" not in index.ids


def test_a_heading_definition_beats_a_lower_scored_one_in_another_file(tmp_path):
    root = _prd(tmp_path, {
        "requirements/a-first.md": "| ID | N |\n|---|---|\n| INV-04 | table row |\n",
        "requirements/b-second.md": "## INV-04 The real definition\n",
    })
    ref = refs._lookup_id(refs.get_index(root), "INV-04")
    assert (ref.path, ref.kind) == ("requirements/b-second.md", "heading")


# ---------------------------------------------------------------------------
# When a reference must NOT become a link
# ---------------------------------------------------------------------------

def test_the_same_id_defined_in_two_files_is_ambiguous_and_unlinked(tmp_path):
    root = _prd(tmp_path, {
        "requirements/a.md": "## NFR-12 one\n",
        "requirements/b.md": "## NFR-12 two\n",
    })
    assert refs._lookup_id(refs.get_index(root), "NFR-12") is None


def test_the_same_id_defined_twice_in_one_file_links_the_first(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "## NFR-12 one\n\ntext\n\n## NFR-12 again\n"})
    assert refs._lookup_id(refs.get_index(root), "NFR-12").line == 1


def test_a_token_with_no_digit_is_never_a_candidate(tmp_path):
    """Findings quote SCREAMING_CASE event names constantly; none of them is an id."""
    root = _prd(tmp_path, {"requirements/a.md": "## CONTRIBUTIONS_RECEIVABLE_RELEASE\n"})
    assert refs.get_index(root).ids == {}


def test_a_finding_shaped_id_needs_a_heading_to_be_linkable(tmp_path):
    """`R8 M2` in a Where line cites an earlier round's finding, not the spec. Such a token
    is only linkable if the PRD gives it a heading of its own."""
    table_only = _prd(tmp_path / "t", {"requirements/a.md": "| ID | N |\n|---|---|\n| M2 | row |\n"})
    assert refs._lookup_id(refs.get_index(table_only), "M2") is None

    heading = _prd(tmp_path / "h", {"requirements/a.md": "## M2 — a real section\n"})
    assert refs._lookup_id(refs.get_index(heading), "M2").kind == "heading"


def test_a_realistic_where_line_links_the_spec_ids_and_leaves_finding_refs_alone(tmp_path):
    root = _prd(tmp_path, {
        "requirements/m07.md": "| ID | N |\n|---|---|\n| M07-FR-03 | journals |\n\n- **BR-07.2** — no deletes\n",
    })
    where = ("R8 M2 resolution (`CONTRIBUTIONS_RECEIVABLE_CATCHUP_RELEASE`); R7 M2 catch-up "
             "pair; M07-FR-03 Group A aggregation rule; BR-07.2")
    html = refs.make_linkifier(root)(render_markdown(where))
    assert [(lab, path) for lab, path, _ in _links(html)] == [
        ("M07-FR-03", "requirements/m07.md"),
        ("BR-07.2", "requirements/m07.md"),
    ]
    for token in ("R8", "M2", "R7"):
        assert f">{token}<" not in html


# ---------------------------------------------------------------------------
# Section and file references
# ---------------------------------------------------------------------------

def test_a_section_reference_resolves_to_the_numbered_heading(tmp_path):
    root = _prd(tmp_path, {"design/12-dict.md": "# 12 Dict\n\n## 13.7 journal\n"})
    html = refs.make_linkifier(root)(render_markdown("Design §13.7 journal.source"))
    assert _links(html) == [("§13.7", "design/12-dict.md", "3")]


def test_an_ambiguous_section_needs_the_leading_word_to_scope_it(tmp_path):
    root = _prd(tmp_path, {
        "design/a.md": "## 13.7 here\n",
        "requirements/b.md": "## 13.7 there\n",
    })
    linkify = refs.make_linkifier(root)
    assert _links(linkify(render_markdown("see §13.7"))) == []
    scoped = _links(linkify(render_markdown("design §13.7")))
    assert scoped and scoped[0][1] == "design/a.md"


def test_a_file_path_links_to_line_one(tmp_path):
    root = _prd(tmp_path, {"requirements/m07-accounting.md": "# M07\n"})
    html = refs.make_linkifier(root)(render_markdown("See requirements/m07-accounting.md."))
    assert _links(html) == [("requirements/m07-accounting.md", "requirements/m07-accounting.md", "1")]


def test_a_backticked_file_path_wraps_the_whole_code_span(tmp_path):
    """The clarification prompt now asks findings to open the Where line with the file path
    in backticks, so that shape has to be clickable even though code spans are otherwise
    left strictly alone."""
    root = _prd(tmp_path, {"requirements/m07.md": "# M07\n"})
    html = refs.make_linkifier(root)(render_markdown("See `requirements/m07.md` for detail."))
    assert '<a class="spec-ref" href="#" data-spec-path="requirements/m07.md"' in html
    assert "<code>requirements/m07.md</code></a>" in html


def test_a_duplicated_basename_is_ambiguous_but_the_full_path_still_works(tmp_path):
    root = _prd(tmp_path, {"design/README.md": "# d\n", "requirements/README.md": "# r\n"})
    linkify = refs.make_linkifier(root)
    assert _links(linkify(render_markdown("open README.md"))) == []
    assert _links(linkify(render_markdown("open design/README.md")))[0][1] == "design/README.md"


def test_a_traversal_path_is_never_linked(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "# A\n"})
    html = refs.make_linkifier(root)(render_markdown("try ../../secret.md please"))
    assert _links(html) == []


# ---------------------------------------------------------------------------
# What the linkifier must not corrupt
# ---------------------------------------------------------------------------

def test_an_id_inside_a_code_span_stays_plain(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "- **BR-07.2** — rule\n"})
    html = refs.make_linkifier(root)(render_markdown("Quoted `BR-07.2` stays plain."))
    assert html == "<p>Quoted <code>BR-07.2</code> stays plain.</p>"


def test_entities_and_escaped_markup_survive_untouched(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "- **BR-07.2** — rule\n"})
    html = refs.make_linkifier(root)(render_markdown('AT&T & <tags> and "quotes" plus BR-07.2.'))
    assert "AT&amp;T &amp; &lt;tags&gt; and &quot;quotes&quot;" in html
    assert len(_links(html)) == 1


def test_an_id_appearing_in_an_attribute_value_is_not_rewritten(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "- **BR-07.2** — rule\n"})
    html = refs.make_linkifier(root)('<section data-key="BR-07.2"><p>BR-07.2</p></section>')
    assert 'data-key="BR-07.2"' in html
    assert len(_links(html)) == 1


def test_an_id_inside_strong_or_em_still_links(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "- **BR-07.2** — rule\n"})
    html = refs.make_linkifier(root)(render_markdown("**BR-07.2** matters."))
    assert "<strong>" in html and len(_links(html)) == 1


def test_the_emitted_anchor_has_the_shape_the_front_end_reads(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "- **BR-07.2** — rule\n"})
    html = refs.make_linkifier(root)(render_markdown("BR-07.2"))
    assert ('<a class="spec-ref" href="#" data-spec-path="requirements/a.md" '
            'data-spec-line="1" data-spec-kind="id" '
            'title="requirements/a.md · line 1">BR-07.2</a>') in html


# ---------------------------------------------------------------------------
# Caching and failure
# ---------------------------------------------------------------------------

def test_the_index_is_cached_and_rebuilt_only_when_a_file_changes(tmp_path):
    root = _prd(tmp_path, {"requirements/a.md": "## NFR-01 one\n"})
    refs.get_index(root)
    refs.get_index(root)
    assert refs._build_index.cache_info().misses == 1

    target = root / "requirements/a.md"
    target.write_text("## NFR-01 one\n## NFR-02 two\n", encoding="utf-8")
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert "NFR-02" in refs.get_index(root).ids
    assert refs._build_index.cache_info().misses == 2


def test_a_missing_prd_folder_yields_an_empty_index_and_a_no_op_linkifier(tmp_path):
    refs._build_index.cache_clear()
    missing = tmp_path / "nope"
    assert refs.get_index(missing).empty
    html = render_markdown("BR-07.2 and requirements/a.md")
    assert refs.make_linkifier(missing)(html) == html


def test_no_prd_dir_at_all_is_a_no_op_linkifier():
    html = "<p>BR-07.2</p>"
    assert refs.make_linkifier(None)(html) == html
