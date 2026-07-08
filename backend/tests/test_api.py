"""API + state tests over a seeded app (no Binance connection)."""

from fastapi.testclient import TestClient

from config import Settings
from detect import build_anomaly
from main import create_app
from models import Candle

START = 1_700_000_000


def make_app():
    settings = Settings(symbols=("BTCUSDT", "ETHUSDT"), iforest_min_buffer=10**9)
    return create_app(settings, connect_binance=False)


def flat_candles(n, start=START):
    return [
        Candle(
            time=start + 60 * i, open=100, high=101, low=99, close=100.5, volume=10
        )
        for i in range(n)
    ]


def make_anomaly(symbol, t):
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


def test_symbols_endpoint():
    with TestClient(make_app()) as client:
        body = client.get("/api/symbols").json()
    assert body == {"symbols": ["BTCUSDT", "ETHUSDT"], "interval": "1m"}


def test_health_endpoint():
    with TestClient(make_app()) as client:
        body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["symbols"] == 2


def test_history_roundtrip_including_forming_candle():
    app = make_app()
    market = app.state.market
    candles = flat_candles(50)
    market.seed("BTCUSDT", candles, [make_anomaly("BTCUSDT", candles[10].time)])
    forming = Candle(
        time=candles[-1].time + 60,
        open=100.5,
        high=100.6,
        low=100.4,
        close=100.55,
        volume=3,
    )
    market.symbols["BTCUSDT"].forming = forming

    with TestClient(app) as client:
        resp = client.get("/api/history/btcusdt")  # lowercase must work too
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["interval"] == "1m"
    assert len(body["candles"]) == 51  # 50 closed + the forming candle
    assert body["candles"][-1]["time"] == forming.time
    assert body["candles"][0] == candles[0].model_dump()
    assert len(body["anomalies"]) == 1
    assert body["anomalies"][0]["symbol"] == "BTCUSDT"


def test_unknown_symbol_404():
    with TestClient(make_app()) as client:
        assert client.get("/api/history/NOPEUSDT").status_code == 404


def test_apply_kline_dedupes_and_clears_forming():
    app = make_app()
    market = app.state.market
    market.seed("BTCUSDT", flat_candles(50), [])
    state = market.symbols["BTCUSDT"]
    last_time = START + 49 * 60

    forming = Candle(
        time=last_time + 60, open=100.5, high=100.6, low=100.4, close=100.52, volume=10
    )
    assert market.apply_kline("BTCUSDT", forming, closed=False) is None
    assert state.forming == forming
    assert len(state.candles) == 50  # forming candle not appended

    closed = forming.model_copy(update={"close": 100.6})
    market.apply_kline("BTCUSDT", closed, closed=True)
    assert len(state.candles) == 51
    assert state.forming is None

    # Replay of the same closed candle (resync overlap) must not double-count.
    market.apply_kline("BTCUSDT", closed, closed=True)
    assert len(state.candles) == 51

    # Stale/out-of-order candles are ignored.
    older = closed.model_copy(update={"time": closed.time - 120})
    market.apply_kline("BTCUSDT", older, closed=True)
    assert len(state.candles) == 51

    # Unknown symbols are ignored.
    assert market.apply_kline("WATUSDT", closed, closed=True) is None


def test_anomalies_endpoint_merges_and_sorts():
    app = make_app()
    market = app.state.market
    market.symbols["BTCUSDT"].anomalies.append(make_anomaly("BTCUSDT", 1000))
    market.symbols["ETHUSDT"].anomalies.append(make_anomaly("ETHUSDT", 2000))
    with TestClient(app) as client:
        body = client.get("/api/anomalies").json()
    assert [a["time"] for a in body["anomalies"]] == [2000, 1000]
    assert body["anomalies"][0]["explanation"]


def test_websocket_hello():
    with TestClient(make_app()) as client:
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
    assert hello["type"] == "hello"
    assert hello["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert hello["interval"] == "1m"
