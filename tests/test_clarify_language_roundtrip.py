"""A findings file written in a non-English language still parses, links and answers.

The Evaluation card's Language picker translates the prose a person reads and nothing else
(see tempa_prompts._output_language_block). That split is only worth anything if the rest of
the pipeline is genuinely language-blind, so this walks one findings file per offered
language through the whole read path:

    parse -> per-severity counts -> spec-reference linkification -> render -> answer

A regression here is not cosmetic. A finding whose labels or markers stopped being matched
does not render wrong — it disappears from the answer UI and can never be answered.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import dashboard_spec_refs as refs
import tempa_config
from dashboard_api_clarify import apply_answers_to_file
from dashboard_clarify_parse import _file_severity_stats, file_answer_status, parse_file
from dashboard_clarify_render import _render_blocks_html

PRD = """\
# Mortgage Simulator PRD

## M01-FR-01 Amortization schedule

The schedule shows at most 360 rows.

## BR-07 Rounding

| id | rule |
|----|------|
| BR-07.2 | Round half up to 2 decimals |
"""

# One finding per language: the prose is that language, every structural part is English.
# Deliberately written the way the prompt tells the agent to write it — the ids quoted from
# the PRD (`M01-FR-01`, `BR-07.2`) sit inside translated sentences, which is exactly the case
# that has to keep resolving.
PROSE = {
    "en": ("Rounding rule is unreachable",
           "The section defines M01-FR-01 but no rule reaches BR-07.2.",
           "Which rounding rule applies to the schedule?",
           "Apply BR-07.2 to every row of M01-FR-01."),
    "id": ("Aturan pembulatan tidak terjangkau",
           "Bagian ini mendefinisikan M01-FR-01 tetapi tidak ada aturan yang mencapai BR-07.2.",
           "Aturan pembulatan mana yang berlaku untuk jadwal angsuran?",
           "Terapkan BR-07.2 pada setiap baris M01-FR-01."),
    "zh": ("舍入规则无法到达",
           "该章节定义了 M01-FR-01，但没有规则触及 BR-07.2。",
           "哪条舍入规则适用于还款计划？",
           "对 M01-FR-01 的每一行应用 BR-07.2。"),
    "hi": ("पूर्णांकन नियम अगम्य है",
           "यह अनुभाग M01-FR-01 को परिभाषित करता है पर कोई नियम BR-07.2 तक नहीं पहुँचता।",
           "अनुसूची पर कौन सा पूर्णांकन नियम लागू होता है?",
           "M01-FR-01 की हर पंक्ति पर BR-07.2 लागू करें।"),
    "es": ("La regla de redondeo es inalcanzable",
           "La sección define M01-FR-01 pero ninguna regla alcanza BR-07.2.",
           "¿Qué regla de redondeo se aplica al cuadro de amortización?",
           "Aplicar BR-07.2 a cada fila de M01-FR-01."),
    "ar": ("قاعدة التقريب غير قابلة للوصول",
           "يعرّف القسم M01-FR-01 لكن لا توجد قاعدة تصل إلى BR-07.2.",
           "ما قاعدة التقريب التي تنطبق على الجدول؟",
           "طبّق BR-07.2 على كل صف من M01-FR-01."),
    "pt": ("A regra de arredondamento é inalcançável",
           "A seção define M01-FR-01 mas nenhuma regra alcança BR-07.2.",
           "Qual regra de arredondamento se aplica ao cronograma?",
           "Aplicar BR-07.2 a cada linha de M01-FR-01."),
    "ru": ("Правило округления недостижимо",
           "Раздел определяет M01-FR-01, но ни одно правило не достигает BR-07.2.",
           "Какое правило округления применяется к графику?",
           "Применить BR-07.2 к каждой строке M01-FR-01."),
    "ja": ("丸めルールに到達できない",
           "この節は M01-FR-01 を定義しているが、BR-07.2 に到達する規則がない。",
           "返済予定表にはどの丸めルールが適用されるのか？",
           "M01-FR-01 の全行に BR-07.2 を適用する。"),
    "fr": ("La règle d'arrondi est inatteignable",
           "La section définit M01-FR-01 mais aucune règle n'atteint BR-07.2.",
           "Quelle règle d'arrondi s'applique à l'échéancier ?",
           "Appliquer BR-07.2 à chaque ligne de M01-FR-01."),
}


def _findings_file(title, body, question, recommendation) -> str:
    return f"""\
# Clarification round 1

<!-- clarify:item id="C1" severity="critical" -->
### {title}

**Where:** — `PRD.md` — M01-FR-01

{body}

**Question:** — {question}

**Recommendation:** — {recommendation}

**Your answer:**
<!-- clarify:answer-start -->

<!-- clarify:answer-end -->
<!-- clarify:enditem -->
"""


@pytest.fixture
def prd_dir(tmp_path) -> Path:
    root = tmp_path / "prd"
    root.mkdir()
    (root / "PRD.md").write_text(PRD, encoding="utf-8")
    refs._build_index.cache_clear()
    return root


@pytest.mark.parametrize("language", [code for code, _n, _l in tempa_config.CLARIFICATION_LANGUAGES])
def test_a_findings_file_in_any_offered_language_parses_links_and_answers(tmp_path, prd_dir,
                                                                         language):
    title, body, question, recommendation = PROSE[language]
    path = tmp_path / "clarification-20260101-000000.md"
    text = _findings_file(title, body, question, recommendation)
    path.write_text(text, encoding="utf-8")

    # 1. It parses as one critical finding, with the translated prose in the right slots.
    items, blocks = parse_file(path, text, 0)
    assert [i.raw_id for i in items] == ["C1"]
    item = items[0]
    assert (item.severity, item.title) == ("critical", title)
    assert item.question.endswith(question)
    assert item.recommendation.endswith(recommendation)
    assert item.existing_answer == ""

    # 2. The overview counts it — a finding that stopped parsing would silently count zero.
    stats = _file_severity_stats(path)
    assert (stats["total"], stats["answered"], stats["critical"]["total"]) == (1, 0, 1)

    # 3. The ids quoted from the PRD still resolve, even inside a translated sentence —
    # each pointing at the PRD line that DEFINES it (a heading for one, a table row for the
    # other), which is what the reference drawer opens.
    html = _render_blocks_html(blocks, refs.make_linkifier(prd_dir))
    links = {(m.group(3), m.group(1), m.group(2)) for m in re.finditer(
        r'class="spec-ref"[^>]*data-spec-path="([^"]+)" data-spec-line="(\d+)"[^>]*>(.*?)</a>',
        html)}
    assert ("M01-FR-01", "PRD.md", "3") in links
    assert ("BR-07.2", "PRD.md", "11") in links

    # 4. An answer written in that language round-trips into the file and back out.
    answer = f"{recommendation} (OK)"
    assert apply_answers_to_file(
        path, [{"id": item.key, "mode": "own", "answer": answer}]) == (1, 1)
    reread = path.read_text(encoding="utf-8")
    again = parse_file(path, reread, 0)[0][0]
    assert again.resolved_answer == answer
    assert file_answer_status(path) == (1, 1)
    # The structural parts survive the rewrite untouched — that rewrite is what would eat
    # them if anything in this path were language-aware.
    for marker in ('<!-- clarify:item id="C1" severity="critical" -->', "**Where:**",
                   "**Question:**", "**Recommendation:**", "**Your answer:**",
                   "<!-- clarify:answer-start -->", "<!-- clarify:answer-end -->",
                   "<!-- clarify:enditem -->"):
        assert marker in reread


def test_following_the_recommendation_works_in_any_language(tmp_path, prd_dir):
    """"Follow the recommendation" stores an empty body plus mode="recommendation", so the
    recommendation text itself is what the PRD later gets — in whatever language it was
    written in."""
    title, body, question, recommendation = PROSE["id"]
    path = tmp_path / "clarification-20260101-000000.md"
    text = _findings_file(title, body, question, recommendation)
    path.write_text(text, encoding="utf-8")
    item = parse_file(path, text, 0)[0][0]

    apply_answers_to_file(path, [{"id": item.key, "mode": "recommendation", "answer": ""}])
    again = parse_file(path, path.read_text(encoding="utf-8"), 0)[0][0]
    assert again.answer_mode == "recommendation"
    assert again.resolved_answer.endswith(recommendation)
    assert file_answer_status(path) == (1, 1)
