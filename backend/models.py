"""Pydantic schemas shared by ingest, detection, and the API layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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
