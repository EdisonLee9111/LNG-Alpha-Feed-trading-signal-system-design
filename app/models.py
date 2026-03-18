from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal


class SignalEvent(BaseModel):
    """
    Final output object of the funnel architecture.

    Contains information from three stages:
      1. Raw text + metadata
      2. FastClassifier output: category, tickers, matched_rules
      3. Sentiment output: sentiment, confidence, reason
    """
    ts: datetime
    author: str

    # --- Raw information ---
    text: str

    # --- Layer 1: FastClassifier ---
    category: str                                           # e.g. "LNG_SUPPLY"
    tickers: list[str] = Field(default_factory=list)        # e.g. ["UNG", "TTF=F"]
    matched_rules: list[str] = Field(default_factory=list)

    # --- Layer 2: Sentiment ---
    sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"
    confidence: float = 0.5
    reason: str = ""
