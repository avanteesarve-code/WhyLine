"""In-memory market state shared by the ingest worker and the API layer."""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass

from fastapi import WebSocket

from config import Settings
from detect import SymbolDetector
from models import Anomaly, Candle

log = logging.getLogger("whyline.state")


class Broadcaster:
    """Fan-out of JSON messages to every connected frontend socket."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def add(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        if not self._clients:
            return
        text = json.dumps(payload)
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)


@dataclass
class SymbolState:
    detector: SymbolDetector
    candles: deque[Candle]
    anomalies: deque[Anomaly]
    forming: Candle | None = None


class MarketState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.broadcaster = Broadcaster()
        self.symbols: dict[str, SymbolState] = {
            sym: SymbolState(
                detector=SymbolDetector(sym, settings),
                candles=deque(maxlen=settings.max_candles_kept),
                anomalies=deque(maxlen=settings.max_anomalies_kept),
            )
            for sym in settings.symbols
        }

    def seed(
        self, symbol: str, candles: list[Candle], anomalies: list[Anomaly]
    ) -> None:
        """Load backfilled history and warm the streaming detector."""
        ss = self.symbols[symbol]
        ss.candles.extend(candles)
        ss.anomalies.extend(anomalies)
        ss.detector.prime(candles)

    def apply_kline(self, symbol: str, candle: Candle, closed: bool) -> Anomaly | None:
        """Absorb one kline event; returns an Anomaly if the candle flags one.

        Deduplicates by open time, so replayed candles (reconnect resync,
        backfill overlap) never double-count in the detector.
        """
        ss = self.symbols.get(symbol)
        if ss is None:
            return None
        if not closed:
            ss.forming = candle
            return None

        if ss.candles and candle.time == ss.candles[-1].time:
            ss.candles[-1] = candle  # same candle re-delivered; refresh only
            return None
        if ss.candles and candle.time < ss.candles[-1].time:
            return None  # stale / out-of-order
        ss.candles.append(candle)
        if ss.forming is not None and ss.forming.time <= candle.time:
            ss.forming = None

        anomaly = ss.detector.update(candle)
        if anomaly is not None:
            ss.anomalies.append(anomaly)
        return anomaly

    def history(self, symbol: str) -> tuple[list[Candle], list[Anomaly]] | None:
        """Closed candles (+ the forming candle, if newer) and anomalies."""
        ss = self.symbols.get(symbol)
        if ss is None:
            return None
        candles = list(ss.candles)
        if ss.forming is not None and (
            not candles or ss.forming.time > candles[-1].time
        ):
            candles.append(ss.forming)
        return candles, list(ss.anomalies)

    def recent_anomalies(self, limit: int = 100) -> list[Anomaly]:
        merged: list[Anomaly] = []
        for ss in self.symbols.values():
            merged.extend(ss.anomalies)
        merged.sort(key=lambda a: a.time, reverse=True)
        return merged[:limit]
