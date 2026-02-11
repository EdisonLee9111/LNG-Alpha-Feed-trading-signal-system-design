from dataclasses import dataclass, field
import os
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# 领域知识映射 (Domain Knowledge Mappings)
# 不要把规则写死在代码里，全部集中在这里维护
# ---------------------------------------------------------------------------

# 资产映射：类别 -> 股票/期货代码
# FastClassifier 命中类别后，直接决定去看哪些 K 线图
ASSET_MAP: dict[str, list[str]] = {
    "LNG_SUPPLY":    ["UNG", "TTF=F"],       # 美国天然气基金, 欧洲基准
    "AUSTRALIA_LNG": ["UNG"],                 # 澳洲项目主要影响全球供给
    "US_EXPORT":     ["EQT", "AR"],           # 美国出口 -> 看生产商
    "JAPAN_POWER":   ["9501.T", "9503.T"],    # 东京电力, 关西电力
    "OIL_MACRO":     ["CL=F", "XOM"],         # 原油期货, 埃克森美孚
    "SHIPPING":      ["UNG"],                 # 航运中断 -> 供给侧
    "LABOR_STRIKE":  ["UNG", "TTF=F"],        # 罢工 -> 供给中断
}

# 关键词规则：Regex 模式 -> 类别
# 只要命中这些词，直接分类，不需要问 AI
RULES: dict[str, str] = {
    "LNG_SUPPLY":    r"(?i)(gorgon|prelude|wheatstone|qatar|north field|force majeure|outage|shutdown|lng\s+terminal)",
    "AUSTRALIA_LNG": r"(?i)(woodside|ichthys|darwin\s+lng|gladstone|pluto\s+lng)",
    "US_EXPORT":     r"(?i)(freeport|sabine pass|cheniere|liquefaction|train\s+\d|cameron\s+lng|corpus christi)",
    "JAPAN_POWER":   r"(?i)(jepx|tepco|kansai electric|nuclear restart|mihama|takahama|thermal limit|sendai reactor)",
    "OIL_MACRO":     r"(?i)(opec|brent|wti|crude\s+oil|oil\s+embargo|strategic reserve)",
    "SHIPPING":      r"(?i)(panama.{0,15}draft|suez.{0,15}traffic|maran gas|lng\s+carrier|charter\s+rate)",
    "LABOR_STRIKE":  r"(?i)(strike|union|offshore alliance|work ban|industrial action)",
}

# 噪音过滤：碰到这些词直接丢弃
NOISE_PATTERN: str = r"(?i)(climate\s+change|net\s*zero|activist|protest|webinar|podcast|hiring|green\s*washing)"

# 默认兜底资产（没命中任何规则时）
DEFAULT_TICKERS: list[str] = ["UNG"]


# ---------------------------------------------------------------------------
# 运行时配置 (Runtime Settings)
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

    # LLM (留空 = 跳过 NLP 层，只走 FastClassifier)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")


settings = Settings()
