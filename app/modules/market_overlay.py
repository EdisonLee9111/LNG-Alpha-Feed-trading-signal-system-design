"""
Module: Market Overlay (Post-validation Verification Layer)

Responsibilities:
  Receive the tickers list mapped by FastClassifier,
  fetch prices for each ticker and draw red arrows at alert time points.
  Support multi-ticker subplot overlay, generate one overall chart per alert.
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


def _to_utc_aware(ts: datetime) -> datetime:
    """Ensure timestamp is UTC aware, for comparison with pandas tz-aware index."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _fetch_price(symbol: str, start: datetime, end: datetime) -> pd.Series | None:
    """Try to fetch minute-level data; degrade to coarser granularity on failure."""
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
    Draw a subplot for each ticker with a red arrow marking the alert time point.
    Return the generated image path; return None if all fetches fail.
    """
    end_utc = alert_ts.astimezone(timezone.utc)
    start_utc = end_utc - timedelta(hours=max(1, lookback_hours))
    end_pad = end_utc + timedelta(minutes=5)

    # Fetch ticker data one by one (1.5s interval to avoid Yahoo rate limit)
    ticker_data: list[tuple[str, pd.Series]] = []
    for i, sym in enumerate(tickers):
        if i > 0:
            time.sleep(1.5)
        series = _fetch_price(sym, start_utc, end_pad)
        if series is not None:
            ticker_data.append((sym, series))

    if not ticker_data:
        return None

    # Subplot layout
    n = len(ticker_data)
    cols = min(n, 2)
    rows = math.ceil(n / cols)

    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 3.5 * rows), squeeze=False)
    alert_utc = _to_utc_aware(alert_ts)

    for idx, (sym, close) in enumerate(ticker_data):
        r, c = divmod(idx, cols)
        ax = axes[r][c]

        x = close.index
        y = close.values
        ax.plot(x, y, linewidth=1.2, color="#1f77b4")

        # Align alert time to nearest candle point (use aware datetime consistently)
        def _ts_dist(i: int) -> float:
            pt = x[i].to_pydatetime()
            # pandas may return naive or aware, handle uniformly
            if pt.tzinfo is None:
                pt = pt.replace(tzinfo=timezone.utc)
            return abs((pt - alert_utc).total_seconds())

        nearest = min(range(len(x)), key=_ts_dist)
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

    # Hide extra subplots
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
