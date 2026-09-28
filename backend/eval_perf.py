"""WhyLine latency micro-benchmarks (evaluation tooling, offline by default).

Measures, on this machine, with no network except when --binance is given:

1. detection latency   : MarketState.apply_kline per closed candle
                         (rolling z-scores + periodic Isolation Forest refit)
2. REST snapshot cost   : recent_anomalies() + model_dump() per API call
3. WS loopback latency : broadcaster.broadcast -> websocket client receipt
                         (loopback; a lower bound — true browser receipt
                         needs browser instrumentation and is reported N/A)

Usage (from backend/, venv active):
    python eval_perf.py
    python eval_perf.py --binance   # also times one Binance klines fetch

Prints n / avg / median / min / max in milliseconds. Deterministic seed.
"""

from __future__ import annotations

import argparse
import statistics
import time

from fastapi.testclient import TestClient

from config import Settings
from main import create_app
from models import Candle, attribution_message
from state import MarketState

START = 1_700_000_000


def _stats_ms(samples: list[float]) -> dict:
    ms = [s * 1000.0 for s in samples]
    return {
        "n": len(ms),
        "avg_ms": round(statistics.mean(ms), 3),
        "median_ms": round(statistics.median(ms), 3),
        "min_ms": round(min(ms), 3),
        "max_ms": round(max(ms), 3),
    }


def detection_latency(n_candles: int = 300) -> dict:
    """Time apply_kline over flat candles with periodic spikes."""
    settings = Settings(symbols=("BTCUSDT",))
    market = MarketState(settings)
    # Warm the rolling state first (not timed).
    prev = 100.0
    for i in range(240):
        close = 100.0 + (0.05 if i % 2 == 0 else -0.05)
        market.apply_kline(
            "BTCUSDT",
            Candle(time=START + 60 * i, open=prev,
                   high=max(prev, close) * 1.0005,
                   low=min(prev, close) * 0.9995, close=close, volume=10.0),
            closed=True,
        )
        prev = close
    samples: list[float] = []
    anomalies = 0
    for i in range(n_candles):
        t = START + 60 * (240 + i)
        if i % 25 == 24:  # spike candle, usually flags an anomaly
            candle = Candle(time=t, open=100.0, high=112.0, low=99.0,
                            close=110.0, volume=10.0)
        else:
            close = prev + (0.05 if i % 2 == 0 else -0.05)
            candle = Candle(time=t, open=prev,
                            high=max(prev, close) * 1.0005,
                            low=min(prev, close) * 0.9995,
                            close=close, volume=10.0)
            prev = close
        t0 = time.perf_counter()
        anomaly = market.apply_kline("BTCUSDT", candle, closed=True)
        samples.append(time.perf_counter() - t0)
        anomalies += anomaly is not None
    out = _stats_ms(samples)
    out["anomalies_flagged"] = anomalies
    return out


def rest_snapshot_latency(n_calls: int = 50) -> dict:
    settings = Settings(symbols=("BTCUSDT",))
    app = create_app(settings, connect_binance=False)
    market: MarketState = app.state.market
    prev = 100.0
    for i in range(300):
        close = 100.0 + (0.05 if i % 2 == 0 else -0.05)
        market.apply_kline(
            "BTCUSDT",
            Candle(time=START + 60 * i, open=prev,
                   high=max(prev, close) * 1.0005,
                   low=min(prev, close) * 0.9995, close=close, volume=10.0),
            closed=True,
        )
        prev = close
    samples: list[float] = []
    with TestClient(app) as client:
        for _ in range(n_calls):
            t0 = time.perf_counter()
            resp = client.get("/api/anomalies?limit=100")
            assert resp.status_code == 200
            resp.json()
            samples.append(time.perf_counter() - t0)
    return _stats_ms(samples)


def ws_loopback_latency(n_msgs: int = 20) -> dict:
    """Broadcast -> receipt over a loopback websocket (lower bound)."""
    from detect import build_anomaly

    settings = Settings(symbols=("BTCUSDT",))
    app = create_app(settings, connect_binance=False)
    market: MarketState = app.state.market
    candle = Candle(time=START, open=100, high=106, low=100, close=105, volume=50)
    anomaly = build_anomaly(
        symbol="BTCUSDT", candle=candle, pct_change=5.0, methods=["return_z"],
        return_z=4.2, volume_z=None, iforest_score=None, vol_ratio=2.0,
    )
    from models import anomaly_message as _am

    msg = _am(anomaly)
    samples: list[float] = []
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            for _ in range(n_msgs):
                t0 = time.perf_counter()

                async def _send() -> None:
                    await market.broadcaster.broadcast(msg)

                import asyncio

                asyncio.run(_send())
                got = ws.receive_json()
                assert got["type"] == "anomaly"
                samples.append(time.perf_counter() - t0)
    _ = attribution_message  # helper exists; unrelated to timing
    return _stats_ms(samples)


def binance_fetch_latency() -> dict:
    """Time one real Binance klines fetch (needs network)."""
    import asyncio

    import ingest

    settings = Settings(symbols=("BTCUSDT",))

    async def go() -> list[Candle]:
        return await ingest.fetch_history(
            "BTCUSDT", settings.interval, 1000, base=settings.rest_base
        )

    t0 = time.perf_counter()
    candles = asyncio.run(go())
    dt = time.perf_counter() - t0
    return {"n": 1, "candles": len(candles), "elapsed_ms": round(dt * 1000.0, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description="WhyLine latency benchmarks")
    parser.add_argument("--binance", action="store_true",
                        help="also time one live Binance fetch (network)")
    args = parser.parse_args()

    det = detection_latency()
    print("detection apply_kline per candle:", det)
    print("rest GET /api/anomalies:", rest_snapshot_latency())
    print("ws broadcast->receipt (loopback lower bound):", ws_loopback_latency())
    if args.binance:
        print("binance klines fetch (1000 candles):", binance_fetch_latency())


if __name__ == "__main__":
    main()
