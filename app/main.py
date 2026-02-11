"""
LNG-Alpha-Feed 主流程 — 漏斗架构 (The Funnel Architecture)

数据流:
  Bluesky Jetstream (firehose)
    │
    ▼  Harvester (WhitelistFilter + 跨品种关键词预筛)
  异步队列
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

用法:
  python -m app.main              # 实时模式（连接 Jetstream firehose）
  python -m app.main --test       # 测试模式（用硬编码推文验证管线）
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from app.models import SignalEvent
from app.modules.classifier import FastClassifier
from app.modules.harvester import JetstreamClient
from app.modules.sentiment import AsyncSentimentAnalyzer
from app.modules.watchtower import Watchtower

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
logger = logging.getLogger("lng-alpha-feed")


# =========================================================================
# 漏斗处理器
# =========================================================================

async def process_text(
    text: str,
    author: str,
    classifier: FastClassifier,
    sentiment_engine: AsyncSentimentAnalyzer,
    watchtower: Watchtower,
) -> None:
    """单条文本走完整个漏斗。"""
    # ---- 第一层: 毫秒级分类 ----
    signal = classifier.classify(text)
    if signal is None:
        return  # FastClassifier 噪音过滤已丢弃

    logger.info(
        "✅ 命中  Category=%s  Tickers=%s  Rules=%s  Text=%.60s",
        signal.category,
        signal.tickers,
        signal.matched_rules,
        text,
    )

    # ---- 第二层: 异步情绪分析 ----
    result = await sentiment_engine.analyze(text)
    logger.info(
        "🧠 情绪  %s (%.0f%%)  %s",
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
    logger.info("🚀 已发布 → %s | %s", event.category, event.sentiment)


# =========================================================================
# Worker: 从队列消费消息，走漏斗
# =========================================================================

async def worker(
    queue: asyncio.Queue[tuple[str, str]],
    classifier: FastClassifier,
    sentiment_engine: AsyncSentimentAnalyzer,
    watchtower: Watchtower,
    worker_id: int,
) -> None:
    logger.info("Worker-%d started", worker_id)
    while True:
        text, author = await queue.get()
        try:
            await process_text(text, author, classifier, sentiment_engine, watchtower)
        except Exception:
            logger.exception("Worker-%d error processing: %.60s", worker_id, text)
        finally:
            queue.task_done()


# =========================================================================
# 实时模式: Jetstream firehose → 队列 → workers
# =========================================================================

async def run_live() -> None:
    logger.info("=" * 60)
    logger.info("LNG-Alpha-Feed — LIVE MODE (Jetstream firehose)")
    logger.info("=" * 60)

    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=5000)
    classifier = FastClassifier()
    sentiment_engine = AsyncSentimentAnalyzer()
    watchtower = Watchtower()
    harvester = JetstreamClient(output_queue=queue)

    # 启动 harvester + 2 个 worker
    tasks = [
        asyncio.create_task(harvester.start()),
        asyncio.create_task(worker(queue, classifier, sentiment_engine, watchtower, 1)),
        asyncio.create_task(worker(queue, classifier, sentiment_engine, watchtower, 2)),
    ]
    await asyncio.gather(*tasks)


# =========================================================================
# 测试模式: 硬编码推文验证管线
# =========================================================================

async def run_test() -> None:
    logger.info("=" * 60)
    logger.info("LNG-Alpha-Feed — TEST MODE (sample tweets)")
    logger.info("=" * 60)

    classifier = FastClassifier()
    sentiment_engine = AsyncSentimentAnalyzer()
    watchtower = Watchtower()

    test_tweets = [
        ("Just a webinar about climate change targets.", "@noise_account"),
        ("URGENT: Workers at Gorgon LNG facility voted to STRIKE starting next week.", "@reuters_energy"),
        ("Japan's Takahama nuclear reactor expected to restart tomorrow.", "@nikkei_energy"),
        ("Freeport LNG Train 2 outage extended by another week.", "@platts_lng"),
        ("Panama Canal draft restrictions tightened, LNG carrier traffic impacted.", "@splash247"),
    ]

    for text, author in test_tweets:
        await process_text(text, author, classifier, sentiment_engine, watchtower)

    logger.info("Test mode complete.")


# =========================================================================
# 入口
# =========================================================================

if __name__ == "__main__":
    if "--test" in sys.argv:
        asyncio.run(run_test())
    else:
        asyncio.run(run_live())
