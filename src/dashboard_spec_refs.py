"""Resolving the spec references a clarification finding cites, so they can be linked.

A finding's `**Where:**` line is free text written by an agent — `M07-FR-03 Group A
aggregation rule; BR-07.2`. Those ids are real, but nothing records *where* they live, so
answering a finding meant hunting through the PRD by eye. This module builds an
id -> (file, line) index by scanning the PRD, and hands back a function that rewrites those
ids inside already-rendered finding HTML into links the Clarification pane can open.

Two properties are deliberate:

- **Resolution happens at render time, not when the finding was written.** A line number
  recorded a month ago would be stale; one looked up now cannot be. It also means every
  clarification round already on disk gets links without being re-run.
- **No workspace vocabulary is hard-coded.** `Mxx-FR-nn` / `BR-xx.n` / `INV-nn` are one
  workspace's convention, not Tempa's. The scanner learns which tokens exist by reading the
  PRD, and `ids` holds only tokens the PRD *defines* (in a heading, a table's first cell, or
  a bold lead-in) — never one that merely appears in prose. That single rule is what keeps a
  finding's references to *earlier findings* (`R8`, `M2`, `C1`) out of the index without
  naming them anywhere.

Everything fails open: an unreadable PRD yields an empty index and linkification becomes a
no-op, because a finding that renders without links is still a usable finding.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dashboard_spec import MARKDOWN_EXTENSIONS, TEXT_EXTENSIONS

# Walking a PRD is cheap, but it is a user-chosen folder and could be anything. These bound
# the damage from one far larger than a spec folder has any business being.
_MAX_FILES = 500
_MAX_FILE_BYTES = 2_000_000

# How strongly a line shape says "this is where the id is DEFINED" rather than "this line
# happens to mention it". Highest score wins; an unscored line never enters the index.
_SCORE_HEADING = 100
_SCORE_TABLE = 80
_SCORE_BULLET = 70
_SCORE_BOLD = 60

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
# A numbered heading ("## 13.7 journal", "# 16. Design Decisions") -> the "13.7" that a
# `Design §13.7` reference in a finding is pointing at.
_HEADING_NUMBER_RE = re.compile(r"^\s{0,3}#{1,6}\s+(\d+(?:\.\d+)*)\b")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_BULLET_BOLD_RE = re.compile(r"^\s*[-*+]\s+\*\*(.+?)\*\*")
_PARAGRAPH_BOLD_RE = re.compile(r"^\s*\*\*(.+?)\*\*")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")

# An id-shaped token: starts uppercase, then optional `-`/`.` separated uppercase-or-digit
# groups. The lookarounds stop it matching the tail of a longer word or a fragment of a
# path. Deliberately loose — _is_id_token below does the real filtering.
_ID_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9_./-])[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*(?![A-Za-z0-9_-])"
)

# A reference to an earlier FINDING, not to the spec: the clarification prompt tells the
# agent to id its findings `C1`/`M1`/`N1`, and a Where line cites them as "R8 M2". Matching
# the *shape* rather than a prefix list keeps this workspace-agnostic. Such a token is only
# linkable when the PRD defines it in a heading — see _lookup_id.
_FINDING_ID_RE = re.compile(r"^[A-Z]\d{1,2}$")

_TEXT_SUFFIXES = {s for s in TEXT_EXTENSIONS if s.startswith(".")}
_MARKDOWN_SUFFIXES = tuple(MARKDOWN_EXTENSIONS)


@dataclass(frozen=True)
class SpecRef:
    """One resolved location in the PRD."""

    path: str    # relative to the PRD folder, forward slashes
    line: int    # 1-based
    kind: str    # "heading" | "table" | "bullet" | "bold" | "file"
    score: int


@dataclass(frozen=True)
class SpecIndex:
    ids: dict[str, tuple[SpecRef, ...]]       # upper-cased id -> its definition sites
    sections: dict[str, tuple[SpecRef, ...]]  # "13.7" -> numbered headings with that number
    files: dict[str, str]                     # rel path AND bare name (lowered) -> rel path

    @property
    def empty(self) -> bool:
        return not (self.ids or self.sections or self.files)


_EMPTY_INDEX = SpecIndex(ids={}, sections={}, files={})


def _is_id_token(tok: str) -> bool:
    """Whether `tok` is worth looking up at all.

    The digit requirement is what separates an identifier from ordinary capitalised prose
    and from the SCREAMING_CASE field names findings quote constantly
    (`CONTRIBUTIONS_RECEIVABLE_CATCHUP_RELEASE` has no digit, so it never gets this far)."""
    return 2 <= len(tok) <= 24 and any(c.isdigit() for c in tok)


def _tokens_in(text: str) -> list[str]:
    return [m.group(0) for m in _ID_CANDIDATE.finditer(text) if _is_id_token(m.group(0))]


def _strip_code(text: str) -> str:
    """Blank out `inline code` — an id inside backticks is an example or a field name, never
    the place the id is introduced."""
    return _INLINE_CODE_RE.sub(" ", text)


def _definition_tokens(raw: str) -> tuple[list[str], str, int]:
    """The ids `raw` DEFINES, plus the kind and score of that definition.

    Returns `([], "", 0)` for a line that defines nothing — which is most lines, including
    every line that merely mentions an id in passing."""
    heading = _HEADING_RE.match(raw)
    if heading:
        return _tokens_in(_strip_code(heading.group(1))), "heading", _SCORE_HEADING
    if raw.lstrip().startswith("|") and not _TABLE_SEPARATOR_RE.match(raw):
        # ONLY the first cell — the id column (`| M07-FR-03 | P0 | Automatic journals... |`).
        # Every later cell is prose, and this spec's prose cites other requirements
        # constantly; counting those as definitions made one heavily cross-referenced row
        # claim half the ids in the spec, which then read as "defined in three files" and
        # got suppressed as ambiguous. Note the cell is taken from the RAW line and only
        # then stripped of code: a row whose id column is a config key in backticks
        # (`| `m07.coa.chart` | ... |`) must define nothing rather than fall through to the
        # prose beside it.
        cells = raw.strip().strip("|").split("|")
        first = _strip_code(cells[0]).strip() if cells else ""
        return _tokens_in(first), "table", _SCORE_TABLE
    bullet = _BULLET_BOLD_RE.match(raw)
    if bullet:
        return _tokens_in(_strip_code(bullet.group(1))), "bullet", _SCORE_BULLET
    bold = _PARAGRAPH_BOLD_RE.match(raw)
    if bold:
        return _tokens_in(_strip_code(bold.group(1))), "bold", _SCORE_BOLD
    return [], "", 0


def _scan_markdown(text: str, rel: str, ids: dict, sections: dict) -> None:
    """Record every id and numbered heading `text` defines into `ids` / `sections`."""
    fence: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        fence_match = _FENCE_RE.match(raw)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue

        number = _HEADING_NUMBER_RE.match(raw)
        if number:
            sections.setdefault(number.group(1), []).append(
                SpecRef(rel, lineno, "heading", _SCORE_HEADING))

        # _definition_tokens strips inline code itself, per line shape — it needs the raw
        # line to tell an id column apart from a code-quoted one.
        tokens, kind, score = _definition_tokens(raw)
        for tok in tokens:
            ids.setdefault(tok.upper(), []).append(SpecRef(rel, lineno, kind, score))


def _fingerprint(root: Path) -> tuple:
    """A cheap stat-only signature of the PRD folder, used as the index cache key.

    `st_mtime_ns` rather than `st_mtime`: two edits inside the same second are ordinary
    while typing, and a coarse mtime would serve a stale index for the rest of it."""
    out: list[tuple] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        st = path.stat()
        out.append((path.relative_to(root).as_posix(), st.st_mtime_ns, st.st_size))
        if len(out) >= _MAX_FILES:
            break
    return tuple(out)


@lru_cache(maxsize=4)
def _build_index(root_str: str, fingerprint: tuple) -> SpecIndex:
    """Read and parse every spec file. Runs only when the fingerprint changes; `maxsize=4`
    so moving between a couple of workspaces doesn't thrash the cache."""
    root = Path(root_str)
    ids: dict[str, list[SpecRef]] = {}
    sections: dict[str, list[SpecRef]] = {}
    files: dict[str, str] = {}
    ambiguous_names: set[str] = set()

    for rel, _mtime, size in fingerprint:
        files[rel.lower()] = rel
        name = rel.rsplit("/", 1)[-1].lower()
        if name in files and files[name] != rel:
            ambiguous_names.add(name)      # a basename claimed by two files links nowhere
        else:
            files.setdefault(name, rel)
        if size > _MAX_FILE_BYTES or not rel.lower().endswith(_MARKDOWN_SUFFIXES):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        _scan_markdown(text, rel, ids, sections)

    for name in ambiguous_names:
        files.pop(name, None)

    return SpecIndex(
        ids={k: tuple(v) for k, v in ids.items()},
        sections={k: tuple(v) for k, v in sections.items()},
        files=files,
    )


def get_index(root: Path) -> SpecIndex:
    """The PRD's reference index, rebuilt only when a file under `root` has changed."""
    try:
        return _build_index(str(root), _fingerprint(root))
    except OSError:
        return _EMPTY_INDEX


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def _best(refs: tuple[SpecRef, ...]) -> SpecRef | None:
    """The definition to link to, or None when the answer is genuinely ambiguous.

    Several definitions in ONE file means the id is restated (a table row and an acceptance
    criterion, say) — the first is the one to open. Definitions in TWO files means the index
    cannot tell which the finding meant, and sending someone confidently to the wrong file
    is worse than leaving the text unlinked."""
    if not refs:
        return None
    top = max(r.score for r in refs)
    best = sorted((r for r in refs if r.score == top), key=lambda r: (r.path, r.line))
    if len({r.path for r in best}) > 1:
        return None
    return best[0]


def _lookup_id(index: SpecIndex, token: str) -> SpecRef | None:
    ref = _best(index.ids.get(token.upper(), ()))
    if ref is None:
        return None
    # "R8 M2" cites an earlier finding. `M2` only survives if the PRD gives it a heading of
    # its own, which a requirement id shaped that way would have.
    if _FINDING_ID_RE.match(token) and ref.kind != "heading":
        return None
    return ref


def _lookup_section(index: SpecIndex, number: str, before: str) -> SpecRef | None:
    """`§13.7` is only linkable when it can be pinned to one file.

    Numbered headings repeat across a spec folder, so a bare `§13.7` is usually ambiguous.
    The word in front of it is what disambiguates — findings write `Design §13.7`, and
    `design/` is a real folder."""
    refs = index.sections.get(number, ())
    if not refs:
        return None
    if len({r.path for r in refs}) > 1:
        word_match = re.search(r"([A-Za-z][\w-]*)[\s(\[]*$", before[-40:])
        if not word_match:
            return None
        word = word_match.group(1).lower()
        scoped = [r for r in refs if word in r.path.lower()]
        if len({r.path for r in scoped}) != 1:
            return None
        refs = tuple(scoped)
    return sorted(refs, key=lambda r: (r.path, r.line))[0]


def _lookup_file(index: SpecIndex, raw: str) -> SpecRef | None:
    rel = index.files.get(raw.lower().lstrip("./"))
    if rel is None:
        return None
    return SpecRef(rel, 1, "file", 0)


# ---------------------------------------------------------------------------
# Linkifying rendered HTML
# ---------------------------------------------------------------------------

# Splits rendered HTML into alternating text / markup pieces. Substituting into the text
# pieces only is what makes this safe where a plain regex over the whole string would not
# be: an attribute value lives inside a markup piece and is never touched, and an entity
# (`&amp;`, `&#39;`) is its own piece, so a token match can never bite a chunk out of one.
# `<[^>]+>` cannot terminate early because the renderer escapes `>` inside attributes.
_SPLIT_RE = re.compile(r"(<[^>]+>|&[#A-Za-z0-9]+;)")

# One pass, three alternatives, so a single left-to-right scan can never produce overlapping
# matches: a file path, a `§`-style section reference, or a bare identifier.
_REF_RE = re.compile(
    r"(?P<file>(?:[\w.-]+/)+[\w.-]+\.(?:md|markdown|txt|json|ya?ml))"
    r"|(?:§|(?:Section|Sec\.)\s*)(?P<section>\d+(?:\.\d+)*)"
    r"|(?P<ident>(?<![A-Za-z0-9_./-])[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*(?![A-Za-z0-9_-]))"
)

_CODE_OPEN_RE = re.compile(r"^<code[\s>]", re.IGNORECASE)
_CODE_CLOSE_RE = re.compile(r"^</code\s*>$", re.IGNORECASE)


def _anchor(ref: SpecRef, kind: str, label_html: str) -> str:
    """`label_html` is already-escaped rendered HTML and is reinserted verbatim — escaping
    it again would turn `&amp;` into `&amp;amp;` in a finding's own text."""
    path = html_lib.escape(ref.path, quote=True)
    return (
        f'<a class="spec-ref" href="#" data-spec-path="{path}" '
        f'data-spec-line="{ref.line}" data-spec-kind="{kind}" '
        f'title="{path} · line {ref.line}">{label_html}</a>'
    )


def _identity(html: str) -> str:
    return html


def make_linkifier(prd_dir: Path | None) -> Callable[[str], str]:
    """A function that turns spec references in rendered finding HTML into `a.spec-ref` links.

    Applied as a post-pass over the finished HTML rather than as a step inside
    `dashboard_clarify_render._md_inline`, on purpose: that renderer applies bold before code
    spans, and reordering it to accommodate linkification would change the output of every
    clarification file already on disk. A post-pass leaves it byte-identical."""
    if prd_dir is None:
        return _identity
    index = get_index(Path(prd_dir))
    if index.empty:
        return _identity

    def linkify_text(piece: str) -> str:
        def replace(match: re.Match) -> str:
            token = match.group(0)
            if match.group("file"):
                ref, kind = _lookup_file(index, match.group("file")), "file"
            elif match.group("section"):
                ref = _lookup_section(index, match.group("section"), piece[:match.start()])
                kind = "section"
            else:
                ident = match.group("ident")
                if not _is_id_token(ident):
                    return token
                ref, kind = _lookup_id(index, ident), "id"
            return _anchor(ref, kind, token) if ref else token

        return _REF_RE.sub(replace, piece)

    def linkify(html: str) -> str:
        if not html:
            return html
        parts = _SPLIT_RE.split(html)
        out: list[str] = []
        code_depth = 0
        code_start = -1          # index in `out` of the <code> tag currently open at depth 1
        for i, piece in enumerate(parts):
            is_markup = i % 2 == 1
            if not is_markup:
                out.append(linkify_text(piece) if code_depth == 0 else piece)
                continue
            if _CODE_OPEN_RE.match(piece):
                if code_depth == 0:
                    code_start = len(out)
                code_depth += 1
                out.append(piece)
                continue
            if _CODE_CLOSE_RE.match(piece) and code_depth > 0:
                code_depth -= 1
                out.append(piece)
                if code_depth == 0 and code_start >= 0:
                    _wrap_code_span_if_a_file(out, code_start, index)
                    code_start = -1
                continue
            out.append(piece)
        return "".join(out)

    return linkify


def _wrap_code_span_if_a_file(out: list[str], start: int, index: SpecIndex) -> None:
    """Wrap a whole `<code>…</code>` in a link when its entire content is a spec file path.

    Nothing *inside* a code span is ever rewritten — an id in backticks is a quotation, and
    breaking it up would corrupt what the finding is quoting. But a code span that IS a path
    is a citation, and this is the shape the clarification prompt now asks findings to use
    (`` `requirements/m07-accounting.md` ``), so it has to be clickable."""
    body = "".join(out[start + 1:-1])
    if not body or "<" in body:
        return
    ref = _lookup_file(index, html_lib.unescape(body).strip())
    if ref is None:
        return
    span = "".join(out[start:])
    del out[start:]
    out.append(_anchor(ref, "file", span))
