"""
Module: AsyncSentimentAnalyzer (漏斗第二层 - 秒级, 异步)

职责：
  只有通过 FastClassifier 筛选的高价值信号才到这一层。
  调用 LLM API 判断 BULLISH / BEARISH / NEUTRAL。
  如果 API key 未配置，降级为基于关键词的本地估算。

异步设计：不阻塞后续推文的处理。
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
# 供给中断关键词 -> 价格看涨; 恢复/重启 -> 价格看跌
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
    "Output **JSON only** with exactly these fields:\n"
    '{"sentiment": "BULLISH", "confidence": 0.9, "reason": "short one-line summary"}\n'
    "Do NOT output anything outside the JSON object."
)


class AsyncSentimentAnalyzer:
    """
    漏斗第二层。

    优先走 LLM API（OPENAI_API_KEY 配置时）；
    否则用本地规则快速估算（零延迟、零成本）。
    """

    def __init__(self) -> None:
        self._llm_client = None
        if settings.OPENAI_API_KEY:
            # 延迟导入，避免未安装 openai 时报错
            from openai import AsyncOpenAI
            self._llm_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    async def analyze(self, text: str) -> SentimentResult:
        """根据文本返回情绪判断。LLM 可用时走 API，否则本地规则。"""
        if self._llm_client is not None:
            return await self._analyze_llm(text)
        return self._analyze_local(text)

    # ------------------------------------------------------------------
    # LLM 路径 (秒级, IO-bound)
    # ------------------------------------------------------------------
    async def _analyze_llm(self, text: str) -> SentimentResult:
        try:
            resp = await self._llm_client.chat.completions.create(  # type: ignore[union-attr]
                model=settings.LLM_MODEL,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
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
            # LLM 失败 -> 降级本地估算
            result = self._analyze_local(text)
            result.reason = f"LLM fallback ({exc.__class__.__name__}): {result.reason}"
            return result

    # ------------------------------------------------------------------
    # 本地规则路径 (微秒级, CPU-bound)
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
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_json(s: str) -> dict:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if not m:
            raise ValueError("No JSON found in LLM response")
        return json.loads(m.group(0))
