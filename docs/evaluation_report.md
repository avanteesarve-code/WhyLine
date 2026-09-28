# WhyLine Evaluation Report

Measurement date: 2026-09-28. Every value below was obtained from the
repository, its test suite, a reproducible script, or live runtime
measurement on this machine. Nothing is estimated. Metrics that cannot be
legitimately measured are marked **N/A** with the reason stated.

## 1. System Configuration

| Component | Configuration |
|---|---|
| Backend language/runtime | Python 3.14.6 (measured via `python --version`) |
| Backend framework | FastAPI + uvicorn (`backend/requirements.txt`) |
| Detection/ML | numpy, pandas, scikit-learn (IsolationForest) |
| News HTTP | httpx; config via python-dotenv (`backend/.env`) |
| Sentiment model | ProsusAI/finbert (Hugging Face, lazy-loaded, CPU) |
| Frontend runtime | Node 24.14.0, React 19.2.7, Vite 8.1.3, TypeScript 6.0.3 |
| Charting library | lightweight-charts 5.2.0 (installed version) |
| Market data source | Binance public market data (`https://data-api.binance.vision` REST backfill, `wss://data-stream.binance.vision` live kline stream, no API key) |
| Symbols monitored | 10: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, LTCUSDT |
| Candle interval | 1m (`interval: 1m` from `/api/symbols`) |
| Backfill depth | 1000 closed candles per symbol (`history_limit`) |
| Return z-score threshold | 3.0 (absolute) |
| Volume z-score threshold | 3.5 (one-sided) |
| Isolation Forest | 100 estimators, contamination 0.01, 240-row rolling buffer, refit every 15 candles, scoring starts at 180 rows |
| OS/hardware | Windows 11 Home 64-bit, 12th Gen Intel i5-1235U, 16 GB RAM (measured via `Get-ComputerInfo`) |

## 2. Dataset

Live Binance 1-minute klines. Measured snapshot from the running backend
(`GET /api/history/{symbol}` for all 10 symbols, same script run):

- Symbols: 10
- Candle interval: 1 minute
- Total candles evaluated: **10,880**
- Time range (Unix seconds): **1790549640 → 1790614860** (span **18.12 h**)
- Stored anomalies at snapshot: **177** (172 historical backfill-scan anomalies with `attribution: null` by design; 5 live anomalies with real attributed news)
- Data provenance: Binance public market-data API. The dataset is public
  third-party market data, not proprietary or licensed project data.

## 3. Testing Methodology

- Backend: `pytest tests -q` from `backend/` — **206 passed**, fully offline
  (Binance/Finnhub/Hugging Face all mocked via MockTransport and stubs;
  no test needs a real API key or network).
- Per-file coverage: detect 18, api 7, models 5, news 39, sentiment 46,
  attribute 38, live-attribution 8, finnhub-real-news 37, ingest 8.
- Frontend: `npm run test:store` (market-store merge, 10 checks) and
  `npm run test:views` (server-rendered AttributionView, 17 checks) — both
  execute the real transpiled/compiled source, not copies.
- Build: `npm run build` (`tsc -b && vite build`) clean.
- Latency benchmarks: `backend/eval_perf.py` (checked in) plus structured
  server-log lines (`attribution <id>: status=.. items=.. (Nms)`).
- Live provider verification: per-symbol `fetch_news` calls against Finnhub
  with the configured key; only key-presence (boolean), HTTP status, counts,
  and article metadata recorded — the key never appears in output.

## 4. Anomaly Detection Evaluation (Milestone A)

Method: live-dataset observation + `eval_perf.py` micro-benchmark
(`MarketState.apply_kline` over 300 candles after 240 warmup candles,
16 spikes flagged) + offline unit tests.

- Evaluation window: 18.12 h of 1-minute candles across 10 symbols
- Anomalies detected (stored, snapshot): 177
- Detection events (total): 177
- Offline detection test cases passing: 18 (`test_detect.py`) covering
  z-score math, streaming/batch agreement, planted-outlier flagging,
  cooldown/warmup
- Detection latency, per closed candle (n=300): avg **19.8 ms**,
  median **11.8 ms**, min 7.6 ms, max 171.7 ms (maxima coincide with
  periodic Isolation Forest refits; the feed consumes one candle per
  minute per symbol, so this is ~3 orders of magnitude inside budget)
- False positives: **N/A** — no manually labeled ground-truth event set
  exists; manufacturing a rate would require one.
- False negatives: **N/A** — same reason.

## 5. Semantic Event Mapping Evaluation (Milestone B)

Method: controlled per-symbol `fetch_news` run (key configured, live
Finnhub `category=crypto` feed: HTTP 200, 86 articles, 86/86 parsed),
plus live-server attribution log lines.

Per-symbol verification (all 10 monitored symbols, same run):

| Symbol | Normalized query | Provider result | Real articles | Fallback |
|---|---|---|---|---|
| BTCUSDT | bitcoin/btc | relaxed_window | 5 | no |
| ETHUSDT | ethereum/eth | relaxed_window | 4 | no |
| SOLUSDT | solana/sol | relaxed_window | 2 | no |
| XRPUSDT | xrp/ripple | relaxed_window | 1 | no |
| BNBUSDT | bnb/binance | broad_market | 5 | no |
| DOGEUSDT | dogecoin/doge | broad_market | 5 | no |
| ADAUSDT | cardano/ada | broad_market | 5 | no |
| AVAXUSDT | avalanche/avax | broad_market | 5 | no |
| LINKUSDT | chainlink | relaxed_window | 1 | no |
| LTCUSDT | litecoin/ltc | broad_market | 5 | no |

- Anomalies evaluated: 10 (one per monitored symbol)
- Events matched with candidate real news: 10 (5 asset-specific via the
  relaxed 24 h lookback; 5 general-market via the broad tier — all real
  provider articles with true source/URL/timestamps, never sample data)
- Matching rate (events with real provider articles / evaluated): **100%**
  (asset-specific subset: 5/10 = 50%; remainder served as explicitly marked
  broad crypto-market context)
- Sentiment performance (manual validation): **Not formally evaluated** —
  no labeled headline-tone validation set exists. The 46 sentiment unit
  tests cover output parsing and failure contracts with stubbed
  classifiers, not predictive accuracy.
- Average attribution-processing latency (task start → completion, from
  server logs, includes Finnhub fetch + FinBERT scoring in a worker
  thread): n=6, avg **3963 ms**, median **893 ms**, min 241 ms,
  max 10863 ms (the maximum is the first-ever run including the one-time
  FinBERT model download; steady-state completions complete in under ~1 s
  in these samples — see §7 for the small-sample caveat).

New live anomalies on the running server confirm the path end-to-end,
e.g. `BTCUSDT-1790612160` (`ok`, 5 real URLs) and
`DOGEUSDT-1790612160` (`ok`, broad tier, 5 real URLs).

## 6. End-to-End System Performance

| Metric | Start → end | n | Avg | Median | Min | Max |
|---|---|---|---|---|---|---|
| REST ingestion (Binance klines, 1000 candles) | request → parsed candles | 1 | 1169.4 ms | — | — | — |
| REST snapshot (`GET /api/anomalies`) | request → JSON (loopback TestClient) | 50 | 2.01 ms | 2.07 ms | 0.93 ms | 3.04 ms |
| Detection (`apply_kline`/candle) | candle in → anomaly out | 300 | 19.77 ms | 11.81 ms | 7.60 ms | 171.66 ms |
| Semantic processing (live log) | attribution task start → broadcast | 6 | 3963 ms | 893 ms | 241 ms | 10863 ms |
| WebSocket propagation (loopback) | broadcast → client receipt | 20 | 9.62 ms | 3.94 ms | 1.20 ms | 25.26 ms |
| End-to-end anomaly → UI render | detection → browser paint | — | **N/A** | — | — | — |

Notes:

- The WebSocket figure is a loopback lower bound measured with a test
  client; true browser receipt needs browser instrumentation that does
  not exist in this repo.
- End-to-end anomaly-to-UI latency is **N/A** for the same reason: no
  browser-side timing harness exists. Claiming it would require measuring
  paint in a real browser session.
- REST ingestion n=1 is a single timed fetch, reported as such.

## 7. Discussion

- Return/volume z-scores are O(window) rolling statistics over
  60-element deques — microseconds of arithmetic per candle — which is why
  median per-candle detection cost stays near 12 ms even with the
  periodic Isolation Forest refit included in the average.
- Isolation Forest complements the z-scores by scoring the joint
  `[log-return, range, log-volume]` vector: live anomalies in this window
  were flagged by `return_z`, `volume_z`, `isolation_forest`, and their
  combinations, with a per-symbol cooldown suppressing repeats.
- Attribution is fully asynchronous: the anomaly broadcasts immediately
  with `attribution: pending` and the news/sentiment work completes in a
  worker thread, so the 0.2–11 s semantic latency never stalls the
  1-minute candle feed.
- Real-news matching is tiered (strict 1 h + keyword → relaxed 24 h +
  keyword → broad recent-market) because the free-tier feed is sparse
  (~1 article/hour): the strict window alone matched 0/10 symbols in the
  verification run, while the relaxed/broad tiers produced real articles
  for 10/10 without ever serving sample data as news.
- Sentiment (FinBERT, `P(positive) − P(negative)`) describes headline
  tone only; a missing score stays missing and is never invented as
  neutral.
- WebSocket propagation is a JSON fan-out measured in single-digit
  milliseconds on loopback; REST snapshots cost ~2 ms.
- Fallback (sample headlines) still exists server-side for no-key /
  provider-failure / empty-feed cases, but the UI deliberately never
  renders it — those states collapse to “No related news found.” or
  “Related news unavailable.”
- All latency samples are small (n ≤ 300 for benchmarks, n = 6 for live
  attribution); no statistical significance is claimed.

## 8. Limitations

- No labeled ground truth → no false-positive/false-negative rates and no
  sentiment accuracy/precision/recall/F1 (marked N/A above).
- Free-tier news sparsity: asset-specific matches required the relaxed
  24 h lookback for 5/10 symbols; 5/10 were served explicitly marked
  broad-market context. Symbols with no provider coverage at all still
  fall back (server-side only, never displayed).
- Latency samples are small; the 10.9 s attribution maximum includes a
  one-time model download.
- No browser instrumentation → true UI-paint latency unmeasured.
- Association is not causation: every headline is temporally associated
  candidate context; the system never claims a headline caused a move
  (see also the UI caption “association, not causation”).

## 9. Reproducibility

```bash
cd backend && python -m pytest tests -q          # 206 passed, offline
cd backend && python eval_perf.py                 # detection/REST/WS benchmarks
cd backend && python eval_perf.py --binance       # + one live Binance fetch
cd backend && python tune.py                      # threshold back-test (network)
cd frontend && npm run test:store                 # store merge checks
cd frontend && npm run test:views                 # AttributionView render checks
cd frontend && npm run build                      # tsc + vite production build
```

Environment: Python 3.14.6, Node 24.14.0, `backend/requirements.txt`,
`frontend/package.json` (React 19.2.7, lightweight-charts 5.2.0).
Real-news verification needs `FINNHUB_API_KEY=` in `backend/.env`
(gitignored; never committed, never printed). All pytest/frontend tests
are mocked and need no key or network.
