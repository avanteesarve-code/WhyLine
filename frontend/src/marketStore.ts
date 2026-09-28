import type {
  Anomaly,
  Attribution,
  Candle,
  HistoryResponse,
  ServerMessage,
} from './types'

// A tiny external store, deliberately outside React state: the live feed is
// 10-40 messages/second and must reach the chart imperatively. Components
// that render lists (watchlist, anomaly log) subscribe via
// useSyncExternalStore against immutable snapshots; the chart subscribes to
// per-symbol events and talks to Lightweight Charts directly.

export interface SymbolData {
  candles: Candle[]
  anomalies: Anomaly[]
  loaded: boolean
}

export interface TickerRow {
  symbol: string
  price: number | null
  changePct: number | null // over the trailing hour (60 candles at 1m)
  anomalyCount: number
  lastAnomalyAt: number | null // wall-clock ms of last *live* anomaly
  spark: number[] // closes over the trailing hour, for the watchlist sparkline
}

export type ChartEvent =
  | { type: 'reset' }
  | { type: 'candle'; candle: Candle; closed: boolean }
  | { type: 'anomaly'; anomaly: Anomaly }
  | { type: 'attribution'; anomaly: Anomaly }

const HOUR_CANDLES = 60
const FEED_LIMIT = 150
const PENDING_LIMIT = 300
const TICKER_FLUSH_MS = 250

type PendingEvent =
  | { kind: 'candle'; candle: Candle; closed: boolean }
  | { kind: 'anomaly'; anomaly: Anomaly }
  | { kind: 'attribution'; anomalyId: string; attribution: Attribution }

/** Final attribution states: a later `pending` must never overwrite these. */
function isFinalAttribution(
  a: Attribution | null | undefined,
): a is Attribution {
  return (
    a?.status === 'ok' || a?.status === 'no_news' || a?.status === 'error'
  )
}

class MarketStore {
  symbols: string[] = []
  interval = '1m'

  private data = new Map<string, SymbolData>()
  private pending = new Map<string, PendingEvent[]>()
  private chartListeners = new Map<string, Set<(ev: ChartEvent) => void>>()

  private tickers: TickerRow[] = []
  private tickerListeners = new Set<() => void>()
  private tickerTimer: number | null = null
  private liveAnomalyAt = new Map<string, number>()

  private feed: Anomaly[] = []
  private feedListeners = new Set<() => void>()
  private liveListeners = new Set<(anomaly: Anomaly) => void>()

  // ---- wire protocol -----------------------------------------------------

  handleMessage = (msg: ServerMessage): void => {
    if (msg.type === 'hello') {
      this.init(msg.symbols, msg.interval)
    } else if (msg.type === 'candle') {
      this.applyCandle(msg.symbol, msg.candle, msg.closed)
    } else if (msg.type === 'anomaly') {
      this.applyAnomaly(msg.anomaly, true)
    } else if (msg.type === 'attribution') {
      this.applyAttribution(msg.symbol, msg.anomaly_id, msg.attribution)
    }
  }

  /** Called on every hello — including reconnects, where the reload heals
   *  any candles missed while the socket was down. */
  private init(symbols: string[], interval: string): void {
    this.symbols = symbols
    this.interval = interval
    for (const s of symbols) {
      if (!this.data.has(s)) {
        this.data.set(s, { candles: [], anomalies: [], loaded: false })
      }
    }
    this.scheduleTickerFlush()
    void this.loadAllHistory()
  }

  private async loadAllHistory(): Promise<void> {
    await Promise.all(
      this.symbols.map(async (symbol) => {
        try {
          const resp = await fetch(`/api/history/${symbol}`)
          if (!resp.ok) return
          this.applyHistory((await resp.json()) as HistoryResponse)
        } catch {
          // backend not up yet; the next reconnect retries
        }
      }),
    )
  }

  private applyHistory(resp: HistoryResponse): void {
    const d = this.data.get(resp.symbol)
    if (!d) return
    d.candles = [...resp.candles]
    // Reconnect race: the history snapshot may be older than live state in
    // either direction. Never let a stale snapshot undo known attribution:
    // a final state (ok / no_news / error) is never replaced by pending or
    // null, a known pending is never replaced by null, and an incoming
    // pending is adopted (it carries the loading state the backend
    // assigned). The backend response object itself is left untouched.
    const existingById = new Map(d.anomalies.map((a) => [a.id, a]))
    d.anomalies = resp.anomalies.map((incoming) => {
      const existing = existingById.get(incoming.id)
      if (!existing) return incoming
      if (
        isFinalAttribution(existing.attribution) &&
        (!incoming.attribution || incoming.attribution.status === 'pending')
      ) {
        return { ...incoming, attribution: existing.attribution }
      }
      if (
        existing.attribution?.status === 'pending' &&
        !incoming.attribution
      ) {
        return { ...incoming, attribution: existing.attribution }
      }
      return incoming
    })
    d.loaded = true
    const queued = this.pending.get(resp.symbol) ?? []
    this.pending.delete(resp.symbol)
    this.emitChart(resp.symbol, { type: 'reset' })
    for (const ev of queued) {
      if (ev.kind === 'candle') this.applyCandle(resp.symbol, ev.candle, ev.closed)
      else if (ev.kind === 'anomaly') this.applyAnomaly(ev.anomaly, true)
      else this.applyAttribution(resp.symbol, ev.anomalyId, ev.attribution)
    }
    this.rebuildFeed()
    this.scheduleTickerFlush()
  }

  private applyCandle(symbol: string, candle: Candle, closed: boolean): void {
    const d = this.data.get(symbol)
    if (!d) return
    if (!d.loaded) {
      this.queuePending(symbol, { kind: 'candle', candle, closed })
      return
    }
    const candles = d.candles
    const last = candles[candles.length - 1]
    if (!last || candle.time > last.time) {
      candles.push(candle)
    } else if (candle.time === last.time) {
      candles[candles.length - 1] = candle
    } else {
      return // stale
    }
    this.emitChart(symbol, { type: 'candle', candle, closed })
    this.scheduleTickerFlush()
  }

  private applyAnomaly(anomaly: Anomaly, live: boolean): void {
    const d = this.data.get(anomaly.symbol)
    if (!d) return
    if (!d.loaded) {
      this.queuePending(anomaly.symbol, { kind: 'anomaly', anomaly })
      return
    }
    const existingIdx = d.anomalies.findIndex((a) => a.id === anomaly.id)
    if (existingIdx >= 0) {
      // Duplicate delivery (e.g. history already contained this id while a
      // live event carries newer attribution progress): merge the
      // attribution forward instead of dropping it. Ordering, counts, and
      // final states are untouched — a final (ok / no_news / error) state
      // is never downgraded.
      const existing = d.anomalies[existingIdx]
      const incoming = anomaly.attribution
      if (
        incoming &&
        (!existing.attribution ||
          (existing.attribution.status === 'pending' &&
            incoming.status !== 'pending'))
      ) {
        const updated: Anomaly = { ...existing, attribution: incoming }
        d.anomalies[existingIdx] = updated
        const feedIdx = this.feed.findIndex((a) => a.id === anomaly.id)
        if (feedIdx >= 0) {
          this.feed = [
            ...this.feed.slice(0, feedIdx),
            updated,
            ...this.feed.slice(feedIdx + 1),
          ]
          for (const fn of this.feedListeners) fn()
        }
        this.emitChart(anomaly.symbol, { type: 'attribution', anomaly: updated })
      }
      if (live) this.liveAnomalyAt.set(anomaly.symbol, Date.now())
      return
    }
    d.anomalies.push(anomaly)
    if (live) this.liveAnomalyAt.set(anomaly.symbol, Date.now())
    this.emitChart(anomaly.symbol, { type: 'anomaly', anomaly })
    this.feed = [anomaly, ...this.feed].slice(0, FEED_LIMIT)
    for (const fn of this.feedListeners) fn()
    if (live) for (const fn of this.liveListeners) fn(anomaly)
    this.scheduleTickerFlush()
  }

  /** Merge a backend attribution update into the existing anomaly.
   *
   *  An update — never a new anomaly: unknown ids are ignored, ordering and
   *  counts are untouched, and a `pending` arrival never downgrades a final
   *  (ok / no_news / error) state. Malformed payloads are ignored safely. */
  private applyAttribution(
    symbol: string,
    anomalyId: string,
    attribution: Attribution,
  ): void {
    if (typeof anomalyId !== 'string' || anomalyId.length === 0) return
    if (typeof attribution !== 'object' || attribution === null) return
    const status = attribution.status
    if (
      status !== 'pending' &&
      status !== 'ok' &&
      status !== 'no_news' &&
      status !== 'error'
    ) {
      return
    }
    const d = this.data.get(symbol)
    if (!d) return
    if (!d.loaded) {
      this.queuePending(symbol, { kind: 'attribution', anomalyId, attribution })
      return
    }
    const idx = d.anomalies.findIndex((a) => a.id === anomalyId)
    if (idx < 0) {
      if (import.meta.env.DEV) {
        console.debug('[whyline] attribution for unknown anomaly', anomalyId)
      }
      return
    }
    const current = d.anomalies[idx]
    if (isFinalAttribution(current.attribution) && status === 'pending') return
    const updated: Anomaly = { ...current, attribution }
    d.anomalies[idx] = updated
    const feedIdx = this.feed.findIndex((a) => a.id === anomalyId)
    if (feedIdx >= 0) {
      this.feed = [
        ...this.feed.slice(0, feedIdx),
        updated,
        ...this.feed.slice(feedIdx + 1),
      ]
      for (const fn of this.feedListeners) fn()
    }
    this.emitChart(symbol, { type: 'attribution', anomaly: updated })
  }

  private queuePending(symbol: string, ev: PendingEvent): void {
    const queue = this.pending.get(symbol) ?? []
    queue.push(ev)
    if (queue.length > PENDING_LIMIT) queue.shift()
    this.pending.set(symbol, queue)
  }

  // ---- chart subscription (imperative consumers) --------------------------

  getSymbolData = (symbol: string): SymbolData | undefined => this.data.get(symbol)

  subscribeChart = (symbol: string, fn: (ev: ChartEvent) => void): (() => void) => {
    const set = this.chartListeners.get(symbol) ?? new Set()
    set.add(fn)
    this.chartListeners.set(symbol, set)
    return () => {
      set.delete(fn)
    }
  }

  private emitChart(symbol: string, ev: ChartEvent): void {
    const set = this.chartListeners.get(symbol)
    if (set) for (const fn of set) fn(ev)
  }

  // ---- watchlist snapshot (useSyncExternalStore) ---------------------------

  getTickers = (): TickerRow[] => this.tickers

  subscribeTickers = (fn: () => void): (() => void) => {
    this.tickerListeners.add(fn)
    return () => this.tickerListeners.delete(fn)
  }

  /** Coalesce high-frequency candle updates into ~4 renders/second. */
  private scheduleTickerFlush(): void {
    if (this.tickerTimer !== null) return
    this.tickerTimer = window.setTimeout(() => {
      this.tickerTimer = null
      this.tickers = this.symbols.map((symbol) => {
        const d = this.data.get(symbol)
        const candles = d?.candles ?? []
        const lastCandle = candles[candles.length - 1]
        const base = candles[candles.length - 1 - HOUR_CANDLES] ?? candles[0]
        return {
          symbol,
          price: lastCandle?.close ?? null,
          changePct:
            lastCandle && base && base.close > 0
              ? ((lastCandle.close - base.close) / base.close) * 100
              : null,
          anomalyCount: d?.anomalies.length ?? 0,
          lastAnomalyAt: this.liveAnomalyAt.get(symbol) ?? null,
          spark: candles.slice(-HOUR_CANDLES - 1).map((c) => c.close),
        }
      })
      for (const fn of this.tickerListeners) fn()
    }, TICKER_FLUSH_MS)
  }

  // ---- global anomaly feed (useSyncExternalStore) ---------------------------

  getFeed = (): Anomaly[] => this.feed

  subscribeFeed = (fn: () => void): (() => void) => {
    this.feedListeners.add(fn)
    return () => this.feedListeners.delete(fn)
  }

  /** Fires once per newly detected live anomaly (never for history loads). */
  subscribeLive = (fn: (anomaly: Anomaly) => void): (() => void) => {
    this.liveListeners.add(fn)
    return () => this.liveListeners.delete(fn)
  }

  private rebuildFeed(): void {
    const merged: Anomaly[] = []
    for (const d of this.data.values()) merged.push(...d.anomalies)
    merged.sort((a, b) => b.time - a.time)
    this.feed = merged.slice(0, FEED_LIMIT)
    for (const fn of this.feedListeners) fn()
  }
}

export const marketStore = new MarketStore()

declare global {
  interface Window {
    __whyline?: {
      handleMessage: (m: ServerMessage) => void
      getFeed: () => Anomaly[]
    }
  }
}

// Development-only handle for manual attribution validation (no test
// framework in this frontend). Absent from production builds.
if (import.meta.env.DEV) {
  window.__whyline = {
    handleMessage: marketStore.handleMessage,
    getFeed: marketStore.getFeed,
  }
}
