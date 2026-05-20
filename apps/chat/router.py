"""Hybrid model routing — pick the cheapest model that can answer accurately.

Routes:
  - SIMPLE_LOOKUP    → Kimi K2.6   (cheap, fast, sufficient for direct factual)
  - BANGLA_COMPLEX   → Claude Sonnet (better Bangla statutory reasoning)
  - LEGAL_EDGE_CASE  → Claude Opus  (best reasoning, expensive)

Decision factors:
  1. Intent complexity (from intent classifier)
  2. Language (Bangla edge cases prefer Claude)
  3. Tier (free users: Kimi only; paid: full routing)
  4. Override flags (admin can force a model per query)

Expected cost mix at scale:
  70% Kimi   ($0.60 in / $2.50 out)
  25% Sonnet ($3.00 in / $15.00 out)
   5% Opus   ($15.00 in / $75.00 out)
  Blended:   ~$1.45/M input, ~$8/M output
  vs pure Opus: $15/M input, $75/M output (10x reduction)

──────────────────────────────────────────────────────────────────────
 ⚠️  TEMPORARY OVERRIDE — Kimi disabled
──────────────────────────────────────────────────────────────────────
Kimi (Moonshot) account currently suspended due to insufficient balance.
All traffic is routed to Anthropic until the Moonshot account is recharged.
To re-enable Kimi routing, set FORCE_ANTHROPIC_ONLY = False below.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from django.conf import settings

from .anthropic_provider import GenerationChunk as AnthropicChunk
from .anthropic_provider import stream_generate as anthropic_stream
from .kimi_provider import GenerationChunk as KimiChunk
from .kimi_provider import stream_generate as kimi_stream

ProviderName = Literal["kimi", "anthropic"]

# ──────────────────────────────────────────────────────────────────────
# Feature flag — set to False after Moonshot recharge to re-enable Kimi.
# ──────────────────────────────────────────────────────────────────────
FORCE_ANTHROPIC_ONLY = True


@dataclass
class RoutingDecision:
    provider: ProviderName
    tier_for_provider: str  # "free" | "mini" | "max"
    rationale: str
    estimated_cost_per_1k_tokens_usd: float


def route_request(
    *,
    intent_label: str,
    language: str,
    complexity: str,
    user_tier: str,
    force_provider: ProviderName | None = None,
) -> RoutingDecision:
    """Decide which provider+model handles this request.

    Args:
        intent_label: From classify_intent — e.g. "factual", "situation",
                      "document_review", "edge_case", "out_of_scope"
        language: "en" | "bn"
        complexity: "low" | "medium" | "high"
        user_tier: "free_guest" | "free_subscribed" | "mini" | "max"
        force_provider: Admin override
    """

    # ──────────────────────────────────────────────────────────────────
    # GLOBAL OVERRIDE: Force Anthropic for ALL queries while Kimi is
    # suspended. Pick a sensible Claude tier based on complexity so we
    # don't burn Opus credits on simple factual lookups.
    # ──────────────────────────────────────────────────────────────────
    if FORCE_ANTHROPIC_ONLY and force_provider != "kimi":
        if intent_label == "edge_case" or complexity == "high":
            claude_tier = "max"
            est_cost = 0.045
        elif complexity == "medium" or intent_label == "document_review":
            claude_tier = "mini"
            est_cost = 0.009
        else:
            claude_tier = "mini"  # Sonnet for simple too — cheaper than Opus
            est_cost = 0.009
        return RoutingDecision(
            provider="anthropic",
            tier_for_provider=claude_tier,
            rationale=f"forced_anthropic_kimi_suspended:{claude_tier}",
            estimated_cost_per_1k_tokens_usd=est_cost,
        )

    # Explicit per-request overrides still respected.
    if force_provider == "anthropic":
        return RoutingDecision(
            provider="anthropic",
            tier_for_provider=user_tier,
            rationale="forced=anthropic",
            estimated_cost_per_1k_tokens_usd=0.018,
        )

    if force_provider == "kimi":
        return RoutingDecision(
            provider="kimi",
            tier_for_provider=user_tier,
            rationale="forced=kimi",
            estimated_cost_per_1k_tokens_usd=0.0015,
        )

    # ──────────────────────────────────────────────────────────────────
    # Normal routing logic (unreachable while FORCE_ANTHROPIC_ONLY is True)
    # ──────────────────────────────────────────────────────────────────

    # Free guests — always Kimi (lowest cost, sufficient for casual Q's)
    if user_tier == "free_guest":
        return RoutingDecision(
            provider="kimi",
            tier_for_provider="free",
            rationale="free_guest_default_kimi",
            estimated_cost_per_1k_tokens_usd=0.0015,
        )

    # Legal edge cases — pay for Opus regardless of language
    if intent_label == "edge_case" or complexity == "high":
        return RoutingDecision(
            provider="anthropic",
            tier_for_provider="max",
            rationale="edge_case_or_high_complexity→opus",
            estimated_cost_per_1k_tokens_usd=0.045,
        )

    # Bangla + medium complexity → Sonnet (better Bangla statutory grasp)
    if language == "bn" and complexity == "medium":
        return RoutingDecision(
            provider="anthropic",
            tier_for_provider="mini",
            rationale="bn_medium→sonnet",
            estimated_cost_per_1k_tokens_usd=0.009,
        )

    # Document review (Bangla or English) → Sonnet for reliability
    if intent_label == "document_review":
        return RoutingDecision(
            provider="anthropic",
            tier_for_provider="mini",
            rationale="document_review→sonnet",
            estimated_cost_per_1k_tokens_usd=0.009,
        )

    # Default: Kimi for direct factual / English / paid-tier simple queries
    return RoutingDecision(
        provider="kimi",
        tier_for_provider=user_tier,
        rationale="default→kimi",
        estimated_cost_per_1k_tokens_usd=0.0015,
    )


def stream_via_router(
    *,
    decision: RoutingDecision,
    system: str,
    user_message: str,
    max_tokens: int = 2048,
) -> Iterator[AnthropicChunk | KimiChunk]:
    """Dispatch to the chosen provider. Both providers yield compatible chunks."""

    if decision.provider == "kimi":
        yield from kimi_stream(
            system=system,
            user_message=user_message,
            tier=decision.tier_for_provider,
            max_tokens=max_tokens,
        )
    else:
        yield from anthropic_stream(
            system=system,
            user_message=user_message,
            tier=decision.tier_for_provider,
            max_tokens=max_tokens,
        )