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
from pathlib import Path

from dotenv import load_dotenv

# Anchor to backend/.env regardless of the process working directory
# (uvicorn, pytest, and one-off scripts may all start elsewhere).
# override=False (the default) keeps real environment variables higher
# priority than the .env file.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

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
    # Relaxed lookback (seconds) for the second match pass in news.py: the
    # free-tier crypto feed is sparse (~1 article/hour), so a strict 1h
    # window chronically yields nothing even when keyword-relevant real
    # articles exist a few hours back. The relaxed pass still requires an
    # asset-keyword match and prefers the most recent items; recency stays
    # visible via each item's published_at.
    news_relaxed_window_before: int = 86400  # 24h extended lookback
    # Shared provider-response cache TTL (seconds). All symbols query the
    # same category endpoint, so simultaneous anomalies reuse one response.
    # Failures are never cached; 0 disables the cache.
    news_cache_ttl: float = 120.0
    news_max_items: int = 5
    news_timeout: float = 5.0  # seconds per provider request
    news_fallback_enabled: bool = True  # serve local sample headlines on failure

    # Milestone B FinBERT sentiment (standalone layer in sentiment.py; not
    # wired into anomalies yet)
    sentiment_enabled: bool = True
    sentiment_model: str = "ProsusAI/finbert"

    # Milestone B attribution workflow (attribute.py): fetch news + score
    # headlines in the background after a live anomaly is broadcast.
    attribution_enabled: bool = True

    @property
    def finnhub_api_key(self) -> str | None:
        """Alias for the Finnhub token (FINNHUB_API_KEY, legacy NEWS_API_KEY).

        Single source of truth stays ``news_api_key`` (consumed by news.py);
        this alias exists so configuration checks can use the provider name
        directly. Never logged (see ``repr=False`` on ``news_api_key``).
        """
        return self.news_api_key

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
            news_api_key=_env_api_key(
                os.getenv("FINNHUB_API_KEY") or os.getenv("NEWS_API_KEY")
            ),
            news_api_base=os.getenv("NEWS_API_BASE", cls.news_api_base),
            news_window_before=int(
                os.getenv("NEWS_WINDOW_BEFORE", cls.news_window_before)
            ),
            news_window_after=int(
                os.getenv("NEWS_WINDOW_AFTER", cls.news_window_after)
            ),
            news_relaxed_window_before=int(
                os.getenv(
                    "NEWS_RELAXED_WINDOW_BEFORE",
                    cls.news_relaxed_window_before,
                )
            ),
            news_cache_ttl=float(
                os.getenv("NEWS_CACHE_TTL", cls.news_cache_ttl)
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
            attribution_enabled=_env_flag(
                os.getenv("ATTRIBUTION_ENABLED"), cls.attribution_enabled
            ),
        )


settings = Settings.from_env()
