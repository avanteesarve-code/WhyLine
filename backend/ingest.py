"""Binance market-data ingestion.

Only public market-data endpoints are used (no API key):

    REST  GET /api/v3/klines                     -> historical backfill
    WS    /stream?streams=<sym>@kline_<interval> -> live candle updates

Defaults point at data-api.binance.vision / data-stream.binance.vision,
Binance's official hosts dedicated to public market data;
api.binance.com / stream.binance.com:9443 work as drop-in fallbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Awaitable, Callable, Iterable

import certifi
import httpx
import websockets

from models import Candle

log = logging.getLogger("whyline.ingest")

KLINES_PATH = "/api/v3/klines"
MAX_KLINE_LIMIT = 1000  # Binance per-request cap


# ---------------------------------------------------------------------------
# Parsing (pure, unit-tested)
# ---------------------------------------------------------------------------


def parse_rest_kline(row: list) -> tuple[Candle, int]:
    """Convert one REST kline row into (candle, close_time_ms).

    Row layout per Binance API docs:
    [open_time_ms, open, high, low, close, volume, close_time_ms, ...]
    """
    candle = Candle(
        time=int(row[0]) // 1000,  # ms -> s exactly once, at the boundary
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )
    return candle, int(row[6])


def parse_stream_message(raw: str | bytes) -> tuple[str, Candle, bool] | None:
    """Parse one WebSocket payload into (symbol, candle, closed).

    Accepts both combined-stream envelopes ({"stream": ..., "data": {...}})
    and raw kline events; returns None for anything that is not a kline.
    """
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    data = obj.get("data", obj)
    if not isinstance(data, dict) or data.get("e") != "kline":
        return None
    k = data["k"]
    candle = Candle(
        time=int(k["t"]) // 1000,
        open=float(k["o"]),
        high=float(k["h"]),
        low=float(k["l"]),
        close=float(k["c"]),
        volume=float(k["v"]),
    )
    return str(k["s"]).upper(), candle, bool(k["x"])


def stream_url(base: str, symbols: Iterable[str], interval: str) -> str:
    streams = "/".join(f"{s.lower()}@kline_{interval}" for s in symbols)
    return f"{base}/stream?streams={streams}"


# ---------------------------------------------------------------------------
# Historical backfill (REST)
# ---------------------------------------------------------------------------


async def fetch_history(
    symbol: str,
    interval: str,
    limit: int,
    *,
    base: str,
    client: httpx.AsyncClient | None = None,
    now_ms: int | None = None,
) -> list[Candle]:
    """Fetch up to `limit` most-recent *closed* candles, oldest first.

    Paginates backwards past Binance's 1000-per-request cap. The final,
    still-forming candle Binance includes is dropped — the live stream
    delivers and finishes it, so history and stream stay consistent.
    """
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        rows: list[list] = []
        end_time: int | None = None
        remaining = limit + 1  # +1 covers the dropped still-open candle
        while remaining > 0:
            params: dict = {
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": min(remaining, MAX_KLINE_LIMIT),
            }
            if end_time is not None:
                params["endTime"] = end_time
            resp = await client.get(base + KLINES_PATH, params=params)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            rows = batch + rows
            remaining -= len(batch)
            if len(batch) < params["limit"]:
                break  # reached the start of the symbol's history
            end_time = int(batch[0][0]) - 1
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        candles: list[Candle] = []
        for row in rows:
            candle, close_ms = parse_rest_kline(row)
            if close_ms > now:
                continue  # still forming
            candles.append(candle)
        return candles[-limit:]
    finally:
        if own_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Live stream (WebSocket)
# ---------------------------------------------------------------------------

KlineHandler = Callable[[str, Candle, bool], Awaitable[None]]


async def run_kline_stream(
    symbols: Iterable[str],
    interval: str,
    on_kline: KlineHandler,
    *,
    base: str,
    on_connect: Callable[[bool], Awaitable[None]] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Consume Binance kline events forever, reconnecting with backoff.

    `on_connect(first)` fires after every (re)connect so the caller can
    resync candles missed while disconnected.
    """
    url = stream_url(base, list(symbols), interval)
    # Explicit certifi CA bundle: python.org macOS installs ship no system
    # certs for stdlib ssl, which would fail the TLS handshake silently-ish.
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    backoff = 1.0
    first = True
    while stop is None or not stop.is_set():
        try:
            async with websockets.connect(
                url, ssl=ssl_ctx, ping_interval=20, ping_timeout=20
            ) as ws:
                log.info("Binance stream connected (%s)", url.split("?")[0])
                if on_connect is not None:
                    await on_connect(first)
                first = False
                backoff = 1.0
                async for raw in ws:
                    parsed = parse_stream_message(raw)
                    if parsed is not None:
                        await on_kline(*parsed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "Binance stream dropped (%s: %s); reconnecting in %.0fs",
                type(exc).__name__,
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)
