"""Quantitative anomaly detection over closed candles.

Two complementary detectors:

1. **Rolling z-scores** — the fast, explainable path. The latest log-return
   and log-volume are compared against a trailing window that *excludes* the
   current candle, so a spike cannot inflate its own baseline. Volume is
   one-sided (only spikes above baseline are anomalous).
2. **Isolation Forest** — the multivariate ML path over the feature vector
   [log_return, high-low range fraction, log_volume]. Streaming mode refits
   on a rolling buffer every few candles; batch mode (startup back-scan)
   fits once over the whole history, the standard offline usage.

`SymbolDetector` is the streaming entry point (one instance per symbol);
`scan_history` annotates backfilled history so the chart starts with
anomaly markers instead of waiting for live events.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
from sklearn.ensemble import IsolationForest

from config import Settings
from models import Anomaly, Candle

_EPS = 1e-12


# ---------------------------------------------------------------------------
# Pure statistics helpers (unit-tested directly)
# ---------------------------------------------------------------------------


def zscore(value: float, baseline) -> float | None:
    """Z-score of `value` against `baseline` (which must not include it).

    Returns None when the baseline is too short or has ~zero variance,
    meaning "no judgement" rather than "not anomalous".
    """
    arr = np.asarray(baseline, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return None
    std = float(arr.std(ddof=1))
    if std < _EPS:
        return None
    return float((value - float(arr.mean())) / std)


def trailing_zscores(values, window: int, min_periods: int) -> np.ndarray:
    """Vectorised counterpart of the streaming z-score.

    out[i] = z of values[i] against values[i-window:i] (current excluded);
    NaN while fewer than `min_periods` finite baseline points exist.
    """
    v = np.asarray(values, dtype=float)
    out = np.full(v.size, np.nan)
    for i in range(v.size):
        if not np.isfinite(v[i]):
            continue
        base = v[max(0, i - window) : i]
        base = base[np.isfinite(base)]
        if base.size >= min_periods:
            z = zscore(float(v[i]), base)
            if z is not None:
                out[i] = z
    return out


def log_return(close: float, prev_close: float | None) -> float | None:
    if prev_close is None or prev_close <= 0 or close <= 0:
        return None
    return math.log(close / prev_close)


def candle_features(candle: Candle, prev_close: float | None) -> list[float] | None:
    """[log_return, high-low range fraction, log_volume] or None pre-warmup."""
    r = log_return(candle.close, prev_close)
    if r is None:
        return None
    rng = (candle.high - candle.low) / candle.open if candle.open > 0 else 0.0
    return [r, rng, math.log(candle.volume + 1.0)]


def _volume_ratio(volume: float, prior_volumes) -> float:
    arr = np.asarray(prior_volumes, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 1.0
    med = float(np.median(arr))
    if med <= _EPS:
        return 1.0
    return float(volume / med)


# ---------------------------------------------------------------------------
# Anomaly construction (shared by streaming and batch paths)
# ---------------------------------------------------------------------------


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def build_explanation(
    direction: str,
    pct_change: float,
    methods: list[str],
    return_z: float | None,
    volume_z: float | None,
    vol_ratio: float,
) -> str:
    parts: list[str] = []
    if "return_z" in methods and return_z is not None:
        verb = "jumped" if direction == "up" else "dropped"
        parts.append(
            f"price {verb} {abs(pct_change):.2f}% in one candle "
            f"(z {return_z:+.1f} vs recent baseline)"
        )
    if "volume_z" in methods and volume_z is not None:
        parts.append(
            f"volume ran {vol_ratio:.1f}x the trailing median (z {volume_z:+.1f})"
        )
    if "isolation_forest" in methods:
        parts.append(
            "Isolation Forest marked the move/range/volume mix as an outlier"
        )
    if not parts:
        parts.append(f"price moved {pct_change:+.2f}%")
    text = "; ".join(parts)
    return text[0].upper() + text[1:]


def build_anomaly(
    *,
    symbol: str,
    candle: Candle,
    pct_change: float,
    methods: list[str],
    return_z: float | None,
    volume_z: float | None,
    iforest_score: float | None,
    vol_ratio: float,
) -> Anomaly:
    direction = "up" if pct_change >= 0 else "down"
    return Anomaly(
        id=f"{symbol}-{candle.time}",
        symbol=symbol,
        time=candle.time,
        price=candle.close,
        direction=direction,
        methods=methods,
        return_z=_round(return_z),
        volume_z=_round(volume_z),
        iforest_score=_round(iforest_score, 4),
        pct_change=round(pct_change, 3),
        vol_ratio=round(vol_ratio, 2),
        explanation=build_explanation(
            direction, pct_change, methods, return_z, volume_z, vol_ratio
        ),
    )


# ---------------------------------------------------------------------------
# Streaming detectors
# ---------------------------------------------------------------------------


class StreamingIsolationForest:
    """Isolation Forest refit periodically on a rolling buffer of features."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._buffer: deque[list[float]] = deque(maxlen=settings.iforest_buffer)
        self._model: IsolationForest | None = None
        self._since_fit = 0

    def _fit(self) -> None:
        model = IsolationForest(
            n_estimators=100,
            contamination=self._settings.iforest_contamination,
            random_state=42,
        )
        model.fit(np.asarray(self._buffer, dtype=float))
        self._model = model
        self._since_fit = 0

    def absorb(self, features: list[float]) -> None:
        """Add history without scoring (used when priming from backfill)."""
        self._buffer.append(features)

    def warm_fit(self) -> None:
        if len(self._buffer) >= self._settings.iforest_min_buffer:
            self._fit()

    def observe(self, features: list[float]) -> tuple[bool, float | None]:
        """Score `features` against candles seen so far, then absorb them."""
        flagged, score = False, None
        if len(self._buffer) >= self._settings.iforest_min_buffer:
            if (
                self._model is None
                or self._since_fit >= self._settings.iforest_refit_every
            ):
                self._fit()
            row = np.asarray([features], dtype=float)
            flagged = bool(self._model.predict(row)[0] == -1)
            score = float(-self._model.decision_function(row)[0])
        self._buffer.append(features)
        self._since_fit += 1
        return flagged, score


class SymbolDetector:
    """Streaming per-symbol detector; call `update` with each *closed* candle."""

    def __init__(self, symbol: str, settings: Settings) -> None:
        self.symbol = symbol
        self._s = settings
        self._returns: deque[float] = deque(maxlen=settings.window)
        self._log_vols: deque[float] = deque(maxlen=settings.window)
        self._volumes: deque[float] = deque(maxlen=settings.window)
        self._iforest = StreamingIsolationForest(settings)
        self._prev_close: float | None = None
        self._index = 0
        self._last_flag = -(10**9)

    def prime(self, candles: list[Candle]) -> None:
        """Warm all rolling state from backfilled history without emitting."""
        for candle in candles:
            feats = candle_features(candle, self._prev_close)
            if feats is not None:
                self._returns.append(feats[0])
                self._log_vols.append(feats[2])
                self._iforest.absorb(feats)
            self._volumes.append(candle.volume)
            self._prev_close = candle.close
            self._index += 1
        self._iforest.warm_fit()

    def update(self, candle: Candle) -> Anomaly | None:
        feats = candle_features(candle, self._prev_close)
        anomaly: Anomaly | None = None

        if feats is not None:
            r, _rng, lv = feats
            ret_z = (
                zscore(r, self._returns)
                if len(self._returns) >= self._s.min_periods
                else None
            )
            vol_z = (
                zscore(lv, self._log_vols)
                if len(self._log_vols) >= self._s.min_periods
                else None
            )
            if_flag, if_score = self._iforest.observe(feats)

            methods: list[str] = []
            if ret_z is not None and abs(ret_z) >= self._s.return_z_threshold:
                methods.append("return_z")
            if vol_z is not None and vol_z >= self._s.volume_z_threshold:
                methods.append("volume_z")
            if if_flag:
                methods.append("isolation_forest")

            if methods and self._index - self._last_flag > self._s.cooldown:
                anomaly = build_anomaly(
                    symbol=self.symbol,
                    candle=candle,
                    pct_change=(math.exp(r) - 1.0) * 100.0,
                    methods=methods,
                    return_z=ret_z,
                    volume_z=vol_z,
                    iforest_score=if_score,
                    vol_ratio=_volume_ratio(candle.volume, self._volumes),
                )
                self._last_flag = self._index

            self._returns.append(r)
            self._log_vols.append(lv)

        self._volumes.append(candle.volume)
        self._prev_close = candle.close
        self._index += 1
        return anomaly


# ---------------------------------------------------------------------------
# Batch back-scan for backfilled history
# ---------------------------------------------------------------------------


def scan_history(
    symbol: str, candles: list[Candle], settings: Settings
) -> list[Anomaly]:
    """Annotate historical candles so the chart starts with markers.

    Z-scores use exactly the trailing definition of the streaming path (the
    two are asserted equal in tests). The Isolation Forest is fit once over
    the full history — offline mode — so live and historical IF flags can
    differ slightly; that is expected and documented.
    """
    n = len(candles)
    if n < settings.min_periods + 1:
        return []

    closes = np.array([c.close for c in candles], dtype=float)
    opens = np.array([c.open for c in candles], dtype=float)
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    volumes = np.array([c.volume for c in candles], dtype=float)

    with np.errstate(divide="ignore", invalid="ignore"):
        safe_closes = np.where(closes > 0, closes, np.nan)
        rets = np.concatenate([[np.nan], np.diff(np.log(safe_closes))])
    ranges = np.where(opens > 0, (highs - lows) / opens, 0.0)
    log_vols = np.log(volumes + 1.0)

    ret_z = trailing_zscores(rets, settings.window, settings.min_periods)
    vol_z = trailing_zscores(log_vols, settings.window, settings.min_periods)

    # Offline Isolation Forest over the whole history.
    if_flags = np.zeros(n, dtype=bool)
    if_scores = np.full(n, np.nan)
    feats = np.column_stack([rets, ranges, log_vols])
    valid = np.all(np.isfinite(feats), axis=1)
    if int(valid.sum()) >= settings.iforest_min_buffer:
        model = IsolationForest(
            n_estimators=100,
            contamination=settings.iforest_contamination,
            random_state=42,
        )
        model.fit(feats[valid])
        if_flags[valid] = model.predict(feats[valid]) == -1
        if_scores[valid] = -model.decision_function(feats[valid])

    anomalies: list[Anomaly] = []
    last_flag = -(10**9)
    for i in range(n):
        if i < settings.min_periods or not np.isfinite(rets[i]):
            continue
        methods: list[str] = []
        if np.isfinite(ret_z[i]) and abs(ret_z[i]) >= settings.return_z_threshold:
            methods.append("return_z")
        if np.isfinite(vol_z[i]) and vol_z[i] >= settings.volume_z_threshold:
            methods.append("volume_z")
        if if_flags[i]:
            methods.append("isolation_forest")
        if not methods or i - last_flag <= settings.cooldown:
            continue
        anomalies.append(
            build_anomaly(
                symbol=symbol,
                candle=candles[i],
                pct_change=(math.exp(float(rets[i])) - 1.0) * 100.0,
                methods=methods,
                return_z=float(ret_z[i]) if np.isfinite(ret_z[i]) else None,
                volume_z=float(vol_z[i]) if np.isfinite(vol_z[i]) else None,
                iforest_score=float(if_scores[i]) if np.isfinite(if_scores[i]) else None,
                vol_ratio=_volume_ratio(
                    float(volumes[i]), volumes[max(0, i - settings.window) : i]
                ),
            )
        )
        last_flag = i
    return anomalies
