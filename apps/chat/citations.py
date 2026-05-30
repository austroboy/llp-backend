"""Citation extraction and Zone 2 (legal-basis table) assembly.

This is the deterministic side of the answer pipeline. The model produces
prose with inline citations like "Section 264(10), Labour Act 2006". We:

1. Extract every section/rule citation from the model's text.
2. Match each citation back to a `node_id` from the retrieval set.
3. Build the legal_basis_rows[] table the system renders below the answer.
4. Flag unmatched citations so the verifier loop or admin audit can catch them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Sequence

from apps.documents.models import Node
from apps.documents.retrieval import RetrievalHit, find_node_by_section

logger = logging.getLogger(__name__)


# Citation patterns — covers English and Bangla forms
_PATTERNS = [
    re.compile(
        r"Section\s+(\d+[A-Za-z]?)(?:\(([\d\w]+)\))?"
        r"(?:[,\s]+(?:Bangladesh\s+)?(?:Labour\s+)?(Act|Rules|Amendment|Ordinance)[^,\n)]*"
        r"(?:[,\s]+(\d{4}))?)?",
        re.IGNORECASE,
    ),
    re.compile(r"ধারা\s+([০-৯\d]+[ক-হA-Za-z]?)(?:\s*\(([০-৯\d\w]+)\))?"),
    re.compile(r"Rule\s+(\d+[A-Za-z]?)(?:\(([\d\w]+)\))?", re.IGNORECASE),
]


@dataclass
class ExtractedCitation:
    raw_text: str
    section: str
    sub: str = ""
    rule: str = ""
    instrument: str = ""
    year: str = ""

    def normalized(self) -> str:
        if self.rule:
            base = f"Rule {self.rule}"
        else:
            base = f"Section {self.section}"
            if self.sub:
                base += f"({self.sub})"
        suffix = []
        if self.instrument:
            suffix.append(self.instrument)
        if self.year:
            suffix.append(self.year)
        if suffix:
            return f"{base}, {' '.join(suffix)}"
        return base


@dataclass
class LegalBasisRow:
    issue: str
    reference_label: str
    node_id: str
    verdict: str = "verified"  # 'verified' | 'unverified' | 'partial'

    def to_dict(self) -> dict:
        return {
            "issue": self.issue,
            "reference_label": self.reference_label,
            "node_id": self.node_id,
            "verdict": self.verdict,
        }


def extract_citations(text: str) -> list[ExtractedCitation]:
    """Pull every legal citation out of the model's prose."""
    found: list[ExtractedCitation] = []
    seen: set[str] = set()

    for pat in _PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(0).strip()
            key = raw.lower().replace(" ", "")
            if key in seen:
                continue
            seen.add(key)
            groups = m.groups()
            section = groups[0] if groups else ""
            sub = groups[1] if len(groups) >= 2 and groups[1] else ""
            instrument = ""
            year = ""
            if len(groups) >= 3 and groups[2]:
                instrument = f"{'Labour ' if 'labour' not in raw.lower() else ''}{groups[2]}".strip()
                if "labour" not in instrument.lower() and "rules" not in instrument.lower() \
                        and "act" in instrument.lower():
                    instrument = "Labour " + instrument
            if len(groups) >= 4 and groups[3]:
                year = groups[3]
            # Distinguish Rule from Section by the matched pattern
            is_rule = pat.pattern.startswith("Rule")
            found.append(ExtractedCitation(
                raw_text=raw,
                section="" if is_rule else section,
                sub=sub,
                rule=section if is_rule else "",
                instrument=instrument,
                year=year,
            ))
    return found


def _format_reference_label(c: ExtractedCitation) -> str:
    """Produce a clean 'Section X(Y), Labour Act 2006' label."""
    base = c.normalized()
    # Default instrument/year for plain "Section X"
    if not c.instrument and not c.year and not c.rule:
        base += ", Labour Act 2006"
    elif not c.instrument and c.rule and not c.year:
        base += ", Labour Rules 2015"
    return base


def _match_to_node(c: ExtractedCitation, retrieved: Sequence[RetrievalHit]) -> str:
    """Resolve a citation to one of the retrieved node_ids if possible.

    If the citation specifies an Amendment Act year (e.g. "Section 12 of
    the Bangladesh Labour (Amendment) Act 2026"), prefer the node whose
    doc_code corresponds to that amendment year — avoids the failure
    pattern where a citation chip surfaces DOC-004 (2013) while the
    answer text refers to the 2026 Act.
    """
    target_section = c.section or c.rule
    if not target_section:
        return ""

    # Map amendment-year mentions to doc_codes. The parent Act stays
    # DOC-010, parent Rules DOC-007.
    AMENDMENT_YEAR_TO_DOC = {
        "2009": "DOC-002",
        "2010": "DOC-003",
        "2013": "DOC-004",
        "2018": "DOC-005",
        "2025": "DOC-006",
        "2026": "DOC-011",
        "2022": "DOC-008",  # Rules Amendment
    }
    preferred_doc = ""
    if c.year and "amend" in (c.instrument or "").lower():
        preferred_doc = AMENDMENT_YEAR_TO_DOC.get(c.year, "")
    elif c.year == "2006" or (not c.year and "amend" not in (c.instrument or "").lower()):
        preferred_doc = "DOC-010"  # parent Act

    candidates = [h for h in retrieved if h.section_number == target_section
                  or h.rule_number == target_section]
    if not candidates:
        candidates = [h for h in retrieved if target_section in h.node_id]
    if not candidates:
        return ""

    # First filter: prefer the doc_code that matches the citation's
    # explicit Amendment Act year.
    if preferred_doc:
        preferred = [h for h in candidates if h.doc_code == preferred_doc]
        if preferred:
            candidates = preferred

    # Second filter: prefer sub-section node when sub is named.
    if c.sub:
        with_sub = [h for h in candidates if c.sub in h.node_id]
        if with_sub:
            return with_sub[0].node_id
    return candidates[0].node_id


def _verify_unmatched(citation: ExtractedCitation) -> str:
    """Look up the corpus directly when retrieval missed a cited section."""
    target = citation.section or citation.rule
    if not target:
        return ""
    node = find_node_by_section(target)
    return node.node_id if node else ""


def _summarize_issue(c: ExtractedCitation, node_id: str) -> str:
    """Derive a short issue label for the legal-basis table from the cited node."""
    if not node_id:
        return c.raw_text
    try:
        node = Node.objects.filter(node_id=node_id, version__is_current=True).first()
    except Exception:  # noqa: BLE001
        return c.raw_text
    if not node:
        return c.raw_text
    if node.summary:
        # Take the first sentence, cap at ~80 chars
        first = node.summary.split(". ")[0].split("।")[0]
        return first[:120].strip()
    if node.title:
        return node.title[:120]
    return c.raw_text


def build_legal_basis(
    answer_text: str,
    retrieved: Sequence[RetrievalHit],
    *,
    max_rows: int = 6,
    enable_verifier: bool = True,
) -> tuple[list[LegalBasisRow], list[ExtractedCitation]]:
    """Build the Zone 2 legal-basis rows from the model answer + retrieval set.

    Returns (rows, unverified_citations).
    """
    citations = extract_citations(answer_text)
    rows: list[LegalBasisRow] = []
    unverified: list[ExtractedCitation] = []
    seen_keys: set[str] = set()

    for c in citations:
        node_id = _match_to_node(c, retrieved)
        verdict = "verified"
        if not node_id and enable_verifier:
            node_id = _verify_unmatched(c)
            if node_id:
                verdict = "partial"
        if not node_id:
            unverified.append(c)
            continue

        key = node_id
        if key in seen_keys:
            continue
        seen_keys.add(key)

        rows.append(LegalBasisRow(
            issue=_summarize_issue(c, node_id),
            reference_label=_format_reference_label(c),
            node_id=node_id,
            verdict=verdict,
        ))
        if len(rows) >= max_rows:
            break
    return rows, unverified


def confidence_band(rows: Sequence[LegalBasisRow], total_citations: int) -> dict:
    """Produce a confidence summary for observability."""
    if total_citations == 0:
        return {"band": "no_citations", "ratio": 0.0, "rows": len(rows)}
    matched = sum(1 for r in rows if r.verdict == "verified")
    ratio = matched / max(total_citations, 1)
    if ratio >= 0.9:
        band = "high"
    elif ratio >= 0.7:
        band = "medium"
    else:
        band = "low"
    return {"band": band, "ratio": ratio, "rows": len(rows), "total": total_citations}