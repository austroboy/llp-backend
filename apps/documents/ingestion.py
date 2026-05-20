"""Document ingestion: convert source files into the node tree + indexes.

Three entry points:
  1. ingest_json_document(doc_code, raw_json) — single DOC-XXX.json
  2. ingest_zip(zip_path) — bulk import the entire llp-chat-data6.zip shape
  3. ingest_docx(path, doc_code, ...) — basic Word→nodes (admin uploads)

The first two are the production path: they accept the existing corpus shape
verbatim, no transformation. The third is a stub that produces a flat single-
node document for manual editing — proper structure detection is admin work.
"""
from __future__ import annotations

import logging
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from django.db import transaction

from .models import (
    Crossref,
    Document,
    DocumentVersion,
    KeywordIndex,
    Node,
    StatutorySynonym,
)

logger = logging.getLogger(__name__)

# ── JSON ingestion (the canonical path) ───────────────────────────────────


def _flatten_nodes(node: dict, *, version_id: int, doc_code: str,
                   parent_id: str = "", depth: int = 0) -> Iterable[dict]:
    """Walk the recursive node tree and yield Django-ready row dicts."""
    children = node.get("nodes") or []
    is_leaf = len(children) == 0
    section_number = _extract_section_number(node)
    rule_number = _extract_rule_number(node)

    yield {
        "version_id": version_id,
        "node_id": node["node_id"],
        "parent_node_id": parent_id,
        "doc_code": doc_code,
        "title": node.get("title", "") or "",
        "start_index": node.get("start_index"),
        "end_index": node.get("end_index"),
        "language": node.get("language", "en"),
        "summary": node.get("summary", "") or "",
        "content": node.get("content", "") or "",
        "section_number": section_number,
        "rule_number": rule_number,
        "supersession": node.get("supersession", {}),
        "raw_node": _node_without_children(node),
        "is_leaf": is_leaf,
        "depth": depth,
    }

    for child in children:
        yield from _flatten_nodes(
            child, version_id=version_id, doc_code=doc_code,
            parent_id=node["node_id"], depth=depth + 1,
        )


def _node_without_children(node: dict) -> dict:
    return {k: v for k, v in node.items() if k != "nodes"}


_SECTION_RE = re.compile(r"(?:Section|ধারা)\s+([০-৯\d]+[A-Za-zক-হ]?)", re.IGNORECASE)
_RULE_RE = re.compile(r"Rule\s+(\d+[A-Za-z]?)", re.IGNORECASE)


def _extract_section_number(node: dict) -> str:
    title = node.get("title") or ""
    m = _SECTION_RE.search(title)
    if m:
        return m.group(1)
    return ""


def _extract_rule_number(node: dict) -> str:
    title = node.get("title") or ""
    m = _RULE_RE.search(title)
    if m:
        return m.group(1)
    return ""


@transaction.atomic
def ingest_json_document(
    doc_code: str,
    raw_json: dict,
    *,
    metadata: dict | None = None,
    user_id: int | None = None,
    embedding_model: str = "text-embedding-004",
) -> DocumentVersion:
    """Create or update a Document and its first/next DocumentVersion."""
    metadata = metadata or {}
    document, _ = Document.objects.update_or_create(
        doc_code=doc_code,
        defaults={
            "title": metadata.get("title") or raw_json.get("title", doc_code),
            "instrument_type": metadata.get("instrument_type", "Act"),
            "instrument_number": metadata.get("instrument_number", ""),
            "date_enacted": metadata.get("date_enacted"),
            "language": metadata.get("language") or raw_json.get("language", "en"),
            "is_parent": metadata.get("is_parent", False),
            "status": metadata.get("status", Document.STATUS_ACTIVE),
            "metadata": metadata,
        },
    )

    next_version = (
        DocumentVersion.objects.filter(document=document).count() + 1
    )

    # Demote any prior current version
    DocumentVersion.objects.filter(document=document, is_current=True).update(
        is_current=False
    )

    version = DocumentVersion.objects.create(
        document=document,
        version=next_version,
        raw_json=raw_json,
        is_current=True,
        ingested_by_id=user_id,
        embedding_model=embedding_model,
    )

    rows = list(_flatten_nodes(
        raw_json, version_id=version.id, doc_code=doc_code,
    ))
    Node.objects.bulk_create(
        [Node(**r) for r in rows], batch_size=500, ignore_conflicts=True
    )

    document.current_version = version
    document.save(update_fields=["current_version"])

    logger.info(
        "ingested_json_document",
        extra={"doc_code": doc_code, "version": next_version, "nodes": len(rows)},
    )
    return version


def attach_embeddings(
    version: DocumentVersion, embeddings_map: dict[str, list[float]]
) -> int:
    """Attach precomputed embeddings (keyed by node_id) to nodes in this version."""
    updated = 0
    nodes = Node.objects.filter(version=version, node_id__in=embeddings_map.keys())
    for node in nodes:
        emb = embeddings_map.get(node.node_id)
        if emb is None:
            continue
        node.embedding = emb
        updated += 1
    Node.objects.bulk_update(nodes, ["embedding"], batch_size=200)
    return updated


def attach_search_vectors(version: DocumentVersion) -> int:
    """Build Postgres tsvector for every node in this version."""
    from django.contrib.postgres.search import SearchVector

    qs = Node.objects.filter(version=version)
    return qs.update(
        search_vector=SearchVector("title", "summary", "content", config="simple")
    )


def attach_crossrefs(version: DocumentVersion, crossrefs_json: dict) -> int:
    """Load section-crossrefs.json into the Crossref table."""
    rows = []
    for section_num, payload in crossrefs_json.items():
        rows.append(Crossref(
            version=version,
            section_number=str(section_num),
            ref_type=payload.get("type", "section"),
            node_ids=payload.get("node_ids", []),
            references=[str(x) for x in payload.get("references", [])],
            referenced_by=[str(x) for x in payload.get("referenced_by", [])],
        ))
    Crossref.objects.bulk_create(rows, batch_size=200, ignore_conflicts=True)
    return len(rows)


def attach_keyword_index(version: DocumentVersion, keyword_json: dict) -> int:
    rows = []
    for key, payload in keyword_json.items():
        rows.append(KeywordIndex(
            version=version,
            key=str(key),
            doc_code=payload.get("doc_id", ""),
            prior_doc_code=payload.get("prior_doc_id", ""),
            keywords=payload.get("keywords", []),
        ))
    KeywordIndex.objects.bulk_create(rows, batch_size=200)
    return len(rows)


def attach_synonyms(synonyms_json: dict, language: str) -> int:
    """Loads bn-statutory-synonyms.json or en-statutory-synonyms.json."""
    rows = []
    for term, syns in synonyms_json.items():
        rows.append(StatutorySynonym(
            language=language,
            term=str(term),
            synonyms=list(syns) if isinstance(syns, list) else [],
        ))
    # Upsert one-by-one to handle duplicates cleanly
    created = 0
    for r in rows:
        StatutorySynonym.objects.update_or_create(
            language=r.language, term=r.term,
            defaults={"synonyms": r.synonyms},
        )
        created += 1
    return created


# ── Zip ingestion ─────────────────────────────────────────────────────────


@transaction.atomic
def ingest_corpus_zip(zip_path: str | Path, *, user_id: int | None = None) -> dict:
    """Ingest an llp-chat-data6.zip-shaped archive in one shot.

    Expected files (any subset is OK):
      - universe-registry.json  (document metadata)
      - DOC-XXX.json            (document content)
      - node-embeddings.json    (precomputed embeddings)
      - section-crossrefs.json  (cross-references)
      - section-keyword-index.json
      - bn-statutory-synonyms.json / en-statutory-synonyms.json
    """
    import json

    summary: dict[str, Any] = {
        "documents": 0, "nodes": 0, "embeddings_attached": 0,
        "crossrefs": 0, "keyword_entries": 0, "synonyms": 0,
        "errors": [],
    }

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

        # 1. Read registry first to learn metadata
        registry = {}
        for n in names:
            if n.endswith("universe-registry.json"):
                registry = json.loads(zf.read(n).decode("utf-8"))
                break
        registry_by_code: dict[str, dict] = {
            d["id"]: d for d in registry.get("documents", [])
        }

        # 2. Ingest each DOC-XXX.json
        versions_by_code: dict[str, DocumentVersion] = {}
        for n in sorted(names):
            base = Path(n).name
            if not (base.startswith("DOC-") and base.endswith(".json")):
                continue
            doc_code = base.replace(".json", "")
            try:
                payload = json.loads(zf.read(n).decode("utf-8"))
                meta = registry_by_code.get(doc_code, {})
                meta_clean = {
                    "title": meta.get("title"),
                    "instrument_type": meta.get("instrument_type", "Act"),
                    "instrument_number": meta.get("instrument_number", ""),
                    "language": meta.get("language", "en").lower()[:2],
                    "is_parent": meta.get("is_parent", False),
                    "status": _map_status(meta.get("status", "active")),
                }
                if meta.get("date_enacted"):
                    try:
                        raw_date = str(meta["date_enacted"]).strip()
                        if len(raw_date) == 4:  # শুধু year, যেমন "2022"
                            raw_date = f"{raw_date}-01-01"
                        meta_clean["date_enacted"] = raw_date[:10]
                    except ValueError:
                        pass
                version = ingest_json_document(
                    doc_code, payload, metadata=meta_clean, user_id=user_id,
                )
                versions_by_code[doc_code] = version
                summary["documents"] += 1
                summary["nodes"] += version.nodes.count()
            except Exception as e:  # noqa: BLE001
                logger.exception("zip_doc_ingest_failed", extra={"file": n})
                summary["errors"].append(f"{n}: {e}")

        # 3. Set supersession FK relationships now that all docs exist
        for code, meta in registry_by_code.items():
            try:
                doc = Document.objects.get(doc_code=code)
            except Document.DoesNotExist:
                continue
            if meta.get("amends"):
                doc.amends = Document.objects.filter(doc_code=meta["amends"]).first()
            if meta.get("superseded_by"):
                doc.superseded_by = Document.objects.filter(
                    doc_code=meta["superseded_by"]
                ).first()
            doc.save(update_fields=["amends", "superseded_by"])

        # 4. Attach embeddings
        for n in names:
            if n.endswith("node-embeddings.json"):
                emb_payload = json.loads(zf.read(n).decode("utf-8"))
                # Group by doc_code derived from node_id
                by_doc: dict[str, dict[str, list[float]]] = {}
                for node_id, entry in emb_payload.items():
                    doc_code = node_id.split("-")
                    code = f"{doc_code[0]}-{doc_code[1]}"  # 'DOC-010'
                    by_doc.setdefault(code, {})[node_id] = entry["embedding"]
                for code, mapping in by_doc.items():
                    if code in versions_by_code:
                        summary["embeddings_attached"] += attach_embeddings(
                            versions_by_code[code], mapping
                        )
                break

        # 5. Crossrefs (apply to every doc version — they're cross-doc)
        for n in names:
            if n.endswith("section-crossrefs.json"):
                cr = json.loads(zf.read(n).decode("utf-8"))
                # Store under each version so retrieval can join
                for v in versions_by_code.values():
                    summary["crossrefs"] += attach_crossrefs(v, cr)
                break

        # 6. Keyword index
        for n in names:
            if n.endswith("section-keyword-index.json"):
                kw = json.loads(zf.read(n).decode("utf-8"))
                for v in versions_by_code.values():
                    summary["keyword_entries"] += attach_keyword_index(v, kw)
                break

        # 7. Synonyms
        for n in names:
            base = Path(n).name
            if base == "bn-statutory-synonyms.json":
                syn = json.loads(zf.read(n).decode("utf-8"))
                summary["synonyms"] += attach_synonyms(syn, "bn")
            elif base == "en-statutory-synonyms.json":
                syn = json.loads(zf.read(n).decode("utf-8"))
                summary["synonyms"] += attach_synonyms(syn, "en")

        # 8. Build search_vectors for every ingested version
        for v in versions_by_code.values():
            attach_search_vectors(v)

    return summary


def _map_status(s: str) -> str:
    """Map registry status strings to our enum."""
    table = {
        "active": Document.STATUS_ACTIVE,
        "superseded": Document.STATUS_SUPERSEDED,
        "active_with_amendments": Document.STATUS_ACTIVE_WITH_AMENDMENTS,
        "draft": Document.STATUS_DRAFT,
    }
    return table.get(s, Document.STATUS_ACTIVE)


# ── DOCX upload (basic) ───────────────────────────────────────────────────


def ingest_docx(path: str | Path, doc_code: str, *,
                title: str = "", language: str = "en",
                user_id: int | None = None) -> DocumentVersion:
    """Convert a .docx into a flat single-node Document.

    This is intentionally minimal — proper section detection is hard and
    error-prone. Admins can split the resulting node tree manually in the
    Django admin, or pre-convert their docs to JSON before upload.
    """
    try:
        from docx import Document as DocxDocument
    except ImportError:
        raise RuntimeError("python-docx is not installed")

    docx = DocxDocument(str(path))
    paragraphs = [p.text for p in docx.paragraphs if p.text.strip()]
    full_text = "\n".join(paragraphs)
    summary = (paragraphs[0] if paragraphs else "")[:500]

    raw_json = {
        "title": title or doc_code,
        "node_id": f"{doc_code}-0000",
        "language": language,
        "summary": summary,
        "content": full_text,
        "nodes": [],
        "supersession": {"status": "active"},
    }
    return ingest_json_document(
        doc_code, raw_json,
        metadata={"title": title or doc_code, "language": language},
        user_id=user_id,
    )


def ingest_pdf(path: str | Path, doc_code: str, *,
               title: str = "", language: str = "en",
               user_id: int | None = None) -> DocumentVersion:
    """Convert a .pdf into a flat single-node Document."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("pypdf is not installed")

    reader = PdfReader(str(path))
    full_text = "\n\n".join(
        (page.extract_text() or "") for page in reader.pages
    )
    summary = (full_text[:500] if full_text else "")
    raw_json = {
        "title": title or doc_code,
        "node_id": f"{doc_code}-0000",
        "language": language,
        "summary": summary,
        "content": full_text,
        "nodes": [],
        "supersession": {"status": "active"},
    }
    return ingest_json_document(
        doc_code, raw_json,
        metadata={"title": title or doc_code, "language": language},
        user_id=user_id,
    )
