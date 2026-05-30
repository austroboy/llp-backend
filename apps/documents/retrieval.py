"""Hybrid retrieval: pgvector cosine similarity + Postgres full-text search.

Returns the top-K most relevant nodes for a query, with supersession filtering
applied. The `boost_via_crossrefs` step adds +0.1 to any retrieved node that
is referenced by another retrieved node — favors well-connected provisions.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F, FloatField, Q, Value
from django.db.models.functions import Greatest

from .embeddings import embed_query
from .models import Crossref, Node

logger = logging.getLogger(__name__)


@dataclass
class RetrievalHit:
    node_id: str
    doc_code: str
    title: str
    summary: str
    content: str
    section_number: str
    rule_number: str
    language: str
    score: float
    is_superseded: bool


def hybrid_search(
    query: str,
    *,
    top_k: int = 8,
    language: str | None = None,
    include_superseded: bool = False,
    min_score: float = 0.0,
) -> list[RetrievalHit]:
    """Hybrid lexical + vector search.

    `language` filters by the source node language ('en'|'bn'). If the query
    is bilingual or the user's language is unset, we don't filter.
    """
    # 1. Embed query (768-dim, matches stored embeddings)
    try:
        query_vec = embed_query(query)
    except Exception as e:  # noqa: BLE001
        logger.warning("query_embedding_failed", extra={"err": str(e)})
        query_vec = None

    qs = Node.objects.filter(version__is_current=True)
    if language:
        qs = qs.filter(language=language)
    if not include_superseded:
        # Demote (don't drop) superseded nodes — we still allow them in results
        # but boost active ones. Achieved later via score adjustment.
        pass

    # 2. Vector search candidates
    vector_hits: list[tuple[str, float]] = []
    if query_vec is not None:
        from pgvector.django import CosineDistance
        v_qs = (
            qs.exclude(embedding__isnull=True)
            .annotate(distance=CosineDistance("embedding", query_vec))
            .order_by("distance")[: top_k * 3]
        )
        for n in v_qs:
            sim = 1.0 - float(n.distance)  # cosine sim in [-1,1]; usually [0,1]
            vector_hits.append((n.node_id, max(sim, 0.0)))

    # 3. Lexical (FTS) candidates
    lex_qs = qs.annotate(
        rank=SearchRank(F("search_vector"), SearchQuery(query, search_type="websearch", config="simple")),
    ).filter(rank__gt=0).order_by("-rank")[: top_k * 2]
    lex_hits = [(n.node_id, float(n.rank)) for n in lex_qs]

    # 4. Combine — weighted sum
    combined: dict[str, float] = {}
    for node_id, score in vector_hits:
        combined[node_id] = combined.get(node_id, 0.0) + 0.7 * score
    # Normalize lexical (they're typically 0.05–1.0)
    if lex_hits:
        max_lex = max(s for _, s in lex_hits) or 1.0
        for node_id, score in lex_hits:
            combined[node_id] = combined.get(node_id, 0.0) + 0.3 * (score / max_lex)

    # 5. Crossref boost
    boosted = _apply_crossref_boost(list(combined.keys()), combined)

    # 6. Demote superseded
    if not include_superseded:
        boosted = _demote_superseded(boosted)

    # 6b. Section-number force-include
    # If the user's query explicitly names a section number (e.g. "Section
    # 2(49)", "§1(4)(d)", "Rule 111(5)", "ধারা ২৬"), pull the corresponding
    # node directly and force it to the top of the result set — regardless of
    # cosine ranking. This closes the "punts on text that's in the corpus"
    # failure pattern: retrieval rank can rank an exact-section node low for
    # short queries, leading the LLM to think the provision is absent. The
    # boost is a constant added on top of the highest current score so the
    # forced node always lands in the top-K window.
    forced_ids = _force_include_by_section(query, language=language)
    if forced_ids:
        top_score = max(boosted.values(), default=0.0)
        for nid in forced_ids:
            boosted[nid] = max(boosted.get(nid, 0.0), top_score + 0.5)

    # 7. Filter and sort
    if not boosted:
        return []
    sorted_ids = sorted(boosted.items(), key=lambda kv: kv[1], reverse=True)
    sorted_ids = [(nid, s) for nid, s in sorted_ids if s >= min_score]
    sorted_ids = sorted_ids[:top_k]

    # 8. Hydrate
    nodes_by_id = {
        n.node_id: n
        for n in Node.objects.filter(node_id__in=[nid for nid, _ in sorted_ids],
                                      version__is_current=True)
    }
    out: list[RetrievalHit] = []
    for nid, score in sorted_ids:
        n = nodes_by_id.get(nid)
        if not n:
            continue
        out.append(RetrievalHit(
            node_id=n.node_id, doc_code=n.doc_code, title=n.title,
            summary=n.summary, content=n.content,
            section_number=n.section_number, rule_number=n.rule_number,
            language=n.language, score=score,
            is_superseded=n.is_superseded,
        ))
    return out


def _apply_crossref_boost(node_ids: list[str], scores: dict[str, float]) -> dict[str, float]:
    """If node A is referenced by another retrieved node B, A gets +0.1."""
    if len(node_ids) < 2:
        return scores
    refs = Crossref.objects.filter(
        version__is_current=True, node_ids__overlap=node_ids,
    )
    referenced_set: set[str] = set()
    for r in refs:
        for ref_section in r.references:
            # find node_ids in our retrieved set whose section_number == ref_section
            for nid in node_ids:
                # inexpensive check: ref appears in any retrieved node id
                if ref_section in nid:
                    referenced_set.add(nid)
    boosted = dict(scores)
    for nid in referenced_set:
        boosted[nid] = boosted.get(nid, 0.0) + 0.1
    return boosted


def _demote_superseded(scores: dict[str, float]) -> dict[str, float]:
    """Multiply superseded nodes' scores by 0.5."""
    sup_ids = set(
        Node.objects.filter(
            node_id__in=list(scores.keys()),
            version__is_current=True,
        )
        .filter(supersession__contains={"status": "superseded"})
        .values_list("node_id", flat=True)
    )
    return {
        nid: (s * 0.5 if nid in sup_ids else s)
        for nid, s in scores.items()
    }


# ── Section-number force-include (Pattern 3 fix from Batch 03) ────────
# Patterns we want to catch in the user's query:
#   "Section 2(49)"  /  "section 2 (49)"
#   "§1(4)(d)"  /  "Sec. 1(4)"
#   "ধারা ২৩(৩)"  /  "ধারা ২৩"   (Bangla numerals normalised)
#   "Rule 111(5)"  /  "rule 111"  /  "BLR 29"  /  "বিধি ২৯"
_BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

_SECTION_PATTERNS = [
    re.compile(r"(?:section|sec\.?|§)\s*(\d+[A-Za-z]?)(?:\s*\([^)]+\))*", re.IGNORECASE),
    re.compile(r"(?:ধারা)\s*(\d+[A-Za-z]?)"),
]
_RULE_PATTERNS = [
    re.compile(r"(?:rule|blr)\s*(\d+[A-Za-z]?)(?:\s*\([^)]+\))*", re.IGNORECASE),
    re.compile(r"(?:বিধি)\s*(\d+[A-Za-z]?)"),
]


def _extract_cited_provisions(query: str) -> tuple[set[str], set[str]]:
    """Return (set of section numbers, set of rule numbers) the query
    explicitly cites. Bangla numerals are normalised to ASCII."""
    normalised = query.translate(_BANGLA_DIGITS)
    sections: set[str] = set()
    rules: set[str] = set()
    for pat in _SECTION_PATTERNS:
        for m in pat.finditer(normalised):
            sections.add(m.group(1).strip())
    for pat in _RULE_PATTERNS:
        for m in pat.finditer(normalised):
            rules.add(m.group(1).strip())
    return sections, rules


def _force_include_by_section(query: str, *, language: str | None) -> list[str]:
    """If the user's query explicitly cites a section/rule, return the
    node_ids of those provisions so the caller can boost them to the top
    of the result list. Returns at most one node per cited provision,
    preferring the latest version and (for sections) the latest amendment
    that touched it."""
    sections, rules = _extract_cited_provisions(query)
    if not sections and not rules:
        return []

    forced: list[str] = []
    if sections:
        qs = Node.objects.filter(
            version__is_current=True,
            section_number__in=list(sections),
        )
        if language:
            qs = qs.filter(language=language)
        # Group by section_number and pick the highest doc_code per group
        # (DOC-011 > DOC-006 > DOC-005 > ... > DOC-010). Lexical sort works
        # because the codes are zero-padded and Amendment Acts sort above
        # the parent Act in this corpus.
        seen: dict[str, str] = {}
        for n in qs.order_by("section_number", "-doc_code"):
            if n.section_number not in seen:
                seen[n.section_number] = n.node_id
        forced.extend(seen.values())

    if rules:
        qs = Node.objects.filter(
            version__is_current=True,
            rule_number__in=list(rules),
        )
        if language:
            qs = qs.filter(language=language)
        seen_r: dict[str, str] = {}
        for n in qs.order_by("rule_number", "-doc_code"):
            if n.rule_number not in seen_r:
                seen_r[n.rule_number] = n.node_id
        forced.extend(seen_r.values())

    if forced:
        logger.info(
            "retrieval.force_include_by_section",
            extra={"sections": list(sections), "rules": list(rules), "n_forced": len(forced)},
        )
    return forced


def find_node_by_section(section_number: str, doc_code: str | None = None) -> Optional[Node]:
    """Direct section-number lookup. Used by the verifier."""
    qs = Node.objects.filter(
        version__is_current=True,
        section_number=section_number,
    )
    if doc_code:
        qs = qs.filter(doc_code=doc_code)
    return qs.order_by("-version__version").first()