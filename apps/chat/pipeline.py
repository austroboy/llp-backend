"""The full chat pipeline. Composes auth → quota → intent → retrieval →
prompt → ROUTER (Kimi or Claude) → citations → Zone 2 → persist.

V3.1 (May 16 2026):
  - Multi-question bypass: when user clearly asks multiple specific
    questions (numbered or '?'-separated), do NOT enter clarification mode
    even if classifier flagged AMBIGUOUS_SCENARIO. User wants answers.
  - Sub-question retrieval: each part of a multi-part query gets its own
    retrieval pass so each sub-question pulls its own most-relevant sections.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.audit.services import record_event
from apps.common.exceptions import IntentBlocked, QuotaExceeded, RateLimited
from apps.documents.models import CitationAudit
from apps.documents.retrieval import RetrievalHit, hybrid_search
from apps.subscriptions.constants import Intent
from apps.subscriptions.services import (
    check_daily_quota,
    check_intent_access,
    check_rate_limit,
    consume_quota,
    resolve_tier,
    subject_id_for,
)

from .router import route_request, stream_via_router
from .citations import (
    LegalBasisRow,
    build_legal_basis,
    confidence_band,
    extract_citations,
)
from .intent import IntentClassification, classify_intent, select_mode
from .models import ChatMessage, Conversation, FileAttachment, ResponseCache
from .prompts import PromptBundle, build_prompt

logger = logging.getLogger(__name__)


@dataclass
class PipelineEvent:
    type: str
    data: dict[str, Any]


@dataclass
class PipelineContext:
    user: Any
    guest_token: str | None
    conversation: Conversation
    user_message: str
    language: str
    attachments: list[FileAttachment] = field(default_factory=list)


def _query_hash(message: str, tier: str, language: str) -> str:
    return hashlib.sha256(
        f"{tier}|{language}|{message.strip().lower()}".encode("utf-8")
    ).hexdigest()


def _load_response_cache(query_hash: str, tier: str, language: str) -> dict | None:
    if not settings.ENABLE_RESPONSE_CACHE:
        return None
    try:
        cached = ResponseCache.objects.get(query_hash=query_hash)
        if cached.expires_at < timezone.now():
            return None
        if cached.tier != tier or cached.language != language:
            return None
        cached.hits = (cached.hits or 0) + 1
        cached.save(update_fields=["hits"])
        return cached.payload
    except ResponseCache.DoesNotExist:
        return None


def _save_response_cache(*, query_hash: str, tier: str, language: str,
                         payload: dict, ttl_hours: int = 24) -> None:
    if not settings.ENABLE_RESPONSE_CACHE:
        return
    expires = timezone.now() + timezone.timedelta(hours=ttl_hours)
    ResponseCache.objects.update_or_create(
        query_hash=query_hash,
        defaults={
            "tier": tier, "language": language,
            "payload": payload, "expires_at": expires,
        },
    )


def _load_history(conv: Conversation, max_turns: int = 6) -> list[dict]:
    msgs = list(
        conv.messages.exclude(role=ChatMessage.ROLE_SYSTEM)
        .order_by("-created_at")[:max_turns]
    )
    msgs.reverse()
    return [{"role": m.role, "content": m.content[:1500]} for m in msgs]


def _format_conversation_summary(history: list[dict]) -> str:
    if not history:
        return ""
    pieces = []
    for h in history[-4:]:
        prefix = "User" if h["role"] == "user" else "Assistant"
        pieces.append(f"{prefix}: {h['content'][:400]}")
    return "\n".join(pieces)


def _attached_text(attachments: list[FileAttachment]) -> str:
    if not attachments:
        return ""
    return "\n\n---\n\n".join(
        f"[{a.filename}]\n{a.extracted_text}" for a in attachments if a.extracted_text
    )


def _detect_blocked_intents(tier_config: dict) -> list[str]:
    blocked: list[str] = []
    if not tier_config.get("advisory_allowed"):
        blocked.append(Intent.ADVISORY)
    if not tier_config.get("cross_domain_allowed"):
        blocked.append(Intent.CROSS_DOMAIN)
    return blocked


def _create_unverified_audits(unverified, message_id: int) -> None:
    for c in unverified:
        try:
            CitationAudit.objects.create(
                raised_text=c.raw_text[:200],
                expected_section=c.section or c.rule,
                chat_message_id=message_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("citation_audit_create_failed")


def _estimate_complexity(classification: IntentClassification, mode: str) -> str:
    if mode == "document_review":
        return "high"
    if classification.primary_intent in (Intent.ADVISORY, Intent.CROSS_DOMAIN):
        return "high"
    if mode == "situation":
        return "medium"
    return "low"


def _map_intent_to_router_label(intent: str, mode: str) -> str:
    if mode == "document_review":
        return "document_review"
    if intent in (Intent.ADVISORY, Intent.CROSS_DOMAIN):
        return "edge_case"
    if mode == "situation":
        return "situation"
    return "factual"


def _split_subquestions(text: str) -> list[str]:
    """Split a multi-part query into sub-questions."""
    numbered = re.split(r"(?:^|\n)\s*\d+[\.\)]\s+", text)
    numbered = [p.strip() for p in numbered if p.strip()]
    if len(numbered) > 1:
        return numbered

    questions = re.split(r"(?<=\?)\s+(?=[A-Z\u0980-\u09FF])", text)
    questions = [p.strip() for p in questions if p.strip()]
    if len(questions) > 1:
        return questions

    return [text]


def _looks_like_multi_question(text: str) -> bool:
    """Heuristic: user clearly asks multiple concrete questions?"""
    return len(_split_subquestions(text)) > 1


def run_pipeline(ctx: PipelineContext) -> Iterator[PipelineEvent]:
    started = time.perf_counter()

    # ── 1. Tier resolution ────────────────────────────────────────────
    tier_resolution = resolve_tier(
        user=ctx.user if (ctx.user and ctx.user.is_authenticated) else None,
        guest_token=ctx.guest_token,
    )
    tier = tier_resolution.tier
    tier_cfg = tier_resolution.config
    subject = subject_id_for(
        user=ctx.user if (ctx.user and ctx.user.is_authenticated) else None,
        guest_token=ctx.guest_token,
    )

    # ── 2. Rate limit ────────────────────────────────────────────────
    rl = check_rate_limit(subject, tier_cfg["rate_limit_per_min"])
    if not rl.allowed:
        raise RateLimited(retry_after=rl.retry_after_seconds)

    # ── 3. Daily quota ───────────────────────────────────────────────
    quota = check_daily_quota(subject, tier_cfg["daily_request_limit"], tier)
    if not quota.allowed:
        record_event("chat.quota_blocked", actor=ctx.user, payload={"tier": tier})
        raise QuotaExceeded(upgrade_cta=quota.upgrade_cta)

    # ── 4. Intent classification ─────────────────────────────────────
    classification = classify_intent(ctx.user_message)
    has_attachment = bool(ctx.attachments) and tier_cfg["file_upload_allowed"]
    mode = select_mode(classification, has_attachment=has_attachment, message=ctx.user_message)

    # ── 4b. V3.1 Multi-question bypass ───────────────────────────────
    # If user clearly asked multiple concrete questions (numbered or
    # question-mark separated), do NOT enter clarification mode even if
    # classifier flagged AMBIGUOUS_SCENARIO. The user wants answers.
    if mode == "clarification" and _looks_like_multi_question(ctx.user_message):
        logger.info(
            "pipeline.multi_question_bypass",
            extra={
                "original_intent": classification.primary_intent,
                "num_subquestions": len(_split_subquestions(ctx.user_message)),
            },
        )
        classification.primary_intent = Intent.FACTUAL
        mode = "direct"

    # ── 5. Intent gating ─────────────────────────────────────────────
    intent_check = check_intent_access(classification.primary_intent, tier_cfg, tier)
    blocked_intents: list[str] = _detect_blocked_intents(tier_cfg)
    if not intent_check.allowed:
        record_event(
            "chat.intent_blocked", actor=ctx.user,
            payload={"intent": classification.primary_intent, "tier": tier},
        )
        classification.primary_intent = Intent.FACTUAL
        if mode != "clarification":
            mode = "direct"
        ctx_intent_blocked_cta = intent_check.upgrade_cta
    else:
        ctx_intent_blocked_cta = None

    # ── 6. Emit meta event ───────────────────────────────────────────
    yield PipelineEvent(
        type="meta",
        data={
            "conversation_id": ctx.conversation.id,
            "intent": classification.primary_intent,
            "mode": mode,
            "tier": tier,
            "language": classification.language,
            "remaining_quota": quota.remaining,
        },
    )

    # ── 7. Greeting mode short-circuit ───────────────────────────────
    # User just said hi / hello / কেমন আছেন etc. Reply warmly with a
    # one-liner that invites a real question, no retrieval / LLM call.
    if mode == "greeting":
        yield from _run_greeting(ctx, classification)
        return

    # ── 7b. Clarification mode short-circuit ─────────────────────────
    if mode == "clarification":
        yield from _run_clarification(ctx, classification)
        return

    # ── 8. Retrieval (per-sub-question for multi-part) ───────────────
    lang_filter = None
    if classification.language == "english":
        lang_filter = "en"
    elif classification.language == "bangla":
        lang_filter = "bn"

    sub_queries = _split_subquestions(ctx.user_message)
    if len(sub_queries) > 1:
        logger.info(
            "pipeline.subquestion_split",
            extra={"num_subquestions": len(sub_queries)},
        )
        all_hits: list[RetrievalHit] = []
        seen_node_ids: set[str] = set()
        for sub in sub_queries:
            sub_hits = hybrid_search(sub, top_k=5, language=lang_filter)
            for h in sub_hits:
                if h.node_id not in seen_node_ids:
                    all_hits.append(h)
                    seen_node_ids.add(h.node_id)
        hits: list[RetrievalHit] = all_hits[:12]
    else:
        hits = hybrid_search(ctx.user_message, top_k=8, language=lang_filter)

    # If retrieval is empty
    if not hits:
        no_coverage = (
            "This topic is outside Labor Law Partner's coverage. "
            "I focus on the Bangladesh Labour Act 2006, the Labour Rules 2015, "
            "and their amendments. Could you rephrase the question or share more "
            "context about the workplace situation?"
        )
        yield PipelineEvent(type="text", data={"delta": no_coverage})
        message = ChatMessage.objects.create(
            conversation=ctx.conversation, role=ChatMessage.ROLE_ASSISTANT,
            content=no_coverage, intent=classification.primary_intent, mode=mode,
            verdict="no_coverage",
        )
        yield PipelineEvent(type="done", data={
            "message_id": message.id,
            "tokens_in": 0, "tokens_out": 0, "cached": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        })
        return

    # ── 9. Response cache ────────────────────────────────────────────
    qhash = _query_hash(ctx.user_message, tier, classification.language)
    cached = _load_response_cache(qhash, tier, classification.language)
    if cached:
        yield from _replay_cached(ctx, cached, classification, mode, started)
        return

    # ── 10. History → context ────────────────────────────────────────
    history = _load_history(ctx.conversation)
    summary = _format_conversation_summary(history)
    attached = _attached_text(ctx.attachments)

    # ── 11. Prompt assembly ──────────────────────────────────────────
    prompt: PromptBundle = build_prompt(
        user_query=ctx.user_message,
        hits=hits, tier=tier, mode=mode,
        blocked_intents=blocked_intents,
        conversation_summary=summary,
        attached_text=attached,
    )

    ChatMessage.objects.create(
        conversation=ctx.conversation, role=ChatMessage.ROLE_USER,
        content=ctx.user_message, intent=classification.primary_intent, mode=mode,
        attachments=[a.id for a in ctx.attachments],
    )

    # ── 12. Router decision ───────────────────────────────────────────
    router_language = "bn" if classification.language == "bangla" else "en"
    router_intent_label = _map_intent_to_router_label(
        classification.primary_intent, mode,
    )
    router_complexity = _estimate_complexity(classification, mode)

    decision = route_request(
        intent_label=router_intent_label,
        language=router_language,
        complexity=router_complexity,
        user_tier=tier,
    )

    logger.info(
        "router.decision",
        extra={
            "provider": decision.provider,
            "provider_tier": decision.tier_for_provider,
            "rationale": decision.rationale,
            "user_tier": tier,
            "intent": router_intent_label,
            "language": router_language,
            "complexity": router_complexity,
        },
    )

    # ── 13. Generate (streaming via router) ──────────────────────────
    full_text_parts: list[str] = []
    tokens_in = tokens_out = 0
    model_name_used = f"{decision.provider}:{decision.tier_for_provider}"

    for chunk in stream_via_router(
        decision=decision,
        system=prompt.system,
        user_message=prompt.user,
        max_tokens=2048 if tier == "max" else 1280,
    ):
        if chunk.final:
            tokens_in = chunk.tokens_in
            tokens_out = chunk.tokens_out
            break
        if chunk.delta:
            full_text_parts.append(chunk.delta)
            yield PipelineEvent(type="text", data={"delta": chunk.delta})

    full_text = "".join(full_text_parts).strip()

    # ── 14. Citation extraction & Zone 2 ─────────────────────────────
    rows, unverified = build_legal_basis(
        full_text, hits,
        max_rows=tier_cfg["zone2_max_rows"],
        enable_verifier=settings.ENABLE_VERIFIER_LOOP and tier == "max",
    )
    yield PipelineEvent(
        type="legal_basis",
        data={"rows": [r.to_dict() for r in rows]},
    )

    # ── 15. CTA (if intent was blocked) ──────────────────────────────
    if ctx_intent_blocked_cta:
        yield PipelineEvent(type="cta", data=ctx_intent_blocked_cta)

    # ── 16. Persist assistant message ────────────────────────────────
    confidence = confidence_band(rows, len(extract_citations(full_text)))
    message = ChatMessage.objects.create(
        conversation=ctx.conversation, role=ChatMessage.ROLE_ASSISTANT,
        content=full_text, intent=classification.primary_intent, mode=mode,
        retrieved_node_ids=[h.node_id for h in hits],
        legal_basis=[r.to_dict() for r in rows],
        citations=[
            {"section": c.section, "rule": c.rule, "raw": c.raw_text}
            for c in extract_citations(full_text)
        ],
        cta=(ctx_intent_blocked_cta or {}),
        prompt_hash=prompt.fingerprint,
        tokens_in=tokens_in, tokens_out=tokens_out,
        model_name=model_name_used,
        latency_ms=int((time.perf_counter() - started) * 1000),
        verdict=confidence["band"],
    )

    # ── 17. Quota deduction ──────────────────────────────────────────
    consume_quota(subject)

    # ── 18. Cache the response ───────────────────────────────────────
    _save_response_cache(
        query_hash=qhash, tier=tier, language=classification.language,
        payload={
            "answer": full_text,
            "legal_basis": [r.to_dict() for r in rows],
            "intent": classification.primary_intent,
            "mode": mode,
            "cta": ctx_intent_blocked_cta or None,
        },
    )

    # ── 19. Citation audit ───────────────────────────────────────────
    if unverified:
        _create_unverified_audits(unverified, message.id)

    record_event(
        "ai.generate", actor=ctx.user,
        payload={
            "tier": tier,
            "intent": classification.primary_intent,
            "provider": decision.provider,
            "provider_tier": decision.tier_for_provider,
            "rationale": decision.rationale,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "verdict": confidence["band"],
            "node_ids": [h.node_id for h in hits],
        },
    )

    yield PipelineEvent(
        type="done",
        data={
            "message_id": message.id,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cached": False,
            "verdict": confidence["band"],
            "model": model_name_used,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
    )


def _run_greeting(ctx: PipelineContext, classification: IntentClassification) -> Iterator[PipelineEvent]:
    """Greeting / small-talk mode.

    Sends back a warm one-liner that nudges the user toward a real question.
    No retrieval, no LLM — fixed text, two language variants.
    """
    if classification.language == "bangla":
        text = (
            "আস্সালামু আলাইকুম। আমি Labor Law Partner — বাংলাদেশের শ্রম আইন ২০০৬ "
            "ও শ্রম বিধি ২০১৫ নিয়ে আপনাকে সাহায্য করব। আপনার কর্মস্থলের পরিস্থিতি "
            "বা আইনি প্রশ্নটি লিখুন — যেমন নোটিশ পিরিয়ড, গ্র্যাচুইটি, ছুটি, "
            "ওভারটাইম, বা চাকরিচ্যুতির নিয়ম।"
        )
    else:
        text = (
            "Hi! I'm Labor Law Partner — I help with the Bangladesh Labour Act 2006, "
            "the Labour Rules 2015, and their amendments. Ask me anything about your "
            "workplace situation — for example notice period, gratuity, leave, overtime, "
            "or termination rules."
        )
    yield PipelineEvent(type="text", data={"delta": text})
    msg = ChatMessage.objects.create(
        conversation=ctx.conversation, role=ChatMessage.ROLE_ASSISTANT,
        content=text, intent=Intent.NOT_A_QUESTION, mode="greeting",
        verdict="greeting",
    )
    yield PipelineEvent(
        type="done",
        data={"message_id": msg.id, "cached": False, "latency_ms": 0},
    )


def _run_clarification(ctx: PipelineContext, classification: IntentClassification) -> Iterator[PipelineEvent]:
    """Mode 4: prompt the user with structured options.

    Two paths:
    1. If the classifier returned scenarios (AMBIGUOUS_SCENARIO with a
       populated `scenarios` field), present those exactly — they are
       phrased to match what the user actually asked. E.g.
           "Do you mean termination-related benefits or termination timeline?"
    2. Otherwise, fall back to the generic four-option picker.
    """
    is_bangla = classification.language == "bangla"
    scenarios = list(classification.scenarios or [])

    if scenarios:
        # Build a natural-sounding opening that names the topic the user asked
        # about, instead of the boilerplate "I want to make sure I guide…".
        topic = classification.domain.replace("_", " ").strip()
        if is_bangla:
            if topic and topic != "general":
                opening = (
                    f"আপনার প্রশ্নটি \"{topic}\" সম্পর্কে — কোন দিকটি জানতে চান?"
                )
            else:
                opening = "ঠিক কোন বিষয়টি জানতে চান বুঝতে চাইছি — নিচের কোনটি আপনার পরিস্থিতির সাথে মেলে?"
        else:
            if topic and topic != "general":
                opening = (
                    f"To answer this precisely, which of these best matches "
                    f"what you mean by \"{topic}\"?"
                )
            else:
                opening = (
                    "To answer this precisely, which of these best matches "
                    "your situation?"
                )
        options = scenarios[:4]
    else:
        # Fallback: original generic picker, retained for backward
        # compatibility when the classifier doesn't emit scenarios.
        if is_bangla:
            options = [
                "এটা আইনগতভাবে সঠিক হয়েছে কি না জানতে চাই",
                "আমি কী ক্ষতিপূরণ বা সুবিধা পাব তা বুঝতে চাই",
                "এটার বিরুদ্ধে আমার কী অপশন আছে জানতে চাই",
                "আমি একটি ডকুমেন্ট রিভিউ করাতে চাই",
            ]
            opening = (
                "আপনাকে সঠিক দিকনির্দেশনা দিতে চাই। এই মুহূর্তে কোনটি সবচেয়ে বেশি সাহায্য করবে?"
            )
        else:
            options = [
                "I want to know if this was done legally",
                "I want to understand what compensation or benefits I'm owed",
                "I want to know my options to challenge this",
                "I want help reviewing a document",
            ]
            opening = (
                "I want to make sure I guide you in the right direction. "
                "What would help you most right now?"
            )

    yield PipelineEvent(type="clarification", data={"opening": opening, "options": options})
    msg = ChatMessage.objects.create(
        conversation=ctx.conversation, role=ChatMessage.ROLE_ASSISTANT,
        content=opening, intent=Intent.NOT_A_QUESTION, mode="clarification",
        clarification_options=options,
    )
    yield PipelineEvent(
        type="done",
        data={"message_id": msg.id, "cached": False, "latency_ms": 0},
    )


def _replay_cached(ctx: PipelineContext, cached: dict, classification: IntentClassification,
                   mode: str, started: float) -> Iterator[PipelineEvent]:
    answer = cached.get("answer", "")
    rows = cached.get("legal_basis", [])
    if answer:
        yield PipelineEvent(type="text", data={"delta": answer})
    if rows:
        yield PipelineEvent(type="legal_basis", data={"rows": rows})
    if cached.get("cta"):
        yield PipelineEvent(type="cta", data=cached["cta"])
    msg = ChatMessage.objects.create(
        conversation=ctx.conversation, role=ChatMessage.ROLE_ASSISTANT,
        content=answer, intent=classification.primary_intent, mode=mode,
        legal_basis=rows, cached=True, verdict="cached",
    )
    yield PipelineEvent(
        type="done",
        data={
            "message_id": msg.id, "cached": True,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
    )