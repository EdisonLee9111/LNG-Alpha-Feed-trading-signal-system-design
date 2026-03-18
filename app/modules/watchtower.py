"""
Module: Watchtower (Alert + Compliance + Post-validation Overlay)

Responsibilities:
  1. Write dashboard_feed.jsonl (for frontend scrolling wall)
  2. COMPLIANCE_MODE=true -> write local trade_signals.log only
  3. COMPLIANCE_MODE=false + token/chat_id filled -> send Telegram (text + overlay image)
  4. Post-validation overlay: use SignalEvent.tickers (from FastClassifier) to drive market_overlay
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiofiles
from telegram import Bot

from app.config import settings
from app.models import SignalEvent
from app.modules.market_overlay import build_overlay_chart


class Watchtower:
    def __init__(self) -> None:
        self.bot = Bot(token=settings.TELEGRAM_BOT_TOKEN) if settings.TELEGRAM_BOT_TOKEN else None

    # ------------------------------------------------------------------
    # Emoji Mapping
    # ------------------------------------------------------------------
    @staticmethod
    def _emoji(event: SignalEvent) -> str:
        if any(r in ("LNG_SUPPLY", "LABOR_STRIKE") for r in event.matched_rules):
            return "🚨"
        if event.sentiment == "BULLISH":
            return "🟢"
        if event.sentiment == "BEARISH":
            return "🔴"
        return "🟡"

    # ------------------------------------------------------------------
    # File Writing
    # ------------------------------------------------------------------
    @staticmethod
    async def _append_jsonl(path: str, payload: dict) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(p, "a", encoding="utf-8") as f:
            await f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # Main Publishing Pipeline
    # ------------------------------------------------------------------
    async def publish(self, event: SignalEvent) -> None:
        payload = event.model_dump(mode="json")

        # 1. Always write dashboard feed
        await self._append_jsonl(settings.DASHBOARD_JSONL, payload)

        # 2. Post-validation overlay (use classifier-mapped tickers, not hardcoded)
        overlay_path = await asyncio.to_thread(
            build_overlay_chart,
            event.tickers,
            event.ts,
            settings.OVERLAY_OUTPUT_DIR,
            settings.OVERLAY_LOOKBACK_HOURS,
        )

        # 3. Compliance mode -> write log only
        if settings.COMPLIANCE_MODE:
            await self._append_jsonl(settings.COMPLIANCE_LOG, payload)
            return

        # 4. Telegram push
        if self.bot and settings.TELEGRAM_CHAT_ID:
            emj = self._emoji(event)
            tickers_str = ", ".join(event.tickers)
            message = (
                f"{emj} <b>{event.category}</b> | {event.sentiment} ({event.confidence:.0%})\n"
                f"Tickers: {tickers_str}\n"
                f"Rules: {', '.join(event.matched_rules)}\n"
                f"Reason: {event.reason}\n"
                f"Author: {event.author}\n"
                f"Text: {event.text}"
            )
            await self.bot.send_message(
                chat_id=settings.TELEGRAM_CHAT_ID,
                text=message,
                parse_mode="HTML",
            )
            if overlay_path:
                with open(overlay_path, "rb") as img:
                    await self.bot.send_photo(
                        chat_id=settings.TELEGRAM_CHAT_ID,
                        photo=img,
                        caption=f"Overlay: {tickers_str}",
                    )
