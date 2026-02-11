"""
Module: Market Overlay (后验验证层)

职责：
  接收 FastClassifier 映射出的 tickers 列表，
  为每个 ticker 拉取价格并在告警时点画红色箭头。
  支持多 ticker 子图叠加，一次生成一张总图。
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # 无头模式，兼容服务器
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf


def _to_utc_naive(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(timezone.utc).replace(tzinfo=None)


def _fetch_price(symbol: str, start: datetime, end: datetime) -> pd.Series | None:
    """尝试拉取分钟级数据；失败则降级到更粗粒度。"""
    for interval in ("1m", "5m", "15m", "1h"):
        try:
            data = yf.download(
                symbol,
                start=start,
                end=end,
                interval=interval,
                progress=False,
                auto_adjust=True,
            )
        except Exception:
            continue

        if data is None or data.empty:
            continue

        if "Close" not in data.columns:
            continue

        close = data["Close"].copy()
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        if not close.empty:
            return close

    return None


def build_overlay_chart(
    tickers: list[str],
    alert_ts: datetime,
    output_dir: str,
    lookback_hours: int = 12,
) -> str | None:
    """
    为每个 ticker 画一张子图，告警时点标红色箭头。
    返回生成的图片路径；全部拉取失败返回 None。
    """
    end_utc = alert_ts.astimezone(timezone.utc)
    start_utc = end_utc - timedelta(hours=max(1, lookback_hours))
    end_pad = end_utc + timedelta(minutes=5)

    # 逐个拉取 ticker 数据（间隔 1.5 秒防 Yahoo 限流）
    ticker_data: list[tuple[str, pd.Series]] = []
    for i, sym in enumerate(tickers):
        if i > 0:
            time.sleep(1.5)
        series = _fetch_price(sym, start_utc, end_pad)
        if series is not None:
            ticker_data.append((sym, series))

    if not ticker_data:
        return None

    # 子图布局
    n = len(ticker_data)
    cols = min(n, 2)
    rows = math.ceil(n / cols)

    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 3.5 * rows), squeeze=False)
    alert_naive = _to_utc_naive(alert_ts)

    for idx, (sym, close) in enumerate(ticker_data):
        r, c = divmod(idx, cols)
        ax = axes[r][c]

        x = close.index
        y = close.values
        ax.plot(x, y, linewidth=1.2, color="#1f77b4")

        # 对齐告警时间到最近的 K 线点
        nearest = min(range(len(x)), key=lambda i: abs(x[i].to_pydatetime() - alert_naive))
        ax.annotate(
            "Alert",
            xy=(x[nearest], float(y[nearest])),
            xytext=(x[nearest], float(y[nearest]) * 1.006),
            color="red",
            fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="red", lw=2),
            ha="center",
        )

        ax.set_title(sym, fontsize=11, fontweight="bold")
        ax.set_ylabel("Price")
        ax.grid(alpha=0.2)
        ax.tick_params(axis="x", rotation=30, labelsize=8)

    # 隐藏多余子图
    for idx in range(n, rows * cols):
        r, c = divmod(idx, cols)
        axes[r][c].set_visible(False)

    fig.suptitle("Market Overlay — Signal Validation", fontsize=13, y=1.01)
    fig.tight_layout()

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    tag = "_".join(t.replace("=", "") for t in tickers[:3])
    out_path = Path(output_dir) / f"{tag}_{int(end_utc.timestamp())}.png"
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)
