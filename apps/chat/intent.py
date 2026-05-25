"""Intent classifier. Uses Claude Haiku for fast, cheap structured output."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
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
    # AMBIGUOUS_SCENARIO populates this with 2-4 scenarios the question
    # could plausibly map to. Used to drive the contextual clarification
    # question (e.g. "Do you mean termination-related benefits or
    # termination timeline?"). Empty for non-ambiguous intents.
    scenarios: list[str] = field(default_factory=list)


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
- "What's the safest way to terminate?" → AMBIGUOUS_SCENARIO,
   scenarios: ["Termination of a permanent worker for misconduct",
               "Retrenchment due to redundancy",
               "Discharge for inefficiency or ill health",
               "Termination of a probationary worker"]
- "What am I entitled to after my job ended?" (no context) → AMBIGUOUS_SCENARIO,
   scenarios: ["Termination compensation and notice pay",
               "Gratuity for completed service",
               "Provident fund withdrawal",
               "Unpaid wages and accrued leave encashment"]
- "Tell me about termination" (vague) → AMBIGUOUS_SCENARIO,
   scenarios: ["Termination notice period and timeline",
               "Termination-related compensation and benefits",
               "Grounds and procedure for lawful termination",
               "Worker remedies against wrongful termination"]
- "What's the procedure?" (no domain) → AMBIGUOUS_SCENARIO,
   scenarios: ["Filing a grievance with the labour court",
               "Registering a trade union",
               "Disciplinary action procedure against a worker",
               "Termination procedure under the Labour Act"]

When you emit AMBIGUOUS_SCENARIO, the `scenarios` field MUST contain 2-4
distinct, mutually exclusive options that read like full clarifying answers
(not 1-2 word labels). Each scenario should be a phrase the user could
recognise as describing their real situation.

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

    raw_scenarios = data.get("scenarios") or []
    if isinstance(raw_scenarios, list):
        scenarios = [str(s).strip() for s in raw_scenarios if str(s).strip()][:4]
    else:
        scenarios = []

    return IntentClassification(
        primary_intent=data.get("primary_intent", Intent.FACTUAL),
        intents=data.get("intents", [Intent.FACTUAL]),
        urgency=data.get("urgency", "general"),
        perspective=data.get("perspective", "neutral"),
        language=data.get("language", "english"),
        domain=data.get("domain", "general"),
        requires_file=data.get("requires_file", False),
        is_followup=data.get("is_followup", False),
        scenarios=scenarios,
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

# Greeting / small-talk patterns. When a message is just a greeting we want
# to reply warmly with a one-liner that invites a real question — NOT push a
# legal-clarification picker. Worker who says "hi" gets a friendly nudge,
# not a 4-button "I want to know if this was done legally" wall.
_GREETING_PATTERNS = [
    r"^\s*(hi|hello|hey|yo|hii+|heya|sup|wassup|what'?s up|howdy)\b",
    r"^\s*(hi|hello|hey)\s+(there|llp|bot|partner)\b",
    r"\bhow\s+(are|r)\s+(you|u|ya)\b",
    r"\bwhat'?s up\b",
    r"\bgood\s+(morning|afternoon|evening|night)\b",
    r"\bnice to meet you\b",
    r"\bthank\s*(s|you)\b\s*$",  # bare "thanks", not "thanks, but..."
    r"\bthx\b\s*$",
    # Bangla greetings
    r"^\s*(আসসালামু আলাইকুম|আস্সালামু আলাইকুম|সালাম)",
    r"^\s*(হ্যালো|হাই|নমস্কার|আদাব)",
    r"\bকেমন\s+আছেন\b",
    r"\bকেমন\s+আছো\b",
    r"\bসুপ্রভাত\b",
    r"\bশুভ\s+(সকাল|সন্ধ্যা|রাত্রি|রাত)\b",
    r"\bধন্যবাদ\b\s*$",
]
_GREETING_RE = re.compile("|".join(_GREETING_PATTERNS), re.IGNORECASE)


def _looks_like_greeting(message: str) -> bool:
    """True if the message is a greeting / small-talk rather than a question.

    Tightening rule: even if a greeting word is present, treat the whole
    message as a real question if it contains a '?' or any obvious legal/HR
    domain keyword. This handles things like 'Hi, what is the notice
    period?' which are real questions wrapped in pleasantries.
    """
    if not message:
        return False
    text = message.strip()
    if len(text) > 60:
        return False
    # If a question mark is present anywhere AND the message isn't itself
    # something like "How are you?", treat it as a question — except for the
    # pure greeting forms that legitimately end with "?".
    has_q = "?" in text
    # Any legal/HR domain keyword anywhere → it's a question, not a greeting.
    domain_re = re.compile(
        r"\b(notice|period|leave|gratuity|maternity|overtime|salary|wage|"
        r"terminat|resign|dismiss|retrench|fire|fired|compensation|benefit|"
        r"work permit|visa|holiday|provident|pf|bonus|contract|employ|labor|"
        r"labour|hr|হোলিডে|ছুটি|বেতন|মজুরি|চাকরি|নোটিশ|গ্র্যাচুইটি|"
        r"কর্মী|শ্রম|আইন|কর্মঘণ্টা|মাতৃত্ব|ওভারটাইম)\b",
        re.IGNORECASE,
    )
    if domain_re.search(text):
        return False
    # Bare "how are you?" still counts as greeting, but "hi, what notice" doesn't
    if has_q:
        # Only allow the explicit how-are-you / what's-up greeting questions
        pure_greeting_q = re.compile(
            r"^\s*(hi|hello|hey|hii+)?\s*,?\s*"
            r"(how\s+(are|r)\s+(you|u|ya)|what'?s up|how'?s it going|"
            r"কেমন\s+আছেন|কেমন\s+আছো)\s*\??\s*$",
            re.IGNORECASE,
        )
        if not pure_greeting_q.search(text):
            return False
    return bool(_GREETING_RE.search(text))


def _looks_like_settled_rule(message: str) -> bool:
    """True if the message is asking about a statutory rule with one answer.

    Used as a defensive backstop: even if the LLM classifier returns
    AMBIGUOUS_SCENARIO, we override to FACTUAL for these common phrasings
    so users get the actual rule instead of a 'pick your scenario' prompt.
    """
    return bool(_SETTLED_RULE_RE.search(message or ""))


def select_mode(intent: IntentClassification, has_attachment: bool, message: str = "") -> str:
    """Map an intent classification to a response mode.

    `message` is the original user text — used for greeting detection and
    the settled-rule backstop that overrides over-eager AMBIGUOUS_SCENARIO
    classifications.
    """
    if has_attachment:
        return "document_review"
    # Greetings and small-talk get a warm one-liner reply, not the legal
    # clarification picker. Check this BEFORE the NOT_A_QUESTION branch
    # because Haiku usually classifies "hi how are you" as NOT_A_QUESTION.
    if _looks_like_greeting(message):
        logger.info(
            "intent.greeting_detected",
            extra={"primary_intent": intent.primary_intent},
        )
        return "greeting"
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