"""Kimi K2.6 / Moonshot AI integration — streaming generation.

Drop-in companion to anthropic_provider.py. Uses OpenAI-compatible API so
the existing `openai` SDK works (or plain `requests`).

Pricing (May 2026, Moonshot direct):
  - Input:  $0.60 / 1M tokens
  - Output: $2.50 / 1M tokens
  - Cached input: $0.15 / 1M tokens (automatic, 75% discount on repeats)

Compare Claude Opus 4.7:
  - Input:  $15.00 / 1M tokens  (25x more expensive)
  - Output: $75.00 / 1M tokens  (30x more expensive)
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class GenerationChunk:
    """Same shape as anthropic_provider.GenerationChunk — drop-in compatible."""
    delta: str = ""
    final: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    stop_reason: str = ""


def _select_model(tier: str) -> str:
    """Map tier → Kimi model. K2.6 is the latest, K2.5 still available."""
    if tier == "max":
        return getattr(settings, "KIMI_MODEL_PRO", "kimi-k2-thinking")
    return getattr(settings, "KIMI_MODEL_DEFAULT", "kimi-k2-0905")


def stream_generate(
    *,
    system: str,
    user_message: str,
    tier: str,
    max_tokens: int = 2048,
) -> Iterator[GenerationChunk]:
    """Stream a Kimi K2 completion.

    Same signature as anthropic_provider.stream_generate so the pipeline can
    swap providers transparently.
    """
    api_key = getattr(settings, "KIMI_API_KEY", "") or getattr(settings, "MOONSHOT_API_KEY", "")

    if not api_key or api_key == "test-key":
        # Test/dev fallback — emit a deterministic placeholder
        placeholder = (
            "[stub-kimi] Configure KIMI_API_KEY for real Kimi K2.6 output."
        )
        yield GenerationChunk(delta=placeholder)
        yield GenerationChunk(final=True, tokens_in=0, tokens_out=0, stop_reason="stop")
        return

    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.moonshot.ai/v1",  # Kimi OpenAI-compatible endpoint
    )
    model = _select_model(tier)

    tokens_in = 0
    tokens_out = 0
    stop_reason = ""

    try:
        with client.chat.completions.stream(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            stream_options={"include_usage": True},
        ) as stream:
            for event in stream:
                if event.type == "content.delta":
                    yield GenerationChunk(delta=event.delta)
                elif event.type == "message.completed":
                    if hasattr(event, "snapshot") and event.snapshot.usage:
                        tokens_in = event.snapshot.usage.prompt_tokens
                        tokens_out = event.snapshot.usage.completion_tokens
                    stop_reason = "stop"

    except Exception as exc:
        logger.exception("kimi.stream_failed", extra={"model": model})
        yield GenerationChunk(delta=f"\n[error: {exc.__class__.__name__}]")
        yield GenerationChunk(final=True, stop_reason="error")
        return

    yield GenerationChunk(
        final=True,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        stop_reason=stop_reason or "stop",
    )