# Build Roadmap — Real-Time Anomaly Detection & Semantic Event Mapping

**Project:** Real-Time Anomaly Detection and Semantic Event Mapping in Financial Time Series
**Goal:** Ship a working demo — ~50% tomorrow, ~50% next week — built with AI assistance (Claude Code / opencode).
**Author of roadmap:** planning doc for the project team (Rashmin, Avantee, Shambhavi, Swayam).

---

## 0. Read this first — the honest reality check

Your PPT is ambitious and describes an *institutional-grade* system ("millisecond", "microservices", "Rust", "real-time US equities"). That framing is great for a seminar, but a literal build of it in two weeks by a student team is not realistic and not necessary. This roadmap builds a **faithful, demonstrable version** of the same idea that a panel will find genuinely impressive, while cutting the parts that add cost and complexity without adding demo value.

Three decisions I'm making for you, with reasons:

| PPT says | This roadmap does | Why |
|---|---|---|
| Real-time US equities (Polygon/Binance) | **Binance crypto WebSocket** as the primary live feed; stocks as an optional delayed/paid path | Free real-time stock data does **not** exist — free tiers are 15-min delayed. Binance crypto is free *and* truly real-time, so your "real-time" claim becomes true instead of aspirational. |
| Rust + Node.js ingestion, microservices | **Single Python backend (FastAPI)** + **React frontend** | Rust and a microservice mesh cost days and demo nothing. One Python service does ingest + detect + attribute cleanly. |
| Kafka message queue | **In-process async queue** (Python `asyncio.Queue`), Kafka optional | Kafka is operational overhead you don't need for one machine. Mention it as "future scaling" in your report. |

> ⚠️ **Verify-before-you-trust note:** Throughout this doc, code snippets are *minimal illustrations of shape*, not guaranteed-correct API calls. Library APIs (especially Lightweight Charts markers, Binance/Finnhub endpoints, and HuggingFace model loading) change. Whenever you see a specific function name, have your AI assistant confirm it against **current official docs** before relying on it. I have flagged the highest-risk spots inline.

---

## 1. Final architecture (what you're actually building)

A single pipeline with four stages — the same **INGEST → DETECT → ATTRIBUTE → VISUALIZE** flow from your methodology slide, just consolidated:

```
┌─────────────────────────────────────────────────────────────┐
│                    PYTHON BACKEND (FastAPI)                   │
│                                                              │
│  INGEST            DETECT           ATTRIBUTE                 │
│  ┌────────┐       ┌─────────┐      ┌──────────────┐          │
│  │Binance │──────▶│ Rolling │─────▶│ News fetch + │          │
│  │WebSocket│ ticks │ Z-score │ flag │ FinBERT NLP  │          │
│  │  feed  │       │ +Isolation│    │  sentiment   │          │
│  └────────┘       │  Forest  │     └──────┬───────┘          │
│                   └─────────┘             │                  │
│                        │                  │                  │
│                        ▼                  ▼                  │
│                   ┌──────────────────────────────┐          │
│                   │  WebSocket broadcast to UI    │          │
│                   └──────────────┬───────────────┘          │
└──────────────────────────────────┼──────────────────────────┘
                                    │  (candles + anomaly + news JSON)
                                    ▼
┌─────────────────────────────────────────────────────────────┐
│              REACT FRONTEND (Vite + TypeScript)              │
│   Lightweight Charts v5 candlestick chart (dark mode)       │
│   + anomaly markers + hover tooltip with news & sentiment   │
└─────────────────────────────────────────────────────────────┘
```

**Final tech stack:**

| Layer | Tool | Notes |
|---|---|---|
| Language (backend) | Python 3.11+ | Everything ML/NLP lives here |
| Backend framework | FastAPI + Uvicorn | Serves REST + WebSocket to the frontend |
| Live market data | Binance WebSocket (crypto) | Free, truly real-time. `websockets` or `python-binance` |
| Anomaly detection | `scikit-learn` (Isolation Forest) + rolling Z-score (`numpy`/`pandas`) | Z-score is your fast path; Isolation Forest is the "ML" flex |
| News source | NewsAPI.org **or** Finnhub company-news endpoint | Both have free tiers — see §7 for the honesty caveat |
| NLP sentiment | FinBERT (`ProsusAI/finbert` on HuggingFace) via `transformers` | The centrepiece of your "semantic" half |
| Frontend build | Vite + React + TypeScript | Fast dev server, clean setup |
| Charting | `lightweight-charts` **v5** | ⚠️ v5 API differs from v4 tutorials — see §9 |
| Realtime transport | WebSockets (FastAPI ↔ React) | One socket carries candles + anomaly events + news |

> 🔎 **On model names:** `ProsusAI/finbert` is the widely-used FinBERT sentiment model on HuggingFace and is what your literature survey (Araci, 2019) refers to. I'm confident this identifier is correct, but confirm it loads in your environment before building around it, and have a fallback ready (`yiyanghkust/finbert-tone` is a commonly cited alternative — verify availability yourself rather than assuming).

---

## 2. The 50 / 50 split

You need to show **half tomorrow** and **half next week**. Split along the natural seam in your own title: **quantitative (anomaly detection)** vs **qualitative (semantic/NLP)**.

### 🎯 Milestone A — due TOMORROW (the "quantitative" 50%)
A running system that:
1. Streams live crypto price data from Binance.
2. Aggregates ticks into candles (e.g., 1-minute).
3. Detects statistical anomalies (Z-score + Isolation Forest) on price/volume.
4. Displays a **dark-mode and light-mode candlestick chart** in the browser that **updates live** and **marks anomalies** on the chart.

This alone is a complete, honest, working demo. It maps to INGEST + DETECT + VISUALIZE.

### 🎯 Milestone B — due NEXT WEEK (the "semantic" 50%)
Add the ATTRIBUTE layer + polish:
1. When an anomaly fires, fetch news around that timestamp.
2. Run FinBERT to extract sentiment (and a headline/event label).
3. Attach that context to the anomaly marker as an **interactive tooltip** ("why it happened").
4. Polish the dashboard (UI/UX, dark mode, layout) and write up results + latency notes for the paper.

This maps to ATTRIBUTE + the finished VISUALIZE, and completes the story your PPT tells.

---

## 3. How to work with the AI assistant (this is the important part)

You said you'll build with Claude/opencode. The difference between a smooth build and a frustrating one is **how you drive the agent**. Rules that actually matter:

1. **Build the skeleton first, then fill it.** Ask the agent to scaffold the whole repo structure with empty/stub functions before writing real logic. You catch structural problems early.
2. **One vertical slice at a time.** Get *fake data → chart* working end-to-end before adding real data. Get *real data → chart* working before adding anomaly detection. Never build three layers blind.
3. **Give the agent the docs, don't let it guess.** For Lightweight Charts specifically, tell it: *"Use lightweight-charts v5 API (`chart.addSeries(CandlestickSeries, ...)`), not v4. Check the official v5 docs and the shipped agent skill."* This prevents the single most common failure mode here.
4. **Ask it to run and show you output at every step.** "Run it and paste the output" beats "looks right to me."
5. **Commit after every working slice** (git). When the agent breaks something, you roll back instead of debugging a mess.
6. **When something fails, paste the exact error back** — don't paraphrase.

Copy-pasteable starter prompts are provided at each phase below (§5, §6). Adapt names to taste.

---

## 4. Repository structure

Ask the agent to create exactly this on day one:

```
anomaly-finance/
├── backend/
│   ├── main.py                 # FastAPI app: REST + WebSocket endpoints
│   ├── ingest.py               # Binance WebSocket client → candle builder
│   ├── detect.py               # Z-score + Isolation Forest anomaly logic
│   ├── attribute.py            # News fetch + FinBERT (Milestone B)
│   ├── models.py               # Pydantic schemas for candle/anomaly/news
│   ├── config.py               # API keys, symbol, timeframe (from .env)
│   ├── requirements.txt
│   └── .env.example            # BINANCE_SYMBOL, NEWS_API_KEY, etc.
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── ChartPanel.tsx      # Lightweight Charts v5 wrapper
│   │   ├── useLiveSocket.ts    # hook: connect to backend WS
│   │   └── types.ts
│   ├── package.json
│   └── vite.config.ts
├── notebooks/
│   └── explore.ipynb           # tune Z-score threshold / Isolation Forest offline
├── docs/
│   └── architecture.md         # keep this updated → feeds your final paper
├── .gitignore                  # MUST ignore .env and node_modules
└── README.md
```

---

## 5. MILESTONE A — step by step (due tomorrow)

Work through these in order. **Do not skip ahead** — each step de-risks the next.

### Phase A0 — Environment (30–45 min)
- Install Python 3.11+, Node 18+ (verify with `python --version`, `node --version`).
- Create the repo, `git init`, add `.gitignore` (ignore `.env`, `node_modules`, `__pycache__`, `venv`).
- Backend: create a virtualenv, install `fastapi uvicorn websockets pandas numpy scikit-learn python-dotenv`.
- Frontend: `npm create vite@latest frontend -- --template react-ts`, then `npm install lightweight-charts`.

> ⚠️ Verify the exact Vite template invocation and `lightweight-charts` install against current npm — the create-vite CLI flags occasionally change.

**AI prompt:**
> "Scaffold the repo structure I'll paste below. Create a Python venv in `backend/` with a `requirements.txt` containing fastapi, uvicorn, websockets, pandas, numpy, scikit-learn, python-dotenv. Set up a Vite React-TypeScript app in `frontend/` and install `lightweight-charts`. Add a `.gitignore` that ignores `.env`, `node_modules`, `venv`, `__pycache__`. Then run both dev servers and confirm they start. [paste structure from §4]"

### Phase A1 — Fake-data chart end-to-end (1–1.5 hr) ← *do this before touching real data*
Prove the pipe works with garbage data first.
- Backend: a FastAPI WebSocket endpoint that emits a **hardcoded/randomly-walking** candle every second.
- Frontend: connect, render a dark-mode candlestick chart that updates live.

**Why:** if real data breaks later, you already know the chart + socket half is solid.

**AI prompt:**
> "In `backend/main.py`, add a FastAPI WebSocket endpoint `/ws` that every 1 second sends a JSON candle `{time, open, high, low, close, volume}` generated by a random walk. In `frontend/src`, create a Lightweight Charts **v5** dark-mode candlestick chart (`chart.addSeries(CandlestickSeries, ...)` — NOT the v4 `addCandlestickSeries`). Connect to `/ws` via a `useLiveSocket` hook and update the series with `series.update(candle)` on each message. Run both and confirm candles appear and move."

**✅ Checkpoint:** candles animate in the browser. Commit.

### Phase A2 — Real Binance data (1.5–2 hr)
Swap the random walk for a real feed.
- In `ingest.py`, connect to Binance's public WebSocket stream for a symbol (e.g. `btcusdt`) at a kline/candlestick interval (e.g. `1m`), OR subscribe to the trade stream and aggregate ticks into candles yourself.
- Feed those candles into the same `/ws` broadcast you built in A1.

> ⚠️ **Verify the exact Binance stream URL and message format** (kline vs trade stream field names) against Binance's current official API docs. Do not let the agent invent the endpoint — have it fetch the real docs. Binance public market-data streams do not require an API key, which is why we start here.

**AI prompt:**
> "Replace the random-walk generator with a real Binance WebSocket kline stream for `btcusdt` at `1m` interval. Look up the current official Binance stream URL and message schema first — don't guess the field names. Parse each kline into our candle schema and broadcast it to connected frontend clients over `/ws`. Handle reconnection if the Binance socket drops. Run it and paste the first few real candles."

**✅ Checkpoint:** the chart now shows real BTC/USDT candles moving live. Commit. **This is already a legitimately impressive demo on its own.**

### Phase A3 — Anomaly detection (2–3 hr) ← *the core of Milestone A*
Now the "detect" stage.
- **Z-score (fast path):** maintain a rolling window (e.g. last 30–60 candles) of returns or volume; compute `z = (x − mean) / std`; flag when `|z| > threshold` (start with 3.0, tune later).
- **Isolation Forest (the ML flex):** in `detect.py`, fit `sklearn.ensemble.IsolationForest` on a small feature set (e.g. return, volatility, volume) over a rolling buffer and flag points it scores as outliers.
- When either flags an anomaly, emit an **anomaly event** over the same socket: `{type:"anomaly", time, price, zscore, method}`.
- Frontend: place a **marker** on the chart at that candle (see §9 for the v5 marker caveat).

> 💡 Tune the Z-score threshold and Isolation Forest `contamination` in `notebooks/explore.ipynb` on historical Binance candles first, so your live demo actually fires anomalies at sensible moments instead of never (or constantly).

**AI prompt:**
> "In `backend/detect.py`, implement two detectors over a rolling window of recent candles: (1) a rolling Z-score on log-returns and on volume, flagging `|z| > 3`; (2) a scikit-learn IsolationForest on features [return, rolling volatility, volume] refit on the rolling buffer. When either flags the latest candle, broadcast an anomaly event `{type:'anomaly', time, price, method, score}` over `/ws`. On the frontend, add a visible marker at the anomaly's candle. Run against live BTC data and show me anomaly events firing."

**✅ Checkpoint:** live chart + anomalies marked in real time. **Milestone A is done.** Commit and tag `milestone-a`.

### Phase A4 — Buffer for the demo (30 min)
- Add a tiny `README` "how to run" section.
- Screenshot / screen-record it working (in case live BTC is quiet during your presentation — you want a backup).
- Prepare a one-line explanation of *what an anomaly means here* so you can answer the panel.

---

## 6. MILESTONE B — step by step (due next week)

The "why did it happen" half. This is what separates your project from the ten papers in your literature survey (each of which, per your own gap analysis, does detection **or** attribution but not both in one live view).

### Phase B1 — News fetching on anomaly (2–3 hr)
- In `attribute.py`, when an anomaly fires, query a news API for headlines around that timestamp (and, for crypto, around the asset — e.g. "Bitcoin").
- Return the top few headlines.

> ⚠️ **News-source honesty caveat:** free news APIs are restrictive. NewsAPI.org's free tier is limited (historical window and rate limits) and Finnhub's news endpoint has its own limits. **Verify the current free-tier terms yourself before committing** — they change, and I don't want you to design around a limit that no longer holds. For a *guaranteed-to-work demo*, keep a small local JSON of canned headlines as a fallback so a rate-limit doesn't kill your presentation. Disclose this fallback honestly in your report if you use it.

**AI prompt:**
> "In `backend/attribute.py`, add a `fetch_news(timestamp, query)` function that calls [chosen news API] for recent headlines about the traded asset. First look up the current free-tier rate limits and required params for that API — tell me what they are before coding. Add a local JSON fallback of 5 canned headlines used when the API is unavailable. Wire it so that when an anomaly fires, we fetch news and attach it to the anomaly event."

### Phase B2 — FinBERT sentiment (3–4 hr) ← *centrepiece of Milestone B*
- Load `ProsusAI/finbert` via HuggingFace `transformers`.
- For each fetched headline, get a sentiment label (positive/negative/neutral) and a score. Map it toward your PPT's −1.0…+1.0 scale (e.g. positive → +score, negative → −score, neutral → ~0).
- Attach `{headline, sentiment_label, sentiment_score}` to the anomaly event.

> ⚠️ Loading a transformer model is heavy (downloads weights, needs `torch`). Load the model **once at startup**, not per request, or your latency dies. Run FinBERT inference **off the main async loop** (a thread/executor) so it doesn't block the WebSocket. Verify `transformers` + `torch` install cleanly in your environment early — this is the most common Milestone-B blocker.

**AI prompt:**
> "In `attribute.py`, load `ProsusAI/finbert` with HuggingFace transformers **once at app startup**. Add `score_sentiment(text)` returning a label and a signed score in roughly [-1, 1]. Run inference in a thread executor so it doesn't block the FastAPI event loop. For each anomaly's fetched headlines, compute sentiment and attach the top headline + score to the anomaly event sent to the frontend. Confirm the model loads and print a sample classification for a test headline."

**✅ Checkpoint:** anomaly events now carry a headline + FinBERT sentiment. Commit.

### Phase B3 — The tooltip (the payoff) (2–3 hr)
- Frontend: on hovering/clicking an anomaly marker, show a tooltip with: the headline, the extracted event, the FinBERT sentiment score, and the anomaly's Z-score.
- This is the exact "MAJOR MARKET ANOMALY" tooltip mock on your Objectives/Conclusion slides — now real.

**AI prompt:**
> "On the frontend, when the user hovers or clicks an anomaly marker, render a styled dark-mode tooltip showing: headline, sentiment label + score (−1 to +1), and the anomaly Z-score/method. Position it near the marker. Use the Lightweight Charts v5 crosshair/click subscription to detect which anomaly is under the cursor — check the v5 docs for the correct subscription method."

### Phase B4 — Polish + paper (rest of week)
- Dark-mode UI/UX pass (layout, spacing, a header, a symbol selector if time).
- Add a small "anomaly log" side panel listing recent anomalies + their news — makes the demo readable.
- **Latency notes:** log timestamps at ingest / detect / attribute / render and report end-to-end latency. Your PPT and paper both promise "low-latency" — measure it and state the real number honestly (it will be seconds, not milliseconds, and that's fine — be honest about it).
- Update `docs/architecture.md`; it feeds your final paper (Phase 5 of your timeline).

**✅ Checkpoint:** full INGEST→DETECT→ATTRIBUTE→VISUALIZE loop, polished. Tag `milestone-b`.

---

## 7. API keys & accounts — get these TODAY

Sign up before you build so you're not blocked mid-session:

| Service | For | Cost | Note |
|---|---|---|---|
| Binance | Live crypto market data | Free, **no key needed** for public market streams | Verify current public-stream access; no account strictly required for public WS, but confirm |
| NewsAPI.org **or** Finnhub | Headlines for attribution | Free tier | ⚠️ Verify current free-tier limits — they're restrictive and change |
| HuggingFace | Downloading FinBERT | Free | Account optional for public models; verify |

> Do **not** commit any key to git. Put them in `backend/.env`, and make sure `.env` is in `.gitignore`. Ship a `.env.example` with blank values.

---

## 8. Suggested time budget

**Tomorrow (Milestone A) — one focused day:**
```
A0 Environment .............. 0.75 hr
A1 Fake-data chart .......... 1.5 hr   ← proves the pipe
A2 Real Binance feed ........ 2 hr
A3 Anomaly detection ........ 3 hr     ← the core
A4 Demo buffer/screenshots .. 0.75 hr
                     Total ≈ 8 hr
```
If you're short on time, **A1+A2 alone** (live real-time candlestick chart) is a defensible 50% — anomaly detection can slip into the next day's buffer. Don't sacrifice a *working* smaller demo for a *broken* bigger one.

**Next week (Milestone B):**
```
B1 News fetch ............... 3 hr
B2 FinBERT sentiment ........ 4 hr     ← centrepiece, expect install friction
B3 Tooltip payoff ........... 3 hr
B4 Polish + latency + paper . rest of week
```

---

## 9. Known foot-guns (read before you hit them)

1. **Lightweight Charts v4 vs v5.** Most tutorials online are v4 and use `chart.addCandlestickSeries()`. **v5 uses `chart.addSeries(CandlestickSeries, {...})`.** If the agent writes v4 code against a v5 install, nothing renders and the error is cryptic. Tell it explicitly to use v5, and point it at the official v5 docs / the shipped agent skill.
2. **Series markers moved in v5.** In older versions markers were set via `series.setMarkers([...])`. In v5 the markers API changed (it moved into a separate primitive/plugin). **Do not assume `setMarkers` exists — verify the current v5 marker API in the docs** before building your anomaly-marker code. This is the single most likely thing to silently break.
3. **Time format mismatch.** Lightweight Charts expects time as a UNIX timestamp (seconds) or a `'yyyy-mm-dd'` string, consistently. Binance gives you milliseconds. Divide by 1000. Mixed formats = blank chart, no error.
4. **FinBERT is heavy.** `torch` + model download can be hundreds of MB and slow on first run. Do it early, not the night before Milestone B. Load the model once.
5. **Blocking the async loop.** Running FinBERT or a synchronous news request directly inside an `async def` WebSocket handler freezes your live feed. Use a thread executor.
6. **CORS / WebSocket origin.** The Vite dev server (e.g. :5173) and FastAPI (e.g. :8000) are different origins. Configure FastAPI CORS and use the right `ws://localhost:8000/ws` URL, or the socket silently fails to connect.
7. **Free news-API limits mid-demo.** Have the canned-JSON fallback ready (§B1).

---

## 10. What to actually say to the panel (framing)

- Present it as your PPT's story but **be honest about the scope choices**: "We built the full detect→attribute→visualize loop on a real-time crypto feed; the same pipeline extends to equities with a paid real-time data plan." Panels respect honest scoping far more than overclaiming.
- Be honest about **latency**: say the measured end-to-end number. "Low-latency" for a Python + browser demo means seconds; claiming milliseconds you can't demonstrate is a credibility risk.
- Your **differentiator** is exactly the gap you identified in your own literature survey: detection **and** live semantic attribution **in one view**. Lead with that.

---

## 11. Quick reference — the whole build in one screen

```
DAY 1 (Milestone A — 50%)
  A0 env + repo scaffold
  A1 fake candle → dark chart (prove the pipe)
  A2 real Binance BTC feed → chart
  A3 Z-score + IsolationForest → anomaly markers
  → live chart with real-time anomaly detection ✅

WEEK 2 (Milestone B — 50%)
  B1 anomaly → fetch news (with canned fallback)
  B2 FinBERT sentiment on headlines
  B3 hover tooltip: headline + sentiment + z-score
  B4 dark-mode polish + latency measurement + paper
  → full "detects AND explains" dashboard ✅
```

**Golden rule:** every phase must *run and show output* before you move on. A working small thing beats a broken big thing — especially the night before a demo.
