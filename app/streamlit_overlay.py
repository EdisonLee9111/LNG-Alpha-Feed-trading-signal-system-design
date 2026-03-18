"""
Streamlit page — Signal retrospective view + post-validation overlay chart

Usage:
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
    st.caption("First predict (FastClassifier → asset mapping), then verify (post-validation price overlay).")

    lookback = st.slider("Lookback (hours)", 1, 72, value=settings.OVERLAY_LOOKBACK_HOURS)

    events = _load_events(settings.DASHBOARD_JSONL)
    if not events:
        st.warning("No signal data yet. Please run `python -m app.main` to trigger events.")
        return

    idx = st.selectbox(
        "Select signal event",
        range(len(events)),
        format_func=lambda i: (
            f"{events[i].ts.isoformat()} | {events[i].category} | "
            f"{events[i].sentiment} | {', '.join(events[i].tickers)}"
        ),
        index=len(events) - 1,
    )
    event = events[idx]

    # Signal Details
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Signal Details")
        st.markdown(f"**Category**: `{event.category}`")
        st.markdown(f"**Matched Rules**: `{', '.join(event.matched_rules)}`")
        st.markdown(f"**Author**: {event.author}")
        st.markdown(f"**Text**: {event.text}")
    with col2:
        st.subheader("Sentiment Analysis")
        color = {"BULLISH": "green", "BEARISH": "red"}.get(event.sentiment, "orange")
        st.markdown(
            f"<span style='color:{color}; font-size:1.8em; font-weight:bold'>"
            f"{event.sentiment} ({event.confidence:.0%})</span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"**Reason**: {event.reason}")
        st.markdown(f"**Mapped Tickers**: `{', '.join(event.tickers)}`")

    # Overlay Chart
    st.subheader("Post-validation Price Overlay (Market Overlay)")
    with st.spinner("Fetching prices and drawing chart..."):
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
        st.error("Price data fetch failed (possibly during market closure). Try expanding the lookback window.")


if __name__ == "__main__":
    main()
