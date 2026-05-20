"""Tier constants — single source of truth for tier names and intent taxonomy.

Mirrors `src/lib/ai/framework-types.ts` from the existing frontend.
"""
from __future__ import annotations

from typing import TypedDict


# Intent taxonomy
class Intent:
    FACTUAL = "FACTUAL"
    ADVISORY = "ADVISORY"
    DRAFTING = "DRAFTING"
    CALCULATION = "CALCULATION"
    PROCEDURAL = "PROCEDURAL"
    CROSS_DOMAIN = "CROSS_DOMAIN"
    PRODUCT_INQUIRY = "PRODUCT_INQUIRY"
    NOT_A_QUESTION = "NOT_A_QUESTION"
    AMBIGUOUS_SCENARIO = "AMBIGUOUS_SCENARIO"   

    ALL: tuple[str, ...] = (
        FACTUAL, ADVISORY, DRAFTING, CALCULATION,
        PROCEDURAL, CROSS_DOMAIN, PRODUCT_INQUIRY, NOT_A_QUESTION,
    )


# Tier names
class Tier:
    FREE_GUEST = "free_guest"
    FREE_SUBSCRIBED = "free_subscribed"
    MINI = "mini"
    MAX = "max"

    ALL: tuple[str, ...] = (FREE_GUEST, FREE_SUBSCRIBED, MINI, MAX)


class CTAMessage(TypedDict):
    text: str
    text_bn: str
    target_tier: str | None


CTA_MESSAGES: dict[str, CTAMessage] = {
    Tier.FREE_GUEST: {
        "text": "Sign up free for 15 chats/day →",
        "text_bn": "বিনামূল্যে সাইন আপ করুন → প্রতিদিন ১৫টি চ্যাট",
        "target_tier": Tier.FREE_SUBSCRIBED,
    },
    Tier.FREE_SUBSCRIBED: {
        "text": "Need drafting, memory & more? → Mini ১৪৯৳/mo",
        "text_bn": "ড্রাফটিং, মেমোরি ও আরও বেশি? → Mini ১৪৯৳/মাস",
        "target_tier": Tier.MINI,
    },
    Tier.MINI: {
        "text": "Need advisory, file analysis & persistent memory? → Max ২৯৯৳/mo",
        "text_bn": "পরামর্শ, ফাইল বিশ্লেষণ ও স্থায়ী মেমোরি? → Max ২৯৯৳/মাস",
        "target_tier": Tier.MAX,
    },
}


DEFAULT_TIER_CONFIGS = [
    {
        "tier": Tier.FREE_GUEST,
        "label": "Free Guest",
        "label_bn": "ফ্রি গেস্ট",
        "daily_request_limit": 5,
        "rate_limit_per_min": 5,
        "session_response_cap": None,
        "allowed_intents": [Intent.FACTUAL, Intent.PROCEDURAL, Intent.PRODUCT_INQUIRY],
        "file_upload_allowed": False,
        "cross_domain_allowed": False,
        "advisory_allowed": False,
        "memory_window_days": 0,
        "zone2_max_rows": 3,
        "price_bdt": None,
    },
    {
        "tier": Tier.FREE_SUBSCRIBED,
        "label": "Free Subscribed",
        "label_bn": "ফ্রি সাবস্ক্রাইবড",
        "daily_request_limit": 15,
        "rate_limit_per_min": 10,
        "session_response_cap": None,
        "allowed_intents": [
            Intent.FACTUAL, Intent.PROCEDURAL,
            Intent.CALCULATION, Intent.PRODUCT_INQUIRY,
        ],
        "file_upload_allowed": False,
        "cross_domain_allowed": False,
        "advisory_allowed": False,
        "memory_window_days": 1,
        "zone2_max_rows": 3,
        "price_bdt": None,
    },
    {
        "tier": Tier.MINI,
        "label": "Mini — ১৪৯৳/mo",
        "label_bn": "মিনি — ১৪৯৳/মাস",
        "daily_request_limit": 100,
        "rate_limit_per_min": 20,
        "session_response_cap": None,
        "allowed_intents": [
            Intent.FACTUAL, Intent.ADVISORY, Intent.DRAFTING,
            Intent.CALCULATION, Intent.PROCEDURAL,
            Intent.CROSS_DOMAIN, Intent.PRODUCT_INQUIRY,
        ],
        "file_upload_allowed": False,
        "cross_domain_allowed": True,
        "advisory_allowed": False,
        "memory_window_days": 7,
        "zone2_max_rows": 4,
        "price_bdt": 149,
    },
    {
        "tier": Tier.MAX,
        "label": "Max — ২৯৯৳/mo",
        "label_bn": "ম্যাক্স — ২৯৯৳/মাস",
        "daily_request_limit": 500,
        "rate_limit_per_min": 30,
        "session_response_cap": None,
        "allowed_intents": list(Intent.ALL[:-1]),  # all except NOT_A_QUESTION
        "file_upload_allowed": True,
        "cross_domain_allowed": True,
        "advisory_allowed": True,
        "memory_window_days": 90,
        "zone2_max_rows": 6,
        "price_bdt": 299,
    },
]
