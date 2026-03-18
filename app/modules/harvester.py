"""
Module 1: Harvester (Data Collection Layer)

Responsibilities:
  1. Connect to Bluesky Jetstream firehose (WebSocket)
  2. Whitelist filtering: only process posts from specified DIDs / handles
  3. Cross-commodity keyword pre-filtering: LNG / JEPX / Shipping etc.
  4. Output clean (text, author) tuples to async queue

Running modes (HARVESTER_MODE):
  "whitelist" — whitelist accounts only
  "keyword"  — all posts, filter by keywords only (recommended when whitelist is empty)
  "both"     — all whitelist accounts pass + non-whitelist filtered by keywords
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiohttp
import websockets
from websockets.asyncio.client import ClientConnection

from app.config import (
    CROSS_COMMODITY_KEYWORDS,
    JETSTREAM_URL,
    WHITELIST_DIDS,
    WHITELIST_HANDLES,
    settings,
)

logger = logging.getLogger("harvester")


# =========================================================================
# Whitelist Filter
# =========================================================================

class WhitelistFilter:
    """DID exact match + handle suffix match."""

    def __init__(self) -> None:
        self._dids = {d.lower() for d in WHITELIST_DIDS if d}
        self._handles = {h.lower().lstrip("@") for h in WHITELIST_HANDLES if h}
        # On startup, resolve handle → DID and merge (optional, async)
        self._resolved_dids: set[str] = set()

    async def warm_up(self) -> None:
        """On startup, resolve handles to DIDs for direct matching (faster)."""
        if not self._handles:
            return
        async with aiohttp.ClientSession() as session:
            for handle in list(self._handles):
                try:
                    url = f"https://bsky.social/xrpc/com.atproto.identity.resolveHandle?handle={handle}"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            did = data.get("did", "")
                            if did:
                                self._resolved_dids.add(did.lower())
                                logger.info("Resolved %s → %s", handle, did)
                except Exception:
                    logger.warning("Failed to resolve handle: %s", handle)

    def is_whitelisted(self, did: str) -> bool:
        d = did.lower()
        return d in self._dids or d in self._resolved_dids

    @property
    def is_empty(self) -> bool:
        return not self._dids and not self._handles and not self._resolved_dids


# =========================================================================
# Keyword Pre-filtering
# =========================================================================

def _passes_keyword_filter(text: str) -> bool:
    """Cross-commodity keyword rough filter — quickly discard unrelated posts before FastClassifier."""
    t = text.lower()
    return any(kw in t for kw in CROSS_COMMODITY_KEYWORDS)


# =========================================================================
# Jetstream Message Parsing
# =========================================================================

def _parse_jetstream_msg(raw: str) -> tuple[str, str] | None:
    """
    Parse Jetstream JSON message, return (text, author_did) or None.

    Jetstream message structure:
      {
        "did": "did:plc:...",
        "time_us": 1234567890123456,
        "kind": "commit",
        "commit": {
          "operation": "create",
          "collection": "app.bsky.feed.post",
          "record": { "text": "...", ... }
        }
      }
    """
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    if msg.get("kind") != "commit":
        return None

    commit = msg.get("commit")
    if not commit:
        return None

    if commit.get("operation") != "create":
        return None

    if commit.get("collection") != "app.bsky.feed.post":
        return None

    record = commit.get("record", {})
    text = record.get("text", "").strip()
    if not text:
        return None

    did = msg.get("did", "unknown")
    return text, did


# =========================================================================
# JetstreamClient — Primary Harvester
# =========================================================================

class JetstreamClient:
    """
    Async connection to Bluesky Jetstream firehose.

    Each post passing filters is put into output_queue as (text, author_did).
    Auto-reconnect with exponential backoff, max 60s.
    """

    def __init__(self, output_queue: asyncio.Queue[tuple[str, str]]) -> None:
        self.output_queue = output_queue
        self.whitelist = WhitelistFilter()
        self._mode = settings.HARVESTER_MODE.lower()
        self._stats = {"received": 0, "passed_whitelist": 0, "passed_keyword": 0, "queued": 0}

    async def start(self) -> None:
        """Start harvesting: warm up whitelist → connect to firehose → loop read."""
        await self.whitelist.warm_up()
        logger.info(
            "Harvester starting  mode=%s  whitelist_dids=%d  resolved=%d  keywords=%d",
            self._mode,
            len(self.whitelist._dids),
            len(self.whitelist._resolved_dids),
            len(CROSS_COMMODITY_KEYWORDS),
        )

        backoff = 1
        while True:
            try:
                await self._listen()
            except (websockets.ConnectionClosed, ConnectionError, OSError) as exc:
                logger.warning("Jetstream disconnected: %s — reconnecting in %ds", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception:
                logger.exception("Unexpected harvester error — reconnecting in %ds", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _listen(self) -> None:
        """Single WebSocket session."""
        logger.info("Connecting to Jetstream: %s", JETSTREAM_URL)
        async with websockets.connect(
            JETSTREAM_URL,
            ping_interval=20,
            ping_timeout=10,
            max_size=2**20,       # 1 MB
            close_timeout=5,
        ) as ws:
            logger.info("Connected to Jetstream firehose ✓")
            # Connection successful, reset backoff
            await self._read_loop(ws)

    async def _read_loop(self, ws: ClientConnection) -> None:
        async for raw in ws:
            self._stats["received"] += 1

            parsed = _parse_jetstream_msg(str(raw))
            if not parsed:
                continue

            text, did = parsed

            # --- Whitelist / keyword filtering ---
            if not self._should_process(text, did):
                continue

            self._stats["queued"] += 1
            try:
                self.output_queue.put_nowait((text, did))
            except asyncio.QueueFull:
                logger.warning("Output queue full, dropping message")

            # Print stats every 500 posts
            if self._stats["queued"] % 500 == 0:
                logger.info("Harvester stats: %s", self._stats)

    def _should_process(self, text: str, did: str) -> bool:
        """Decide whether to pass based on HARVESTER_MODE."""
        wl_hit = self.whitelist.is_whitelisted(did)
        kw_hit = _passes_keyword_filter(text)

        if self._mode == "whitelist":
            if self.whitelist.is_empty:
                # Whitelist is empty → degrade to keyword mode
                self._stats["passed_keyword"] += int(kw_hit)
                return kw_hit
            self._stats["passed_whitelist"] += int(wl_hit)
            return wl_hit

        if self._mode == "both":
            if wl_hit:
                self._stats["passed_whitelist"] += 1
                return True
            self._stats["passed_keyword"] += int(kw_hit)
            return kw_hit

        # mode == "keyword" (default)
        self._stats["passed_keyword"] += int(kw_hit)
        return kw_hit
