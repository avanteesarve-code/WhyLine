# WhyLine

**Real-Time Anomaly Detection and Semantic Event Mapping in Financial Time Series.**

WhyLine watches live crypto markets, statistically detects the moment something
*sudden* happens (price spikes, volume surges, multivariate outliers), marks it
on a live candlestick chart, and — in Milestone B — explains **why** it happened
by attaching sentiment-scored news to each anomaly.

![WhyLine dashboard](docs/screenshots/dashboard-dark.png)

## Status

- ✅ **Milestone A (done)** — the quantitative half:
  live Binance ingest → z-score + Isolation Forest detection → live chart
  with historical backfill, anomaly markers, tooltips, watchlist, anomaly log,
  dark/light themes.
- ✅ **Milestone B (done)** — the semantic half: every live anomaly is
  immediately marked `pending`, then enriched asynchronously with Finnhub
  crypto news + FinBERT headline sentiment (`backend/attribute.py`,
  `backend/news.py`, `backend/sentiment.py`). The UI renders **real news
  only** — sample/fallback headlines are never displayed.

## Architecture (Milestone A)

```
           Binance (public market data, no API key)
     REST /api/v3/klines          WS <sym>@kline_1m  x10 symbols
              │                          │
              ▼                          ▼
   ┌──────────────────────────────────────────────┐
   │              backend (FastAPI)               │
   │  ingest.py    backfill 1000 candles/symbol   │
   │               + live stream, reconnect+resync│
   │  detect.py    rolling z-score (returns, vol) │
   │               + Isolation Forest (streaming  │
   │               refits; offline scan for       │
   │               backfilled history)            │
│  state.py     per-symbol candles/anomalies,  │
│               authoritative attribution      │
│               state, WebSocket broadcaster   │
│  main.py      GET /api/history/{sym}         │
│               GET /api/anomalies, /ws        │
│  attribute.py async news + sentiment per live│
│               anomaly (pending → ok)         │
│  news.py      Finnhub crypto news (real      │
│               articles; sample fallback kept │
│               server-side only, never shown) │
│  sentiment.py FinBERT headline tone          │
   └──────────────────────┬───────────────────────┘
                          │ candles + anomaly events (JSON)
                          ▼
   ┌──────────────────────────────────────────────┐
   │        frontend (Vite + React + TS)          │
   │  Lightweight Charts v5 candlesticks + volume │
   │  amber anomaly markers + evidence tooltip    │
   │  watchlist (10 symbols) · anomaly log        │
   │  dark / light themes                         │
   └──────────────────────────────────────────────┘
```

## Run it

Backend (Python 3.11+):

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Real news needs a free Finnhub key (https://finnhub.io/dashboard).
Put it in `backend/.env` (gitignored — never commit it):

```bash
FINNHUB_API_KEY=
```

See `backend/.env.example` for every knob (symbols, thresholds, news
windows, sentiment, attribution). Without a key the pipeline still works
end-to-end but serves sample fallback headlines internally, which the UI
deliberately does not render.

Frontend (Node 18+), in a second terminal:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api and /ws to :8000)
```

On startup the backend backfills ~16 h of 1-minute candles per symbol,
back-scans them so the chart opens **already annotated with historical
anomalies**, then switches to the live stream — so you never stare at an empty
chart waiting for something to happen.

## The 10 symbols (and why these)

Chosen so sudden moves are (a) visible on a 1-minute chart and (b) plausibly
attributable to a findable cause — which is exactly what Milestone B needs:

| Symbol | Typical "why" behind sudden moves |
|---|---|
| BTCUSDT | macro prints, ETF flows |
| ETHUSDT | ETF news, protocol upgrades |
| SOLUSDT | ecosystem news, network outages |
| XRPUSDT | regulatory / court rulings |
| BNBUSDT | Binance exchange news |
| DOGEUSDT | social-media (celebrity) posts |
| ADAUSDT | protocol milestones |
| AVAXUSDT | ecosystem / partnership news |
| LINKUSDT | partnership announcements |
| LTCUSDT | payments adoption, halving cycles |

Override with `SYMBOLS=` in `backend/.env` (see `.env.example` for every knob:
interval, thresholds, window sizes, history depth).

## Detection (the quantitative core)

Each *closed* candle is scored three ways:

1. **Return z-score** — log-return vs. a trailing 60-candle baseline that
   excludes the current candle; flags `|z| ≥ 3`.
2. **Volume z-score** — log-volume vs. the same style of baseline, one-sided,
   flags `z ≥ 3.5`.
3. **Isolation Forest** — multivariate outlier score over
   `[log-return, high-low range, log-volume]`, refit on a rolling 4 h buffer;
   catches unusual *combinations* the individual z-scores miss.

Flags are merged into one anomaly event with a plain-English explanation
("Price jumped 0.55% in one candle (z +4.8); volume 17.1× the trailing
median"), deduplicated per candle, and rate-limited by a per-symbol cooldown.
Tune thresholds against real history with `python backend/tune.py`.

## Tests

```bash
cd backend && ./venv/bin/python -m pytest tests -q     # 206 tests, offline
cd frontend && npm run test:store && npm run test:views && npm run build
```

Covers the z-score math (including streaming/batch agreement), Isolation
Forest flagging of planted outliers, cooldown/warmup behaviour, Binance
REST/WS parsing, backfill pagination, the API surface, the live
pending → attribution flow against the authoritative anomaly state,
Finnhub request/response mapping (mocked — no network, no API key needed),
frontend store merging, and attribution rendering (real news shown, sample
never shown) — all offline.

## Screenshots

| Dark | Light | Anomaly tooltip |
|---|---|---|
| ![dark](docs/screenshots/dashboard-dark.png) | ![light](docs/screenshots/dashboard-light.png) | ![tooltip](docs/screenshots/anomaly-tooltip.png) |

## Team

- Rashmin Chaudhari
- Avantee Sarve
- Shambhavi Tongaonkar
- Swayam Takkamore

Dept. of Computer Science and Business Systems </br>
St. Vincent Pallotti College of Engineering & Technology, Nagpur.
