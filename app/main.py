"""
LNG-Alpha-Feed Main Pipeline — Funnel Architecture

Data Flow:
  Bluesky Jetstream (firehose)
    │
    ▼  Harvester (WhitelistFilter + Cross-commodity keyword pre-filtering)
  Async Queue
    │
    ▼  Layer 1 (millisecond, CPU)
  FastClassifier  ──noise──> discard
    │
    │  Output: category + tickers
    ▼  Layer 2 (second, IO, async)
  AsyncSentimentAnalyzer
    │
    │  Output: BULLISH / BEARISH / NEUTRAL
    ▼
  Watchtower  ──> Telegram / Log / Dashboard + Overlay

Usage:
  python -m app.main              # Live mode (connect to Jetstream firehose)
  python -m app.main --test       # Test mode (verify pipeline with hardcoded tweets)
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from app.models import SignalEvent
from app.modules.classifier import FastClassifier
from app.modules.harvester import JetstreamClient
from app.modules.market_state import MarketStateManager # newly added
from app.modules.sentiment import AsyncSentimentAnalyzer
from app.modules.watchtower import Watchtower

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
logger = logging.getLogger("lng-alpha-feed")


# =========================================================================
# Funnel Processor
# =========================================================================

async def process_text(
    text: str,
    author: str,
    classifier: FastClassifier,
    sentiment_engine: AsyncSentimentAnalyzer,
    watchtower: Watchtower,
    market_state_manager: MarketStateManager = None,
) -> None:
    """Process a single text through the complete funnel."""
    # ---- Layer 1: millisecond-level classification ----
    signal = classifier.classify(text)
    if signal is None:
        return  # FastClassifier noise filter already discarded

    logger.info(
        "✅ Hit  Category=%s  Tickers=%s  Rules=%s  Text=%.60s",
        signal.category,
        signal.tickers,
        signal.matched_rules,
        text,
    )

    # ---- Layer 2: async sentiment analysis ----
    # Get real-time, zero-latency market state snapshot
    state_snapshot = market_state_manager.get_current_state() if market_state_manager else None
    result = await sentiment_engine.analyze(text, state_snapshot)
    logger.info(
        "🧠 Sentiment  %s (%.0f%%)  %s",
        result.sentiment,
        result.confidence * 100,
        result.reason,
    )

    # ---- Assemble SignalEvent ----
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

    # ---- Layer 3: alert + post-validation overlay ----
    await watchtower.publish(event)
    logger.info("🚀 Published → %s | %s", event.category, event.sentiment)


# =========================================================================
# Worker: consume messages from queue, process through funnel
# =========================================================================

async def worker(
    queue: asyncio.Queue[tuple[str, str]],
    classifier: FastClassifier,
    sentiment_engine: AsyncSentimentAnalyzer,
    watchtower: Watchtower,
    market_state_manager: MarketStateManager,
    worker_id: int,
) -> None:
    logger.info("Worker-%d started", worker_id)
    while True:
        text, author = await queue.get()
        try:
            await process_text(text, author, classifier, sentiment_engine, watchtower, market_state_manager)
        except Exception:
            logger.exception("Worker-%d error processing: %.60s", worker_id, text)
        finally:
            queue.task_done()


# =========================================================================
# Live Mode: Jetstream firehose → queue → workers
# =========================================================================

async def run_live() -> None:
    logger.info("=" * 60)
    logger.info("LNG-Alpha-Feed — LIVE MODE (Jetstream firehose)")
    logger.info("=" * 60)

    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=5000)
    classifier = FastClassifier()
    sentiment_engine = AsyncSentimentAnalyzer()
    watchtower = Watchtower()
    market_state_manager = MarketStateManager()
    harvester = JetstreamClient(output_queue=queue)

    # Start harvester + state polling + workers
    tasks = [
        asyncio.create_task(market_state_manager.start()),
        asyncio.create_task(harvester.start()),
        asyncio.create_task(worker(queue, classifier, sentiment_engine, watchtower, market_state_manager, 1)),
        asyncio.create_task(worker(queue, classifier, sentiment_engine, watchtower, market_state_manager, 2)),
    ]
    await asyncio.gather(*tasks)


# =========================================================================
# Test Mode: hardcoded tweets to verify pipeline
# =========================================================================

async def run_test() -> None:
    logger.info("=" * 60)
    logger.info("LNG-Alpha-Feed — TEST MODE (sample tweets)")
    logger.info("=" * 60)

    classifier = FastClassifier()
    sentiment_engine = AsyncSentimentAnalyzer()
    watchtower = Watchtower()
    market_state_manager = MarketStateManager()
    # For testing, we can simply launch the state manager as a background task
    # and wait a brief moment for phase 1 bootstrap.
    bg_task = asyncio.create_task(market_state_manager.start())
    await asyncio.sleep(2)  # Give it time to fetch Yahoo data

    test_tweets = [
        ("Just a webinar about climate change targets.", "@noise_account"),
        ("URGENT: Workers at Gorgon LNG facility voted to STRIKE starting next week.", "@reuters_energy"),
        ("Japan's Takahama nuclear reactor expected to restart tomorrow.", "@nikkei_energy"),
        ("Freeport LNG Train 2 outage extended by another week.", "@platts_lng"),
        ("Panama Canal draft restrictions tightened, LNG carrier traffic impacted.", "@splash247"),
    ]

    for text, author in test_tweets:
        await process_text(text, author, classifier, sentiment_engine, watchtower, market_state_manager)

    logger.info("Test mode complete.")
    bg_task.cancel()


# =========================================================================
# Entry Point
# =========================================================================

if __name__ == "__main__":
    if "--test" in sys.argv:
        asyncio.run(run_test())
    else:
        asyncio.run(run_live())
