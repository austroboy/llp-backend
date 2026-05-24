"""Convert OCR'd Labour Act/Rules .docx files into the structured JSON
shape that `ingest_json_document` expects, then ingest + embed.

This is the "Path B" replacement for the stub `ingest_docx` in ingestion.py.
The stub built a single flat node containing the entire document, which made
vector retrieval useless. This module splits each docx into one node per
Section (matching the way the original corpus was chunked), and generates
fresh embeddings via the Gemini API.

Usage from a management command:

    from apps.documents.ingestion_docx import ingest_labour_docx
    ingest_labour_docx(
        path="Bangladesh_Labour_Act__2006_-_OCR.docx",
        doc_code="DOC-010",
        title="Bangladesh Labour Act, 2006",
        instrument_type="Act",
        language="en",
    )
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Iterator

from django.db import transaction

from apps.documents.embeddings import embed_text, EmbeddingError
from apps.documents.ingestion import ingest_json_document
from apps.documents.models import DocumentVersion, Node

logger = logging.getLogger(__name__)

# ── Parsing regexes ────────────────────────────────────────────────
# "Section 1. Short title.—(1) blah" or "Section 1. Title:" (Rules style)
_SECTION_RE = re.compile(
    r"^\s*\*{0,2}Section\s+(\d+[A-Za-z]?)\.\s*(.+?)\s*[.\u2014:]\s*[-\u2014]?\s*(.*)$",
    re.DOTALL,
)
# "## 3. Title." style used by the 2025 Ordinance and 2026 Amendment OCR exports
_MD_SECTION_RE = re.compile(
    r"^\s*#{1,3}\s+(\d+[A-Za-z]?)\.\s*(.+?)\s*[.\u2014:]\s*[-\u2014]?\s*(.*)$",
    re.DOTALL,
)
# "CHAPTER I" / "CHAPTER X" / "CHAPTER 5"
_CHAPTER_RE = re.compile(r"^\s*\*{0,2}CHAPTER\s+([IVXLCDM\d]+)\b", re.IGNORECASE)
# Markdown-style headers like "## PRELIMINARY" — but NOT "## 3. Title" (handled above)
_MD_HEADER_RE = re.compile(r"^\s*#{1,3}\s+([A-Z][A-Z\s,\-]+?)\s*$")
# An inline section split — Ordinance/Amendment 2025+2026 docx exports
# put everything on one line. Split before each '## N.' / 'Section N.' marker
# so the parser sees them as separate paragraphs.
_INLINE_SPLIT_RE = re.compile(
    r"(?=(?:\s|^)(?:\*{0,2}Section|#{1,3})\s+\d+[A-Za-z]?\.)"
)
# Things to skip entirely — boilerplate from the OCR conversion
_SKIP_PATTERNS = [
    re.compile(r"^See PDF\s*$", re.IGNORECASE),
    re.compile(r"^Viewing in (English|Bangla)", re.IGNORECASE),
    re.compile(r"^Act No\.", re.IGNORECASE),
    re.compile(r"^Ordinance No\.", re.IGNORECASE),
    re.compile(r"^\d{4}\s*\d+\s*pages?", re.IGNORECASE),
    re.compile(r"^RulesActive|^ActActive|^OrdinanceActive|^Amendment ActActive", re.IGNORECASE),
    re.compile(r"^<!--.*-->\s*$"),
    re.compile(r"^Full Text\s*$", re.IGNORECASE),
    re.compile(r"^This document amends\s+DOC-", re.IGNORECASE),
]


def _is_skip(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    return any(p.match(s) for p in _SKIP_PATTERNS)


# ── docx → paragraph list ──────────────────────────────────────────
def _read_docx_paragraphs(path: str | Path) -> list[str]:
    try:
        from docx import Document as DocxDocument
    except ImportError as e:
        raise RuntimeError("python-docx is not installed") from e
    docx = DocxDocument(str(path))
    return [p.text for p in docx.paragraphs if p.text.strip()]


# ── paragraph stream → structured JSON ─────────────────────────────
def _explode_inline_paragraphs(paragraphs: list[str]) -> list[str]:
    """Some OCR exports (Ordinance 2025, Amendment 2026) concatenate the
    entire act body into a single 90K-char paragraph. Split such paragraphs
    on the next section/markdown-section marker so the parser sees one
    section per line.
    """
    out: list[str] = []
    for p in paragraphs:
        if len(p) > 4000 and ("Section" in p or "## " in p):
            # Split on lookahead for section markers
            pieces = _INLINE_SPLIT_RE.split(p)
            for piece in pieces:
                piece = piece.strip()
                if piece:
                    out.append(piece)
        else:
            out.append(p)
    return out


def _parse_sections(paragraphs: list[str], doc_code: str) -> list[dict]:
    """Group paragraphs into Section blocks. Returns flat list of section dicts."""
    # Pre-pass: explode any giant single-line paragraphs (Ord 2025 / Amend 2026)
    paragraphs = _explode_inline_paragraphs(paragraphs)

    current_chapter: str | None = None
    chapter_title_for: str | None = None
    chapter_titles: dict[str, str] = {}

    sections: list[dict] = []
    cur: dict | None = None

    for raw in paragraphs:
        line = raw.strip()
        if _is_skip(line):
            continue

        ch_m = _CHAPTER_RE.match(line)
        if ch_m:
            current_chapter = ch_m.group(1).strip().upper()
            chapter_title_for = current_chapter
            continue

        # Try the markdown section style FIRST (## 3. Title.—body) — these
        # are full sections, not just chapter headers. If it matches we go
        # straight into "new section" mode.
        md_sec_m = _MD_SECTION_RE.match(line)
        if md_sec_m:
            if cur:
                sections.append(cur)
            cur = {
                "chapter": current_chapter,
                "chapter_title": chapter_titles.get(current_chapter or ""),
                "number": md_sec_m.group(1).strip(),
                "title": md_sec_m.group(2).strip(),
                "content_lines": [md_sec_m.group(3).strip()] if md_sec_m.group(3).strip() else [],
            }
            continue

        # Markdown header (no number) right after a CHAPTER line → chapter title.
        md_m = _MD_HEADER_RE.match(line)
        if md_m and chapter_title_for and chapter_title_for not in chapter_titles:
            chapter_titles[chapter_title_for] = md_m.group(1).strip()
            chapter_title_for = None
            continue

        sec_m = _SECTION_RE.match(line)
        if sec_m:
            if cur:
                sections.append(cur)
            cur = {
                "chapter": current_chapter,
                "chapter_title": chapter_titles.get(current_chapter or ""),
                "number": sec_m.group(1).strip(),
                "title": sec_m.group(2).strip(),
                "content_lines": [sec_m.group(3).strip()] if sec_m.group(3).strip() else [],
            }
            continue

        # Continuation of the current section's body
        if cur is not None:
            cur["content_lines"].append(line)

    if cur:
        sections.append(cur)

    # Backfill chapter titles we discovered late
    for s in sections:
        if not s.get("chapter_title") and s.get("chapter"):
            s["chapter_title"] = chapter_titles.get(s["chapter"])

    # Stringify content
    for s in sections:
        s["content"] = "\n".join(s.pop("content_lines")).strip()

    return sections


def _build_structured_json(
    sections: list[dict],
    *,
    doc_code: str,
    doc_title: str,
    language: str,
) -> dict:
    """Build the nested structure that ingest_json_document expects.

    Top-level: {title, node_id, language, summary, content, nodes:[…]}
    Each child node = one section. We collapse chapters into a flat
    list of section-nodes (no chapter wrapper nodes) because retrieval
    in pgvector benefits from leaf-level granularity. Chapter context
    is preserved in each section node's title.
    """
    root_summary = f"Full text of {doc_title}, parsed into per-section nodes."
    children: list[dict] = []
    for i, sec in enumerate(sections, start=1):
        node_id = f"{doc_code}-{i:04d}"
        chapter_prefix = ""
        if sec.get("chapter"):
            ct = sec.get("chapter_title") or ""
            chapter_prefix = (
                f"Chapter {sec['chapter']}"
                + (f" — {ct}" if ct else "")
                + " · "
            )
        full_title = f"{chapter_prefix}Section {sec['number']} — {sec['title']}"

        # Embedding-friendly body: title + content together so vector search
        # surfaces sections matched by their title alone (e.g. "notice period").
        body = (
            f"{full_title}\n\n"
            f"{sec['content']}".strip()
        )

        children.append({
            "title": full_title,
            "node_id": node_id,
            "start_index": i,
            "end_index": i,
            "language": language,
            "summary": (sec["content"][:280] + ("…" if len(sec["content"]) > 280 else "")),
            "content": body,
            "nodes": [],
            "supersession": {"status": "active"},
        })

    return {
        "title": doc_title,
        "node_id": f"{doc_code}-0000",
        "start_index": 0,
        "end_index": len(children),
        "language": language,
        "summary": root_summary,
        "content": f"# {doc_title}",
        "nodes": children,
        "supersession": {"status": "active"},
    }


# ── public entry point ─────────────────────────────────────────────
def ingest_labour_docx(
    path: str | Path,
    *,
    doc_code: str,
    title: str,
    instrument_type: str = "Act",
    instrument_number: str = "",
    language: str = "en",
    embed: bool = True,
    embed_sleep: float = 0.15,
    user_id: int | None = None,
) -> dict:
    """Parse a Labour Act/Rules .docx into sectioned nodes, ingest, and embed.

    Returns a summary dict:
        {
          "doc_code": "DOC-010",
          "version": 2,
          "sections": 295,
          "nodes_created": 296,        # root + 295 sections
          "embeddings_attached": 295,  # leaves only
          "embedding_errors": 0,
        }
    """
    paragraphs = _read_docx_paragraphs(path)
    sections = _parse_sections(paragraphs, doc_code=doc_code)
    if not sections:
        raise RuntimeError(f"No sections detected in {path}")

    raw_json = _build_structured_json(
        sections,
        doc_code=doc_code,
        doc_title=title,
        language=language,
    )

    version = ingest_json_document(
        doc_code,
        raw_json,
        metadata={
            "title": title,
            "language": language,
            "instrument_type": instrument_type,
            "instrument_number": instrument_number,
            "status": "active",
        },
        user_id=user_id,
    )

    nodes_created = Node.objects.filter(version=version).count()
    summary = {
        "doc_code": doc_code,
        "version": version.version,
        "sections": len(sections),
        "nodes_created": nodes_created,
        "embeddings_attached": 0,
        "embedding_errors": 0,
    }

    if not embed:
        return summary

    # Embed every leaf node (children of root). Skip the root and any
    # branch-only nodes (none in our flat tree but be defensive).
    leaves = list(
        Node.objects.filter(version=version)
        .exclude(node_id=f"{doc_code}-0000")
        .order_by("node_id")
    )
    embeddings: dict[str, list[float]] = {}
    errors = 0
    total = len(leaves)
    for i, node in enumerate(leaves, start=1):
        text = (node.content or node.title or "").strip()
        if not text:
            continue
        try:
            vec = embed_text(text)
            embeddings[node.node_id] = vec
        except EmbeddingError:
            errors += 1
            logger.warning(
                "embed_failed", extra={"node_id": node.node_id, "doc": doc_code},
            )
        except Exception:  # noqa: BLE001
            errors += 1
            logger.exception(
                "embed_unexpected_error", extra={"node_id": node.node_id},
            )
        if i % 25 == 0:
            logger.info(
                "embed_progress",
                extra={"doc": doc_code, "done": i, "total": total},
            )
            print(f"    [{doc_code}] embedded {i}/{total}…", flush=True)
        time.sleep(embed_sleep)

    if embeddings:
        with transaction.atomic():
            for node_id, vec in embeddings.items():
                Node.objects.filter(version=version, node_id=node_id).update(
                    embedding=vec
                )
    summary["embeddings_attached"] = len(embeddings)
    summary["embedding_errors"] = errors
    return summary
