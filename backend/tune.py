"""Threshold tuning: back-test detector settings on real Binance history.

Answers "how often will each detector fire?" so live thresholds are chosen
deliberately instead of guessed. Run from backend/ with the venv active:

    python tune.py                     # default symbols, 1000 candles each
    python tune.py --symbols BTCUSDT,DOGEUSDT --limit 3000

For each candidate setting it reports anomalies per symbol-day, sweeping one
knob at a time around the current .env/default configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses

import detect
import ingest
from config import settings
from models import Candle

RETURN_Z_GRID = (2.5, 3.0, 3.5, 4.0)
VOLUME_Z_GRID = (3.0, 3.5, 4.0, 4.5)
CONTAMINATION_GRID = (0.005, 0.01, 0.02)


def per_day(count: int, candles: int, interval_minutes: float = 1.0) -> float:
    days = candles * interval_minutes / (60 * 24)
    return count / days if days > 0 else 0.0


def sweep(history: dict[str, list[Candle]]) -> None:
    total_candles = sum(len(c) for c in history.values())
    n_symbols = len(history)
    print(f"\nBack-testing on {n_symbols} symbols x ~{total_candles // max(n_symbols, 1)} candles "
          f"({settings.interval} interval)\n")

    def rate(s, method: str) -> float:
        hits = 0
        for symbol, candles in history.items():
            anomalies = detect.scan_history(symbol, candles, s)
            hits += sum(1 for a in anomalies if method in a.methods)
        return per_day(hits, total_candles)

    print(f"{'return_z threshold':>22} | anomalies / symbol-day")
    for th in RETURN_Z_GRID:
        s = dataclasses.replace(settings, return_z_threshold=th)
        marker = " <- current" if th == settings.return_z_threshold else ""
        print(f"{th:>22} | {rate(s, 'return_z'):.2f}{marker}")

    print(f"\n{'volume_z threshold':>22} | anomalies / symbol-day")
    for th in VOLUME_Z_GRID:
        s = dataclasses.replace(settings, volume_z_threshold=th)
        marker = " <- current" if th == settings.volume_z_threshold else ""
        print(f"{th:>22} | {rate(s, 'volume_z'):.2f}{marker}")

    print(f"\n{'IF contamination':>22} | anomalies / symbol-day")
    for c in CONTAMINATION_GRID:
        s = dataclasses.replace(settings, iforest_contamination=c)
        marker = " <- current" if c == settings.iforest_contamination else ""
        print(f"{c:>22} | {rate(s, 'isolation_forest'):.2f}{marker}")

    base = 0
    for symbol, candles in history.items():
        base += len(detect.scan_history(symbol, candles, settings))
    print(f"\nCurrent settings overall: {per_day(base, total_candles):.2f} "
          f"anomalies / symbol-day (all methods, after cooldown/dedupe)")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(settings.symbols))
    parser.add_argument("--limit", type=int, default=settings.history_limit)
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    async def load(symbol: str) -> tuple[str, list[Candle]]:
        candles = await ingest.fetch_history(
            symbol, settings.interval, args.limit, base=settings.rest_base
        )
        return symbol, candles

    pairs = await asyncio.gather(*(load(s) for s in symbols))
    sweep(dict(pairs))


if __name__ == "__main__":
    asyncio.run(main())
