"""
Module: AsyncSentimentAnalyzer (Layer 2 - Second-level, async)

Responsibilities:
  Only high-value signals passing FastClassifier reach this layer.
  Call LLM API to determine BULLISH / BEARISH / NEUTRAL.
  If API key not configured, degrade to keyword-based local estimation.

Async design: doesn't block processing of subsequent posts.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from app.config import settings


class SentimentResult:
    __slots__ = ("sentiment", "confidence", "reason")

    def __init__(
        self,
        sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL",
        confidence: float = 0.5,
        reason: str = "",
    ) -> None:
        self.sentiment = sentiment
        self.confidence = confidence
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "sentiment": self.sentiment,
            "confidence": self.confidence,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Supply disruption keywords -> bullish on price; recovery/restart -> bearish on price
# ---------------------------------------------------------------------------
_BULLISH_RE = re.compile(
    r"(?i)(outage|strike|force majeure|shutdown|suspension|explosion|leak|evacuate|work ban|delay)",
)
_BEARISH_RE = re.compile(
    r"(?i)(restart|resume|ramp.up|back online|reopen|commissioning|surplus|inventory build)",
)

SYSTEM_PROMPT = (
    "You are an LNG/Energy Trading Assistant.\n"
    "Analyze the following headline or tweet.\n"
    "Determine if it is BULLISH, BEARISH, or NEUTRAL for JKM/TTF spot prices.\n"
    "{market_context}\n"
    "Output **JSON only** with exactly these fields:\n"
    '{{"sentiment": "BULLISH", "confidence": 0.9, "reason": "short one-line summary"}}\n'
    "If the event causes divergent outcomes between US (HH) and EU/Asia (TTF/JKM), you MUST explicitly articulate the bifurcation in the reason statement.\n"
    "Do NOT output anything outside the JSON object or conversational fillers."
)


class AsyncSentimentAnalyzer:
    """
    Layer 2.

    Prefer LLM API (when OPENAI_API_KEY configured);
    otherwise use local rules for quick estimation (zero latency, zero cost).
    """

    def __init__(self) -> None:
        self._llm_client = None
        if settings.OPENAI_API_KEY:
            # Lazy import to avoid error when openai not installed
            from openai import AsyncOpenAI
            self._llm_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    # ------------------------------------------------------------------
    # Public Interface
    # ------------------------------------------------------------------
    async def analyze(self, text: str, state_snapshot=None) -> SentimentResult:
        """Return sentiment analysis based on text and market snapshot. Use API if LLM available, else local rules."""
        if self._llm_client is not None:
            return await self._analyze_llm(text, state_snapshot)
        return self._analyze_local(text)

    # ------------------------------------------------------------------
    # LLM Path (second-level, IO-bound)
    # ------------------------------------------------------------------
    async def _analyze_llm(self, text: str, state_snapshot=None) -> SentimentResult:
        context_str = state_snapshot.get_context_string() if state_snapshot else "Market Context: None available."
        formatted_prompt = SYSTEM_PROMPT.format(market_context=context_str)
        try:
            resp = await self._llm_client.chat.completions.create(  # type: ignore[union-attr]
                model=settings.LLM_MODEL,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": formatted_prompt},
                    {"role": "user", "content": text},
                ],
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or "{}"
            data = self._extract_json(raw)

            sentiment = str(data.get("sentiment", "NEUTRAL")).upper()
            if sentiment not in {"BULLISH", "BEARISH", "NEUTRAL"}:
                sentiment = "NEUTRAL"

            return SentimentResult(
                sentiment=sentiment,  # type: ignore[arg-type]
                confidence=float(data.get("confidence", 0.5)),
                reason=str(data.get("reason", "")),
            )
        except Exception as exc:
            # LLM failed -> degrade to local estimation
            result = self._analyze_local(text)
            result.reason = f"LLM fallback ({exc.__class__.__name__}): {result.reason}"
            return result

    # ------------------------------------------------------------------
    # Local Rules Path (microsecond-level, CPU-bound)
    # ------------------------------------------------------------------
    @staticmethod
    def _analyze_local(text: str) -> SentimentResult:
        bull = bool(_BULLISH_RE.search(text))
        bear = bool(_BEARISH_RE.search(text))

        if bull and not bear:
            return SentimentResult("BULLISH", 0.75, "Supply disruption keyword detected")
        if bear and not bull:
            return SentimentResult("BEARISH", 0.70, "Supply recovery keyword detected")
        if bull and bear:
            return SentimentResult("NEUTRAL", 0.40, "Mixed signals in text")
        return SentimentResult("NEUTRAL", 0.30, "No strong directional keyword")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_json(s: str) -> dict:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if not m:
            raise ValueError("No JSON found in LLM response")
        return json.loads(m.group(0))
