"""Embedding generation. Default: Gemini text-embedding-004 (768 dims).

The existing corpus was built with this exact model. Reusing it means the
node-embeddings.json file loads straight in without re-embedding.
"""
from __future__ import annotations

import logging
import time
from typing import Sequence

from django.conf import settings
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    pass


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def embed_text(text: str) -> list[float]:
    """Embed a single string. Retries on transient errors."""
    if not settings.GEMINI_API_KEY:
        raise EmbeddingError("GEMINI_API_KEY not configured")
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise EmbeddingError("google-genai not installed") from e

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    result = client.models.embed_content(
        model=settings.GEMINI_EMBED_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT", output_dimensionality=768,),
    )
    embedding = result.embeddings[0].values
    if len(embedding) != settings.EMBEDDING_DIM:
        raise EmbeddingError(
            f"Embedding dim mismatch: got {len(embedding)}, expected {settings.EMBEDDING_DIM}"
        )
    return list(embedding)


def embed_query(text: str) -> list[float]:
    """Same as embed_text but with retrieval_query task type."""
    if not settings.GEMINI_API_KEY:
        raise EmbeddingError("GEMINI_API_KEY not configured")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    result = client.models.embed_content(
        model=settings.GEMINI_EMBED_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=768,
        ),
    )
    return list(result.embeddings[0].values)


def embed_batch(texts: Sequence[str], batch_size: int = 100, sleep_s: float = 0.1) -> list[list[float]]:
    """Embed many texts. Naive sequential loop."""
    out: list[list[float]] = []
    for i, t in enumerate(texts):
        out.append(embed_text(t))
        if i and i % 50 == 0:
            logger.info("embed_progress", extra={"done": i, "total": len(texts)})
        time.sleep(sleep_s)
    return out