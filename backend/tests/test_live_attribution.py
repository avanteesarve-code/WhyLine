"""Regression tests for the LIVE anomaly news-attribution data flow.

Covers the reported bug: a live anomaly must never be exposed (REST or
WebSocket) with ``attribution=None``, and the final Attribution must land
on the SAME authoritative stored anomaly that ``/api/anomalies`` serves.

No real Finnhub / FinBERT / Binance: news and sentiment are fakes and the
Binance stream is stubbed. Async paths run via asyncio.run.

Test map (per the fix request):
    TEST 1  live anomaly carries pending on the authoritative object
    TEST 2  completed attribution lands on the SAME stored anomaly (ok+news)
    TEST 3  GET /api/anomalies exposes the updated attribution, never null
    TEST 4  WebSocket sequence: anomaly(pending) then attribution(ok)
    TEST 5  attribution message carries the merge contract the frontend
            store needs (matching anomaly_id, no duplicate anomaly)
    TEST 6  historical (seed) anomalies stay unattributed (None)
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import ingest
import news
import sentiment
from attribute import run_attribution
from config import Settings
from detect import build_anomaly
from main import create_app, stream_worker
from models import Candle, NewsItem, attribution_message
from news import NewsResult
from sentiment import SentimentBatch, SentimentResult
from state import MarketState

START = 1_700_000_000


@pytest.fixture(autouse=True)
def _safety(monkeypatch):
    """Fail loudly if the real news network or model loader is reached."""

    async def _boom_news(*args, **kwargs):
        raise AssertionError("real news.fetch_news must not run in tests")

    def _boom_loader(model_name):
        raise AssertionError("real model loader must not run in tests")

    monkeypatch.setattr(news, "fetch_news", _boom_news)
    monkeypatch.setattr(sentiment, "_default_loader", _boom_loader)


def _settings(**overrides) -> Settings:
    base = dict(
        symbols=("BTCUSDT",),
        window=30,
        min_periods=10,
        iforest_min_buffer=10**9,
    )
    base.update(overrides)
    return Settings(**base)


def _seed_baseline(market: MarketState, n=20, start=START) -> int:
    """Low-volatility candles; returns the next free timestamp."""
    prev = 100.0
    for i in range(n):
        close = 100.0 + (0.1 if i % 2 == 0 else -0.1)
        market.apply_kline(
            "BTCUSDT",
            Candle(
                time=start + 60 * i,
                open=prev,
                high=max(prev, close) * 1.0005,
                low=min(prev, close) * 0.9995,
                close=close,
                volume=10.0,
            ),
            closed=True,
        )
        prev = close
    return start + 60 * n


def _spike(t: int) -> Candle:
    return Candle(time=t, open=100.0, high=112.0, low=99.0, close=110.0, volume=10.0)


def _ok_fakes(monkeypatch):
    """Stub news+sentiment for a successful fallback attribution."""
    item = NewsItem(headline="Sample headline", source="Test Wire",
                    published_at=START)

    async def _fake_news(symbol, anomaly_time, settings_, *, client=None):
        return NewsResult(items=[item], is_fallback=True, reason="no_api_key")

    def _fake_sent(texts, settings_, *, classifier=None):
        return SentimentBatch(
            results=[SentimentResult(label="positive", score=0.5, confidence=0.8)],
            reason=None,
        )

    monkeypatch.setattr(news, "fetch_news", _fake_news)
    monkeypatch.setattr(sentiment, "score_texts", _fake_sent)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# TEST 1: live anomaly is pending on the authoritative object, never null
# ---------------------------------------------------------------------------


def test_1_live_anomaly_pending_on_authoritative_object():
    market = MarketState(_settings())
    spike_time = _seed_baseline(market)

    # The deliver ordering in main.py: apply_kline, then mark_pending on the
    # STORED anomaly BEFORE any await (broadcast). Emulate exactly that.
    fresh = market.apply_kline("BTCUSDT", _spike(spike_time), closed=True)
    assert fresh is not None
    stored = market.mark_pending("BTCUSDT", fresh.id)

    assert stored is not None
    assert stored is fresh  # same authoritative object, not a copy
    assert stored.attribution is not None
    assert stored.attribution.status == "pending"

    # What REST serves for this live anomaly must already be pending.
    exposed = market.recent_anomalies(10)[0].model_dump()["attribution"]
    assert exposed is not None
    assert exposed["status"] == "pending"


def test_1_mark_pending_never_downgrades_final():
    market = MarketState(_settings())
    spike_time = _seed_baseline(market)
    fresh = market.apply_kline("BTCUSDT", _spike(spike_time), closed=True)
    assert fresh is not None
    market.mark_pending("BTCUSDT", fresh.id)
    assert market.get_anomaly("BTCUSDT", fresh.id) is fresh

    # Final states are sticky.
    from models import Attribution as A

    market.set_attribution("BTCUSDT", fresh.id, A(status="ok", items=[]))
    market.mark_pending("BTCUSDT", fresh.id)
    assert fresh.attribution is not None
    assert fresh.attribution.status == "ok"

    assert market.mark_pending("BTCUSDT", "NOPE-0") is None


# ---------------------------------------------------------------------------
# TEST 2: completion updates the SAME stored anomaly (ok + news)
# ---------------------------------------------------------------------------


def test_2_completion_updates_authoritative_anomaly(monkeypatch):
    _ok_fakes(monkeypatch)
    market = MarketState(_settings())
    spike_time = _seed_baseline(market)

    fresh = market.apply_kline("BTCUSDT", _spike(spike_time), closed=True)
    assert fresh is not None
    stored = market.mark_pending("BTCUSDT", fresh.id)
    assert stored is fresh

    async def _noop(payload: dict) -> None:
        return None

    run(run_attribution(stored, market.settings, _noop, state=market))

    authoritative = market.symbols["BTCUSDT"].anomalies[-1]
    assert authoritative is stored  # identity: no copy, no replacement
    assert authoritative.attribution is not None
    assert authoritative.attribution.status == "ok"
    assert len(authoritative.attribution.items) == 1
    assert authoritative.attribution.items[0].headline == "Sample headline"


# ---------------------------------------------------------------------------
# TEST 3: GET /api/anomalies exposes the final attribution, never null
# ---------------------------------------------------------------------------


def test_3_rest_exposes_final_attribution(monkeypatch):
    _ok_fakes(monkeypatch)
    settings = _settings()
    app = create_app(settings, connect_binance=False)
    market: MarketState = app.state.market
    spike_time = _seed_baseline(market)

    fresh = market.apply_kline("BTCUSDT", _spike(spike_time), closed=True)
    assert fresh is not None
    stored = market.mark_pending("BTCUSDT", fresh.id)
    assert stored is not None

    async def _noop(payload: dict) -> None:
        return None

    run(run_attribution(stored, settings, _noop, state=market))

    with TestClient(app) as client:
        body = client.get("/api/anomalies").json()
    assert len(body["anomalies"]) == 1
    live = body["anomalies"][0]
    assert live["id"] == stored.id
    assert live["attribution"] is not None
    assert live["attribution"]["status"] == "ok"
    assert live["attribution"]["items"][0]["headline"] == "Sample headline"


# ---------------------------------------------------------------------------
# TEST 4: WebSocket sequence anomaly(pending) -> attribution(ok)
# ---------------------------------------------------------------------------


def test_4_websocket_pending_then_ok(monkeypatch):
    _ok_fakes(monkeypatch)
    market = MarketState(_settings())
    spike_time = _seed_baseline(market)

    messages: list[dict] = []
    attributed = asyncio.Event()

    async def _recorder(payload: dict) -> None:
        messages.append(payload)
        if payload.get("type") == "attribution":
            attributed.set()

    market.broadcaster.broadcast = _recorder  # type: ignore[method-assign]

    async def _fake_stream(symbols, interval, on_kline, *, base, on_connect=None, stop=None):
        await on_kline("BTCUSDT", _spike(spike_time), True)
        await asyncio.wait_for(attributed.wait(), timeout=10)

    monkeypatch.setattr(ingest, "run_kline_stream", _fake_stream)

    run(stream_worker(market))

    kinds = [m["type"] for m in messages]
    assert kinds == ["candle", "anomaly", "attribution"]
    anomaly_msg, attr_msg = messages[1], messages[2]
    assert anomaly_msg["anomaly"]["attribution"]["status"] == "pending"
    assert attr_msg["anomaly_id"] == anomaly_msg["anomaly"]["id"]
    assert attr_msg["attribution"]["status"] == "ok"
    assert len(attr_msg["attribution"]["items"]) == 1

    # And the authoritative REST state agrees (no null, same id).
    stored = market.symbols["BTCUSDT"].anomalies[-1]
    assert stored.id == attr_msg["anomaly_id"]
    assert stored.attribution is not None
    assert stored.attribution.status == "ok"


# ---------------------------------------------------------------------------
# TEST 5: attribution message merge contract (what the frontend store needs)
# ---------------------------------------------------------------------------


def test_5_attribution_message_merge_contract(monkeypatch):
    """The attribution envelope must let the store update by id.

    Guarantees: type field, anomaly_id equal to the anomaly id, symbol
    equal to the anomaly symbol, and an attribution payload whose status
    and items match the authoritative stored object — so the store can
    merge into the existing anomaly instead of creating a duplicate.
    """
    _ok_fakes(monkeypatch)
    market = MarketState(_settings())
    spike_time = _seed_baseline(market)
    fresh = market.apply_kline("BTCUSDT", _spike(spike_time), closed=True)
    assert fresh is not None
    stored = market.mark_pending("BTCUSDT", fresh.id)
    assert stored is not None

    async def _noop(payload: dict) -> None:
        return None

    run(run_attribution(stored, market.settings, _noop, state=market))
    assert stored.attribution is not None

    msg = attribution_message(stored.id, stored.symbol, stored.attribution)
    assert msg["type"] == "attribution"
    assert msg["anomaly_id"] == stored.id
    assert msg["symbol"] == stored.symbol
    assert set(msg) == {"type", "anomaly_id", "symbol", "attribution"}
    assert msg["attribution"] == stored.attribution.model_dump()
    assert msg["attribution"]["status"] == "ok"
    assert len(msg["attribution"]["items"]) == 1


# ---------------------------------------------------------------------------
# TEST 6: historical (seed) anomalies stay unattributed
# ---------------------------------------------------------------------------


def test_6_historical_anomalies_stay_unattributed():
    settings = _settings()
    app = create_app(settings, connect_binance=False)
    market: MarketState = app.state.market

    candle = Candle(time=START, open=100, high=106, low=100, close=105, volume=50)
    historical = build_anomaly(
        symbol="BTCUSDT", candle=candle, pct_change=5.0, methods=["return_z"],
        return_z=4.2, volume_z=None, iforest_score=None, vol_ratio=2.0,
    )
    assert historical.attribution is None
    market.seed("BTCUSDT", [], [historical])

    # Seed path never assigns pending; unknown ids are left alone.
    assert market.get_anomaly("BTCUSDT", historical.id) is historical
    assert historical.attribution is None

    with TestClient(app) as client:
        anomalies = client.get("/api/anomalies").json()["anomalies"]
        history = client.get("/api/history/BTCUSDT").json()["anomalies"]
    assert len(anomalies) == 1
    assert anomalies[0]["attribution"] is None
    assert len(history) == 1
    assert history[0]["attribution"] is None


def test_6_disabled_attribution_is_milestone_a():
    settings = _settings(attribution_enabled=False)
    market = MarketState(settings)
    spike_time = _seed_baseline(market)
    fresh = market.apply_kline("BTCUSDT", _spike(spike_time), closed=True)
    assert fresh is not None
    # deliver() only marks pending when enabled; without it the anomaly
    # keeps Milestone A behaviour (None).
    assert fresh.attribution is None
    assert market.recent_anomalies(10)[0].attribution is None
