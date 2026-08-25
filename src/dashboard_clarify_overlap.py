"""Which earlier findings already decided the surface a finding is about to re-decide.

A recommendation is normally accepted with one click ("Follow the recommendation"), and its
text then becomes the PRD's text. That is where cross-round contradictions come from: a later
round rewords a message, a rule or a field that an earlier round already settled, nobody sees
the two side by side, and the collision only surfaces a round later as a fresh critical
finding. This module computes, for every finding in one clarification file, which EARLIER
findings name at least one of the same surfaces — so the Clarification pane can say so above
the answer controls, at the one moment it is cheap to act on.

Deliberately deterministic and text-only: no model, no PRD read, no network. It is advisory
and gates nothing — a false positive costs the reader one glance, and a false negative leaves
things exactly as they were before this existed.

A "surface" is one of three things a finding NAMES, extracted from its title, Where, Question,
Recommendation and typed answer:

- a field or identifier in `backticks` (`Product.is_active` and `is_active` are the same
  surface — the last dotted segment is the key);
- an entity in **bold** (**SaleItem**), which is how the prompt tells findings to write them;
- a UI string in "double quotes", keyed by its first few normalised words, so
  "In use by N product(s) and M stock movement(s)" and "In use by N product(s), including M
  archived" are recognised as two wordings of one message.

Keys that show up across a large fraction of the whole corpus (see _too_generic) are dropped:
every finding in a POS spec mentions **Product**, and a note that fires on all of them is
noise rather than signal.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

from dashboard_clarify_parse import ClarificationItem, _file_started_at, parse_file

_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_BOLD_SPAN = re.compile(r"\*\*([^*\n]+?)\*\*")
_QUOTED = re.compile(r"\"([^\"\n]{8,240})\"")

# A backticked span is a field/identifier surface only if it looks like code and nothing else:
# `is_active`, `Product.is_active`, `stock_qty`. Prose, values (`"percent"`) and expressions
# (`stock_qty = 0`) fail this and are skipped rather than guessed at.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")

# A bolded span is an entity only when it reads like a type name — capitalised, letters only,
# long enough. That is exactly what excludes the round/finding ids findings cite constantly
# ("round 3 **C3**"): they carry a digit.
_ENTITY = re.compile(r"^[A-Z][A-Za-z]{3,}$")

_LITERALS = {"true", "false", "null", "nil", "none"}
_DOC_SUFFIXES = (".md", ".markdown", ".txt", ".json")

# How many of the corpus's findings a surface may appear in before it is treated as a word
# everything uses rather than a surface somebody decided. The floor keeps a handful of files
# from dropping every key they share; the ratio is what scales to a long-running workspace.
_GENERIC_RATIO = 0.3
_GENERIC_FLOOR = 3

# Caps on what one note may say. Past these it stops being a prompt to check something and
# becomes a wall of text over the answer controls.
_MAX_SOURCES = 5
_MAX_SURFACES_PER_SOURCE = 4


@dataclass(frozen=True)
class DecidedElsewhere:
    """One earlier finding that names a surface this finding also names.

    `decided` is whether that earlier finding has an answer recorded (typed, or "follow the
    recommendation") — an undecided one is a finding to reconcile with rather than a decision
    to preserve.

    `applied` is whether its round has since been written into the PRD, and it is what tells
    the two risks apart. An UNAPPLIED decision exists only as text in a clarification file:
    the PRD still reads the old way, and a recommendation that rewords it collides with
    something no document shows — the round-3-versus-round-4 shape that costs a whole extra
    round to find. An APPLIED one is already in the PRD the current round was evaluated
    against, so the note is provenance rather than a warning: it says which round decided the
    wording now in the spec, and where to check that this answer preserves it. The pane words
    all three states differently."""

    file_name: str
    raw_id: str
    title: str
    decided: bool
    applied: bool
    surfaces: tuple[str, ...]


def _normalise_message(raw: str) -> str:
    """A quoted UI string reduced to its first few words, lowercase and stripped of
    punctuation and placeholders, so two roundings of the same message collide.

    Returns "" for anything too short to be a message worth keying on — a quoted word or two
    is usually an emphasis, not a string the spec promises to render."""
    words = [w for w in re.sub(r"[^0-9a-z]+", " ", raw.lower()).split() if w]
    if len(words) < 3:
        return ""
    return " ".join(words[:5])


def _surface_map(text: str) -> dict[tuple[str, str], str]:
    """Every surface `text` names, as {(kind, key): display label}.

    The display label is the form the finding itself used, so the note reads back in the
    spec's own words rather than in this module's normalised ones."""
    found: dict[tuple[str, str], str] = {}

    for raw in _CODE_SPAN.findall(text):
        span = raw.strip()
        if span.lower().endswith(_DOC_SUFFIXES) or not _IDENTIFIER.match(span):
            continue
        key = span.rsplit(".", 1)[-1].lower()
        if len(key) < 3 or key in _LITERALS:
            continue
        found.setdefault(("field", key), span.rsplit(".", 1)[-1])

    for raw in _BOLD_SPAN.findall(text):
        span = raw.strip()
        if not _ENTITY.match(span):
            continue
        found.setdefault(("entity", span.lower()), span)

    for raw in _QUOTED.findall(text):
        key = _normalise_message(raw)
        if key:
            found.setdefault(("message", key), f'"{key}…"')

    return found


def _item_text(item: ClarificationItem) -> str:
    """What a finding NAMES — everything a reader would read, the recommendation included.

    The recommendation is the load-bearing part: it is what becomes specification when the
    answer is "follow the recommendation", so a collision it introduces has to be visible
    before that button is pressed, not after."""
    return "\n".join([item.title, item.where, item.question, item.recommendation,
                      item.existing_answer])


def _too_generic(keysets: list[dict[tuple[str, str], str]]) -> set[tuple[str, str]]:
    """Keys named by so much of the corpus that they identify a vocabulary, not a surface."""
    if not keysets:
        return set()
    limit = max(_GENERIC_FLOOR, math.ceil(_GENERIC_RATIO * len(keysets)))
    counts: dict[tuple[str, str], int] = {}
    for keys in keysets:
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > limit}


def _corpus(clar_dir: Path, applied_hashes: dict | None = None) -> list[tuple[Path, ClarificationItem, bool]]:
    """Every finding in the folder, oldest round first, then source order within a round.

    That ordering is the whole point: a finding is only warned about surfaces decided BEFORE
    it, so the note always points backwards at what it may be about to overwrite. Rounds are
    ordered by _file_started_at (the name's timestamp) rather than mtime, since answering a
    finding rewrites its file and would otherwise make an old round look like the newest.

    The third element of each entry is whether that file has been applied to the PRD —
    config.json's "clarify_applied_hashes" matching the file's current content, the same test
    dashboard_clarify_parse.pending_resolutions uses to decide what is still pending. A file
    edited since its apply counts as unapplied again, which is correct: the PRD can no longer
    be trusted to reflect what it now says.

    Every file is re-read on each call. Clarification folders hold tens of small markdown
    files, and the pane only calls this when a file is opened, so a cache would buy less than
    the staleness it risks after a save."""
    files: list[tuple[float, str, Path]] = []
    for path in sorted(clar_dir.glob("*.md")) if clar_dir.exists() else []:
        if path.name.lower() == "claude.md":
            continue
        files.append((_file_started_at(path), path.name, path))

    hashes = applied_hashes or {}
    ordered: list[tuple[Path, ClarificationItem, bool]] = []
    for _, _, path in sorted(files):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        applied = hashes.get(path.name) == hashlib.sha256(text.encode("utf-8")).hexdigest()
        items, _ = parse_file(path, text, 0)
        ordered.extend((path, item, applied) for item in items)
    return ordered


def overlaps_for_file(clar_dir: Path, target: Path,
                      applied_hashes: dict | None = None) -> dict[str, list[DecidedElsewhere]]:
    """{raw_id: earlier findings sharing a surface} for the findings in `target`.

    EVERY earlier round is checked, applied or not — an applied decision is still somebody's
    decision, and a recommendation that rewords it is still worth reading side by side. What
    changes with `applied_hashes` (config.json's "clarify_applied_hashes") is how each source
    is labelled, not whether it is reported: see DecidedElsewhere.applied. Passing nothing
    reports every source as unapplied, which is the safe direction — it over-warns rather than
    silently downgrading a collision the PRD does not show.

    Only ids with at least one overlap are present, so an empty dict — the common case in a
    first round — means the pane renders exactly what it rendered before this existed."""
    corpus = _corpus(clar_dir, applied_hashes)
    if not corpus:
        return {}

    keysets = [_surface_map(_item_text(item)) for _, item, _ in corpus]
    generic = _too_generic(keysets)
    keysets = [{k: v for k, v in keys.items() if k not in generic} for keys in keysets]

    overlaps: dict[str, list[DecidedElsewhere]] = {}
    for index, (path, item, _) in enumerate(corpus):
        if path.name != target.name or not keysets[index]:
            continue
        sources: list[tuple[int, int, DecidedElsewhere]] = []
        for earlier in range(index):
            shared = [keysets[index][key] for key in keysets[index] if key in keysets[earlier]]
            if not shared:
                continue
            other_path, other, other_applied = corpus[earlier]
            sources.append((len(shared), earlier, DecidedElsewhere(
                file_name=other_path.name,
                raw_id=other.raw_id,
                title=other.title,
                decided=bool(other.resolved_answer),
                applied=other_applied,
                surfaces=tuple(shared[:_MAX_SURFACES_PER_SOURCE]),
            )))
        if not sources:
            continue
        # Most surfaces in common first, and among equals the most recent — the decision most
        # likely to be the one this finding is actually rewording.
        sources.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        overlaps[item.raw_id] = [entry[2] for entry in sources[:_MAX_SOURCES]]
    return overlaps
