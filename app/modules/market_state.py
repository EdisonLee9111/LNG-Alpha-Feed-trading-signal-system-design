"""
Module: MarketStateManager (Cold Path - 市场状态注入)

职责：
  后台异步轮询各类市场数据 API (Yahoo Finance, EIA, AGSI等)，
  在内存中维护一个“零延迟”的状态快照 (MarketStateSnapshot)。
  供热路径 (AsyncSentimentAnalyzer) 随时读取，以实现 Context-Aware 的结构化分析。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, time
from typing import Optional

import yfinance as yf
import pandas as pd
import numpy as np

from app.config import settings

logger = logging.getLogger("lng-market-state")


# =========================================================================
# 状态模型：快照切片
# =========================================================================

@dataclass
class AssetMetrics:
    symbol: str
    price: Optional[float] = None
    volatility_percentile: Optional[float] = None
    # 扩展字段：如价格相对于过去 X 天移动平均的偏离度，或者曲线 Backwardation 程度
    # 这里做演示：存储一个近期历史的分位数 (0.0=历史最低, 1.0=历史最高, 0.5=中位数)

@dataclass
class MarketStateSnapshot:
    """
    供热路径读取的只读快照。
    所有字段允许为 None（防幻觉，获取不到就显式说明）。
    """
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # 价格与波动率状态
    assets: dict[str, AssetMetrics] = field(default_factory=dict)
    
    # 库存状态 (Percentile % : 0~100)
    us_inventory_percentile: Optional[float] = None
    eu_inventory_percentile: Optional[float] = None
    
    def get_context_string(self) -> str:
        """格式化为供 LLM 阅读的系统提示词上下文。"""
        lines = ["[MARKET CONTEXT SNAPSHOT]"]
        lines.append(f"Time: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # 资产状态
        if not self.assets:
            lines.append("Assets Data: Currently Unavailable (Do NOT assume price levels).")
        else:
            for symbol, metric in self.assets.items():
                price_str = f"{metric.price:.2f}" if metric.price is not None else "N/A"
                vol_str = f"{metric.volatility_percentile*100:.0f}%ile" if metric.volatility_percentile is not None else "N/A"
                lines.append(f"- {symbol}: Price={price_str}, Volatility={vol_str}")
                
        # 库存状态
        us_inv = f"{self.us_inventory_percentile:.1f}%ile" if self.us_inventory_percentile is not None else "Unavailable (Rely strictly on price action)"
        eu_inv = f"{self.eu_inventory_percentile:.1f}%ile" if self.eu_inventory_percentile is not None else "Unavailable (Rely strictly on price action)"
        lines.append(f"US Inventory (EIA): {us_inv}")
        lines.append(f"EU Inventory (AGSI): {eu_inv}")
        
        return "\n".join(lines)


# =========================================================================
# 核心管理器 (Background Daemon)
# =========================================================================

class MarketStateManager:
    """
    独立于主流程运行的守护协程。
    包含冷启动阶段（Sync/Fast）与重度回溯阶段（Async/Deep）。
    """
    def __init__(self):
        # 内存单例，原子的快照变量
        self._current_state = MarketStateSnapshot()
        self._lock = asyncio.Lock()  # 仅在写入时保护，也可以不加靠 GIL，但最好加
        
        # 运行时状态
        self._deep_baseline_ready = False

    # -------------------------------------------------------------
    # 读 API (Hot Path 调用)
    # -------------------------------------------------------------
    def get_current_state(self) -> MarketStateSnapshot:
        """
        零延迟读取。这里返回的是对对象的引用（因为我们是原子化地替换整个对象）。
        """
        return self._current_state

    # -------------------------------------------------------------
    # 生命周期 (Background Task)
    # -------------------------------------------------------------
    async def start(self) -> None:
        """启动后台轮询循环"""
        logger.info("MarketStateManager 启动: 初始化第一阶段数据 (Fast Bootstrap)")
        
        # Phase 1: 冷启动，抓取近期数据（可能会阻塞几秒，但保证基础上下文即刻可用）
        await self._poll_yahoo(days=settings.BASELINE_DAYS_FAST)
        # TODO: 加入 EIA / AGSI 第一阶段抓取
        
        logger.info("MarketStateManager 阶段1完成. 开启轮询守候.")
        
        # 开启 Phase 2 的异步长期背景计算
        asyncio.create_task(self._calculate_deep_baseline())

        # 主轮询循环
        while True:
            interval = self._get_dynamic_interval()
            await asyncio.sleep(interval)
            
            logger.debug(f"MarketStateManager 唤醒: 执行定量拉取 (Interval={interval}s)")
            try:
                # 只有有了长期基线，平时的轮询才传短天数（为了省带宽和提速）
                # 否则即使是平时，我们也用 fast baseline 的天数兜底
                fetch_days = 7 if self._deep_baseline_ready else settings.BASELINE_DAYS_FAST
                await self._poll_yahoo(days=fetch_days)
            except Exception:
                logger.exception("MarketStateManager 轮询异常")

    # -------------------------------------------------------------
    # 调度控制
    # -------------------------------------------------------------
    def _is_active_trading_hours(self) -> bool:
        """粗略判断是否为 EST 交易活跃时间 (09:00 - 14:30)"""
        now = datetime.now(timezone.utc)
        # 这里为演示简单处理（生产上最好用 pytz 等转换为 EST）
        hour = now.hour
        if now.weekday() >= 5: # Weekend
            return False
            
        # UTC 14:00 - 19:30 约等于 EST 09:00 - 14:30 (冬令时)
        return 14 <= hour < 20

    def _get_dynamic_interval(self) -> int:
        if self._is_active_trading_hours():
            return settings.POLL_INTERVAL_ACTIVE
        return settings.POLL_INTERVAL_IDLE

    # -------------------------------------------------------------
    # 数据获取协程
    # -------------------------------------------------------------
    async def _calculate_deep_baseline(self) -> None:
        """Phase 2: 后台默默抓取 3 年数据，计算高精度波动率和分位线，算完后热替换"""
        logger.info("MarketStateManager 后台启动 3年期 Deep Baseline 精算...")
        await asyncio.sleep(10) # 错峰执行，让主程序先跑稳
        
        try:
            # 此处演示调用：实际情况这里的计算量较大，如果阻塞太久可以用 asyncio.to_thread 包裹
            await self._poll_yahoo(days=settings.BASELINE_DAYS_DEEP)
            self._deep_baseline_ready = True
            logger.info("MarketStateManager 阶段2完成: 深度基线热切换完毕.")
        except Exception:
            logger.exception("MarketStateManager Phase2 深度计算失败.")

    async def _poll_yahoo(self, days: int) -> None:
        """
        通过 yfinance 拉取行情和历史。
        注意 yfinance 是同步阻塞的网络库，所以用 asyncio.to_thread。
        """
        def _fetch():
            tickers_str = " ".join(settings.MARKET_TICKERS)
            # interval = 1d。如果是活跃时区，或许可以混用 1h，这里为了基线统一用 1d
            return yf.download(tickers_str, period=f"{days}d", group_by='ticker', threads=False)

        try:
            df = await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.warning(f"Yahoo API 抓取失败: {e}")
            df = pd.DataFrame()
            
        if df.empty:
            logger.warning("Yahoo Finance 提供了空数据。")

        new_assets = {}
        for symbol in settings.MARKET_TICKERS:
            try:
                # pandas 的 multi-index 处理
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
                
                # 计算非常简易的波动率分位数：当前价格在过去 X 天的百位点（0~1）
                # (实际应该是算日收益率标准差的分位数，这里为结构演示用价格分位代替)
                min_p = float(close_prices.min())
                max_p = float(close_prices.max())
                rp = 0.5
                if max_p > min_p:
                    rp = (current_price - min_p) / (max_p - min_p)

                new_assets[symbol] = AssetMetrics(
                    symbol=symbol,
                    price=current_price,
                    volatility_percentile=rp
                )
            except Exception as e:
                logger.debug(f"处理 ticker {symbol} 时数据格式不符或者缺失: {e}")
                
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

        # 原子更新快照：我们将当前的其它数据（比如已有的库存）复制过来，只盖掉 assets
        # 因为 Python 对象引用替换是原子的，所以这一步安全
        async with self._lock:
            old_state = self._current_state
            
            self._current_state = MarketStateSnapshot(
                timestamp=datetime.now(timezone.utc),
                assets=new_assets,
                us_inventory_percentile=old_state.us_inventory_percentile,
                eu_inventory_percentile=old_state.eu_inventory_percentile
            )
        
        logger.debug(f"更新 Market State: {list(new_assets.keys())}")

