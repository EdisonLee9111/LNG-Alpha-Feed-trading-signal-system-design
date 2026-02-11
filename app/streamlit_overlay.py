"""
Streamlit 页面 — 信号回看 + 后验叠加图

用法:
  streamlit run app/streamlit_overlay.py
"""

from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import streamlit as st

from app.config import settings
from app.models import SignalEvent
from app.modules.market_overlay import build_overlay_chart


def _load_events(path: str, limit: int = 30) -> list[SignalEvent]:
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    events: list[SignalEvent] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        events.append(SignalEvent.model_validate(json.loads(line)))
    return events


def main() -> None:
    st.set_page_config(page_title="LNG-Alpha Signal Overlay", layout="wide")
    st.title("LNG-Alpha Feed — Signal Validation")
    st.caption("先预判（FastClassifier → 资产映射），再验证（后验价格叠加）。")

    lookback = st.slider("Lookback (hours)", 1, 72, value=settings.OVERLAY_LOOKBACK_HOURS)

    events = _load_events(settings.DASHBOARD_JSONL)
    if not events:
        st.warning("暂无信号数据，请先运行 `python -m app.main` 触发事件。")
        return

    idx = st.selectbox(
        "选择信号事件",
        range(len(events)),
        format_func=lambda i: (
            f"{events[i].ts.isoformat()} | {events[i].category} | "
            f"{events[i].sentiment} | {', '.join(events[i].tickers)}"
        ),
        index=len(events) - 1,
    )
    event = events[idx]

    # 信号详情
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("信号详情")
        st.markdown(f"**Category**: `{event.category}`")
        st.markdown(f"**Matched Rules**: `{', '.join(event.matched_rules)}`")
        st.markdown(f"**Author**: {event.author}")
        st.markdown(f"**Text**: {event.text}")
    with col2:
        st.subheader("情绪判定")
        color = {"BULLISH": "green", "BEARISH": "red"}.get(event.sentiment, "orange")
        st.markdown(
            f"<span style='color:{color}; font-size:1.8em; font-weight:bold'>"
            f"{event.sentiment} ({event.confidence:.0%})</span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"**Reason**: {event.reason}")
        st.markdown(f"**Mapped Tickers**: `{', '.join(event.tickers)}`")

    # 叠加图
    st.subheader("后验价格叠加 (Market Overlay)")
    with st.spinner("拉取价格并绘图中..."):
        ts = event.ts if event.ts.tzinfo else event.ts.replace(tzinfo=timezone.utc)
        output = build_overlay_chart(
            tickers=event.tickers,
            alert_ts=ts,
            output_dir=settings.OVERLAY_OUTPUT_DIR,
            lookback_hours=lookback,
        )
    if output:
        st.image(output, caption=f"Overlay: {', '.join(event.tickers)}")
    else:
        st.error("价格数据拉取失败（可能处于休市时段），尝试扩大回看窗口。")


if __name__ == "__main__":
    main()
