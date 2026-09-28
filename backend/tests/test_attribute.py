"""Offline tests for the Step 4 attribution workflow (Milestone B).

No real Finnhub, Hugging Face, FinBERT, or Binance: news and sentiment are
fakes, the Binance stream is a stubbed coroutine, and an autouse fixture
blocks the real network/model paths. Async paths run via asyncio.run (no
pytest-asyncio).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading

import pytest

import attribute
import ingest
import news
import sentiment
from attribute import attribute_anomaly, build_attribution, run_attribution
from config import Settings
from detect import build_anomaly
from main import stream_worker
from models import Anomaly, Attribution, Candle, NewsItem, attribution_message
from news import NewsResult
from sentiment import SentimentBatch, SentimentResult
from state import MarketState

START = 1_700_000_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _safety(monkeypatch):
    """Fail loudly if the real news network or model loader is reached."""

    async def _boom_news(*args, **kwargs):
        raise AssertionError("real news.fetch_news must not run in tests")

    def _boom_loader(model_name):
        raise AssertionError("real model loader must not run in tests")

    monkeypatch.setattr(news, "fetch_news", _boom_news)
    monkeypatch.setattr(sentiment, "_default_loader", _boom_loader)


@pytest.fixture()
def settings():
    return Settings()


def make_anomaly(symbol="BTCUSDT", t=START) -> Anomaly:
    candle = Candle(time=t, open=100, high=106, low=100, close=105, volume=50)
    return build_anomaly(
        symbol=symbol,
        candle=candle,
        pct_change=5.0,
        methods=["return_z"],
        return_z=4.2,
        volume_z=None,
        iforest_score=None,
        vol_ratio=2.0,
    )


def make_item(headline, **overrides) -> NewsItem:
    base = {
        "headline": headline,
        "source": "Test Wire",
        "url": None,
        "published_at": START,
    }
    base.update(overrides)
    return NewsItem(**base)


def pos_result(score=0.75, confidence=0.8) -> SentimentResult:
    return SentimentResult(label="positive", score=score, confidence=confidence)


def neg_result(score=-0.8, confidence=0.85) -> SentimentResult:
    return SentimentResult(label="negative", score=score, confidence=confidence)


class FakeNews:
    """Stub for news.fetch_news: records calls, replays a result/exception."""

    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls: list[dict] = []

    async def __call__(self, symbol, anomaly_time, settings_, *, client=None):
        self.calls.append(
            {"symbol": symbol, "anomaly_time": anomaly_time, "client": client}
        )
        if self.exc is not None:
            raise self.exc
        return self.result


class FakeSentiment:
    """Stub for sentiment.score_texts: records calls, thread, and inputs."""

    def __init__(self, batch=None, exc=None, on_call=None):
        self.batch = batch
        self.exc = exc
        self.on_call = on_call
        self.calls: list[dict] = []

    def __call__(self, texts, settings_, *, classifier=None):
        self.calls.append(
            {
                "texts": list(texts),
                "classifier": classifier,
                "ident": threading.get_ident(),
            }
        )
        if self.on_call is not None:
            self.on_call()
        if self.exc is not None:
            raise self.exc
        return self.batch


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1-4. Real news + sentiment outcomes
# ---------------------------------------------------------------------------


def test_real_news_successful_sentiment(monkeypatch, settings):
    items = [make_item("Markets rally"), make_item("Stocks plunge")]
    fake_news = FakeNews(NewsResult(items=items, is_fallback=False, reason=None))
    fake_sent = FakeSentiment(
        SentimentBatch(results=[pos_result(), neg_result()], reason=None)
    )
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "ok"
    assert result.is_fallback is False
    assert result.error is None
    assert [i.sentiment_label for i in result.items] == ["positive", "negative"]
    assert [i.sentiment_score for i in result.items] == pytest.approx([0.75, -0.8])
    assert [i.headline for i in result.items] == ["Markets rally", "Stocks plunge"]


def test_real_news_flag_explicit(monkeypatch, settings):
    fake_news = FakeNews(
        NewsResult(items=[make_item("h")], is_fallback=False, reason=None)
    )
    fake_sent = FakeSentiment(SentimentBatch(results=[pos_result()], reason=None))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))
    assert result.status == "ok"
    assert result.is_fallback is False


def test_no_match_calls_no_sentiment(monkeypatch, settings):
    fake_news = FakeNews(NewsResult(items=[], is_fallback=False, reason="no_match"))
    fake_sent = FakeSentiment(SentimentBatch(results=[], reason=None))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "no_news"
    assert result.items == []
    assert result.error is None
    assert fake_sent.calls == []


def test_fallback_news_identifiable(monkeypatch, settings):
    items = [make_item("Sample headline")]
    fake_news = FakeNews(
        NewsResult(items=items, is_fallback=True, reason="no_api_key")
    )
    fake_sent = FakeSentiment(SentimentBatch(results=[pos_result()], reason=None))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "ok"
    assert result.is_fallback is True
    assert "news_no_api_key" in (result.error or "")
    assert result.items[0].sentiment_label == "positive"


# ---------------------------------------------------------------------------
# 5-8. Sentiment failure semantics (news still valid context)
# ---------------------------------------------------------------------------


def test_sentiment_disabled(monkeypatch, settings):
    fake_news = FakeNews(
        NewsResult(items=[make_item("h")], is_fallback=False, reason=None)
    )
    fake_sent = FakeSentiment(SentimentBatch(results=[None], reason="disabled"))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "ok"
    assert result.error == "sentiment_disabled"
    assert result.items[0].sentiment_label is None
    assert result.items[0].sentiment_score is None


def test_model_unavailable(monkeypatch, settings):
    fake_news = FakeNews(
        NewsResult(items=[make_item("h")], is_fallback=False, reason=None)
    )
    fake_sent = FakeSentiment(
        SentimentBatch(results=[None], reason="model_unavailable")
    )
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "ok"
    assert result.error == "sentiment_model_unavailable"


def test_inference_error(monkeypatch, settings):
    fake_news = FakeNews(
        NewsResult(items=[make_item("h")], is_fallback=False, reason=None)
    )
    fake_sent = FakeSentiment(
        SentimentBatch(results=[None], reason="inference_error")
    )
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "ok"
    assert result.error == "sentiment_inference_error"


def test_sentiment_length_mismatch(monkeypatch, settings):
    items = [make_item("one"), make_item("two")]
    fake_news = FakeNews(NewsResult(items=items, is_fallback=False, reason=None))
    fake_sent = FakeSentiment(
        SentimentBatch(results=[pos_result()], reason=None)  # 1 result, 2 items
    )
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "ok"
    assert all(i.sentiment_label is None for i in result.items)
    assert all(i.sentiment_score is None for i in result.items)
    assert "sentiment_inference_error" in (result.error or "")


# ---------------------------------------------------------------------------
# 9-10. Provider failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["http_error", "timeout", "malformed", "no_api_key"])
def test_provider_failures(monkeypatch, settings, reason):
    fake_news = FakeNews(NewsResult(items=[], is_fallback=False, reason=reason))
    fake_sent = FakeSentiment(SentimentBatch(results=[], reason=None))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "error"
    assert result.error == f"news_{reason}"
    assert fake_sent.calls == []


def test_provider_failure_with_fallback(monkeypatch, settings):
    fake_news = FakeNews(
        NewsResult(items=[make_item("Sample")], is_fallback=True, reason="http_error")
    )
    fake_sent = FakeSentiment(SentimentBatch(results=[pos_result()], reason=None))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "ok"
    assert result.is_fallback is True


# ---------------------------------------------------------------------------
# 11-14. Ordering, exact input, index mapping, None passthrough
# ---------------------------------------------------------------------------


def test_news_runs_before_sentiment(monkeypatch, settings):
    order: list[str] = []
    fake_news = FakeNews(
        NewsResult(items=[make_item("h")], is_fallback=False, reason=None)
    )
    orig_news = fake_news.__call__

    async def _rec_news(*args, **kwargs):
        order.append("news")
        return await orig_news(*args, **kwargs)

    def _rec_sent(*args, **kwargs):
        order.append("sentiment")
        return SentimentBatch(results=[pos_result()], reason=None)

    monkeypatch.setattr(news, "fetch_news", _rec_news)
    monkeypatch.setattr(sentiment, "score_texts", _rec_sent)

    run(attribute_anomaly(make_anomaly(), settings))
    assert order == ["news", "sentiment"]


def test_sentiment_receives_exact_headlines(monkeypatch, settings):
    items = [make_item("first headline"), make_item("second headline")]
    fake_news = FakeNews(NewsResult(items=items, is_fallback=False, reason=None))
    fake_sent = FakeSentiment(SentimentBatch(results=[pos_result(), neg_result()], reason=None))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    run(attribute_anomaly(make_anomaly(), settings))

    assert len(fake_sent.calls) == 1
    assert fake_sent.calls[0]["texts"] == ["first headline", "second headline"]


def test_index_mapping(monkeypatch, settings):
    items = [make_item("a"), make_item("b"), make_item("c")]
    fake_news = FakeNews(NewsResult(items=items, is_fallback=False, reason=None))
    batch = SentimentBatch(
        results=[neg_result(), pos_result(), None], reason=None
    )
    fake_sent = FakeSentiment(batch)
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.items[0].sentiment_label == "negative"
    assert result.items[1].sentiment_label == "positive"
    assert result.items[2].sentiment_label is None
    assert result.items[2].sentiment_score is None
    # One individual None adds no error of its own.
    assert result.error is None


# ---------------------------------------------------------------------------
# 15. Input immutability (including reused fallback items)
# ---------------------------------------------------------------------------


def test_news_items_not_mutated(monkeypatch, settings):
    shared = make_item("Reused sample headline")
    for _ in range(2):  # fallback objects may be reused across anomalies
        fake_news = FakeNews(
            NewsResult(items=[shared], is_fallback=True, reason="no_api_key")
        )
        fake_sent = FakeSentiment(
            SentimentBatch(results=[pos_result()], reason=None)
        )
        monkeypatch.setattr(news, "fetch_news", fake_news)
        monkeypatch.setattr(sentiment, "score_texts", fake_sent)

        result = run(attribute_anomaly(make_anomaly(), settings))

        assert result.items[0] is not shared
        assert result.items[0].sentiment_label == "positive"
    assert shared.sentiment_label is None
    assert shared.sentiment_score is None


# ---------------------------------------------------------------------------
# 16. Unexpected exceptions
# ---------------------------------------------------------------------------


def test_fetch_exception_maps_to_internal_error(monkeypatch, settings):
    fake_news = FakeNews(exc=RuntimeError("socket exploded"))
    monkeypatch.setattr(news, "fetch_news", fake_news)

    result = run(attribute_anomaly(make_anomaly(), settings))

    assert result.status == "error"
    assert result.items == []
    assert result.is_fallback is False
    assert result.error == "internal_error"


def test_build_attribution_never_exposes_exceptions():
    result = build_attribution(None, None)  # type: ignore[arg-type]
    assert result.status == "error"
    assert result.error == "internal_error"


def test_build_attribution_is_pure():
    nr = NewsResult(items=[make_item("h")], is_fallback=False, reason=None)
    sb = SentimentBatch(results=[pos_result()], reason=None)
    result = build_attribution(nr, sb)
    assert isinstance(result, Attribution)
    assert nr.items[0].sentiment_label is None  # input untouched


# ---------------------------------------------------------------------------
# 17-18. run_attribution broadcast behaviour
# ---------------------------------------------------------------------------


def test_run_attribution_attaches_and_broadcasts(monkeypatch, settings):
    items = [make_item("Markets rally")]
    fake_news = FakeNews(NewsResult(items=items, is_fallback=False, reason=None))
    fake_sent = FakeSentiment(SentimentBatch(results=[pos_result()], reason=None))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    anomaly = make_anomaly()
    messages: list[dict] = []

    async def _broadcast(payload: dict) -> None:
        messages.append(payload)

    async def _main():
        await run_attribution(anomaly, settings, _broadcast)

    run(_main())

    assert isinstance(anomaly.attribution, Attribution)
    assert anomaly.attribution.status == "ok"
    assert len(messages) == 1
    assert messages[0] == attribution_message(
        anomaly.id, anomaly.symbol, anomaly.attribution
    )
    assert messages[0]["type"] == "attribution"
    assert messages[0]["anomaly_id"] == anomaly.id
    assert set(messages[0]) == {"type", "anomaly_id", "symbol", "attribution"}


def test_broadcast_failure_contained(monkeypatch, settings):
    fake_news = FakeNews(NewsResult(items=[], is_fallback=False, reason="no_match"))
    monkeypatch.setattr(news, "fetch_news", fake_news)

    anomaly = make_anomaly()

    async def _failing_broadcast(payload: dict) -> None:
        raise RuntimeError("socket gone")

    async def _main():
        await run_attribution(anomaly, settings, _failing_broadcast)

    run(_main())  # must not raise
    assert anomaly.attribution is not None
    assert anomaly.attribution.status == "no_news"


# ---------------------------------------------------------------------------
# 19-20. Thread offloading and non-blocking behaviour
# ---------------------------------------------------------------------------


def test_sentiment_runs_off_event_loop(monkeypatch, settings):
    fake_news = FakeNews(
        NewsResult(items=[make_item("h")], is_fallback=False, reason=None)
    )
    fake_sent = FakeSentiment(SentimentBatch(results=[pos_result()], reason=None))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

    async def _main():
        loop_ident = threading.get_ident()
        await run_attribution(make_anomaly(), settings, _noop_broadcast)
        return loop_ident

    async def _noop_broadcast(payload: dict) -> None:
        return None

    loop_ident = run(_main())
    assert len(fake_sent.calls) == 1
    assert fake_sent.calls[0]["ident"] != loop_ident


def test_attribution_does_not_block_event_loop(monkeypatch, settings):
    gate = threading.Event()
    entered = threading.Event()
    fake_news = FakeNews(
        NewsResult(items=[make_item("h")], is_fallback=False, reason=None)
    )
    monkeypatch.setattr(news, "fetch_news", fake_news)

    def _blocking_score(texts, settings_, *, classifier=None):
        entered.set()
        assert gate.wait(timeout=10), "gate released by test teardown"
        return SentimentBatch(results=[pos_result()], reason=None)

    monkeypatch.setattr(sentiment, "score_texts", _blocking_score)
    messages: list[dict] = []

    async def _broadcast(payload: dict) -> None:
        messages.append(payload)

    async def _main():
        task = asyncio.create_task(
            run_attribution(make_anomaly(), settings, _broadcast)
        )
        while not entered.is_set():
            await asyncio.sleep(0.01)
        # Inference is blocked in the worker thread: the loop must progress.
        ticks = 0
        for _ in range(5):
            await asyncio.sleep(0.01)
            ticks += 1
        assert ticks == 5
        assert not task.done()
        gate.set()
        await asyncio.wait_for(task, timeout=10)

    try:
        run(_main())
    finally:
        gate.set()
    assert len(messages) == 1
    assert messages[0]["type"] == "attribution"


# ---------------------------------------------------------------------------
# 21-23. Live deliver path through stream_worker
# ---------------------------------------------------------------------------


def _e2e_settings(**overrides) -> Settings:
    base = dict(
        symbols=("BTCUSDT",),
        window=30,
        min_periods=10,
        iforest_min_buffer=10**9,
    )
    base.update(overrides)
    return Settings(**base)


def _seed_baseline(market: MarketState, n=20, start=START) -> int:
    """Varying low-volatility candles; returns the next free timestamp."""
    closes = [100.0 + (0.1 if i % 2 == 0 else -0.1) for i in range(n)]
    prev = 100.0
    for i, close in enumerate(closes):
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


def test_live_deliver_pending_then_attribution(monkeypatch):
    settings_ = _e2e_settings()
    market = MarketState(settings_)
    spike_time = _seed_baseline(market)

    fake_news = FakeNews(
        NewsResult(items=[make_item("Markets rally")], is_fallback=False, reason=None)
    )
    fake_sent = FakeSentiment(SentimentBatch(results=[pos_result()], reason=None))
    monkeypatch.setattr(news, "fetch_news", fake_news)
    monkeypatch.setattr(sentiment, "score_texts", fake_sent)

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
    assert set(attr_msg) == {"type", "anomaly_id", "symbol", "attribution"}
    stored = market.symbols["BTCUSDT"].anomalies[-1]
    assert stored.id == attr_msg["anomaly_id"]
    assert stored.attribution is not None
    assert stored.attribution.status == "ok"
    assert stored.attribution.items[0].sentiment_label == "positive"


def test_live_deliver_disabled_is_milestone_a(monkeypatch):
    settings_ = _e2e_settings(attribution_enabled=False)
    market = MarketState(settings_)
    spike_time = _seed_baseline(market)
    market.broadcaster.broadcast = _recorder_list(messages := [])  # type: ignore[method-assign]

    async def _fake_stream(symbols, interval, on_kline, *, base, on_connect=None, stop=None):
        await on_kline("BTCUSDT", _spike(spike_time), True)
        await asyncio.sleep(0.2)

    monkeypatch.setattr(ingest, "run_kline_stream", _fake_stream)

    run(stream_worker(market))

    assert [m["type"] for m in messages] == ["candle", "anomaly"]
    assert messages[1]["anomaly"]["attribution"] is None
    assert market.symbols["BTCUSDT"].anomalies[-1].attribution is None


def _recorder_list(messages: list[dict]):
    async def _recorder(payload: dict) -> None:
        messages.append(payload)

    return _recorder


def test_stream_worker_cancels_inflight_attribution(monkeypatch):
    settings_ = _e2e_settings()
    market = MarketState(settings_)
    spike_time = _seed_baseline(market)

    gate = threading.Event()
    fake_news = FakeNews(
        NewsResult(items=[make_item("h")], is_fallback=False, reason=None)
    )
    monkeypatch.setattr(news, "fetch_news", fake_news)

    def _blocking_score(texts, settings_, *, classifier=None):
        gate.wait(timeout=10)
        return SentimentBatch(results=[pos_result()], reason=None)

    monkeypatch.setattr(sentiment, "score_texts", _blocking_score)
    messages: list[dict] = []
    market.broadcaster.broadcast = _recorder_list(messages)  # type: ignore[method-assign]

    async def _fake_stream(symbols, interval, on_kline, *, base, on_connect=None, stop=None):
        await on_kline("BTCUSDT", _spike(spike_time), True)
        await asyncio.Event().wait()  # run until cancelled

    monkeypatch.setattr(ingest, "run_kline_stream", _fake_stream)

    async def _main():
        worker = asyncio.create_task(stream_worker(market))
        # Wait until the anomaly broadcast lands, so attribution is in flight.
        for _ in range(200):
            if any(m["type"] == "anomaly" for m in messages):
                break
            await asyncio.sleep(0.01)
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        assert worker.cancelled() or worker.done()

    try:
        run(_main())
    finally:
        gate.set()  # release the worker thread so nothing leaks

    assert [m["type"] for m in messages] == ["candle", "anomaly"]
    assert market.symbols["BTCUSDT"].anomalies[-1].attribution.status == "pending"


# ---------------------------------------------------------------------------
# 24. Settings parsing
# ---------------------------------------------------------------------------


def test_attribution_enabled_default():
    assert Settings().attribution_enabled is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        (" TRUE ", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_attribution_enabled_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("ATTRIBUTION_ENABLED", raw)
    assert Settings.from_env().attribution_enabled is expected


def test_attribution_enabled_unset_default(monkeypatch):
    monkeypatch.delenv("ATTRIBUTION_ENABLED", raising=False)
    assert Settings.from_env().attribution_enabled is True
