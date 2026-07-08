"""Unit tests for the quantitative detection core."""

import numpy as np
import pytest

from config import Settings
from detect import SymbolDetector, scan_history, trailing_zscores, zscore
from models import Candle

IF_OFF = 10**9  # iforest_min_buffer high enough to disable the forest
START = 1_700_000_000


def make_settings(**overrides) -> Settings:
    base = dict(
        window=30,
        min_periods=10,
        return_z_threshold=3.0,
        volume_z_threshold=3.5,
        cooldown=3,
        iforest_min_buffer=IF_OFF,
    )
    base.update(overrides)
    return Settings(**base)


def make_candles(closes, volumes=None, start=START):
    closes = list(closes)
    volumes = list(volumes) if volumes is not None else [100.0] * len(closes)
    out = []
    prev = closes[0]
    for i, (close, vol) in enumerate(zip(closes, volumes)):
        out.append(
            Candle(
                time=start + 60 * i,
                open=prev,
                high=max(prev, close) * 1.0005,
                low=min(prev, close) * 0.9995,
                close=close,
                volume=vol,
            )
        )
        prev = close
    return out


def uniform_walk(n, seed=7, scale=0.001, base=100.0):
    """Random walk with *bounded* uniform log-returns.

    Uniform noise on [-scale, scale] has std = scale/sqrt(3), so a noise
    return can never exceed ~1.8 sigma: tests cannot be tripped by an
    unlucky noise candle crossing the z=3 threshold.
    """
    rng = np.random.default_rng(seed)
    rets = rng.uniform(-scale, scale, size=n)
    return list(base * np.exp(np.cumsum(rets)))


def inject_jump(closes, at, factor):
    """Single-candle return of `factor` at index `at` (later returns intact)."""
    return closes[:at] + [c * factor for c in closes[at:]]


def stream_all(candles, settings, symbol="TESTUSDT"):
    det = SymbolDetector(symbol, settings)
    out = []
    for candle in candles:
        anomaly = det.update(candle)
        if anomaly is not None:
            out.append(anomaly)
    return out


class TestZscore:
    def test_matches_hand_computation(self):
        baseline = [1.0, 2.0, 3.0, 4.0, 5.0]
        expected = (6.0 - 3.0) / np.std(baseline, ddof=1)
        assert zscore(6.0, baseline) == pytest.approx(expected)

    def test_constant_baseline_gives_none(self):
        assert zscore(5.0, [2.0] * 20) is None

    def test_short_baseline_gives_none(self):
        assert zscore(5.0, [1.0]) is None


class TestTrailingZscores:
    def test_warmup_is_nan(self):
        z = trailing_zscores(np.arange(50, dtype=float), window=30, min_periods=10)
        assert np.all(np.isnan(z[:10]))
        assert np.all(np.isfinite(z[10:]))

    def test_excludes_current_value_from_baseline(self):
        # The spike must not shrink its own z by inflating the baseline std.
        vals = np.array([0.0, 1.0] * 10 + [50.0])
        z = trailing_zscores(vals, window=20, min_periods=5)
        base = np.array([0.0, 1.0] * 10)
        assert z[-1] == pytest.approx((50.0 - base.mean()) / base.std(ddof=1))


class TestStreamingDetector:
    def test_flat_series_never_flags(self):
        assert stream_all(make_candles([100.0] * 120), make_settings()) == []

    def test_price_spike_up_flagged(self):
        spike_at = 60
        closes = inject_jump(uniform_walk(80), spike_at, 1.05)
        anomalies = stream_all(make_candles(closes), make_settings())
        assert len(anomalies) == 1
        a = anomalies[0]
        assert a.time == START + 60 * spike_at
        assert a.direction == "up"
        assert "return_z" in a.methods
        assert a.pct_change == pytest.approx(5.0, abs=0.3)
        assert a.return_z is not None and a.return_z > 3
        assert a.explanation

    def test_price_drop_flagged_down(self):
        closes = inject_jump(uniform_walk(80), 50, 0.95)
        anomalies = stream_all(make_candles(closes), make_settings())
        assert [a.direction for a in anomalies] == ["down"]

    def test_volume_spike_flagged(self):
        n = 80
        closes = uniform_walk(n)
        volumes = list(np.random.default_rng(3).uniform(95.0, 105.0, size=n))
        volumes[70] = 1000.0
        anomalies = stream_all(make_candles(closes, volumes), make_settings())
        assert len(anomalies) == 1
        a = anomalies[0]
        assert "volume_z" in a.methods
        assert a.vol_ratio > 5

    def test_warmup_prevents_flags(self):
        closes = inject_jump(uniform_walk(9), 5, 1.10)
        assert stream_all(make_candles(closes), make_settings()) == []

    def test_cooldown_suppresses_consecutive_flags(self):
        closes = uniform_walk(80)
        for i in (60, 62):  # second spike lands inside the 3-candle cooldown
            closes = inject_jump(closes, i, 1.05)
        anomalies = stream_all(make_candles(closes), make_settings(cooldown=3))
        assert len(anomalies) == 1
        assert anomalies[0].time == START + 60 * 60

    def test_flags_again_after_cooldown(self):
        closes = uniform_walk(90)
        for i in (60, 70):
            closes = inject_jump(closes, i, 1.05)
        anomalies = stream_all(make_candles(closes), make_settings(cooldown=3))
        assert len(anomalies) == 2

    def test_prime_warms_detector(self):
        settings = make_settings()
        history = make_candles(uniform_walk(60))
        det = SymbolDetector("TESTUSDT", settings)
        det.prime(history)
        last = history[-1]
        jump = Candle(
            time=last.time + 60,
            open=last.close,
            high=last.close * 1.051,
            low=last.close,
            close=last.close * 1.05,
            volume=100.0,
        )
        anomaly = det.update(jump)  # flags immediately: no live warmup needed
        assert anomaly is not None
        assert "return_z" in anomaly.methods

    def test_streaming_isolation_forest_flags_outlier(self):
        n = 260
        outlier_at = 250
        closes = inject_jump(uniform_walk(n, seed=31), outlier_at, 1.08)
        volumes = list(np.random.default_rng(13).uniform(95.0, 105.0, size=n))
        volumes[outlier_at] = 2000.0
        settings = make_settings(
            iforest_min_buffer=120, iforest_refit_every=10, cooldown=0
        )
        anomalies = stream_all(make_candles(closes, volumes), settings)
        flagged = [a for a in anomalies if "isolation_forest" in a.methods]
        assert any(a.time == START + 60 * outlier_at for a in flagged)


class TestScanHistory:
    def test_flat_history_no_anomalies(self):
        candles = make_candles([100.0] * 200)
        assert scan_history("TESTUSDT", candles, make_settings()) == []

    def test_too_short_history_is_safe(self):
        candles = make_candles(uniform_walk(5))
        assert scan_history("TESTUSDT", candles, make_settings()) == []

    def test_streaming_and_batch_agree_on_zscore_flags(self):
        closes = uniform_walk(300, seed=11)
        closes = inject_jump(closes, 80, 1.04)
        closes = inject_jump(closes, 150, 0.96)
        closes = inject_jump(closes, 220, 1.04)
        volumes = list(np.random.default_rng(5).uniform(95.0, 105.0, size=300))
        volumes[150] = 900.0
        candles = make_candles(closes, volumes)
        settings = make_settings()

        streamed = stream_all(candles, settings)
        batch = scan_history("TESTUSDT", candles, settings)

        assert len(streamed) == 3
        assert [a.id for a in streamed] == [a.id for a in batch]
        for s_a, b_a in zip(streamed, batch):
            assert s_a.methods == b_a.methods
            if s_a.return_z is not None:
                assert s_a.return_z == pytest.approx(b_a.return_z, abs=1e-6)
            if s_a.volume_z is not None:
                assert s_a.volume_z == pytest.approx(b_a.volume_z, abs=1e-6)

    def test_offline_isolation_forest_flags_planted_outlier(self):
        n = 400
        outlier_at = 200
        closes = inject_jump(uniform_walk(n, seed=23), outlier_at, 1.08)
        volumes = list(np.random.default_rng(9).uniform(95.0, 105.0, size=n))
        volumes[outlier_at] = 2000.0
        candles = make_candles(closes, volumes)
        settings = make_settings(iforest_min_buffer=100)
        anomalies = scan_history("TESTUSDT", candles, settings)
        target = [a for a in anomalies if a.time == START + 60 * outlier_at]
        assert target
        assert "isolation_forest" in target[0].methods
        assert target[0].iforest_score is not None
        assert target[0].iforest_score > 0
