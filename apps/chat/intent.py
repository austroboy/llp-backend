"""Intent classifier. Uses Claude Haiku for fast, cheap structured output."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from django.conf import settings

from apps.subscriptions.constants import Intent

logger = logging.getLogger(__name__)


@dataclass
class IntentClassification:
    primary_intent: str
    intents: list[str]
    urgency: Literal["general", "time_sensitive", "crisis"]
    perspective: Literal["worker", "employer", "neutral"]
    language: Literal["english", "bangla", "mixed"]
    domain: str  # e.g. "termination", "compensation", "leave"
    requires_file: bool
    is_followup: bool


_CLASSIFIER_SYSTEM = """You are an intent classifier for Labor Law Partner.
Classify the user's message into one of these intent types:
- FACTUAL: factual question about a specific rule (single answer exists)
- ADVISORY: asks for risk analysis or strategic recommendation
- DRAFTING: asks to draft a document (letter, contract, notice)
- CALCULATION: needs a numeric calculation (compensation, gratuity, leave)
- PROCEDURAL: how-to / process question (single procedure exists)
- CROSS_DOMAIN: spans multiple legal regimes (e.g. labour + tax)
- PRODUCT_INQUIRY: asks about LLP itself (what tier, pricing, capabilities)
- NOT_A_QUESTION: greeting, vague statement, or input too short to classify
- AMBIGUOUS_SCENARIO: question requires knowing the specific scenario to give a useful answer
                     (e.g. "safest way to terminate" — which kind? retrenchment, misconduct, lay-off, general?
                      "what compensation am I entitled to" — for what event?
                      "what's the procedure" — for what action?)

For AMBIGUOUS_SCENARIO, the user is asking a question that has multiple correct answers
depending on which scenario applies. Do NOT classify as AMBIGUOUS_SCENARIO if the user
has already specified the scenario (e.g. "safest way to retrench 50 workers" is FACTUAL/PROCEDURAL,
not AMBIGUOUS_SCENARIO).

Output STRICT JSON only — no prose, no markdown fences. Schema:
{
  "primary_intent": "<one of the intents above>",
  "intents": ["<all matching intents>"],
  "urgency": "general | time_sensitive | crisis",
  "perspective": "worker | employer | neutral",
  "language": "english | bangla | mixed",
  "domain": "<short domain label>",
  "requires_file": false,
  "is_followup": false,
  "scenarios": ["<if AMBIGUOUS_SCENARIO, list the 2-4 scenarios that could apply>"]
}"""

def _heuristic_fallback(message: str) -> IntentClassification:
    """Cheap fallback when the LLM call is unavailable or fails."""
    text = message.strip()
    if len(text) < 12:
        return IntentClassification(
            primary_intent=Intent.NOT_A_QUESTION,
            intents=[Intent.NOT_A_QUESTION],
            urgency="general", perspective="neutral",
            language="english", domain="unknown",
            requires_file=False, is_followup=False,
        )
    lower = text.lower()
    intent = Intent.FACTUAL
    if any(k in lower for k in ("draft", "write a letter", "letter for", "notice for")):
        intent = Intent.DRAFTING
    elif any(k in lower for k in ("calculate", "how much", "amount", "compensation")):
        intent = Intent.CALCULATION
    elif any(k in lower for k in ("how do i", "how can i", "procedure", "process to")):
        intent = Intent.PROCEDURAL
    elif any(k in lower for k in ("should i", "risk", "advise", "advice")):
        intent = Intent.ADVISORY
    return IntentClassification(
        primary_intent=intent, intents=[intent],
        urgency="general", perspective="neutral",
        language="bangla" if any(c >= "অ" and c <= "৯" for c in text) else "english",
        domain="general", requires_file=False, is_followup=False,
    )


def classify_intent(message: str) -> IntentClassification:
    """Call Claude Haiku for structured intent classification."""
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY == "test-key":
        return _heuristic_fallback(message)

    try:
        from anthropic import Anthropic
    except ImportError:
        logger.warning("anthropic package not installed; using heuristic fallback")
        return _heuristic_fallback(message)

    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        resp = client.messages.create(
            model=settings.ANTHROPIC_MODEL_HAIKU,
            max_tokens=200,
            system=_CLASSIFIER_SYSTEM,
            messages=[{"role": "user", "content": message[:1500]}],
        )
    except Exception:  # noqa: BLE001
        logger.exception("intent_classifier_call_failed")
        return _heuristic_fallback(message)

    raw = "".join(block.text for block in resp.content if hasattr(block, "text"))
    try:
        cleaned = raw.strip().strip("`").replace("json", "", 1).strip()
        data = json.loads(cleaned)
    except (ValueError, json.JSONDecodeError):
        # Try to find a JSON object in the text
        import re
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                data = json.loads(m.group(0))
            except (ValueError, json.JSONDecodeError):
                return _heuristic_fallback(message)
        else:
            return _heuristic_fallback(message)

    return IntentClassification(
        primary_intent=data.get("primary_intent", Intent.FACTUAL),
        intents=data.get("intents", [Intent.FACTUAL]),
        urgency=data.get("urgency", "general"),
        perspective=data.get("perspective", "neutral"),
        language=data.get("language", "english"),
        domain=data.get("domain", "general"),
        requires_file=data.get("requires_file", False),
        is_followup=data.get("is_followup", False),
    )


def select_mode(intent: IntentClassification, has_attachment: bool) -> str:
    """Map an intent classification to a response mode."""
    if has_attachment:
        return "document_review"
    if intent.primary_intent == Intent.NOT_A_QUESTION:
        return "clarification"
    # NEW: ambiguous scenarios trigger clarification before answering
    if intent.primary_intent == "AMBIGUOUS_SCENARIO":
        return "clarification"
    if intent.urgency == "crisis" or intent.domain in (
        "injury", "accident", "termination_event", "workplace_violence",
    ):
        return "situation"
    if intent.primary_intent in (Intent.ADVISORY, Intent.PROCEDURAL):
        if any(k in intent.domain for k in ("incident", "dispute", "injury")):
            return "situation"
    return "direct"