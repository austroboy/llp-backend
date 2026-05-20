"""Celery tasks for document ingestion."""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .embeddings import embed_text
from .ingestion import (
    attach_search_vectors,
    ingest_corpus_zip,
    ingest_docx,
    ingest_pdf,
)
from .models import IngestionJob, Node

logger = logging.getLogger(__name__)


def _job_running(job_id: int) -> IngestionJob:
    job = IngestionJob.objects.get(id=job_id)
    job.status = IngestionJob.STATUS_RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    return job


def _job_finish(job: IngestionJob, status_str: str, *, progress: dict | None = None,
                error: str = "") -> None:
    job.status = status_str
    job.finished_at = timezone.now()
    if progress is not None:
        job.progress = progress
    if error:
        job.error_message = error
    job.save()


@shared_task(name="apps.documents.tasks.ingest_corpus_zip_task")
def ingest_corpus_zip_task(job_id: int, zip_path: str) -> dict:
    job = _job_running(job_id)
    try:
        summary = ingest_corpus_zip(zip_path, user_id=job.submitted_by_id)
        _job_finish(
            job,
            IngestionJob.STATUS_PARTIAL if summary.get("errors") else IngestionJob.STATUS_SUCCESS,
            progress=summary,
        )
        return summary
    except Exception as e:  # noqa: BLE001
        logger.exception("zip_ingestion_failed")
        _job_finish(job, IngestionJob.STATUS_FAILED, error=str(e))
        raise


@shared_task(name="apps.documents.tasks.ingest_docx_task")
def ingest_docx_task(job_id: int, path: str, doc_code: str, title: str,
                     language: str, user_id: int | None) -> dict:
    job = _job_running(job_id)
    try:
        version = ingest_docx(
            path, doc_code, title=title, language=language, user_id=user_id,
        )
        attach_search_vectors(version)
        # Generate embeddings for each leaf node
        embed_count = embed_version_leaves.apply_async(args=[version.id]).get()
        result = {"version_id": version.id, "embeddings": embed_count}
        _job_finish(job, IngestionJob.STATUS_SUCCESS, progress=result)
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("docx_ingestion_failed")
        _job_finish(job, IngestionJob.STATUS_FAILED, error=str(e))
        raise


@shared_task(name="apps.documents.tasks.ingest_pdf_task")
def ingest_pdf_task(job_id: int, path: str, doc_code: str, title: str,
                    language: str, user_id: int | None) -> dict:
    job = _job_running(job_id)
    try:
        version = ingest_pdf(
            path, doc_code, title=title, language=language, user_id=user_id,
        )
        attach_search_vectors(version)
        embed_count = embed_version_leaves.apply_async(args=[version.id]).get()
        result = {"version_id": version.id, "embeddings": embed_count}
        _job_finish(job, IngestionJob.STATUS_SUCCESS, progress=result)
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("pdf_ingestion_failed")
        _job_finish(job, IngestionJob.STATUS_FAILED, error=str(e))
        raise


@shared_task(name="apps.documents.tasks.embed_version_leaves")
def embed_version_leaves(version_id: int) -> int:
    """Embed every leaf node of a version that doesn't already have one."""
    leaves = Node.objects.filter(
        version_id=version_id, is_leaf=True, embedding__isnull=True,
    )
    count = 0
    for node in leaves.iterator():
        text_for_embedding = "\n".join(filter(None, [node.summary, node.content]))[:4000]
        if not text_for_embedding.strip():
            continue
        try:
            node.embedding = embed_text(text_for_embedding)
            node.save(update_fields=["embedding"])
            count += 1
        except Exception:  # noqa: BLE001
            logger.exception("embed_failed", extra={"node_id": node.node_id})
    return count


@shared_task(name="apps.documents.tasks.reembed_node_task")
def reembed_node_task(node_pk: int) -> bool:
    try:
        node = Node.objects.get(id=node_pk)
    except Node.DoesNotExist:
        return False
    text = "\n".join(filter(None, [node.summary, node.content]))[:4000]
    if not text.strip():
        return False
    try:
        node.embedding = embed_text(text)
        node.save(update_fields=["embedding"])
        return True
    except Exception:  # noqa: BLE001
        logger.exception("reembed_failed", extra={"node_id": node.node_id})
        return False
