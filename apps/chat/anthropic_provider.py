"""Anthropic / Claude API integration: streaming generation + verification."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class GenerationChunk:
    delta: str = ""
    final: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    stop_reason: str = ""


def _select_model(tier: str) -> str:
    if tier == "max":
        return settings.ANTHROPIC_MODEL_OPUS
    if tier == "mini":
        return settings.ANTHROPIC_MODEL_SONNET
    return settings.ANTHROPIC_MODEL_SONNET  # free tiers also use Sonnet


def stream_generate(*, system: str, user_message: str,
                    tier: str, max_tokens: int = 2048) -> Iterator[GenerationChunk]:
    """Stream a Claude completion. Yields GenerationChunk(delta) per token,
    with a final chunk carrying token counts."""
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY == "test-key":
        # Test/dev fallback — emit a deterministic placeholder
        placeholder = (
            "[stub] This is a placeholder response. Configure ANTHROPIC_API_KEY "
            "for real model output."
        )
        yield GenerationChunk(delta=placeholder)
        yield GenerationChunk(final=True, tokens_in=0, tokens_out=0, stop_reason="stop")
        return

    from anthropic import Anthropic

    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    model = _select_model(tier)

    tokens_in = 0
    tokens_out = 0
    stop_reason = ""
    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text in stream.text_stream:
                yield GenerationChunk(delta=text)
            final_msg = stream.get_final_message()
            if final_msg.usage:
                tokens_in = final_msg.usage.input_tokens or 0
                tokens_out = final_msg.usage.output_tokens or 0
            stop_reason = final_msg.stop_reason or ""
    except Exception as e:  # noqa: BLE001
        logger.exception("anthropic_stream_failed")
        yield GenerationChunk(delta=f"\n\n[error: {e!s}]\n")

    yield GenerationChunk(
        final=True, tokens_in=tokens_in, tokens_out=tokens_out, stop_reason=stop_reason,
    )


def verify_citation(citation_text: str, candidate_node_text: str) -> dict:
    """Single-claim audit: does this excerpt support that citation? Returns
    a verdict dict {ok: bool, confidence: float, reason: str}.
    """
    if not settings.ENABLE_VERIFIER_LOOP:
        return {"ok": True, "confidence": 0.0, "reason": "verifier_disabled"}
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY == "test-key":
        return {"ok": True, "confidence": 0.5, "reason": "no_api_key"}

    try:
        from anthropic import Anthropic
    except ImportError:
        return {"ok": True, "confidence": 0.0, "reason": "no_anthropic_pkg"}

    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        resp = client.messages.create(
            model=settings.ANTHROPIC_MODEL_HAIKU,
            max_tokens=120,
            system=(
                "You verify a legal citation against a source excerpt. "
                "Output strict JSON: {\"ok\": bool, \"confidence\": 0..1, \"reason\": \"...\"}"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"CITATION: {citation_text}\n\n"
                    f"SOURCE EXCERPT:\n{candidate_node_text[:2000]}\n\n"
                    "Does the excerpt actually support the citation as written?"
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
        import json, re
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        logger.exception("verify_citation_failed")
    return {"ok": True, "confidence": 0.0, "reason": "fallback"}
