# WhyLine — Implemented Architecture (Milestone A)

*This document describes what is actually built and measured; it feeds the
final paper. The build roadmap lives in `arch.md`; the PPT is
`../majorproject.pdf`.*

## Pipeline

INGEST → DETECT → VISUALIZE (ATTRIBUTE arrives in Milestone B):

1. **INGEST** (`backend/ingest.py`) — Binance *public* market data, no API key:
   - Backfill: `GET /api/v3/klines`, paginated past the 1000-candle cap,
     dropping the still-forming candle so history contains only closed candles.
   - Live: one combined WebSocket stream
     (`/stream?streams=btcusdt@kline_1m/...`) carrying all 10 symbols;
     auto-reconnect with exponential backoff, and a REST resync on reconnect
     so candles missed during an outage are healed (`apply_kline` dedupes by
     open time).
   - Hosts: `data-api.binance.vision` / `data-stream.binance.vision`
     (Binance's dedicated public-data endpoints; `api.binance.com` /
     `stream.binance.com:9443` are drop-in fallbacks via `.env`).
   - Times are converted **once** at the boundary: Binance milliseconds →
     UNIX seconds (what Lightweight Charts consumes).

2. **DETECT** (`backend/detect.py`) — on every *closed* candle:
   - **Rolling return z-score**: log-return vs trailing `window=60` candles,
     baseline excludes the current candle (a spike must not inflate its own
     baseline std); flag at `|z| ≥ 3.0`; needs `min_periods=30` warm-up.
   - **Rolling volume z-score**: same construction over `log(volume+1)`,
     one-sided (`z ≥ 3.5`) — only surges are anomalies.
   - **Isolation Forest** (scikit-learn): features
     `[log_return, (high−low)/open, log_volume]`; streaming mode refits every
     15 candles on a rolling 240-candle buffer and scores the newest candle
     against the previous ones; contamination 0.01.
   - Startup **back-scan**: the same z-score definition vectorised over the
     backfilled history, plus one offline Isolation Forest fit per symbol —
     so the chart opens pre-annotated. (Streaming and batch z-flags are
     asserted identical in tests; IF flags may differ slightly by design —
     online vs offline fitting.)
   - Flags merge into one anomaly event per candle
     (`methods ⊆ {return_z, volume_z, isolation_forest}`) with a
     human-readable `explanation`, then a per-symbol cooldown of 3 candles
     suppresses repeat flags during one sustained move.

3. **VISUALIZE** (`backend/main.py` + `frontend/`):
   - REST snapshots: `GET /api/history/{symbol}` returns candles (closed +
     the forming one) and anomalies; `GET /api/anomalies` merges all symbols.
   - `/ws` pushes `{type:"candle"}` for every kline tick (forming and closed)
     and `{type:"anomaly"}` when a detector fires.
   - Frontend: Lightweight Charts **v5** (`chart.addSeries(CandlestickSeries)`,
     markers via the `createSeriesMarkers` plugin), volume histogram on an
     overlay price scale, amber anomaly markers (arrow = direction, label =
     move %), hover tooltip with the full quantitative evidence, watchlist
     with per-symbol anomaly counts, global anomaly log with click-to-jump,
     dark/light themes. High-frequency updates flow through a small external
     store into the chart imperatively; React re-renders are throttled to
     ~4/s for the watchlist.

## Empirical settings (measured on real data, `backend/tune.py`)

Back-test over 10 symbols × 1000 one-minute candles (≈7 symbol-days), Jul 2026:

| knob | value | anomalies / symbol-day |
|---|---|---|
| return_z threshold | 2.5 / **3.0** / 3.5 / 4.0 | 33.4 / **16.3** / 9.9 / 7.3 |
| volume_z threshold | 3.0 / **3.5** / 4.0 / 4.5 | 9.5 / **4.5** / 2.0 / 1.0 |
| IF contamination | 0.005 / **0.01** / 0.02 | 5.5 / **10.8** / 19.3 |
| **overall (current, after dedupe+cooldown)** | | **≈23** |

Notes: 1-minute crypto returns are fat-tailed, so `z ≥ 3` fires far more often
than the Gaussian 0.27% — the numbers above are the honest, measured rates.
≈23/symbol-day ≈ one anomaly somewhere across 10 symbols every ~6 minutes:
a good demo cadence.

## Latency (honest numbers)

Detection runs on candle *close* (by design: partial-candle statistics are
unstable), so anomaly latency is dominated by the 1-minute candle interval.
After close, the observed path is sub-second — e.g. a live LTC anomaly was
detected, logged, and broadcast **224 ms** after the minute boundary
(backend log, 2026-07-08 15:25:00.224). Say "detection fires within a second
of candle close; the candle interval sets the floor" — not "millisecond".

## Testing

33 offline tests (`backend/tests/`): z-score hand-computed cases, baseline
exclusion, warm-up, cooldown, planted price/volume spikes, planted
multivariate outliers (streaming + offline IF), streaming↔batch agreement,
Binance REST/WS parsing against real payload shapes, backfill pagination via
mocked transport, API + WebSocket handshake via TestClient.

## Milestone B plan (attribute)

When an anomaly fires: fetch headlines around the timestamp for the affected
asset (NewsAPI/Finnhub free tier, with a canned-JSON fallback for demos),
score with FinBERT (`ProsusAI/finbert`, loaded once at startup, inference in
a thread executor), attach `{headline, sentiment}` to the anomaly event and
render it in the existing tooltip + log. The tooltip already reserves the
slot ("semantic context arrives in Milestone B").
