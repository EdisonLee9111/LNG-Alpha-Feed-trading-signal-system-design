# LNG-Alpha-Feed 🌋

Most generic energy sentiment tools treat news as absolute signals (e.g., "an outage is bullish"). This system treats news as a **state-conditional signal**: an identical facility outage report will generate divergent directions and confidence intervals depending on whether it occurs during a tight, low-inventory winter or a loose, over-supplied summer. 

`LNG-Alpha-Feed` is a high-performance, real-time social media intelligence radar purpose-built for the global Liquefied Natural Gas (LNG) market. It ingests the Bluesky Firehose, filters for structural supply/demand events, and cross-references them against live market tension states (inventory percentiles, futures curve backwardation, volatility) to generate institutional-grade context-aware intelligence.

## 🏗️ Implementation Status

- [x] **Real-time Firehose Ingestion**: Bluesky Jetstream integration & async queuing.
- [x] **Event Filtration**: Millisecond keyword & whitelist filtering engine (`FastClassifier`).
- [x] **Telemetry & Alerting**: Telegram Bot integration & Streamlit overlay dashboard (`Watchtower`).
- [ ] **Market State Injection**: Background async polling for inventory/Curve data (`MarketStateManager` - *In Development*).
- [ ] **State-Conditional LLM Engine**: Prompt engineering and dynamic context injection for the sentiment engine - (*In Development*).

## 🌍 The "Alpha-Discovery" Project Ecosystem

This repository is one crucial node in a three-part structural analysis ecosystem for global gas markets:
1. **LNG-Alpha-Feed [This Repo]**: 🌊 *The Radar*. Captures real-time streaming data, generating state-conditional natural language alerts.
2. **[Alpha-Discovery (Event Study)](https://github.com/EdisonLee9111/LNG-Alpha-Event-Study)**: 🔬 *The Laboratory*. Consumes the historical output from this feed to rigorously quantify the structural market impact, performing academic-style placebo testing and state-conditional event studies.
3. **[LNG_Arbitrage_Monitor](https://github.com/EdisonLee9111/LNG_Arbitrage_Monitor)**: ⚖️ *The Executioner*. Real-time pricing dashboard tracking the physical bounds of inter-basin arbitrage (US to EU/Asia), acting on the structural shifts identified by the Feed and Event Study.

## The Funnel Architecture: Fast Events meets Slow State

The system is designed to handle high-velocity, high-noise social streams with zero-latency signal injection. It achieves this by decoupling the "Hot Path" (streaming text assessment) from the "Cold Path" (market background computation).

*(Note: The Market Context mechanism below relies on the principle that Large Language Models perform vastly superior probabilistic reasoning when anchored by deterministic numerical context, akin to Retrieval-Augmented Generation (RAG) paradigms for time-series states).*

```mermaid
flowchart TD
    %% Define styles
    classDef external fill:#f9f,stroke:#333,stroke-width:2px;
    classDef hotpath fill:#bbf,stroke:#333,stroke-width:2px;
    classDef coldpath fill:#bfb,stroke:#333,stroke-width:2px;
    classDef output fill:#fbb,stroke:#333,stroke-width:2px;

    %% External Data Sources
    subgraph External Sources
        BSKY[Bluesky Jetstream Firehose]:::external
        YF[Yahoo Finance / EIA / AGSI]:::external
    end

    %% Cold Path
    subgraph Market Context (Cold Path)
        MSM[Market State Manager\n(Background Async Task)]:::coldpath
        CACHE[(In-Memory State Snapshot)]:::coldpath
        MSM -- Polls Every 5-30 mins --> YF
        MSM -. Updates .-> CACHE
        YF -. progressive fallback/retries .-> MSM
    end

    %% Hot Path Funnel
    subgraph Real-Time Funnel (Hot Path)
        HARV[Harvester\nWhitelist & Keyword Filter]:::hotpath
        Q1[Async Queue]:::hotpath
        FC[Fast Classifier\nMillisecond Rules Engine]:::hotpath
        LLM[Async Sentiment Engine\nState-Conditional Logic]:::hotpath
    end

    %% Signal Outputs
    subgraph Watchtower & Alerting
        TELE[Telegram Bot Alerts]:::output
        DASH[Streamlit Real-Time Dashboard]:::output
        OVERLAY[Market Overlay Charts]:::output
    end

    %% Data Flow
    BSKY -->|WebSocket Streaming| HARV
    HARV -->|Cleaned Text| Q1
    Q1 --> FC
    FC -- "Noise (Discarded)" --> devnull(("Discard"))
    FC -- "Structural Event + Tickers" --> LLM
    CACHE -- "Instant Read (Zero Latency)" --> FC

    %% The Injection: Crucial Step
    CACHE -. "Inject Market Percentiles\n(Inventories, Spreads, Volatility)" .-> LLM
    
    LLM -->|Context-Aware Signal\n(Bullish/Bearish/Neutral + Reason)| TELE
    LLM --> DASH
    LLM --> OVERLAY
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
