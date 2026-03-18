from dataclasses import dataclass, field
import os
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Domain Knowledge Mappings
# Don't hardcode rules in code; maintain everything centralized here
# ---------------------------------------------------------------------------

# Asset mapping: category -> stock/futures code
# After FastClassifier hits a category, directly determines which charts to view
ASSET_MAP: dict[str, list[str]] = {
    "LNG_SUPPLY":    ["UNG", "TTF=F"],       # US natural gas fund, EU benchmark
    "AUSTRALIA_LNG": ["UNG"],                 # Australian projects primarily affect global supply
    "US_EXPORT":     ["EQT", "AR"],           # US exports -> view producers
    "JAPAN_POWER":   ["9501.T", "9503.T"],    # Tokyo Electric, Kansai Electric
    "OIL_MACRO":     ["CL=F", "XOM"],         # Crude oil futures, ExxonMobil
    "SHIPPING":      ["UNG"],                 # Shipping disruption -> supply side
    "LABOR_STRIKE":  ["UNG", "TTF=F"],        # Strike -> supply disruption
}

# Keyword rules: Regex pattern -> category
# Direct classification on keyword match, no AI needed
RULES: dict[str, str] = {
    "LNG_SUPPLY":    r"(?i)(gorgon|prelude|wheatstone|qatar|north field|force majeure|outage|shutdown|lng\s+terminal)",
    "AUSTRALIA_LNG": r"(?i)(woodside|ichthys|darwin\s+lng|gladstone|pluto\s+lng)",
    "US_EXPORT":     r"(?i)(freeport|sabine pass|cheniere|liquefaction|train\s+\d|cameron\s+lng|corpus christi)",
    "JAPAN_POWER":   r"(?i)(jepx|tepco|kansai electric|nuclear restart|mihama|takahama|thermal limit|sendai reactor)",
    "OIL_MACRO":     r"(?i)(opec.{0,10}(cut|quota|meet|output)|brent\s+crude|wti\s+crude|crude\s+oil|oil\s+embargo|strategic.{0,5}reserve)",
    "SHIPPING":      r"(?i)(panama.{0,15}draft|suez.{0,15}traffic|maran gas|lng\s+carrier|charter\s+rate)",
    "LABOR_STRIKE":  r"(?i)(lng.{0,20}strike|gas.{0,20}strike|offshore alliance|work ban|industrial action|chevron.{0,15}strike|woodside.{0,15}strike)",
}

# Noise filtering: discard directly when these words are encountered
NOISE_PATTERN: str = r"(?i)(climate\s+change|net\s*zero|activist|protest|webinar|podcast|hiring|green\s*washing)"

# Default fallback asset (when no rules are matched)
DEFAULT_TICKERS: list[str] = ["UNG"]


# ---------------------------------------------------------------------------
# Runtime Settings
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Jetstream / Harvester Configuration
# ---------------------------------------------------------------------------

JETSTREAM_URL: str = os.getenv(
    "JETSTREAM_URL",
    "wss://jetstream2.us-east.bsky.network/subscribe"
    "?wantedCollections=app.bsky.feed.post",
)

# Whitelist — only process posts from these accounts
# DID exact match; handle suffix match (e.g. "blas" can match javierblas.bsky.social)
# Leave empty = no filtering (process all posts, filter only by keywords)
WHITELIST_DIDS: set[str] = {
    # Placeholder example — replace with your real account DIDs
    # "did:plc:xxxxxxx",
}

WHITELIST_HANDLES: set[str] = {
    # Placeholder example — energy journalists / industry accounts
    "javierblas.bsky.social",      # Javier Blas (Bloomberg energy)
    "faborrell.bsky.social",       # Placeholder
    "energyintel.bsky.social",     # Placeholder
}

# Cross-commodity keyword pre-filtering (firehose volume is huge, do rough filter before FastClassifier)
# Note: shorter words are more prone to false matches. Use 2-3 word phrases or proper nouns, avoid generic words like "strike"/"nuclear"/"crude"
CROSS_COMMODITY_KEYWORDS: tuple[str, ...] = (
    # LNG / Natural Gas
    " lng ", " lng,", " lng.", "lng terminal", "lng cargo", "lng carrier",
    "jkm ", "ttf ", "natural gas", "gas price", "gas market",
    "henry hub", "nbp ",
    # Trading / Spot
    "spot cargo", "tender offer", "fob cargo", "des cargo",
    "force majeure",
    # Japan Power
    "jepx", "japan power", "tepco", "nuclear restart", "thermal limit",
    # Facilities / Projects (proper nouns, very low false positives)
    "freeport lng", "sabine pass", "cheniere", "cameron lng", "corpus christi",
    "gorgon", "prelude", "wheatstone", "ichthys", "darwin lng", "pluto lng",
    "woodside", "qatar energy", "north field",
    # Crude oil (context-limited)
    "opec", "brent crude", "wti crude", "crude oil", "oil embargo",
    # Shipping (combined words)
    "maran gas", "panama canal", "suez canal", "charter rate",
    # Strike (energy context-limited)
    "offshore alliance", "work ban", "industrial action",
)


# ---------------------------------------------------------------------------
# Runtime Settings
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    COMPLIANCE_MODE: bool = os.getenv("COMPLIANCE_MODE", "true").lower() == "true"
    COMPLIANCE_LOG: str = os.getenv("COMPLIANCE_LOG", "data/trade_signals.log")
    DASHBOARD_JSONL: str = os.getenv("DASHBOARD_JSONL", "data/dashboard_feed.jsonl")
    OVERLAY_LOOKBACK_HOURS: int = int(os.getenv("OVERLAY_LOOKBACK_HOURS", "12"))
    OVERLAY_OUTPUT_DIR: str = os.getenv("OVERLAY_OUTPUT_DIR", "data/overlays")

    # -----------------------------------------------------------------------
    # Market State Data Sources (APIs)
    # -----------------------------------------------------------------------
    # Yahoo Finance tickers for price & volatility baselines
    MARKET_TICKERS: list[str] = field(default_factory=lambda: ["NG=F", "TTF=F", "JKM=F"])

    # Inventory data external API placeholder
    EIA_API_KEY: str = os.getenv("EIA_API_KEY", "").strip()
    EIA_API_URL: str = "https://api.eia.gov/v2/natural-gas/stor/wkly/data/"

    AGSI_API_KEY: str = os.getenv("AGSI_API_KEY", "").strip()
    AGSI_API_URL: str = "https://agsi.gie.eu/api"

    # -----------------------------------------------------------------------
    # Polling Intervals (seconds)
    # -----------------------------------------------------------------------
    # Active trading hours (09:00 - 14:30 EST) refresh every 5 minutes, after-hours every hour
    POLL_INTERVAL_ACTIVE: int = int(os.getenv("POLL_INTERVAL_ACTIVE", "300"))
    POLL_INTERVAL_IDLE: int = int(os.getenv("POLL_INTERVAL_IDLE", "3600"))

    # Deep lookback time range (build historical percentile baseline)
    BASELINE_DAYS_FAST: int = 90    # Cold start phase 1: quickly fetch past 90 days
    BASELINE_DAYS_DEEP: int = 365 * 3 # After stabilization async deep calculation: fetch past 3 years

    # EIA inventory data polling
    EIA_POLL_INTERVAL: int = 3600       # seconds, EIA data updates weekly, check at most every hour
    EIA_SEASONAL_YEARS: int = 5         # seasonal percentile lookback years


    # LLM (leave empty = skip NLP layer, use only FastClassifier)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")

    # Harvester running mode
    # "whitelist" = whitelist accounts only; "keyword" = all keyword filtering; "both" = whitelist priority + keyword fallback
    HARVESTER_MODE: str = os.getenv("HARVESTER_MODE", "keyword")


settings = Settings()
