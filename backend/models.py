"""Pydantic schemas shared by ingest, detection, and the API layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Candle(BaseModel):
    """One OHLCV candle.

    `time` is the candle *open* time in UNIX seconds (UTC) — the format
    Lightweight Charts consumes directly (Binance sends milliseconds; the
    ingest layer divides by 1000 exactly once, here at the boundary).
    """

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class NewsItem(BaseModel):
    """One headline attached to an anomaly (Milestone B semantic context)."""

    headline: str
    source: str | None = None  # publisher name
    url: str | None = None
    published_at: int | None = None  # UNIX seconds UTC, same convention as Candle.time
    sentiment_label: Literal["positive", "negative", "neutral"] | None = None
    sentiment_score: float | None = None  # signed score in roughly [-1, +1]


class Attribution(BaseModel):
    """Semantic attribution for an anomaly (filled by a later step)."""

    status: Literal["pending", "ok", "no_news", "error"]
    is_fallback: bool = False  # True when headlines come from a local canned set
    items: list[NewsItem] = Field(default_factory=list)
    error: str | None = None


class Anomaly(BaseModel):
    """A flagged candle plus the quantitative evidence behind the flag."""

    id: str  # "<symbol>-<time>", stable across restarts
    symbol: str
    time: int  # open time of the flagged candle, UNIX seconds
    price: float  # close of the flagged candle
    direction: Literal["up", "down"]
    methods: list[str]  # subset of {"return_z", "volume_z", "isolation_forest"}
    return_z: float | None = None
    volume_z: float | None = None
    iforest_score: float | None = None  # higher = more anomalous
    pct_change: float  # close-over-close % move of the flagged candle
    vol_ratio: float  # volume / trailing median volume
    explanation: str  # human-readable one-liner shown in the UI
    attribution: Attribution | None = None  # None = not attributed (Milestone A behaviour / backfilled history).


# ---------------------------------------------------------------------------
# WebSocket message envelopes (backend -> frontend)
# ---------------------------------------------------------------------------


def hello_message(symbols: tuple[str, ...] | list[str], interval: str) -> dict:
    return {"type": "hello", "symbols": list(symbols), "interval": interval}


def candle_message(symbol: str, candle: Candle, closed: bool) -> dict:
    return {
        "type": "candle",
        "symbol": symbol,
        "closed": closed,
        "candle": candle.model_dump(),
    }


def anomaly_message(anomaly: Anomaly) -> dict:
    return {"type": "anomaly", "anomaly": anomaly.model_dump()}


def attribution_message(
    anomaly_id: str, symbol: str, attribution: Attribution
) -> dict:
    """Enrichment arrives separately because the frontend dedupes anomalies by id."""
    return {
        "type": "attribution",
        "anomaly_id": anomaly_id,
        "symbol": symbol,
        "attribution": attribution.model_dump(),
    }
