"""Citation extraction + Zone 2 assembly tests.

These tests guard the anti-hallucination contract: every citation in the
model's prose must be extractable, and the legal-basis table only contains
citations that match a retrieved node.
"""
from __future__ import annotations

import pytest

from apps.chat.citations import (
    build_legal_basis,
    extract_citations,
    confidence_band,
)
from apps.documents.retrieval import RetrievalHit


def _hit(node_id: str, section: str, doc_code: str = "DOC-010", title: str = "",
         summary: str = "", content: str = "", language: str = "en"):
    return RetrievalHit(
        node_id=node_id, doc_code=doc_code, title=title or f"Section {section}",
        summary=summary, content=content,
        section_number=section, rule_number="",
        language=language, score=0.9, is_superseded=False,
    )


def test_extract_simple_section_citation():
    text = "Per Section 26, Labour Act 2006, the employer must…"
    cites = extract_citations(text)
    assert len(cites) >= 1
    assert any(c.section == "26" for c in cites)


def test_extract_section_with_subsection():
    text = "See Section 264(10), Labour Act 2006."
    cites = extract_citations(text)
    found = next(c for c in cites if c.section == "264")
    assert found.sub == "10"


def test_extract_rule_citation():
    text = "Under Rule 79, Labour Rules 2015, the form must be filed."
    cites = extract_citations(text)
    found = [c for c in cites if c.rule == "79"]
    assert len(found) >= 1


def test_extract_bangla_section():
    text = "ধারা ২৬ অনুযায়ী, নিয়োগকর্তাকে…"
    cites = extract_citations(text)
    # The pattern matches Bangla numerals if present
    assert any(c.section for c in cites)


def test_extract_dedupes_repeated_citations():
    text = "Section 26 says X. Later, Section 26 also says Y."
    cites = extract_citations(text)
    # Same citation shouldn't appear twice
    sec_26_count = sum(1 for c in cites if c.section == "26")
    assert sec_26_count == 1


def test_legal_basis_matches_retrieved_node():
    text = "Per Section 26, Labour Act 2006, the employer must give notice."
    hits = [_hit("DOC-010-0026", "26")]
    rows, unverified = build_legal_basis(text, hits, max_rows=6, enable_verifier=False)
    assert len(rows) == 1
    assert rows[0].node_id == "DOC-010-0026"
    assert rows[0].verdict == "verified"
    assert "Section 26" in rows[0].reference_label
    assert len(unverified) == 0


def test_legal_basis_drops_unmatched_citation_when_verifier_disabled():
    """When the model cites a section we didn't retrieve and verifier is off,
    the citation is flagged unverified (not silently included)."""
    text = "Per Section 999, Labour Act 2006, something is true."
    hits = [_hit("DOC-010-0026", "26")]  # unrelated
    rows, unverified = build_legal_basis(text, hits, max_rows=6, enable_verifier=False)
    assert len(rows) == 0
    assert len(unverified) == 1
    assert unverified[0].section == "999"


def test_legal_basis_respects_max_rows():
    """Tier limit on Zone 2 row count is enforced."""
    text = (
        "Section 1, Labour Act 2006. Section 2, Labour Act 2006. "
        "Section 3, Labour Act 2006. Section 4, Labour Act 2006. "
        "Section 5, Labour Act 2006."
    )
    hits = [_hit(f"DOC-010-000{i}", str(i)) for i in range(1, 6)]
    rows, _ = build_legal_basis(text, hits, max_rows=3, enable_verifier=False)
    assert len(rows) == 3


def test_confidence_band_high_when_all_verified():
    text = "Section 26, Labour Act 2006."
    hits = [_hit("DOC-010-0026", "26")]
    rows, _ = build_legal_basis(text, hits, enable_verifier=False)
    band = confidence_band(rows, total_citations=1)
    assert band["band"] == "high"


def test_confidence_band_no_citations():
    band = confidence_band([], total_citations=0)
    assert band["band"] == "no_citations"


def test_extract_handles_empty_text():
    assert extract_citations("") == []
    assert extract_citations("Just some prose with no citations.") == []
