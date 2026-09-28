"""WhyLine backend — FastAPI app serving REST snapshots + a live WebSocket.

Pipeline (Milestone A):
    INGEST  Binance kline stream + REST backfill        (ingest.py)
    DETECT  rolling z-scores + Isolation Forest         (detect.py)
    VISUALIZE  /api/history snapshots + /ws live feed   (this file)

Run:  uvicorn main:app --reload --port 8000  (from backend/, venv active)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import detect
import ingest
from config import Settings
from config import settings as default_settings
from models import (
    Attribution,
    Candle,
    anomaly_message,
    candle_message,
    hello_message,
)
from state import MarketState

import attribute

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("whyline")


async def bootstrap(market: MarketState) -> None:
    """Backfill history for every symbol and pre-annotate it with anomalies."""
    s = market.settings

    async def load(symbol: str) -> None:
        for attempt in (1, 2):
            try:
                candles = await ingest.fetch_history(
                    symbol, s.interval, s.history_limit, base=s.rest_base
                )
                break
            except Exception as exc:
                log.warning(
                    "backfill %s attempt %d failed: %r", symbol, attempt, exc
                )
                if attempt == 2:
                    return  # stream will still feed this symbol live
                await asyncio.sleep(1.0)
        anomalies = detect.scan_history(symbol, candles, s)
        market.seed(symbol, candles, anomalies)
        log.info(
            "seeded %s: %d candles, %d historical anomalies",
            symbol,
            len(candles),
            len(anomalies),
        )

    await asyncio.gather(*(load(sym) for sym in s.symbols))


async def attribute_history(market: MarketState) -> None:
    """Attach related news to the backfilled (historical) anomalies.

    Runs once in the background after ``bootstrap``, newest anomaly first so
    the entries a viewer sees first fill in first. Every anomaly is marked
    ``pending`` up front, so REST never serves a historical anomaly without
    an attribution state while this runs. Sequential on purpose: all
    anomalies share one cached provider response and FinBERT inference is
    serialized anyway, so parallel tasks would only add provider pressure.
    Matching stays time-bounded per anomaly (see ``news.fetch_news``).
    """
    s = market.settings
    backlog = sorted(
        (
            a
            for ss in market.symbols.values()
            for a in ss.anomalies
            if not a.live and a.attribution is None
        ),
        key=lambda a: a.time,
        reverse=True,
    )
    for anomaly in backlog:
        market.mark_pending(anomaly.symbol, anomaly.id)
    for anomaly in backlog:
        await attribute.run_attribution(
            anomaly, s, market.broadcaster.broadcast, state=market
        )
    if backlog:
        log.info("attributed %d historical anomalies", len(backlog))


async def stream_worker(market: MarketState) -> None:
    """Consume the Binance stream and fan events out to frontend sockets."""
    s = market.settings
    attribution_tasks: set[asyncio.Task] = set()

    async def deliver(symbol: str, candle: Candle, closed: bool) -> None:
        anomaly = market.apply_kline(symbol, candle, closed)
        if anomaly is not None and s.attribution_enabled:
            # Mark pending on the AUTHORITATIVE stored anomaly BEFORE the
            # first await below: REST readers can never observe a live
            # anomaly with attribution=None, and the broadcast + background
            # task below operate on the same stored object the API serves.
            # The final attribution arrives later as a separate message;
            # attribution is never awaited here, so the feed never waits
            # for news/sentiment.
            stored = market.mark_pending(symbol, anomaly.id)
            anomaly = stored if stored is not None else anomaly
            if stored is None:
                anomaly.attribution = Attribution(status="pending")
        await market.broadcaster.broadcast(candle_message(symbol, candle, closed))
        if anomaly is not None:
            log.info("ANOMALY %s: %s", symbol, anomaly.explanation)
            await market.broadcaster.broadcast(anomaly_message(anomaly))
            if s.attribution_enabled:
                task = asyncio.create_task(
                    attribute.run_attribution(
                        anomaly, s, market.broadcaster.broadcast, state=market
                    ),
                    name=f"attribution-{anomaly.id}",
                )
                attribution_tasks.add(task)
                task.add_done_callback(attribution_tasks.discard)

    async def resync(first: bool) -> None:
        if first:
            return
        # Fill candles missed while disconnected (apply_kline dedupes).
        for symbol in s.symbols:
            try:
                candles = await ingest.fetch_history(
                    symbol, s.interval, 5, base=s.rest_base
                )
            except Exception as exc:
                log.warning("resync %s failed: %s", symbol, exc)
                continue
            for candle in candles:
                await deliver(symbol, candle, True)

    try:
        await ingest.run_kline_stream(
            s.symbols, s.interval, deliver, base=s.ws_base, on_connect=resync
        )
    finally:
        # Shut down background attribution without stalling the feed:
        # cancel in-flight tasks, absorb their outcome, re-raise if we
        # are being cancelled ourselves.
        if attribution_tasks:
            for pending in attribution_tasks:
                pending.cancel()
            await asyncio.gather(*attribution_tasks, return_exceptions=True)


def create_app(
    settings: Settings | None = None, *, connect_binance: bool = True
) -> FastAPI:
    s = settings or default_settings
    market = MarketState(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks: list[asyncio.Task] = []
        if connect_binance:
            await bootstrap(market)
            tasks.append(
                asyncio.create_task(stream_worker(market), name="binance-stream")
            )
            if s.attribution_enabled:
                tasks.append(
                    asyncio.create_task(
                        attribute_history(market), name="history-attribution"
                    )
                )
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="WhyLine", version="0.1.0", lifespan=lifespan)
    app.state.market = market

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "clients": market.broadcaster.client_count,
            "symbols": len(market.symbols),
        }

    @app.get("/api/symbols")
    def symbols() -> dict:
        return {"symbols": list(s.symbols), "interval": s.interval}

    @app.get("/api/history/{symbol}")
    def history(symbol: str) -> dict:
        snapshot = market.history(symbol.upper())
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}")
        candles, anomalies = snapshot
        return {
            "symbol": symbol.upper(),
            "interval": s.interval,
            "candles": [c.model_dump() for c in candles],
            "anomalies": [a.model_dump() for a in anomalies],
        }

    @app.get("/api/anomalies")
    def anomalies(limit: int = 100) -> dict:
        return {
            "anomalies": [a.model_dump() for a in market.recent_anomalies(limit)]
        }

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        market.broadcaster.add(ws)
        try:
            await ws.send_json(hello_message(s.symbols, s.interval))
            while True:
                await ws.receive_text()  # keepalive/no-op; raises on disconnect
        except WebSocketDisconnect:
            pass
        finally:
            market.broadcaster.remove(ws)

    return app


app = create_app()
