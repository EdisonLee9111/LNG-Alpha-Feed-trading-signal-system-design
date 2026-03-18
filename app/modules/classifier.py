"""
Module: FastClassifier (Layer 1 - Millisecond-level)

Responsibilities:
  1. Noise filtering (Hard Filter) — discard directly when noise keywords are encountered
  2. Category Matching — traverse rule library, classify on hit
  3. Asset Mapping — automatically map categories to ticker lists

No network / LLM calls, pure CPU, completes in microseconds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import ASSET_MAP, DEFAULT_TICKERS, NOISE_PATTERN, RULES


@dataclass(frozen=True)
class ClassifiedSignal:
    """Structured signal output from FastClassifier."""
    category: str                          # e.g. "LNG_SUPPLY", "JAPAN_POWER"
    tickers: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)  # which rule categories matched
    raw_text: str = ""


class FastClassifier:
    """
    Layer 1: pure Regex hard classification + asset mapping.

    Usage:
        fc = FastClassifier()
        signal = fc.classify("URGENT: Gorgon LNG outage ...")
        # signal.category == "LNG_SUPPLY"
        # signal.tickers == ["UNG", "TTF=F"]
    """

    def __init__(self) -> None:
        self._noise_re = re.compile(NOISE_PATTERN)
        self._rules = {cat: re.compile(pat) for cat, pat in RULES.items()}

    def classify(self, text: str) -> ClassifiedSignal | None:
        """
        Input: raw text
        Output: ClassifiedSignal or None (noise is discarded)
        """
        # ---- 1. Noise filtering ----
        if self._noise_re.search(text):
            return None

        # ---- 2. Traverse rule library, find all matched categories ----
        matched_rules: list[str] = []
        all_tickers: list[str] = []

        for category, pattern in self._rules.items():
            if pattern.search(text):
                matched_rules.append(category)
                all_tickers.extend(ASSET_MAP.get(category, []))

        # ---- 3. No core rules matched → discard (don't alert) ----
        if not matched_rules:
            return None

        primary_category = matched_rules[0]

        # ---- 4. Deduplicate ----
        unique_tickers = list(dict.fromkeys(all_tickers))  # preserve-order dedup

        return ClassifiedSignal(
            category=primary_category,
            tickers=unique_tickers,
            matched_rules=matched_rules,
            raw_text=text,
        )
