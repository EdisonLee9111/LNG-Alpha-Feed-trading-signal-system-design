"""
Module 1: Harvester (数据采集层)

职责：
  1. 连接 Bluesky Jetstream firehose (WebSocket)
  2. 白名单过滤：只处理指定 DID / handle 的帖子
  3. 跨品种关键词预筛：LNG / JEPX / Shipping 等
  4. 输出干净的 (text, author) 元组到异步队列

运行模式 (HARVESTER_MODE):
  "whitelist" — 仅白名单账户
  "keyword"  — 全量帖子，仅靠关键词筛（白名单为空时推荐）
  "both"     — 白名单账户全部通过 + 非白名单靠关键词筛
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
# 白名单过滤器
# =========================================================================

class WhitelistFilter:
    """按 DID 精确匹配 + handle 后缀匹配。"""

    def __init__(self) -> None:
        self._dids = {d.lower() for d in WHITELIST_DIDS if d}
        self._handles = {h.lower().lstrip("@") for h in WHITELIST_HANDLES if h}
        # 启动时解析 handle → DID 并合并（可选，异步）
        self._resolved_dids: set[str] = set()

    async def warm_up(self) -> None:
        """启动时把 handle 解析成 DID，之后用 DID 直接匹配（更快）。"""
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
# 关键词预筛
# =========================================================================

def _passes_keyword_filter(text: str) -> bool:
    """跨品种关键词粗筛 — 在进入 FastClassifier 之前快速丢弃无关帖子。"""
    t = text.lower()
    return any(kw in t for kw in CROSS_COMMODITY_KEYWORDS)


# =========================================================================
# Jetstream 消息解析
# =========================================================================

def _parse_jetstream_msg(raw: str) -> tuple[str, str] | None:
    """
    解析 Jetstream JSON 消息，返回 (text, author_did) 或 None。

    Jetstream 消息结构:
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
# JetstreamClient — 主采集器
# =========================================================================

class JetstreamClient:
    """
    异步连接 Bluesky Jetstream firehose。

    每条通过过滤的帖子以 (text, author_did) 放入 output_queue。
    自动重连（指数退避，最大 60s）。
    """

    def __init__(self, output_queue: asyncio.Queue[tuple[str, str]]) -> None:
        self.output_queue = output_queue
        self.whitelist = WhitelistFilter()
        self._mode = settings.HARVESTER_MODE.lower()
        self._stats = {"received": 0, "passed_whitelist": 0, "passed_keyword": 0, "queued": 0}

    async def start(self) -> None:
        """启动采集：预热白名单 → 连接 firehose → 循环读取。"""
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
        """单次 WebSocket 会话。"""
        logger.info("Connecting to Jetstream: %s", JETSTREAM_URL)
        async with websockets.connect(
            JETSTREAM_URL,
            ping_interval=20,
            ping_timeout=10,
            max_size=2**20,       # 1 MB
            close_timeout=5,
        ) as ws:
            logger.info("Connected to Jetstream firehose ✓")
            # 连接成功，重置退避
            await self._read_loop(ws)

    async def _read_loop(self, ws: ClientConnection) -> None:
        async for raw in ws:
            self._stats["received"] += 1

            parsed = _parse_jetstream_msg(str(raw))
            if not parsed:
                continue

            text, did = parsed

            # --- 白名单 / 关键词 过滤 ---
            if not self._should_process(text, did):
                continue

            self._stats["queued"] += 1
            try:
                self.output_queue.put_nowait((text, did))
            except asyncio.QueueFull:
                logger.warning("Output queue full, dropping message")

            # 每 500 条打印一次统计
            if self._stats["queued"] % 500 == 0:
                logger.info("Harvester stats: %s", self._stats)

    def _should_process(self, text: str, did: str) -> bool:
        """根据 HARVESTER_MODE 决定是否放行。"""
        wl_hit = self.whitelist.is_whitelisted(did)
        kw_hit = _passes_keyword_filter(text)

        if self._mode == "whitelist":
            if self.whitelist.is_empty:
                # 白名单为空 → 降级为关键词模式
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

        # mode == "keyword" (默认)
        self._stats["passed_keyword"] += int(kw_hit)
        return kw_hit
