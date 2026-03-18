# LNG-Alpha-Feed 

This system treats news as a **state-conditional signal**: an identical facility outage report will generate divergent directions and confidence intervals depending on whether it occurs during a tight, low-inventory winter or a loose, over-supplied summer. 

`LNG-Alpha-Feed` is a high-performance, real-time social media intelligence radar purpose-built for the global Liquefied Natural Gas (LNG) market. It ingests the Bluesky Firehose, filters for structural supply/demand events, and cross-references them against live market tension states (inventory percentiles, futures curve backwardation, volatility) to generate institutional-grade context-aware intelligence.

## Implementation Status

- [x] **Real-time Firehose Ingestion**: Bluesky Jetstream integration & async queuing.
- [x] **Event Filtration**: Millisecond keyword & whitelist filtering engine (`FastClassifier`).
- [x] **Telemetry & Alerting**: Telegram Bot integration & Streamlit overlay dashboard (`Watchtower`).
- [x] **Market State Injection**: Background async polling with EIA weekly storage percentile & 20-day rolling volatility (`MarketStateManager`).
- [x] **State-Conditional LLM Engine**: Prompt engineering and dynamic context injection for the sentiment engine.
- [ ] **EU Inventory (AGSI)**: European gas storage data integration for TTF-side state conditioning.
- [ ] **Local SLM Migration**: Transition from external LLM API to fine-tuned local 7B-14B model.
- [ ] **Event Deduplication**: Clustering and lifecycle management for duplicate/superseding signals.
- [ ] **Per-Region Sentiment Output**: Structured US/EU/Asia directional bifurcation beyond the `reason` field.
- [ ] **Signal Backtesting**: P&L attribution and confidence calibration framework.

## The "Alpha-Discovery" Project Ecosystem

This repository is one crucial node in a three-part structural analysis ecosystem for global gas markets:
1. **LNG-Alpha-Feed [This Repo]**: *The Radar*. Captures real-time streaming data, generating state-conditional natural language alerts.
2. **[Structural-Event-Study-Framework-for-LNG-Energy-Markets](https://github.com/EdisonLee9111/Structural-Event-Study-Framework-for-LNG-Energy-Markets)**: *The Laboratory*. Consumes the historical output from this feed to rigorously quantify the structural market impact, performing academic-style placebo testing and state-conditional event studies.
3. **[LNG_Arbitrage_Monitor](https://github.com/EdisonLee9111/-Global-LNG-Arbitrage-Monitor)**: *The Executioner*. Real-time pricing dashboard tracking the physical bounds of inter-basin arbitrage (US to EU/Asia), acting on the structural shifts identified by the Feed and Event Study.

## The Funnel Architecture: Fast Events meets Slow State

The system is designed to handle high-velocity, high-noise social streams with zero-latency signal injection. It achieves this by decoupling the "Hot Path" (streaming text assessment) from the "Cold Path" (market background computation).

*(Note: The Market Context mechanism below relies on the principle that Large Language Models perform vastly superior probabilistic reasoning when anchored by deterministic numerical context, akin to Retrieval-Augmented Generation (RAG) paradigms for time-series states).*

### Architecture: Data Flow & Subsystems

```text
  [ Hot Path: Real-Time Funnel ]          [ Cold Path: Market Context ]

      (Bluesky Firehose)                     (Yahoo/EIA/AGSI Data)
              │                                        │
              ▼                                        ▼
      ┌───────────────┐                       ┌─────────────────┐
      │   Harvester   │                       │  Market State   │
      │ (Whitelisting │                       │    Manager      │
      │  & Filtering) │                       │ (Async Polling) │
      └───────┬───────┘                       └────────┬────────┘
              │                                        │
              ▼                                        ▼
      ┌───────────────┐                       ┌─────────────────┐
      │  Async Queue  │                       │ In-Memory State │
      │ (Buffer Node) │                       │    Snapshot     │
      └───────┬───────┘                       └────────┬────────┘
              │                                        │
              ▼                                        │
      ┌───────────────┐                            Instant 
      │Fast Classifier│                          Zero-Latency
      │ (Rules Engine)│<────────────────┼      Reads       │
      │ Discards Noise│                 │      (Inventory, │
      └───────┬───────┘                 │      Volatility, │
              │ Events + Tickers        │      Spreads)    │
              ▼                         │                  │
      ┌───────────────┐                 │                  │
      │Async Sentiment│                 │                  │
      │    Engine     │<────────────────┘                  │
      │(State-Condit. │                                    │
      │    Logic)     │                                    │
      └───────┬───────┘                                    │
              │                                            │
              ▼ Context-Aware Signals                      │
       (Bullish / Bearish)                                 │
              │                                            │
              ├──► [ Telegram Bot Alerts ]                 │
              │                                            │
              ├──► [ Streamlit Dashboard ]                 │
              │                                            │
              └──► [ Market Overlay Charts ]               │
```

## Key Architectural Decisions

### 1. Progressive Baseline Bootstrapping (Cold Start Mitigation)
To prevent the pipeline from blocking during startup while pulling massive historical API data to compute percentiles (e.g., past 1-year daily volatility):
- **Phase 1 (Sync)**: Fetches only the last 30~90 days to derive a rapid, "rough" percentile baseline. The Firehose ingestion begins immediately.
- **Phase 2 (Async)**: A background worker fetches the 1~3 year deep history, recalculating the standard deviation/percentile distributions. Once complete, it silently hot-swaps the high-precision baseline in memory.

### 2. Guarding against LLM Hallucinations (Optional State)
If the backend fails to poll a specific data point (e.g., EU AGSI Inventory API down or Yahoo Finance rate limit), the metric is set to `None`. 
The System Prompt is structured to **explicitly report missing data**: 
> *"Inventory data currently unavailable, rely strictly on price action and volatility for conditions."* 
This strictly avoids injecting a default "50th percentile" that the LLM might hallucinate around to justify a false narrative.

### 3. Asymmetric Regional Bifurcation in CoT
A single event can be Bullish for Europe (TTF) but Bearish for the US (Henry Hub)—for example, an export terminal explosion in Texas means supply gets trapped stateside while starving global markets.
While the current MVP outputs a unified primary direction, the LLM utilizes a **Chain of Thought (CoT)** prompt that *mandates* the separation of regional impact in the `reason` field:
> *Output Format requirement: "If the event causes divergent outcomes between US (HH) and EU/Asia (TTF/JKM), you MUST explicitly articulate the bifurcation in the reason statement."*

### 4. Dynamic Polling Intervals
Market State polling actively modulates itself based on market hours. During active HH/TTF trading windows (e.g., 09:00-14:30 EST), the system refreshes volatility and curve spreads every **5 minutes**. During off-hours, it throttles polling down to **30-60 minutes** to preserve API rate limits without losing meaningful context.

### 5. Evolution to Local Small Language Models (SLMs)
While the current MVP utilizes external APIs (e.g., GPT-4o-mini) to rapidly validate the "Context-Aware Signal" logic, relying on external APIs introduces unacceptable latency, rate limiting risks during high-volatility events, and data privacy concerns for institutional-grade trading systems. Therefore, **transitioning to a miniaturized, locally deployed domain-specific model (SLM) is an inevitable technical refactoring in the project's lifecycle**. The current API calls effectively serve as a data pipeline to generate high-quality, structured CoT reasoning logs. Ultimately, these logs will be used to fine-tune a dedicated 7B-14B parameter local model (e.g., Llama-3, Qwen) on local GPU hardware, achieving a system with zero-latency, high-throughput signal execution that is entirely isolated from external rate limits.
