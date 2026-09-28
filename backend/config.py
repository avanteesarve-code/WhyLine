"""Central configuration for the WhyLine backend.

Every value can be overridden via environment variables (see .env.example).
The default symbols are deep-liquidity USDT pairs whose sudden moves usually
have a searchable cause — chosen so Milestone B can viably attribute them:

    BTCUSDT   macro / ETF-flow driven
    ETHUSDT   ETF + protocol upgrades
    SOLUSDT   ecosystem news, network outages
    XRPUSDT   regulatory / court rulings
    BNBUSDT   exchange (Binance) news
    DOGEUSDT  social-media driven, sharp spikes
    ADAUSDT   protocol milestones
    AVAXUSDT  ecosystem / partnership news
    LINKUSDT  partnership announcements
    LTCUSDT   payments adoption / halving cycles
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

DEFAULT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "LTCUSDT",
)


def _env_symbols(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_SYMBOLS
    parsed = tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    return parsed or DEFAULT_SYMBOLS


def _env_api_key(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _env_flag(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # Market data
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    interval: str = "1m"
    history_limit: int = 1000  # closed candles backfilled per symbol
    # Binance public market-data hosts (no API key required).
    # stream.binance.com / api.binance.com work as fallbacks.
    rest_base: str = "https://data-api.binance.vision"
    ws_base: str = "wss://data-stream.binance.vision"

    # Rolling z-score detector
    window: int = 60  # trailing baseline candles (~1h at 1m)
    min_periods: int = 30  # candles required before a z-score is trusted
    return_z_threshold: float = 3.0
    volume_z_threshold: float = 3.5
    cooldown: int = 3  # candles a symbol stays muted after a flag

    # Isolation Forest detector
    iforest_buffer: int = 240  # feature rows kept for streaming refits (~4h)
    iforest_min_buffer: int = 180  # rows required before scoring starts
    iforest_refit_every: int = 15  # closed candles between refits
    iforest_contamination: float = 0.01

    # In-memory caps per symbol
    max_candles_kept: int = 3000
    max_anomalies_kept: int = 300

    # Milestone B news fetch (Finnhub market news; not wired into anomalies yet)
    news_api_key: str | None = field(default=None, repr=False)
    news_api_base: str = "https://finnhub.io/api/v1"
    news_window_before: int = 3600  # seconds before the anomaly time
    news_window_after: int = 900  # seconds after the anomaly time
    news_max_items: int = 5
    news_timeout: float = 5.0  # seconds per provider request
    news_fallback_enabled: bool = True  # serve local sample headlines on failure

    # Milestone B FinBERT sentiment (standalone layer in sentiment.py; not
    # wired into anomalies yet)
    sentiment_enabled: bool = True
    sentiment_model: str = "ProsusAI/finbert"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            symbols=_env_symbols(os.getenv("SYMBOLS")),
            interval=os.getenv("INTERVAL", cls.interval),
            history_limit=int(os.getenv("HISTORY_LIMIT", cls.history_limit)),
            rest_base=os.getenv("BINANCE_REST_BASE", cls.rest_base),
            ws_base=os.getenv("BINANCE_WS_BASE", cls.ws_base),
            window=int(os.getenv("DETECT_WINDOW", cls.window)),
            min_periods=int(os.getenv("DETECT_MIN_PERIODS", cls.min_periods)),
            return_z_threshold=float(
                os.getenv("RETURN_Z_THRESHOLD", cls.return_z_threshold)
            ),
            volume_z_threshold=float(
                os.getenv("VOLUME_Z_THRESHOLD", cls.volume_z_threshold)
            ),
            cooldown=int(os.getenv("ANOMALY_COOLDOWN", cls.cooldown)),
            iforest_buffer=int(os.getenv("IFOREST_BUFFER", cls.iforest_buffer)),
            iforest_min_buffer=int(
                os.getenv("IFOREST_MIN_BUFFER", cls.iforest_min_buffer)
            ),
            iforest_refit_every=int(
                os.getenv("IFOREST_REFIT_EVERY", cls.iforest_refit_every)
            ),
            iforest_contamination=float(
                os.getenv("IFOREST_CONTAMINATION", cls.iforest_contamination)
            ),
            news_api_key=_env_api_key(os.getenv("NEWS_API_KEY")),
            news_api_base=os.getenv("NEWS_API_BASE", cls.news_api_base),
            news_window_before=int(
                os.getenv("NEWS_WINDOW_BEFORE", cls.news_window_before)
            ),
            news_window_after=int(
                os.getenv("NEWS_WINDOW_AFTER", cls.news_window_after)
            ),
            news_max_items=int(os.getenv("NEWS_MAX_ITEMS", cls.news_max_items)),
            news_timeout=float(os.getenv("NEWS_TIMEOUT", cls.news_timeout)),
            news_fallback_enabled=_env_flag(
                os.getenv("NEWS_FALLBACK_ENABLED"), cls.news_fallback_enabled
            ),
            sentiment_enabled=_env_flag(
                os.getenv("SENTIMENT_ENABLED"), cls.sentiment_enabled
            ),
            sentiment_model=(os.getenv("SENTIMENT_MODEL") or "").strip()
            or cls.sentiment_model,
        )


settings = Settings.from_env()
