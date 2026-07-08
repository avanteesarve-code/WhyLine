"""Tests for Binance parsing + backfill. No network: httpx MockTransport."""

import asyncio
import json

import httpx
import pytest

from ingest import fetch_history, parse_rest_kline, parse_stream_message, stream_url

MINUTE_MS = 60_000


def rest_row(open_ms: int, o=100.0, h=101.0, low=99.0, c=100.5, v=12.5):
    """A REST kline row in Binance's actual shape (numerics as strings)."""
    return [
        open_ms,
        f"{o:.8f}",
        f"{h:.8f}",
        f"{low:.8f}",
        f"{c:.8f}",
        f"{v:.8f}",
        open_ms + MINUTE_MS - 1,
        "1265.00000000",
        42,
        "6.00000000",
        "600.00000000",
        "0",
    ]


KLINE_EVENT = {
    "e": "kline",
    "E": 1_700_000_000_123,
    "s": "BTCUSDT",
    "k": {
        "t": 1_700_000_000_000,
        "T": 1_700_000_059_999,
        "s": "BTCUSDT",
        "i": "1m",
        "f": 100,
        "L": 200,
        "o": "100.0",
        "c": "101.5",
        "h": "102.0",
        "l": "99.5",
        "v": "12.5",
        "n": 42,
        "x": False,
        "q": "1265.0",
        "V": "6.0",
        "Q": "600.0",
        "B": "0",
    },
}


class TestParseRestKline:
    def test_parses_real_shape(self):
        row = [
            1783502160000,
            "61855.16000000",
            "61908.16000000",
            "61838.04000000",
            "61846.69000000",
            "35.02176000",
            1783502219999,
            "2167095.87296240",
            6546,
            "12.71452000",
            "786676.11727350",
            "0",
        ]
        candle, close_ms = parse_rest_kline(row)
        assert candle.time == 1783502160  # milliseconds -> seconds
        assert candle.open == pytest.approx(61855.16)
        assert candle.high == pytest.approx(61908.16)
        assert candle.low == pytest.approx(61838.04)
        assert candle.close == pytest.approx(61846.69)
        assert candle.volume == pytest.approx(35.02176)
        assert close_ms == 1783502219999


class TestFetchHistory:
    def test_drops_still_forming_candle(self):
        t0 = 1_700_000_000_000
        rows = [rest_row(t0), rest_row(t0 + MINUTE_MS), rest_row(t0 + 2 * MINUTE_MS)]
        now_ms = t0 + 2 * MINUTE_MS + 30_000  # third candle still open

        async def go():
            transport = httpx.MockTransport(
                lambda request: httpx.Response(200, json=rows)
            )
            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch_history(
                    "BTCUSDT",
                    "1m",
                    10,
                    base="https://mock",
                    client=client,
                    now_ms=now_ms,
                )

        candles = asyncio.run(go())
        assert [c.time for c in candles] == [t0 // 1000, (t0 + MINUTE_MS) // 1000]

    def test_paginates_past_binance_cap(self):
        t0 = 1_700_000_000_000
        total = 1500
        all_rows = [rest_row(t0 + i * MINUTE_MS) for i in range(total)]
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            params = dict(request.url.params)
            calls.append(params)
            limit = int(params["limit"])
            end = int(params.get("endTime", all_rows[-1][0]))
            eligible = [r for r in all_rows if r[0] <= end]
            return httpx.Response(200, json=eligible[-limit:])

        now_ms = t0 + total * MINUTE_MS + 1  # everything already closed

        async def go():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch_history(
                    "BTCUSDT",
                    "1m",
                    1400,
                    base="https://mock",
                    client=client,
                    now_ms=now_ms,
                )

        candles = asyncio.run(go())
        assert len(candles) == 1400
        times = [c.time for c in candles]
        assert times == sorted(times)
        assert times[-1] == (t0 + (total - 1) * MINUTE_MS) // 1000
        assert len(calls) == 2  # one full page (1000) + the remainder


class TestParseStreamMessage:
    def test_combined_stream_forming_candle(self):
        raw = json.dumps({"stream": "btcusdt@kline_1m", "data": KLINE_EVENT})
        parsed = parse_stream_message(raw)
        assert parsed is not None
        symbol, candle, closed = parsed
        assert symbol == "BTCUSDT"
        assert closed is False
        assert candle.time == 1_700_000_000  # ms -> s
        assert candle.open == pytest.approx(100.0)
        assert candle.close == pytest.approx(101.5)
        assert candle.volume == pytest.approx(12.5)

    def test_closed_flag(self):
        event = json.loads(json.dumps(KLINE_EVENT))
        event["k"]["x"] = True
        raw = json.dumps({"stream": "btcusdt@kline_1m", "data": event})
        parsed = parse_stream_message(raw)
        assert parsed is not None and parsed[2] is True

    def test_raw_event_without_envelope(self):
        assert parse_stream_message(json.dumps(KLINE_EVENT)) is not None

    def test_ignores_non_kline_payloads(self):
        depth = {"stream": "btcusdt@depth", "data": {"e": "depthUpdate"}}
        assert parse_stream_message(json.dumps(depth)) is None
        assert parse_stream_message(json.dumps({"result": None, "id": 1})) is None
        assert parse_stream_message("not json") is None
        assert parse_stream_message(json.dumps([1, 2, 3])) is None


def test_stream_url_format():
    url = stream_url("wss://data-stream.binance.vision", ["BTCUSDT", "ethusdt"], "1m")
    assert url == (
        "wss://data-stream.binance.vision/stream"
        "?streams=btcusdt@kline_1m/ethusdt@kline_1m"
    )
