“””
Module: MarketStateManager (Cold Path - Market State Injection)

Responsibilities:
  Background async polling of various market data APIs (Yahoo Finance, EIA, AGSI, etc.),
  maintain a “zero-latency” state snapshot in memory (MarketStateSnapshot).
  Available for hot path (AsyncSentimentAnalyzer) to read anytime, enabling context-aware structured analysis.
“””

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, time, timedelta
from typing import Optional

import aiohttp
import yfinance as yf
import pandas as pd
import numpy as np

from app.config import settings

logger = logging.getLogger("lng-market-state")


# =========================================================================
# State Model: Snapshot Slice
# =========================================================================

@dataclass
class AssetMetrics:
    symbol: str
    price: Optional[float] = None
    volatility_percentile: Optional[float] = None
    # Extended fields: e.g. price deviation relative to past X-day moving average, or Backwardation degree
    # Here for demo: store recent history percentile (0.0=historical low, 1.0=historical high, 0.5=median)

@dataclass
class MarketStateSnapshot:
    """
    Read-only snapshot for hot path.
    All fields allowed to be None (prevent hallucination, explicit if unavailable).
    """
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Price and volatility state
    assets: dict[str, AssetMetrics] = field(default_factory=dict)

    # Inventory state (Percentile % : 0~100)
    us_inventory_percentile: Optional[float] = None
    eu_inventory_percentile: Optional[float] = None

    def get_context_string(self) -> str:
        """Format as system prompt context for LLM reading."""
        lines = ["[MARKET CONTEXT SNAPSHOT]"]
        lines.append(f"Time: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        # Asset state
        if not self.assets:
            lines.append("Assets Data: Currently Unavailable (Do NOT assume price levels).")
        else:
            for symbol, metric in self.assets.items():
                price_str = f"{metric.price:.2f}" if metric.price is not None else "N/A"
                vol_str = f"{metric.volatility_percentile*100:.0f}%ile" if metric.volatility_percentile is not None else "N/A"
                lines.append(f"- {symbol}: Price={price_str}, Volatility={vol_str}")

        # Inventory state
        us_inv = f"{self.us_inventory_percentile:.1f}%ile" if self.us_inventory_percentile is not None else "Unavailable (Rely strictly on price action)"
        eu_inv = f"{self.eu_inventory_percentile:.1f}%ile" if self.eu_inventory_percentile is not None else "Unavailable (Rely strictly on price action)"
        lines.append(f"US Inventory (EIA): {us_inv}")
        lines.append(f"EU Inventory (AGSI): {eu_inv}")
        
        return "\n".join(lines)


# =========================================================================
# Core Manager (Background Daemon)
# =========================================================================

class MarketStateManager:
    """
    Daemon coroutine running independently from main pipeline.
    Contains cold start phase (Sync/Fast) and deep lookback phase (Async/Deep).
    """
    def __init__(self):
        # In-memory singleton, atomic snapshot variable
        self._current_state = MarketStateSnapshot()
        self._lock = asyncio.Lock()  # Protect only on write, could rely on GIL, but safer with lock

        # Runtime state
        self._deep_baseline_ready = False

        # EIA inventory cache
        self._eia_history: list[tuple[str, float]] = []   # [(period_str, value_bcf), ...]
        self._eia_last_period: Optional[str] = None        # Latest period already fetched
        self._eia_last_fetch: Optional[datetime] = None    # Last API call time

    # -------------------------------------------------------------
    # Read API (Hot Path Call)
    # -------------------------------------------------------------
    def get_current_state(self) -> MarketStateSnapshot:
        """
        Zero-latency read. Returns reference to object (we atomically replace entire object).
        """
        return self._current_state

    # -------------------------------------------------------------
    # Lifecycle (Background Task)
    # -------------------------------------------------------------
    async def start(self) -> None:
        """Start background polling loop"""
        logger.info("MarketStateManager starting: initializing phase 1 data (Fast Bootstrap)")

        # Phase 1: cold start, fetch recent data (may block a few seconds, but ensures basic context immediately available)
        await self._poll_yahoo(days=settings.BASELINE_DAYS_FAST)
        await self._poll_eia(years=1)  # 1 year data, fast startup

        logger.info("MarketStateManager phase 1 complete. Starting polling watchdog.")

        # Start phase 2 async long-term background calculation
        asyncio.create_task(self._calculate_deep_baseline())

        # Main polling loop
        while True:
            interval = self._get_dynamic_interval()
            await asyncio.sleep(interval)

            logger.debug(f"MarketStateManager woke up: executing quantitative fetch (Interval={interval}s)")
            try:
                # Only use short days when long-term baseline available (save bandwidth and speed)
                # Otherwise, even during normal times, use fast baseline days as fallback
                fetch_days = 7 if self._deep_baseline_ready else settings.BASELINE_DAYS_FAST
                await self._poll_yahoo(days=fetch_days)
                await self._poll_eia()
            except Exception:
                logger.exception("MarketStateManager polling exception")

    # -------------------------------------------------------------
    # Scheduling Control
    # -------------------------------------------------------------
    def _is_active_trading_hours(self) -> bool:
        """Roughly determine if it's EST active trading hours (09:00 - 14:30)"""
        now = datetime.now(timezone.utc)
        # Simple handling here for demo (production should use pytz etc. to convert to EST)
        hour = now.hour
        if now.weekday() >= 5:  # Weekend
            return False

        # UTC 14:00 - 19:30 approximately equals EST 09:00 - 14:30 (winter time)
        return 14 <= hour < 20

    def _get_dynamic_interval(self) -> int:
        if self._is_active_trading_hours():
            return settings.POLL_INTERVAL_ACTIVE
        return settings.POLL_INTERVAL_IDLE

    # -------------------------------------------------------------
    # Data Fetch Coroutines
    # -------------------------------------------------------------
    async def _calculate_deep_baseline(self) -> None:
        """Phase 2: background quietly fetch 3 years data, calculate high-precision volatility and percentile, hot-replace when done"""
        logger.info("MarketStateManager background starting 3-year Deep Baseline precision calculation...")
        await asyncio.sleep(10)  # Stagger execution, let main program stabilize first

        try:
            # Demo call here: in practice this computation is large, can wrap with asyncio.to_thread if blocks too long
            await self._poll_yahoo(days=settings.BASELINE_DAYS_DEEP)
            await self._poll_eia(years=settings.EIA_SEASONAL_YEARS)  # 5 years precision calculation
            self._deep_baseline_ready = True
            logger.info("MarketStateManager phase 2 complete: deep baseline hot-switch done.")
        except Exception:
            logger.exception("MarketStateManager Phase 2 deep calculation failed.")

    async def _poll_yahoo(self, days: int) -> None:
        """
        Fetch price action and history via yfinance.
        Note: yfinance is a sync-blocking network library, so use asyncio.to_thread.
        """
        def _fetch():
            tickers_str = " ".join(settings.MARKET_TICKERS)
            # interval = 1d. If active timezone, could mix 1h, here use 1d for baseline uniformity
            return yf.download(tickers_str, period=f"{days}d", group_by='ticker', threads=False)

        try:
            df = await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.warning(f"Yahoo API fetch failed: {e}")
            df = pd.DataFrame()

        if df.empty:
            logger.warning("Yahoo Finance provided empty data.")

        new_assets = {}
        for symbol in settings.MARKET_TICKERS:
            try:
                # pandas multi-index handling
                if len(settings.MARKET_TICKERS) > 1:
                    ticker_df = df[symbol].dropna()
                else:
                    ticker_df = df.dropna()

                if ticker_df.empty:
                    continue

                close_prices = ticker_df['Close']
                if close_prices.empty:
                    continue

                current_price = float(close_prices.iloc[-1])

                # Volatility percentile: 20-day rolling return std dev percentile in historical window (0~1)
                returns = close_prices.pct_change().dropna()
                if len(returns) < 20:
                    rp = 0.5  # Insufficient data, use neutral value
                else:
                    rolling_vol = returns.rolling(window=20).std().dropna()
                    if rolling_vol.empty:
                        rp = 0.5
                    else:
                        current_vol = float(rolling_vol.iloc[-1])
                        rp = float((rolling_vol < current_vol).sum() / len(rolling_vol))

                new_assets[symbol] = AssetMetrics(
                    symbol=symbol,
                    price=current_price,
                    volatility_percentile=rp
                )
            except Exception as e:
                logger.debug(f"Processing ticker {symbol}: data format mismatch or missing: {e}")

        # Fallback to mock data if API limits hit
        if not new_assets:
            logger.warning("Falling back to synthetic data due to empty Yahoo response.")
            for symbol in settings.MARKET_TICKERS:
                base_price = 2.5 if symbol == "NG=F" else 10.0
                new_assets[symbol] = AssetMetrics(
                    symbol=symbol,
                    price=base_price + np.random.uniform(-0.5, 0.5),
                    volatility_percentile=np.random.uniform(0.1, 0.9)
                )

        # Atomic snapshot update: copy other current data (e.g. existing inventory), only override assets
        # Safe because Python object reference replacement is atomic
        async with self._lock:
            old_state = self._current_state

            self._current_state = MarketStateSnapshot(
                timestamp=datetime.now(timezone.utc),
                assets=new_assets,
                us_inventory_percentile=old_state.us_inventory_percentile,
                eu_inventory_percentile=old_state.eu_inventory_percentile
            )

        logger.debug(f"Updated Market State: {list(new_assets.keys())}")

    # -------------------------------------------------------------
    # EIA Natural Gas Weekly Storage
    # -------------------------------------------------------------
    async def _poll_eia(self, years: int = 5) -> None:
        """
        Fetch US natural gas weekly inventory data from EIA API v2,
        calculate seasonal percentile (current inventory vs 5-year history of same calendar week).
        """
        if not settings.EIA_API_KEY:
            logger.debug("EIA_API_KEY not configured, skipping inventory fetch")
            return

        # Cache check: less than EIA_POLL_INTERVAL seconds since last request → skip
        now = datetime.now(timezone.utc)
        if self._eia_last_fetch and (now - self._eia_last_fetch).total_seconds() < settings.EIA_POLL_INTERVAL:
            return

        start_date = (now - timedelta(days=365 * years)).strftime("%Y-%m-%d")
        params = {
            "api_key": settings.EIA_API_KEY,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[process][]": "SWO",
            "facets[duoarea][]": "R48",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "start": start_date,
            "length": 5000,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    settings.EIA_API_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    resp.raise_for_status()
                    payload = await resp.json()

            rows = payload.get("response", {}).get("data", [])
            if not rows:
                logger.warning("EIA API returned empty data")
                return

            # Parse and merge to history cache (dedup by period, keep latest value)
            existing = {p: v for p, v in self._eia_history}
            for row in rows:
                period = row.get("period", "")
                value = row.get("value")
                if period and value is not None:
                    try:
                        existing[period] = float(value)
                    except (ValueError, TypeError):
                        continue

            self._eia_history = sorted(existing.items(), key=lambda x: x[0])
            self._eia_last_period = self._eia_history[-1][0] if self._eia_history else None
            self._eia_last_fetch = now

            # Calculate seasonal percentile
            percentile = self._calculate_seasonal_percentile()
            if percentile is not None:
                async with self._lock:
                    old = self._current_state
                    self._current_state = MarketStateSnapshot(
                        timestamp=datetime.now(timezone.utc),
                        assets=old.assets,
                        us_inventory_percentile=percentile,
                        eu_inventory_percentile=old.eu_inventory_percentile,
                    )
                logger.info(f"EIA inventory update: US Inventory Percentile = {percentile:.1f}%ile ({len(self._eia_history)} weeks cached)")

        except Exception as e:
            logger.warning(f"EIA API fetch failed: {e}")

    def _calculate_seasonal_percentile(self) -> Optional[float]:
        """
        Compare current inventory with historical observations of same ISO calendar week,
        return seasonal percentile 0~100.
        """
        if not self._eia_history:
            return None

        latest_period, latest_value = self._eia_history[-1]
        try:
            latest_dt = datetime.strptime(latest_period, "%Y-%m-%d")
        except ValueError:
            return None

        target_week = latest_dt.isocalendar()[1]

        # Collect historical values for same calendar week (exclude latest period itself)
        same_week_values = []
        for period, value in self._eia_history[:-1]:
            try:
                dt = datetime.strptime(period, "%Y-%m-%d")
            except ValueError:
                continue
            if dt.isocalendar()[1] == target_week:
                same_week_values.append(value)

        # Need at least 3 same-week observations; if not, use all history as fallback
        if len(same_week_values) < 3:
            all_values = [v for _, v in self._eia_history[:-1]]
            if not all_values:
                return None
            comparison = all_values
        else:
            comparison = same_week_values

        # Percentile: how many historical values are below current value
        below = sum(1 for v in comparison if v < latest_value)
        return (below / len(comparison)) * 100

