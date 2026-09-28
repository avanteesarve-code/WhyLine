"""Schema tests for the Milestone B attribution data contract."""

import json

import pytest
from pydantic import ValidationError

from detect import build_anomaly
from models import Anomaly, Attribution, Candle, NewsItem, attribution_message


def make_candle(t=1_700_000_000):
    return Candle(time=t, open=100, high=106, low=100, close=105, volume=50)


def make_anomaly():
    return build_anomaly(
        symbol="BTCUSDT",
        candle=make_candle(),
        pct_change=5.0,
        methods=["return_z"],
        return_z=4.2,
        volume_z=None,
        iforest_score=None,
        vol_ratio=2.0,
    )


def test_build_anomaly_has_attribution_none():
    anomaly = make_anomaly()
    assert anomaly.attribution is None
    assert "attribution" in anomaly.model_dump()
    assert anomaly.model_dump()["attribution"] is None


def test_attribution_roundtrip():
    anomaly = make_anomaly()
    anomaly.attribution = Attribution(
        status="ok",
        items=[
            NewsItem(headline="Bitcoin jumps on ETF news", source="Wire"),
            NewsItem(
                headline="Analysts split on rally",
                source="Desk",
                url="https://example.com/x",
                published_at=1_700_000_060,
                sentiment_label="positive",
                sentiment_score=0.8,
            ),
        ],
    )
    restored = Anomaly.model_validate(anomaly.model_dump())
    assert restored == anomaly


def test_attribution_message_shape_and_json_serializable():
    attribution = Attribution(status="ok", items=[NewsItem(headline="h")])
    msg = attribution_message("BTCUSDT-123", "BTCUSDT", attribution)
    assert msg["type"] == "attribution"
    assert msg["anomaly_id"] == "BTCUSDT-123"
    assert msg["symbol"] == "BTCUSDT"
    assert msg["attribution"] == attribution.model_dump()
    assert json.loads(json.dumps(msg)) == msg


def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        Attribution(status="bogus", items=[])


def test_invalid_sentiment_label_rejected():
    with pytest.raises(ValidationError):
        NewsItem(headline="h", sentiment_label="bullish")
