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
- AMBIGUOUS_SCENARIO: USE SPARINGLY. Only when the question literally CANNOT be
                     answered without first knowing which of several incompatible
                     legal pathways the user is on. The question must be so
                     under-specified that any direct answer would mislead.

CRITICAL — do NOT classify as AMBIGUOUS_SCENARIO when:
- The question is about a statutory rule that applies generally
  (notice period, wage rate, working hours, weekly holiday, festival bonus,
   maternity leave duration, gratuity formula, PF contribution rate).
  These have single statutory answers regardless of scenario. → FACTUAL.
- The question asks "what is X" / "how much is X" / "how long is X"
  where X is a defined legal term. → FACTUAL.
- The question has a stated scenario already (even one word like
  "retrenchment", "misconduct", "resignation"). → FACTUAL or PROCEDURAL.
- The user uses concrete numbers, dates, or named entities.

AMBIGUOUS_SCENARIO is correct ONLY when the same surface question maps to
materially different legal outcomes and there is no signal which one applies:
- "safest way to remove an employee" — could be retrenchment, dismissal,
  discharge, lay-off, voluntary separation. Each has distinct procedure.
- "what compensation am I owed" — depends on whether termination, accident,
  unpaid wages, or retrenchment compensation is meant.
- "what's the procedure" with no domain at all.

POSITIVE EXAMPLES (FACTUAL):
- "What is the notice period for termination?" → FACTUAL, domain=termination_notice
- "How much is the maternity leave?" → FACTUAL, domain=maternity_leave
- "What is the gratuity formula?" → FACTUAL, domain=gratuity
- "How many casual leaves per year?" → FACTUAL, domain=leave_entitlement
- "What is the maximum working hours per day?" → FACTUAL, domain=working_hours

POSITIVE EXAMPLES (AMBIGUOUS_SCENARIO):
- "What's the safest way to terminate?" → AMBIGUOUS_SCENARIO
- "What am I entitled to?" (no context) → AMBIGUOUS_SCENARIO
- "What's the procedure?" (no domain) → AMBIGUOUS_SCENARIO

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


import re

# Settled-rule phrasings that ALWAYS have a single statutory answer.
# If the classifier returns AMBIGUOUS_SCENARIO for one of these we override
# to FACTUAL — the user wants the rule, not a scenario picker.
_SETTLED_RULE_PATTERNS = [
    r"\bnotice period\b",
    r"\bworking hours?\b",
    r"\bweekly (holiday|off|leave)\b",
    r"\bfestival (bonus|holiday|leave)\b",
    r"\bmaternity (leave|benefit|pay)\b",
    r"\bpaternity (leave|benefit)\b",
    r"\bcasual leave\b",
    r"\bearned leave\b",
    r"\bsick leave\b",
    r"\bannual leave\b",
    r"\bgratuity (formula|rate|amount|calculation|eligibility)?\b",
    r"\bprovident fund\b",
    r"\bpf (contribution|rate|deduction)\b",
    r"\bovertime (rate|pay|hours)\b",
    r"\bminimum wage\b",
    r"\b(probation|probationary) period\b",
    r"\bretirement age\b",
    r"\bservice book\b",
    r"\bappointment letter\b",
    r"\bwhat is\b.*\b(rate|rule|period|formula|limit|eligibility|requirement)\b",
    r"\bhow (much|many|long)\b",
    # Bangla equivalents
    r"নোটিশ\s*(পিরিয়ড|সময়)",
    r"কর্ম\s*ঘণ্টা",
    r"ছুটি\s*(কত|কতদিন|কয়দিন)",
    r"গ্র্যাচুইটি",
    r"মাতৃত্ব\s*ছুটি",
    r"ন্যূনতম\s*মজুরি",
    r"ওভারটাইম",
]
_SETTLED_RULE_RE = re.compile("|".join(_SETTLED_RULE_PATTERNS), re.IGNORECASE)


def _looks_like_settled_rule(message: str) -> bool:
    """True if the message is asking about a statutory rule with one answer.

    Used as a defensive backstop: even if the LLM classifier returns
    AMBIGUOUS_SCENARIO, we override to FACTUAL for these common phrasings
    so users get the actual rule instead of a 'pick your scenario' prompt.
    """
    return bool(_SETTLED_RULE_RE.search(message or ""))


def select_mode(intent: IntentClassification, has_attachment: bool, message: str = "") -> str:
    """Map an intent classification to a response mode.

    `message` is the original user text — used for the settled-rule backstop
    that overrides over-eager AMBIGUOUS_SCENARIO classifications.
    """
    if has_attachment:
        return "document_review"
    if intent.primary_intent == Intent.NOT_A_QUESTION:
        # Very short / vague inputs still benefit from a clarifying nudge.
        return "clarification"
    # NEW: ambiguous scenarios trigger clarification before answering — BUT
    # only if the surface phrasing isn't a settled-rule question that has
    # one statutory answer regardless of scenario.
    if intent.primary_intent == "AMBIGUOUS_SCENARIO":
        if _looks_like_settled_rule(message):
            logger.info(
                "intent.settled_rule_override",
                extra={"domain": intent.domain, "primary_intent": intent.primary_intent},
            )
            intent.primary_intent = Intent.FACTUAL
            return "direct"
        return "clarification"
    if intent.urgency == "crisis" or intent.domain in (
        "injury", "accident", "termination_event", "workplace_violence",
    ):
        return "situation"
    if intent.primary_intent in (Intent.ADVISORY, Intent.PROCEDURAL):
        if any(k in intent.domain for k in ("incident", "dispute", "injury")):
            return "situation"
    return "direct"