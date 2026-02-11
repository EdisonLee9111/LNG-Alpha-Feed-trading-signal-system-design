"""
LNG-Alpha-Feed 主流程 — 漏斗架构 (The Funnel Architecture)

数据流:
  原始文本
    │
    ▼  第一层 (毫秒级, CPU)
  FastClassifier  ──噪音──> 丢弃
    │
    │  输出: category + tickers
    ▼  第二层 (秒级, IO, 异步)
  AsyncSentimentAnalyzer
    │
    │  输出: BULLISH / BEARISH / NEUTRAL
    ▼
  Watchtower  ──> Telegram / Log / Dashboard + Overlay
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.models import SignalEvent
from app.modules.classifier import FastClassifier
from app.modules.sentiment import AsyncSentimentAnalyzer
from app.modules.watchtower import Watchtower

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
logger = logging.getLogger("lng-alpha-feed")


async def process_text(
    text: str,
    author: str,
    classifier: FastClassifier,
    sentiment_engine: AsyncSentimentAnalyzer,
    watchtower: Watchtower,
) -> None:
    """
    单条文本走完整个漏斗。

    可被 Jetstream listener / RSS poller / 手动测试 调用。
    """
    # ---- 第一层: 毫秒级分类 ----
    signal = classifier.classify(text)
    if signal is None:
        logger.info("🗑️  噪音丢弃: %.40s…", text)
        return

    logger.info(
        "✅ 命中规则  Category=%s  Tickers=%s  Rules=%s",
        signal.category,
        signal.tickers,
        signal.matched_rules,
    )

    # ---- 第二层: 异步情绪分析 ----
    result = await sentiment_engine.analyze(text)
    logger.info(
        "🧠 情绪判定  %s (%.0f%%)  %s",
        result.sentiment,
        result.confidence * 100,
        result.reason,
    )

    # ---- 组装 SignalEvent ----
    event = SignalEvent(
        ts=datetime.now(timezone.utc),
        author=author,
        text=text,
        category=signal.category,
        tickers=signal.tickers,
        matched_rules=signal.matched_rules,
        sentiment=result.sentiment,
        confidence=result.confidence,
        reason=result.reason,
    )

    # ---- 第三层: 告警 + 后验叠加 ----
    await watchtower.publish(event)
    logger.info("🚀 已发布信号 → %s", event.category)


async def main() -> None:
    classifier = FastClassifier()
    sentiment_engine = AsyncSentimentAnalyzer()
    watchtower = Watchtower()

    # 演示用测试推文
    test_tweets = [
        ("Just a webinar about climate change targets.", "@noise_account"),
        ("URGENT: Workers at Gorgon LNG facility voted to STRIKE starting next week.", "@reuters_energy"),
        ("Japan's Takahama nuclear reactor expected to restart tomorrow.", "@nikkei_energy"),
        ("Freeport LNG Train 2 outage extended by another week.", "@platts_lng"),
        ("Panama Canal draft restrictions tightened, LNG carrier traffic impacted.", "@splash247"),
    ]

    # 顺序处理（避免 Yahoo Finance 并发限流）
    for text, author in test_tweets:
        await process_text(text, author, classifier, sentiment_engine, watchtower)


if __name__ == "__main__":
    asyncio.run(main())
