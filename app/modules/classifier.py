"""
Module: FastClassifier (漏斗第一层 - 毫秒级)

职责：
  1. 噪音过滤 (Hard Filter) — 碰到噪音关键词直接丢弃
  2. 类别匹配 (Category Matching) — 遍历规则库，命中即分类
  3. 资产映射 (Asset Mapping) — 根据类别自动映射到 ticker 列表

不调用任何网络 / LLM，纯 CPU，微秒级完成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import ASSET_MAP, DEFAULT_TICKERS, NOISE_PATTERN, RULES


@dataclass(frozen=True)
class ClassifiedSignal:
    """FastClassifier 输出的结构化信号。"""
    category: str                          # e.g. "LNG_SUPPLY", "JAPAN_POWER"
    tickers: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)  # 命中了哪些规则类别
    raw_text: str = ""


class FastClassifier:
    """
    漏斗第一层：纯 Regex 硬分类 + 资产映射。

    用法:
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
        输入: 原始文本
        输出: ClassifiedSignal 或 None（噪音被丢弃）
        """
        # ---- 1. 噪音过滤 ----
        if self._noise_re.search(text):
            return None

        # ---- 2. 遍历规则库，找所有命中类别 ----
        matched_rules: list[str] = []
        all_tickers: list[str] = []

        for category, pattern in self._rules.items():
            if pattern.search(text):
                matched_rules.append(category)
                all_tickers.extend(ASSET_MAP.get(category, []))

        # ---- 3. 没命中任何核心规则 → 丢弃（不发告警） ----
        if not matched_rules:
            return None

        primary_category = matched_rules[0]

        # ---- 4. 去重 ----
        unique_tickers = list(dict.fromkeys(all_tickers))  # 保序去重

        return ClassifiedSignal(
            category=primary_category,
            tickers=unique_tickers,
            matched_rules=matched_rules,
            raw_text=text,
        )
