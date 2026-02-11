from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal


class SignalEvent(BaseModel):
    """
    漏斗架构的最终输出对象。

    包含三个阶段的信息：
      1. 原始文本 + 元数据
      2. FastClassifier 产出: category, tickers, matched_rules
      3. Sentiment 产出: sentiment, confidence, reason
    """
    ts: datetime
    author: str

    # --- 原始信息 ---
    text: str

    # --- 第一层: FastClassifier ---
    category: str                                           # e.g. "LNG_SUPPLY"
    tickers: list[str] = Field(default_factory=list)        # e.g. ["UNG", "TTF=F"]
    matched_rules: list[str] = Field(default_factory=list)

    # --- 第二层: Sentiment ---
    sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"
    confidence: float = 0.5
    reason: str = ""
