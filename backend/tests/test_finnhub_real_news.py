"""Regression tests for REAL Finnhub crypto-news attribution.

The pipeline (pending -> async attribution -> ok) is covered elsewhere;
these tests pin the *provider* wiring: a configured FINNHUB_API_KEY must
yield real provider articles (never SAMPLE headlines), while a missing or
failing provider must fall back safely without crashing the stream.

Fully offline: Finnhub HTTP is mocked via httpx.MockTransport. No test
here needs a real API key or makes network calls.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import news
import sentiment
from attribute import attribute_anomaly, run_attribution
from config import DEFAULT_SYMBOLS, Settings
from detect import build_anomaly
from main import create_app
from models import Candle, NewsItem, attribution_message
from news import (
    NewsResult,
    asset_keywords,
    clear_news_cache,
    fetch_news,
)
from sentiment import SentimentBatch, SentimentResult


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_news_cache()
    yield
    clear_news_cache()

T = 1_700_000_000
REAL_KEY = "finnhub-live-test-key"


def settings_with_key(**overrides) -> Settings:
    base = dict(news_api_key=REAL_KEY)
    base.update(overrides)
    return Settings(**base)


def finnhub_article(headline, dt, *, source="CoinDesk",
                    url="https://www.coindesk.com/markets/real-story",
                    summary=""):
    """One Finnhub /news object in the documented response shape."""
    return {
        "category": "crypto",
        "datetime": dt,
        "headline": headline,
        "id": 1234567,
        "image": "https://image.finnhub.io/x.jpg",
        "related": "BTC",
        "source": source,
        "summary": summary,
        "url": url,
    }


def fetch_with(handler, symbol="BTCUSDT", anomaly_time=T, **overrides):
    settings = settings_with_key(**overrides)
    requests: list[httpx.Request] = []

    def tracking(request):
        requests.append(request)
        return handler(request)

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(tracking)
        ) as client:
            return await fetch_news(symbol, anomaly_time, settings, client=client)

    return asyncio.run(go()), requests


def clear_key_env(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("NEWS_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# 1. Configuration: FINNHUB_API_KEY honored, NEWS_API_KEY kept as alias
# ---------------------------------------------------------------------------


class TestKeyConfig:
    def test_finnhub_key_read_from_env(self, monkeypatch):
        clear_key_env(monkeypatch)
        monkeypatch.setenv("FINNHUB_API_KEY", "fh-live-123")
        assert Settings.from_env().news_api_key == "fh-live-123"

    def test_finnhub_key_takes_precedence(self, monkeypatch):
        clear_key_env(monkeypatch)
        monkeypatch.setenv("FINNHUB_API_KEY", "fh-primary")
        monkeypatch.setenv("NEWS_API_KEY", "legacy-secondary")
        assert Settings.from_env().news_api_key == "fh-primary"

    def test_legacy_news_api_key_still_works(self, monkeypatch):
        clear_key_env(monkeypatch)
        monkeypatch.setenv("NEWS_API_KEY", "legacy-only")
        assert Settings.from_env().news_api_key == "legacy-only"

    def test_blank_keys_become_none(self, monkeypatch):
        clear_key_env(monkeypatch)
        monkeypatch.setenv("FINNHUB_API_KEY", "   ")
        monkeypatch.setenv("NEWS_API_KEY", "  ")
        assert Settings.from_env().news_api_key is None


# ---------------------------------------------------------------------------
# 2. Real provider is called correctly and articles map to Attribution items
# ---------------------------------------------------------------------------


class TestRealProviderCall:
    def test_endpoint_auth_and_params(self):
        seen: dict = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["params"] = dict(request.url.params)
            seen["token"] = request.headers.get("X-Finnhub-Token")
            seen["url"] = str(request.url)
            return httpx.Response(200, json=[])

        fetch_with(handler)
        assert seen["path"] == "/api/v1/news"
        assert seen["params"].get("category") == "crypto"
        assert seen["token"] == REAL_KEY
        assert REAL_KEY not in seen["url"]
        assert "token" not in {k.lower() for k in seen["params"]}

    def test_real_articles_mapped_not_sample(self):
        articles = [
            finnhub_article("Bitcoin jumps as ETF inflows surge", T - 120,
                            source="CoinDesk",
                            url="https://www.coindesk.com/markets/2024/real-btc",
                            summary="bitcoin ETF demand"),
            finnhub_article("Ether follows bitcoin higher", T - 60,
                            source="Reuters",
                            url="https://www.reuters.com/markets/real-eth",
                            summary="bitcoin spillover"),
        ]
        result, _ = fetch_with(lambda req: httpx.Response(200, json=articles))
        assert result.reason is None
        assert result.is_fallback is False
        assert len(result.items) == 2
        first = result.items[0]
        # Closest publication time first.
        assert first.headline == "Ether follows bitcoin higher"
        assert first.source == "Reuters"
        assert first.url == "https://www.reuters.com/markets/real-eth"
        assert first.published_at == T - 60
        # Real provider URLs — never sample placeholders.
        for item in result.items:
            assert isinstance(item, NewsItem)
            assert item.url and item.url.startswith("https://")
            assert item.source != "WhyLine sample data"
        # Sentiment is a later step's job, not the fetcher's.
        assert all(i.sentiment_label is None for i in result.items)


# ---------------------------------------------------------------------------
# 3. Attribution reaches ok with real provider URLs (mocked, no network)
# ---------------------------------------------------------------------------


def test_attribution_ok_with_real_provider_urls(monkeypatch):
    articles = [
        finnhub_article("Bitcoin jumps as ETF inflows surge", T - 120,
                        source="CoinDesk",
                        url="https://www.coindesk.com/markets/2024/real-btc",
                        summary="bitcoin ETF demand"),
    ]

    async def fake_fetch(symbol, anomaly_time, settings_, *, client=None):
        assert settings_.news_api_key == REAL_KEY
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=articles)
        )
        async with httpx.AsyncClient(transport=transport) as c:
            return await fetch_news(symbol, anomaly_time, settings_, client=c)

    def fake_score(texts, settings_, *, classifier=None):
        assert texts == ["Bitcoin jumps as ETF inflows surge"]
        return SentimentBatch(
            results=[SentimentResult(label="positive", score=0.6,
                                     confidence=0.8)],
            reason=None,
        )

    monkeypatch.setattr(news, "fetch_news", fake_fetch)
    monkeypatch.setattr(sentiment, "score_texts", fake_score)

    candle = Candle(time=T, open=100, high=106, low=100, close=105, volume=50)
    anomaly = build_anomaly(
        symbol="BTCUSDT", candle=candle, pct_change=5.0, methods=["return_z"],
        return_z=4.2, volume_z=None, iforest_score=None, vol_ratio=2.0,
    )
    result = asyncio.run(
        attribute_anomaly(anomaly, settings_with_key())
    )
    assert result.status == "ok"
    assert result.is_fallback is False
    assert len(result.items) == 1
    item = result.items[0]
    assert item.headline == "Bitcoin jumps as ETF inflows surge"
    assert item.url == "https://www.coindesk.com/markets/2024/real-btc"
    assert item.source == "CoinDesk"
    assert item.sentiment_label == "positive"


# ---------------------------------------------------------------------------
# 3b. Relaxed pass: stale-but-relevant real news beats sample headlines
# ---------------------------------------------------------------------------


def _relaxed_fetch(article_dt, **overrides):
    articles = [
        finnhub_article("Bitcoin steadies after volatile session", article_dt,
                        source="CoinDesk",
                        url="https://www.coindesk.com/markets/real-stale-btc",
                        summary="bitcoin price action"),
    ]
    return fetch_with(lambda req: httpx.Response(200, json=articles), **overrides)


def test_relaxed_window_serves_real_not_sample():
    # 2h old: outside the strict 1h window, inside the 24h relaxed lookback.
    result, _ = _relaxed_fetch(T - 7200)
    assert result.reason == "relaxed_window"
    assert result.is_fallback is False
    assert len(result.items) == 1
    assert result.items[0].url == "https://www.coindesk.com/markets/real-stale-btc"
    assert result.items[0].source == "CoinDesk"


def test_strict_hit_stays_strict():
    result, _ = _relaxed_fetch(T - 120)
    assert result.reason is None
    assert result.is_fallback is False


def test_relaxed_orders_most_recent_first():
    articles = [
        finnhub_article("Bitcoin older story", T - 20000, source="CoinDesk",
                        url="https://www.coindesk.com/old",
                        summary="bitcoin"),
        finnhub_article("Bitcoin newer story", T - 7000, source="Reuters",
                        url="https://www.reuters.com/newer",
                        summary="bitcoin"),
    ]
    result, _ = fetch_with(lambda req: httpx.Response(200, json=articles))
    assert result.reason == "relaxed_window"
    assert [i.headline for i in result.items] == [
        "Bitcoin newer story", "Bitcoin older story"]
    assert all(i.url for i in result.items)


def test_truly_empty_response_stays_fallback():
    # No usable provider article at all (empty feed): sample fallback kept.
    result, _ = fetch_with(lambda req: httpx.Response(200, json=[]))
    assert result.reason == "no_match"
    assert result.is_fallback is True
    assert all(i.url is None for i in result.items)  # sample placeholders


def test_relaxed_honors_max_items_and_operator_window():
    articles = [
        finnhub_article(f"Bitcoin story {n}", T - 7000 - n,
                        source="CoinDesk",
                        url=f"https://www.coindesk.com/{n}",
                        summary="bitcoin")
        for n in range(5)
    ]
    result, _ = fetch_with(
        lambda req: httpx.Response(200, json=articles), news_max_items=2)
    assert result.reason == "relaxed_window"
    assert len(result.items) == 2
    # Operator-enlarged strict window wins over the relaxed default.
    strict, _ = fetch_with(
        lambda req: httpx.Response(200, json=articles[:1]),
        news_window_before=9000)
    assert strict.reason is None


def test_attribution_ok_with_relaxed_real_news(monkeypatch):
    articles = [
        finnhub_article("Bitcoin steadies after volatile session", T - 7200,
                        source="CoinDesk",
                        url="https://www.coindesk.com/markets/real-stale-btc",
                        summary="bitcoin price action"),
    ]

    async def fake_fetch(symbol, anomaly_time, settings_, *, client=None):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=articles)
        )
        async with httpx.AsyncClient(transport=transport) as c:
            return await fetch_news(symbol, anomaly_time, settings_, client=c)

    def fake_score(texts, settings_, *, classifier=None):
        return SentimentBatch(
            results=[SentimentResult(label="neutral", score=0.1,
                                     confidence=0.6)],
            reason=None,
        )

    monkeypatch.setattr(news, "fetch_news", fake_fetch)
    monkeypatch.setattr(sentiment, "score_texts", fake_score)

    candle = Candle(time=T, open=100, high=106, low=100, close=105, volume=50)
    anomaly = build_anomaly(
        symbol="BTCUSDT", candle=candle, pct_change=5.0, methods=["return_z"],
        return_z=4.2, volume_z=None, iforest_score=None, vol_ratio=2.0,
    )
    result = asyncio.run(attribute_anomaly(anomaly, settings_with_key()))
    assert result.status == "ok"
    assert result.is_fallback is False
    assert result.items[0].url == "https://www.coindesk.com/markets/real-stale-btc"
    assert "news_relaxed_window" in (result.error or "")


# ---------------------------------------------------------------------------
# 4. Missing key -> fallback; failure -> safe fallback; stream never crashes
# ---------------------------------------------------------------------------


def test_missing_key_uses_fallback_without_http():
    async def go():
        return await fetch_news(
            "BTCUSDT", T, Settings(news_api_key=None), client=None
        )

    result = asyncio.run(go())
    assert result.reason == "no_api_key"
    assert result.is_fallback is True
    assert result.items  # sample headlines keep the UI informative
    assert all(i.url is None for i in result.items)


def _raise_timeout(request):
    raise httpx.ConnectTimeout("slow")


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(401, json={"error": "unauthorized"}),
    lambda req: httpx.Response(429, json={"error": "rate limit"}),
    _raise_timeout,
])
def test_provider_failure_falls_back_safely(handler):
    result, _ = fetch_with(handler)
    assert result.is_fallback is True
    assert result.reason in ("http_error", "timeout")
    assert result.items  # fallback still serves sample context


def test_provider_failure_without_fallback_is_error_not_crash(monkeypatch):
    async def failing_fetch(symbol, anomaly_time, settings_, *, client=None):
        return NewsResult(items=[], is_fallback=False, reason="http_error")

    monkeypatch.setattr(news, "fetch_news", failing_fetch)
    candle = Candle(time=T, open=100, high=106, low=100, close=105, volume=50)
    anomaly = build_anomaly(
        symbol="BTCUSDT", candle=candle, pct_change=5.0, methods=["return_z"],
        return_z=4.2, volume_z=None, iforest_score=None, vol_ratio=2.0,
    )
    messages: list[dict] = []

    async def broadcast(payload: dict) -> None:
        messages.append(payload)

    async def go():
        await run_attribution(
            anomaly, settings_with_key(news_fallback_enabled=False), broadcast
        )

    asyncio.run(go())  # must not raise: the anomaly stream stays alive
    assert anomaly.attribution is not None
    assert anomaly.attribution.status == "error"
    assert messages and messages[0]["type"] == "attribution"


# ---------------------------------------------------------------------------
# 5b. Every monitored symbol normalizes and yields real news (mocked)
# ---------------------------------------------------------------------------


def _symbol_article(symbol: str, dt: int) -> dict:
    kw = asset_keywords(symbol)[0]
    slug = kw.replace(" ", "-")
    return finnhub_article(
        f"{kw.capitalize()} rallies into the close on strong volume",
        dt,
        source="CoinDesk",
        url=f"https://www.coindesk.com/markets/real-{slug}",
        summary=f"{kw} leads the session",
    )


@pytest.mark.parametrize("symbol", list(DEFAULT_SYMBOLS))
def test_every_monitored_symbol_gets_real_news(symbol):
    assert asset_keywords(symbol), f"{symbol} must normalize to keywords"
    article = _symbol_article(symbol, T - 120)
    result, _ = fetch_with(
        lambda req: httpx.Response(200, json=[article]),
        symbol=symbol,
    )
    assert result.reason is None, symbol
    assert result.is_fallback is False, symbol
    assert len(result.items) == 1, symbol
    assert result.items[0].url == article["url"], symbol
    assert result.items[0].source != "WhyLine sample data", symbol


def test_unknown_future_symbol_uses_base_ticker():
    assert asset_keywords("PEPEUSDT") == ("pepe",)
    article = finnhub_article(
        "Pepe meme coin jumps 10%", T - 60, source="CoinDesk",
        url="https://www.coindesk.com/markets/real-pepe",
        summary="pepe volume spikes",
    )
    result, _ = fetch_with(
        lambda req: httpx.Response(200, json=[article]), symbol="PEPEUSDT")
    assert result.reason is None
    assert result.is_fallback is False
    assert result.items[0].url == "https://www.coindesk.com/markets/real-pepe"


# ---------------------------------------------------------------------------
# 5c. Broad-market tier: no asset mention -> recent real crypto headlines
# ---------------------------------------------------------------------------


def _general_article(headline, dt, url):
    return finnhub_article(headline, dt, source="Cointelegraph", url=url,
                           summary="crypto market overview")


def test_broad_market_serves_real_not_sample():
    articles = [
        _general_article("Crypto market swings into the weekend", T - 300,
                         "https://cointelegraph.com/real-mkt-1"),
        _general_article("Regulators eye stablecoin rules", T - 600,
                         "https://cointelegraph.com/real-mkt-2"),
    ]
    result, _ = fetch_with(
        lambda req: httpx.Response(200, json=articles), symbol="DOGEUSDT")
    assert result.reason == "broad_market"
    assert result.is_fallback is False
    assert [i.headline for i in result.items] == [
        "Crypto market swings into the weekend",
        "Regulators eye stablecoin rules",
    ]
    assert all(i.url and i.url.startswith("https://") for i in result.items)
    assert all(i.source != "WhyLine sample data" for i in result.items)


def test_broad_market_empty_stays_fallback():
    result, _ = fetch_with(
        lambda req: httpx.Response(200, json=[]), symbol="DOGEUSDT")
    assert result.reason == "no_match"
    assert result.is_fallback is True


def test_broad_market_prefers_asset_specific():
    articles = [
        _general_article("Crypto market swings", T - 60,
                         "https://cointelegraph.com/real-mkt"),
        finnhub_article("Dogecoin spikes on social chatter", T - 5000,
                        source="CoinDesk",
                        url="https://www.coindesk.com/markets/real-doge",
                        summary="dogecoin"),
    ]
    result, _ = fetch_with(
        lambda req: httpx.Response(200, json=articles), symbol="DOGEUSDT")
    # Relaxed asset-specific (2nd tier) outranks general market (3rd tier).
    assert result.reason == "relaxed_window"
    assert result.items[0].url == "https://www.coindesk.com/markets/real-doge"


def test_attribution_ok_with_broad_market_news(monkeypatch):
    articles = [
        _general_article("Crypto market swings into the weekend", T - 300,
                         "https://cointelegraph.com/real-mkt-1"),
    ]

    async def fake_fetch(symbol, anomaly_time, settings_, *, client=None):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=articles)
        )
        async with httpx.AsyncClient(transport=transport) as c:
            return await fetch_news(symbol, anomaly_time, settings_, client=c)

    def fake_score(texts, settings_, *, classifier=None):
        return SentimentBatch(
            results=[SentimentResult(label="neutral", score=0.1,
                                     confidence=0.6)],
            reason=None,
        )

    monkeypatch.setattr(news, "fetch_news", fake_fetch)
    monkeypatch.setattr(sentiment, "score_texts", fake_score)

    candle = Candle(time=T, open=100, high=106, low=100, close=105, volume=50)
    anomaly = build_anomaly(
        symbol="DOGEUSDT", candle=candle, pct_change=5.0, methods=["return_z"],
        return_z=4.2, volume_z=None, iforest_score=None, vol_ratio=2.0,
    )
    result = asyncio.run(attribute_anomaly(anomaly, settings_with_key()))
    assert result.status == "ok"
    assert result.is_fallback is False
    assert result.items[0].url == "https://cointelegraph.com/real-mkt-1"
    assert "news_broad_market" in (result.error or "")


# ---------------------------------------------------------------------------
# 5d. Response cache: one HTTP hit shared across symbols, failures uncached
# ---------------------------------------------------------------------------


def _own_client_fetch(monkeypatch, handler, **overrides):
    """fetch_news on the production (own-client) path with mocked transport."""
    calls: list[httpx.Request] = []

    def tracking(request):
        calls.append(request)
        return handler(request)

    transport = httpx.MockTransport(tracking)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real_client(transport=transport, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    async def go(symbol="BTCUSDT"):
        return await fetch_news(symbol, T, settings_with_key(**overrides))

    return calls, go


def test_cache_shares_one_response_across_symbols(monkeypatch):
    article = _symbol_article("BTCUSDT", T - 120)
    calls, go = _own_client_fetch(
        monkeypatch, lambda req: httpx.Response(200, json=[article]))
    first = asyncio.run(go("BTCUSDT"))
    second = asyncio.run(go("ETHUSDT"))
    assert len(calls) == 1  # second symbol reused the cached response
    assert first.reason is None
    # ETHUSDT has no keyword match in the shared response -> broad tier.
    assert second.reason == "broad_market"
    assert second.is_fallback is False


def test_cache_disabled_with_zero_ttl(monkeypatch):
    article = _symbol_article("BTCUSDT", T - 120)
    calls, go = _own_client_fetch(
        monkeypatch, lambda req: httpx.Response(200, json=[article]),
        news_cache_ttl=0)
    asyncio.run(go("BTCUSDT"))
    asyncio.run(go("BTCUSDT"))
    assert len(calls) == 2


def test_failures_are_never_cached(monkeypatch):
    article = _symbol_article("BTCUSDT", T - 120)
    responses = [
        httpx.Response(500, json={"error": "boom"}),
        httpx.Response(200, json=[article]),
    ]
    calls, go = _own_client_fetch(monkeypatch, lambda req: responses.pop(0))
    failed = asyncio.run(go("BTCUSDT"))
    assert failed.reason == "http_error"
    recovered = asyncio.run(go("BTCUSDT"))
    assert recovered.reason is None
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 5. Key material never leaks into REST or WebSocket payloads
# ---------------------------------------------------------------------------


def test_key_never_leaks_into_api_payloads():
    settings = settings_with_key()
    app = create_app(settings, connect_binance=False)
    market = app.state.market
    candle = Candle(time=T, open=100, high=106, low=100, close=105, volume=50)
    anomaly = build_anomaly(
        symbol="BTCUSDT", candle=candle, pct_change=5.0, methods=["return_z"],
        return_z=4.2, volume_z=None, iforest_score=None, vol_ratio=2.0,
    )
    market.seed("BTCUSDT", [candle], [anomaly])

    with TestClient(app) as client:
        rest_body = client.get("/api/anomalies").text
        history_body = client.get("/api/history/BTCUSDT").text
    ws_msg = attribution_message(
        anomaly.id, anomaly.symbol,
        __import__("models").Attribution(status="ok", items=[]),
    )
    for payload in (rest_body, history_body, json.dumps(ws_msg)):
        assert REAL_KEY not in payload
        assert "X-Finnhub-Token" not in payload
