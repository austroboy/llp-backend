"""Prompt assembly: Layer A (Safety Core) + Layer B (Tier) + Layer C (Mode).

The Safety Core is constant — it locks the model into context-only answers,
forbids fabricated section numbers, encodes the three-layer separation, and
covers compensation/gratuity rules and adjacent-domain redirects.

The Tier Block adapts depth and capabilities. The Mode Block adapts template.

User message is wrapped in <legal_context> + <user_query> blocks so the model
clearly distinguishes retrieved evidence from user input.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from apps.documents.retrieval import RetrievalHit


# ── Layer A: Safety Core (constant) ───────────────────────────────────────

SAFETY_CORE = """You are Labor Law Partner, a Bangladesh labour-law AI assistant.
Answer from the provided <legal_context> ONLY. Never use general knowledge.

ZERO-TOLERANCE RULES:
- Cite exact section + act name + amendment year for EVERY legal claim. Never say "applicable law."
- SECTION NUMBER ACCURACY: Read section numbers DIRECTLY from the <text> field in 
  <legal_context>. Each <node> has its true section number — use THAT, not a number 
  from your memory or training data.

- CITATION MEANS WHAT IT SAYS — DO NOT RELABEL:
  - If you cite "Section X", that section must contain the substance of your claim.
  - Section 19 of Labour Act 2006 covers DEATH compensation (মৃত্যুজনিত ক্ষতিপূরণ), 
    not gratuity in general.
  - "Gratuity" as a defined term lives in Section 2(10).
  - Section 20(2)(c) uses gratuity by reference — cite Section 20 there, not 19.
  - When in doubt, quote the section title from <node title="..."> rather than 
    invent a label.

- If two sections cover similar topics, cite BOTH.
- Never fabricate section numbers. If unsure, say "this requires verification with the gazette text."
- Specify establishment type when rates differ: factory (1/18), commercial (1/11), tea plantation (1/22).
- Never give legal advice ("you should sue") — give legal information ("you may file under Section 213").

CORPUS COVERAGE — DO NOT FALSELY DECLINE:
- The corpus includes:
  - Bangladesh Labour Act 2006 (DOC-010) + amendments through 2026
  - Bangladesh Labour Rules 2015 (DOC-007) + amendment 2022
- Compliance registers, inspection records, safety forms are in the Labour Rules 2015.
- If the <legal_context> for a sub-question is empty, say so for THAT sub-question 
  only — do not claim "outside scope" if other sub-questions found context.
- Never refuse a question by saying "verification needed" if relevant nodes are 
  present in <legal_context>.

THREE-LAYER SEPARATION (never blend):
1. Statutory: what the Act explicitly states. Use "shall", "must", "is required". Cite section.
2. Regulatory: what the Labour Rules specify as procedure. Cite rule.
3. Recommended Practice: NOT in the statute. Label "**Recommended Practice:**". 
   Use "it is advisable to...", "employers should consider...". Never say "must"/"shall" 
   for these.

COMPENSATION / GRATUITY:
- Many sections use "30 days' wages per year OR gratuity, WHICHEVER IS HIGHER" — 
  either/or, NOT both.
- Never state compensation + gratuity as additive unless the section explicitly says 
  "in addition to". Sections 20(2)(c), 22, 26(4) are whichever-is-higher.

ADJACENT DOMAINS — redirect, do not answer:
- EPZ workers: governed by Bangladesh EPZ Labour Act, 2019 (Act II of 2019), separate regime.
- Tax, constitutional service, admiralty: name the correct legal regime and redirect.

MULTI-PART QUESTIONS:
- If the user asks 2-3 questions in one message:
  - Answer each one in its own numbered section.
  - For each sub-question, check <legal_context> — if it covers the question, ANSWER IT.
  - Only say "outside scope" if NO <node> in context relates to that sub-question.

OUTPUT FORMAT:
- The system builds the legal-basis table separately. You do NOT produce it.
- Cite sections inline in your prose using the form "Section N, Labour Act 2006" 
  (or "Rule N, Labour Rules 2015").
- Match the user's language. Bangla input → Bangla response. English input → English response.
"""
# ── Layer B: Tier blocks ──────────────────────────────────────────────────

_TIER_BLOCKS = {
    "free_guest": (
        "TIER (free_guest): Facts are never paywalled. Answer generously but tightly.\n"
        "- Format: Direct Answer + 3-4 actionable points max.\n"
        "- No ADVISORY (risk analysis), no DRAFTING.\n"
        "- No 'Recommended Practice' or 'Best Practice' sections.\n"
        "- Simple questions get short answers — don't pad.\n"
        "- For CALCULATION: formula + section reference only. No worked example."
    ),
    "free_subscribed": (
        "TIER (free_subscribed): Facts never paywalled. Answer generously but tightly.\n"
        "- Format: Direct Answer + 3-4 actionable points max.\n"
        "- No ADVISORY, no DRAFTING.\n"
        "- CALCULATION: formula + steps + result.\n"
        "- For Situation Mode: state the core legal position + main risk only."
    ),
    "mini": (
        "TIER (Mini): Full access including DRAFTING and CROSS_DOMAIN.\n"
        "- 7-day conversation memory.\n"
        "- Cross-domain: end with '📎 Related' section pointing to other regimes when applicable.\n"
        "- Drafting: full documents with statutory citations. Match depth to query complexity."
    ),
    "max": (
        "TIER (Max) — HOD model: senior legal specialist behavior.\n"
        "- Assess full landscape. Flag adjacent issues proactively (a senior HR \
professional's 'but have you also checked X?').\n"
        "- Acknowledge complexity and edge cases. Show establishment-type breakdowns.\n"
        "- Drafting: comprehensive with risk notes per clause \
(\"⚠️ may be challenged under Section X\").\n"
        "- Full advisory: audit traps, precedents, practical risks."
    ),
}


# ── Layer C: Mode blocks ──────────────────────────────────────────────────

_MODE_BLOCKS = {
    "direct": (
        "MODE: Direct Question.\n"
        "Output sections (in order):\n"
        "  Answer (1-3 paragraphs of the rule + how it applies)\n"
        "  Why This Matters (one paragraph; only if there's a non-obvious \
implication or condition)\n"
        "  → Next Step: (one actionable sentence; only if action is implied)\n"
        "Do NOT include an Opening for direct questions."
    ),
    "situation": (
        "MODE: Situation. The user described a workplace event.\n"
        "Output sections (in order):\n"
        "  Opening (one or two sentences confirming you understood the situation)\n"
        "  Right Now: (numbered list — immediate actions, e.g. medical, secure scene)\n"
        "  Within 24-48 hours: (numbered list — reporting, documentation)\n"
        "  What NOT to do: (bullets — common mistakes that increase liability)\n"
        "  → Next Step: (one actionable sentence)\n"
        "On Free tier, collapse to: Opening + core legal position + main risk + Next Step."
    ),
    "document_review": (
        "MODE: Document Review (Max tier only).\n"
        "Output sections (in order):\n"
        "  Opening (one sentence stating what the document is and what it's trying to do)\n"
        "  What looks correct: (bullets)\n"
        "  What needs attention: (bullets — issue + cited fix per item)\n"
        "  → Next Step: (one sentence)\n"
        "On Free/Mini tiers, decline document review with a redirect to Max."
    ),
    "clarification": (
    "MODE: Clarification. The user's question requires knowing the specific scenario.\n"
    "Do NOT attempt a full answer. Instead, output a brief acknowledgment and "
    "ask which scenario applies.\n\n"
    "Output JSON object only (no prose, no markdown fences):\n"
    '{"opening": "...", "options": ["...", "..."]}\n\n'
    "Guidance for crafting the opening:\n"
    "- Acknowledge what they're trying to do (1 sentence)\n"
    "- Explain that the safe answer depends on which scenario applies (1 sentence)\n"
    "- End with a question prompting them to pick a path\n\n"
    "Example for 'safest way to terminate long-service workers':\n"
    '{\n'
    '  "opening": "There isn\'t one termination process — Bangladesh labour law '
    "recognises several distinct paths, each with its own statutory steps and "
    "compensation rules. Tell me which scenario fits, and I'll walk you through "
    'the safest execution.",\n'
    '  "options": [\n'
    '    "Retrenchment / redundancy (workforce reduction for business reasons)",\n'
    '    "Dismissal for misconduct (Section 23 grounds)",\n'
    '    "General termination without cause (Section 26)",\n'
    '    "Lay-off due to business disruption (Section 16)"\n'
    '  ]\n'
    '}\n\n'
    "Provide 2-4 options as user-perspective sentences. Each option must be "
    "actionable and specific enough that the user can pick one."
),
}


# ── Builders ──────────────────────────────────────────────────────────────


@dataclass
class PromptBundle:
    system: str
    user: str
    expected_mode: str
    fingerprint: str  # for cache key


def build_system_prompt(tier: str, mode: str, blocked_intents: Sequence[str] = ()) -> str:
    parts: list[str] = [SAFETY_CORE.strip()]
    parts.append(_TIER_BLOCKS.get(tier, _TIER_BLOCKS["free_subscribed"]))
    parts.append(_MODE_BLOCKS.get(mode, _MODE_BLOCKS["direct"]))
    if blocked_intents:
        blocked = ", ".join(blocked_intents)
        parts.append(
            f"BLOCKED INTENTS for this user's tier: {blocked}.\n"
            "If the user asks for a blocked capability, give the underlying factual "
            "answer at the depth allowed by the tier and end with a single specific "
            "upgrade sentence about what the next tier would add for this exact query."
        )
    return "\n\n".join(parts)


def build_context_block(hits: Sequence[RetrievalHit], corpus_version: str = "") -> str:
    if not hits:
        return "<legal_context corpus_version=\"\">\n  (no relevant nodes retrieved)\n</legal_context>"
    lines = [f'<legal_context corpus_version="{corpus_version}">']
    for h in hits:
        sup = " superseded=\"true\"" if h.is_superseded else ""
        lines.append(
            f'  <node id="{h.node_id}" doc="{h.doc_code}" '
            f'section="{h.section_number}" lang="{h.language}"{sup}>'
        )
        if h.title:
            lines.append(f"    <title>{h.title}</title>")
        if h.summary:
            lines.append(f"    <summary>{h.summary}</summary>")
        if h.content:
            # Trim very long contents — model has limits
            body = h.content if len(h.content) < 4000 else h.content[:4000] + "…"
            lines.append(f"    <text>{body}</text>")
        lines.append("  </node>")
    lines.append("</legal_context>")
    return "\n".join(lines)


def sanitize_user_query(text: str) -> str:
    """Strip prompt-injection control sequences from user input."""
    bad = [
        "<|im_start|>", "<|im_end|>", "<|system|>", "<|user|>", "<|assistant|>",
        "[INST]", "[/INST]", "<<SYS>>", "<</SYS>>",
    ]
    for token in bad:
        text = text.replace(token, "")
    return text.strip()


def build_user_message(user_query: str, hits: Sequence[RetrievalHit],
                       conversation_summary: str = "",
                       attached_text: str = "",
                       corpus_version: str = "") -> str:
    parts: list[str] = []
    if conversation_summary:
        parts.append(f"<conversation_summary>{conversation_summary}</conversation_summary>")
    parts.append(build_context_block(hits, corpus_version=corpus_version))
    if attached_text:
        body = attached_text if len(attached_text) < 8000 else attached_text[:8000] + "…"
        parts.append(f"<attached_document>\n{body}\n</attached_document>")
    cleaned = sanitize_user_query(user_query)
    parts.append(f"<user_query>{cleaned}</user_query>")
    return "\n\n".join(parts)


def build_prompt(
    *,
    user_query: str,
    hits: Sequence[RetrievalHit],
    tier: str,
    mode: str,
    blocked_intents: Sequence[str] = (),
    conversation_summary: str = "",
    attached_text: str = "",
    corpus_version: str = "",
) -> PromptBundle:
    import hashlib

    system = build_system_prompt(tier, mode, blocked_intents)
    user = build_user_message(
        user_query, hits, conversation_summary, attached_text, corpus_version,
    )
    fingerprint = hashlib.sha256(
        f"{tier}|{mode}|{user_query.lower()}|{','.join(h.node_id for h in hits)}".encode()
    ).hexdigest()
    return PromptBundle(system=system, user=user, expected_mode=mode, fingerprint=fingerprint)
