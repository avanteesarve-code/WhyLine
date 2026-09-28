"""Tests for the Finnhub news-fetch layer. Fully offline: httpx MockTransport."""

import asyncio

import httpx

from config import DEFAULT_SYMBOLS, Settings
from models import NewsItem
from news import (
    asset_keywords,
    fallback_news,
    fetch_news,
    filter_news,
    parse_provider_item,
    parse_provider_response,
)

DUMMY_KEY = "test-token"
T = 1_700_000_000


def make_settings(**overrides) -> Settings:
    base = dict(news_api_key=DUMMY_KEY)
    base.update(overrides)
    return Settings(**base)


def provider_item(headline, dt, *, source="CoinDesk", url="https://example.com/a",
                  summary="", **extra):
    """One Finnhub market-news object in the documented response shape."""
    item = {
        "category": "crypto",
        "datetime": dt,
        "headline": headline,
        "id": 1,
        "image": "",
        "related": "",
        "source": source,
        "summary": summary,
        "url": url,
    }
    item.update(extra)
    return item


def fetch_with(handler, symbol="BTCUSDT", anomaly_time=T, **settings_overrides):
    """Run fetch_news against a MockTransport; returns (result, requests)."""
    settings = make_settings(**settings_overrides)
    requests = []

    def tracking_handler(request):
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(tracking_handler)

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_news(symbol, anomaly_time, settings, client=client)

    return asyncio.run(go()), requests


def btc_candidates(headline, dt, summary=""):
    return [(NewsItem(headline=headline, published_at=dt), summary)]


class TestSuccess:
    def test_converts_provider_items(self):
        item = provider_item(
            "Bitcoin jumps on strong demand",
            T - 60,
            source="CoinDesk",
            url="https://example.com/btc",
            summary="bitcoin demand",
        )
        result, _ = fetch_with(lambda req: httpx.Response(200, json=[item]))
        assert result.reason is None
        assert result.is_fallback is False
        assert len(result.items) == 1
        news = result.items[0]
        assert isinstance(news.published_at, int)
        assert news.published_at == T - 60
        assert news.headline == "Bitcoin jumps on strong demand"
        assert news.source == "CoinDesk"
        assert news.url == "https://example.com/btc"
        assert news.sentiment_label is None
        assert news.sentiment_score is None


class TestParsing:
    def test_parse_item_maps_fields(self):
        item = parse_provider_item(
            provider_item("h", 123, source="S", url="https://x.example")
        )
        assert item is not None
        assert (item.headline, item.published_at, item.source, item.url) == (
            "h",
            123,
            "S",
            "https://x.example",
        )

    def test_parse_item_skips_bad_entries(self):
        assert parse_provider_item({"headline": "  ", "datetime": T}) is None
        assert parse_provider_item({"datetime": T}) is None
        assert parse_provider_item({"headline": "h"}) is None
        assert parse_provider_item({"headline": "h", "datetime": "soon"}) is None
        assert parse_provider_item({"headline": "h", "datetime": T + 0.5}) is None
        assert parse_provider_item({"headline": "h", "datetime": True}) is None
        assert parse_provider_item("not a dict") is None

    def test_parse_response_rejects_non_list(self):
        assert parse_provider_response({"error": "bad"}) is None
        assert parse_provider_response(None) is None

    def test_parse_response_skips_bad_items(self):
        payload = [
            provider_item("good", T),
            {"headline": "no datetime"},
            {"datetime": T},
            provider_item("bad time", "yesterday"),
        ]
        items = parse_provider_response(payload)
        assert items is not None
        assert [i.headline for i in items] == ["good"]


class TestTimeWindow:
    def fetch_at(self, dt):
        item = provider_item("Bitcoin steady here", dt, summary="btc calm")
        return fetch_with(lambda req: httpx.Response(200, json=[item]))

    def test_inclusive_lower_boundary(self):
        result, _ = self.fetch_at(T - 3600)
        assert result.reason is None
        assert len(result.items) == 1

    def test_just_before_lower_boundary_excluded(self):
        result, _ = self.fetch_at(T - 3601)
        assert result.reason == "no_match"

    def test_inclusive_upper_boundary(self):
        result, _ = self.fetch_at(T + 900)
        assert result.reason is None
        assert len(result.items) == 1

    def test_just_after_upper_boundary_excluded(self):
        result, _ = self.fetch_at(T + 901)
        assert result.reason == "no_match"


class TestKeywordMapping:
    def test_btc_matches_bitcoin(self):
        kept = filter_news(
            btc_candidates("Bitcoin rallies hard", T), symbol="BTCUSDT",
            anomaly_time=T, before=3600, after=900, max_items=5,
        )
        assert len(kept) == 1

    def test_btc_matches_ticker(self):
        kept = filter_news(
            btc_candidates("BTC steady after volatile hour", T), symbol="BTCUSDT",
            anomaly_time=T, before=3600, after=900, max_items=5,
        )
        assert len(kept) == 1

    def test_link_matches_chainlink(self):
        kept = filter_news(
            [(
                NewsItem(headline="Chainlink partnership announced", published_at=T),
                "",
            )],
            symbol="LINKUSDT", anomaly_time=T,
            before=3600, after=900, max_items=5,
        )
        assert len(kept) == 1

    def test_link_does_not_match_bare_link(self):
        kept = filter_news(
            [(
                NewsItem(headline="click the link to read more", published_at=T),
                "follow the link below",
            )],
            symbol="LINKUSDT", anomaly_time=T,
            before=3600, after=900, max_items=5,
        )
        assert kept == []

    def test_sol_does_not_match_solution(self):
        kept = filter_news(
            [(
                NewsItem(headline="A new solution for scaling emerges", published_at=T),
                "",
            )],
            symbol="SOLUSDT", anomaly_time=T,
            before=3600, after=900, max_items=5,
        )
        assert kept == []

    def test_unknown_symbol_uses_base_ticker(self):
        assert asset_keywords("PEPEUSDT") == ("pepe",)
        kept = filter_news(
            [(
                NewsItem(headline="Pepe meme coin jumps 10%", published_at=T),
                "",
            )],
            symbol="PEPEUSDT", anomaly_time=T,
            before=3600, after=900, max_items=5,
        )
        assert len(kept) == 1

    def test_summary_counts_for_matching(self):
        kept = filter_news(
            [(NewsItem(headline="Markets rally broadly", published_at=T),
              "bitcoin leads the move")],
            symbol="BTCUSDT", anomaly_time=T,
            before=3600, after=900, max_items=5,
        )
        assert len(kept) == 1


class TestDedupOrderCap:
    def candidates(self):
        return [
            (NewsItem(headline="Bitcoin A", published_at=T - 100,
                      url="https://example.com/dup"), ""),
            (NewsItem(headline="Bitcoin B", published_at=T - 50,
                      url="https://example.com/dup"), ""),
            (NewsItem(headline="Bitcoin same", published_at=T - 10), ""),
            (NewsItem(headline="Bitcoin same", published_at=T - 20), ""),
            (NewsItem(headline="Bitcoin far", published_at=T - 300), ""),
        ]

    def test_dedupes_by_url_then_headline(self):
        kept = filter_news(
            self.candidates(), symbol="BTCUSDT", anomaly_time=T,
            before=3600, after=900, max_items=10,
        )
        assert [i.headline for i in kept].count("Bitcoin B") == 0
        assert [i.headline for i in kept].count("Bitcoin same") == 1

    def test_orders_closest_first_with_stable_tiebreak(self):
        tied = [
            (NewsItem(headline="Bitcoin early", published_at=T - 60), ""),
            (NewsItem(headline="Bitcoin late", published_at=T + 60), ""),
            (NewsItem(headline="Bitcoin near", published_at=T - 10), ""),
        ]
        kept = filter_news(
            tied, symbol="BTCUSDT", anomaly_time=T,
            before=3600, after=900, max_items=10,
        )
        assert [i.headline for i in kept] == [
            "Bitcoin near",
            "Bitcoin early",  # tie with "late": provider order wins
            "Bitcoin late",
        ]

    def test_caps_at_max_items(self):
        kept = filter_news(
            self.candidates(), symbol="BTCUSDT", anomaly_time=T,
            before=3600, after=900, max_items=2,
        )
        assert len(kept) == 2
        assert kept[0].headline == "Bitcoin same"  # closest first


class TestEmptyProviderList:
    def test_empty_list_falls_back(self):
        result, _ = fetch_with(lambda req: httpx.Response(200, json=[]))
        assert result.reason == "no_match"
        assert result.is_fallback is True
        assert result.items  # fallback sample headlines

    def test_empty_list_without_fallback(self):
        result, _ = fetch_with(
            lambda req: httpx.Response(200, json=[]),
            news_fallback_enabled=False,
        )
        assert result.reason == "no_match"
        assert result.is_fallback is False
        assert result.items == []


class TestHttpErrors:
    def test_http_500_maps_to_http_error(self):
        result, _ = fetch_with(lambda req: httpx.Response(500, json={}))
        assert result.reason == "http_error"
        assert result.is_fallback is True

    def test_http_429_maps_to_http_error(self):
        result, _ = fetch_with(lambda req: httpx.Response(429, json={}))
        assert result.reason == "http_error"
        assert result.is_fallback is True


class TestTimeout:
    def test_timeout_maps_to_timeout_reason(self):
        def handler(request):
            raise httpx.TimeoutException("timed out", request=request)

        result, _ = fetch_with(handler)
        assert result.reason == "timeout"
        assert result.is_fallback is True


class TestMalformed:
    def test_dict_payload_is_malformed(self):
        result, _ = fetch_with(
            lambda req: httpx.Response(200, json={"error": "bad key"})
        )
        assert result.reason == "malformed"

    def test_mixed_items_do_not_crash(self):
        payload = [
            provider_item("Bitcoin holds gains", T - 30, summary="btc firm"),
            {"headline": "missing datetime entirely"},
            {"datetime": T - 30},
            provider_item("bad time type", "recently"),
            [1, 2, 3],
        ]
        result, _ = fetch_with(lambda req: httpx.Response(200, json=payload))
        assert result.reason is None
        assert [i.headline for i in result.items] == ["Bitcoin holds gains"]

    def test_invalid_json_is_malformed(self):
        result, _ = fetch_with(
            lambda req: httpx.Response(200, content=b"not json{{")
        )
        assert result.reason == "malformed"


class TestMissingKey:
    def test_missing_key_serves_fallback_without_requests(self):
        result, requests = fetch_with(
            lambda req: httpx.Response(200, json=[]),
            news_api_key=None,
        )
        assert requests == []
        assert result.reason == "no_api_key"
        assert result.is_fallback is True
        assert result.items

    def test_missing_key_creates_no_client(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("must not create an HTTP client without a key")

        monkeypatch.setattr(httpx, "AsyncClient", boom)
        result = asyncio.run(
            fetch_news("BTCUSDT", T, make_settings(news_api_key=None))
        )
        assert result.reason == "no_api_key"


class TestFallback:
    def test_fallback_items_follow_sample_rules(self):
        items = fallback_news("BTCUSDT", 5)
        assert items
        for item in items:
            assert item.source == "WhyLine sample data"
            assert item.url is None
            assert item.published_at is None
            assert item.sentiment_label is None
            assert item.sentiment_score is None

    def test_symbol_entries_come_before_generic(self):
        items = fallback_news("BTCUSDT", 10)
        generic = fallback_news("UNKNOWNCOIN", 10)
        assert generic  # unknown symbols still get generic crypto entries
        assert items[0].headline != generic[0].headline

    def test_is_fallback_only_for_fallback(self):
        item = provider_item("Bitcoin jumps", T - 10, summary="btc")
        ok, _ = fetch_with(lambda req: httpx.Response(200, json=[item]))
        assert ok.is_fallback is False
        failed, _ = fetch_with(
            lambda req: httpx.Response(500, json=[]),
            news_fallback_enabled=False,
        )
        assert failed.is_fallback is False
        assert failed.items == []


class TestAuth:
    def test_token_in_header_never_in_url(self):
        seen = {}

        def handler(request):
            seen["token"] = request.headers.get("X-Finnhub-Token")
            seen["url"] = str(request.url)
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json=[])

        fetch_with(handler)
        assert seen["token"] == DUMMY_KEY
        assert DUMMY_KEY not in seen["url"]
        assert "token" not in {k.lower() for k in seen["params"]}
        assert seen["params"].get("category") == "crypto"


class TestSettings:
    NEWS_VARS = (
        "NEWS_API_KEY",
        "NEWS_API_BASE",
        "NEWS_WINDOW_BEFORE",
        "NEWS_WINDOW_AFTER",
        "NEWS_MAX_ITEMS",
        "NEWS_TIMEOUT",
        "NEWS_FALLBACK_ENABLED",
    )

    def clear_news_env(self, monkeypatch):
        for var in self.NEWS_VARS:
            monkeypatch.delenv(var, raising=False)

    def test_defaults_without_env(self, monkeypatch):
        self.clear_news_env(monkeypatch)
        settings = Settings.from_env()
        assert settings.news_api_key is None
        assert settings.news_api_base == "https://finnhub.io/api/v1"
        assert settings.news_window_before == 3600
        assert settings.news_window_after == 900
        assert settings.news_max_items == 5
        assert settings.news_timeout == 5.0
        assert settings.news_fallback_enabled is True

    def test_fields_parse_from_env(self, monkeypatch):
        self.clear_news_env(monkeypatch)
        monkeypatch.setenv("NEWS_API_KEY", "abc123")
        monkeypatch.setenv("NEWS_API_BASE", "https://example.com/v1")
        monkeypatch.setenv("NEWS_WINDOW_BEFORE", "120")
        monkeypatch.setenv("NEWS_WINDOW_AFTER", "60")
        monkeypatch.setenv("NEWS_MAX_ITEMS", "3")
        monkeypatch.setenv("NEWS_TIMEOUT", "2.5")
        monkeypatch.setenv("NEWS_FALLBACK_ENABLED", "YeS")
        settings = Settings.from_env()
        assert settings.news_api_key == "abc123"
        assert settings.news_api_base == "https://example.com/v1"
        assert settings.news_window_before == 120
        assert settings.news_window_after == 60
        assert settings.news_max_items == 3
        assert settings.news_timeout == 2.5
        assert settings.news_fallback_enabled is True

    def test_blank_key_becomes_none(self, monkeypatch):
        self.clear_news_env(monkeypatch)
        monkeypatch.setenv("NEWS_API_KEY", "   ")
        assert Settings.from_env().news_api_key is None

    def test_fallback_flag_parsing(self, monkeypatch):
        self.clear_news_env(monkeypatch)
        for raw in ("1", "true", "YES", "on"):
            monkeypatch.setenv("NEWS_FALLBACK_ENABLED", raw)
            assert Settings.from_env().news_fallback_enabled is True
        for raw in ("0", "false", "no", "off", "", "maybe"):
            monkeypatch.setenv("NEWS_FALLBACK_ENABLED", raw)
            assert Settings.from_env().news_fallback_enabled is False

    def test_api_key_hidden_in_repr(self):
        assert "super-secret-key" not in repr(
            Settings(news_api_key="super-secret-key")
        )


class TestFallbackJson:
    def test_every_default_symbol_has_fallback(self):
        for symbol in DEFAULT_SYMBOLS:
            assert fallback_news(symbol, 10), symbol
