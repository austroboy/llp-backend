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

# DOC-011 (2026 Amendment Act) ingestion gap: nodes are indexed by the
# AMENDING-section number (§35, §61, §44 etc.) NOT by the target parent-
# section they amend (§179, §286, §195 etc.). Until corpus ingestion is
# fixed to add target-section indexing, this mapping lets the retrieval
# force-include the right DOC-011 node when the user mentions the parent
# section. Verified by reading 2026 Amendment Act titles directly.
DOC011_PARENT_TO_AMENDING = {
    # parent_section_number -> amending DOC-011 section_number
    "1":   "3",     # §1 of parent (excluded establishments)
    "2":   "4",     # §2 definitions
    "3A":  "5",
    "4":   "6",
    "5":   "7",
    "14":  "8",
    "16":  "9",
    "17":  "10",
    "19":  "11",
    "23":  "12",
    "27":  "13",
    "32":  "14",
    "45":  "15",
    "46":  "16",
    "47":  "17",
    "48":  "18",
    "49":  "19",
    "50":  "20",
    "61A": "21",    # newly inserted
    "80":  "22",
    "81":  "23",
    "82":  "24",
    "85":  "25",
    "90A": "26",
    "117": "27",
    "118": "28",    # 11->13 festival holidays
    "132": "29",
    "139": "30",
    "151A":"31",    # newly inserted
    "175": "32",    # platform riders
    "178": "33",
    "179": "35",    # TU registration fixed slabs
    "180": "36",
    "182": "37",
    "183": "38",
    "185": "39",
    "185A":"40",
    "188": "41",
    "190": "42",
    "195": "44",    # unfair labour practice list (a)-(p)
    "196A":"45",
    "196B":"46",    # newly inserted
    "202": "47",
    "203": "48",
    "203A":"49",    # newly inserted
    "204": "50",
    "208": "51",
    "211": "52",
    "213": "53",
    "235": "54",
    "242": "55",
    "264": "56",    # PF cumulative conditions
    "266": "57",
    "283": "58",
    "284": "59",
    "285": "60",
    "286": "61",    # maternity fine 50k-1L
    "289": "62",
    "290": "63",
    "291": "64",
    "292": "65",
    "293": "66",
    "294": "67",
    "295": "68",
    "296": "69",
    "299": "70",
    "300": "71",
    "301": "72",
    "307": "73",    # residual penalty 25k-50k
    "309": "74",
    "317": "75",
    "318A":"76",    # newly inserted
    "319": "77",
    "319A":"78",    # newly inserted
    "323": "79",
    "326": "80",
    "332": "81",
    "332A":"82",    # newly inserted
    "338": "83",
    "345": "85",
    "345A":"86",    # newly inserted
    "345B":"87",    # newly inserted
    "345C":"88",    # newly inserted
    "348": "89",
    "348A":"90",
    "348B":"91",    # newly inserted
    "348C":"92",    # newly inserted
}

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
    node_ids of ALL layers of those provisions so the caller can boost
    them to the top of the result list.

    IMPORTANT: returns ALL nodes that match (parent Act DOC-010 + every
    amendment Act DOC-002/003/004/005/006/011 that touches the section)
    — NOT just the latest. The LLM needs to see every amendment layer
    concurrently to (a) synthesise the current consolidated text and
    (b) attribute each substitution to the correct amending year. If
    only the latest layer is surfaced, the bot tends to either miss
    earlier substitutions or attribute changes to the wrong amendment.
    """
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
        # Surface every layer (DOC-010 base + amendments). Cap at 6
        # nodes per cited section so a single section can't crowd
        # out other relevant context.
        per_section_count: dict[str, int] = {}
        for n in qs.order_by("section_number", "-doc_code"):
            count = per_section_count.get(n.section_number, 0)
            if count >= 6:
                continue
            forced.append(n.node_id)
            per_section_count[n.section_number] = count + 1

        # Workaround for DOC-011 (2026 Amendment) ingestion gap: nodes
        # are indexed by the amending-section number, not the target
        # parent section. So a query for parent §179 won't match the
        # DOC-011 §35 node that substitutes it. Use the mapping to also
        # pull the DOC-011 amending node when the user asked about a
        # parent section.
        amending_section_numbers = [
            DOC011_PARENT_TO_AMENDING[s]
            for s in sections
            if s in DOC011_PARENT_TO_AMENDING
        ]
        if amending_section_numbers:
            doc011_qs = Node.objects.filter(
                version__is_current=True,
                doc_code="DOC-011",
                section_number__in=amending_section_numbers,
            )
            if language:
                doc011_qs = doc011_qs.filter(language=language)
            for n in doc011_qs:
                if n.node_id not in forced:
                    forced.append(n.node_id)

    if rules:
        qs = Node.objects.filter(
            version__is_current=True,
            rule_number__in=list(rules),
        )
        if language:
            qs = qs.filter(language=language)
        per_rule_count: dict[str, int] = {}
        for n in qs.order_by("rule_number", "-doc_code"):
            count = per_rule_count.get(n.rule_number, 0)
            if count >= 4:
                continue
            forced.append(n.node_id)
            per_rule_count[n.rule_number] = count + 1

    if forced:
        logger.info(
            "retrieval.force_include_by_section",
            extra={
                "sections": list(sections),
                "rules": list(rules),
                "n_forced": len(forced),
            },
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