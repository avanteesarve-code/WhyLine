import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Toaster, toast } from 'sonner'
import AnomalyLog from './AnomalyLog'
import ChartPanel from './ChartPanel'
import type { JumpRequest } from './ChartPanel'
import CommandPalette from './CommandPalette'
import Tip from './Tip'
import Watchlist from './Watchlist'
import { fmtPct } from './format'
import { marketStore } from './marketStore'
import { useLiveSocket } from './useLiveSocket'
import type { ThemeName } from './types'

const METHOD_LABELS: Record<string, string> = {
  return_z: 'price z-score',
  volume_z: 'volume z-score',
  isolation_forest: 'Isolation Forest',
}

const KPI_HELP = {
  symbols: 'USDT pairs streamed live from Binance at the 1-minute interval.',
  anomalies: 'Anomalies in the log: history scanned at startup plus live detections (newest 150).',
  live: 'Detected on live candles since the backend started.',
  news: 'Anomalies with at least one real, sentiment-scored related headline.',
}

const STATUS_LABEL = {
  connecting: 'Connecting',
  live: 'Live',
  reconnecting: 'Reconnecting',
} as const

/** The candlestick "W" from logo.png, redrawn as SVG so it follows the theme. */
function LogoMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 48 40" aria-hidden="true">
      <g className="logo-up">
        <path d="M6 3 L16 37" />
        <rect x="6" y="8" width="7" height="22" transform="rotate(-14 9.5 19)" />
        <path d="M42 3 L32 37" />
        <rect x="35" y="8" width="7" height="22" transform="rotate(14 38.5 19)" />
        <path d="M24 9 V27" />
        <rect x="20.5" y="13" width="7" height="10" />
      </g>
      <g className="logo-down">
        <path d="M16 15 V38 M32 15 V38" />
        <rect x="12.5" y="20" width="7" height="12" />
        <rect x="28.5" y="20" width="7" height="12" />
      </g>
    </svg>
  )
}

function Clock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(id)
  }, [])
  return (
    <time className="clock" dateTime={now.toISOString()}>
      {now.toLocaleTimeString([], { hour12: false })}
    </time>
  )
}

export default function App() {
  const status = useLiveSocket()
  const tickers = useSyncExternalStore(
    marketStore.subscribeTickers,
    marketStore.getTickers,
  )
  const feed = useSyncExternalStore(marketStore.subscribeFeed, marketStore.getFeed)
  const [theme, setTheme] = useState<ThemeName>(
    () => (localStorage.getItem('whyline-theme') as ThemeName) || 'dark',
  )
  const [selected, setSelected] = useState<string | null>(null)
  const [jump, setJump] = useState<JumpRequest | null>(null)
  const [logFilter, setLogFilter] = useState<'all' | 'live'>('all')
  const [paletteOpen, setPaletteOpen] = useState(false)
  const jumpSeq = useRef(0)

  const handleJump = useCallback((symbol: string, time: number) => {
    setSelected(symbol)
    jumpSeq.current += 1
    setJump({ symbol, time, seq: jumpSeq.current })
  }, [])

  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))

  // ⌘K / Ctrl+K opens the command palette from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Announce each new live detection; "Inspect" jumps the chart to it.
  useEffect(
    () =>
      marketStore.subscribeLive((a) => {
        toast.custom(
          (id) => (
            <button
              className={`toast toast-${a.direction}`}
              onClick={() => {
                handleJump(a.symbol, a.time)
                toast.dismiss(id)
              }}
            >
              <span className="toast-kicker">Live anomaly</span>
              <span className="toast-title">
                {a.symbol.replace(/USDT$/, '')}
                <span className={`dir-${a.direction}`}>
                  {a.direction === 'up' ? '▲' : '▼'} {fmtPct(a.pct_change)}
                </span>
              </span>
              <span className="toast-body">
                {a.methods.map((m) => METHOD_LABELS[m] ?? m).join(' · ')}
              </span>
              <span className="toast-action">Inspect →</span>
            </button>
          ),
          { duration: 7000 },
        )
      }),
    [handleJump],
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('whyline-theme', theme)
  }, [theme])

  const active = selected ?? tickers[0]?.symbol ?? null
  const live = feed.filter((a) => a.live)
  const withNews = feed.filter(
    (a) =>
      a.attribution?.status === 'ok' &&
      !a.attribution.is_fallback &&
      a.attribution.items.length > 0,
  )

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <LogoMark />
          <div>
            <h1 className="wordmark">WhyLine</h1>
            <p className="tagline">Real-time market anomaly detection</p>
          </div>
        </div>

        <dl className="kpis" aria-label="Session summary">
          <Tip content={KPI_HELP.symbols} side="bottom">
            <div tabIndex={0}>
              <dt>Symbols</dt>
              <dd>{tickers.length || '—'}</dd>
            </div>
          </Tip>
          <Tip content={KPI_HELP.anomalies} side="bottom">
            <div tabIndex={0}>
              <dt>Anomalies</dt>
              <dd>{feed.length}</dd>
            </div>
          </Tip>
          <Tip content={KPI_HELP.live} side="bottom">
            <div tabIndex={0}>
              <dt>Live</dt>
              <dd className="kpi-signal">{live.length}</dd>
            </div>
          </Tip>
          <Tip content={KPI_HELP.news} side="bottom">
            <div tabIndex={0}>
              <dt>With news</dt>
              <dd>{withNews.length}</dd>
            </div>
          </Tip>
        </dl>

        <div className="header-right">
          <button
            className="search-button"
            onClick={() => setPaletteOpen(true)}
            aria-label="Open command menu"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <circle cx="9" cy="9" r="5.5" />
              <path d="m13 13 4 4" />
            </svg>
            <span>Jump to…</span>
            <kbd>⌘K</kbd>
          </button>
          <span className="feed-meta">
            Binance · {marketStore.interval} klines
          </span>
          <Clock />
          <span className={`status-pill status-${status}`} role="status">
            <i className="status-dot" />
            {STATUS_LABEL[status]}
          </span>
          <Tip content={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} side="bottom">
            <button
              className="icon-button"
              onClick={toggleTheme}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            >
              {theme === 'dark' ? (
                <svg viewBox="0 0 20 20" aria-hidden="true">
                  <circle cx="10" cy="10" r="3.6" />
                  <path d="M10 1.5v2.2M10 16.3v2.2M1.5 10h2.2M16.3 10h2.2M4 4l1.6 1.6M14.4 14.4 16 16M4 16l1.6-1.6M14.4 5.6 16 4" />
                </svg>
              ) : (
                <svg viewBox="0 0 20 20" aria-hidden="true">
                  <path d="M16.5 12.2A7 7 0 0 1 7.8 3.5a7 7 0 1 0 8.7 8.7Z" />
                </svg>
              )}
            </button>
          </Tip>
        </div>
      </header>

      <main className="layout">
        <Watchlist selected={active} onSelect={setSelected} />
        {active ? (
          <ChartPanel symbol={active} theme={theme} jump={jump} />
        ) : (
          <section className="panel chart-panel">
            <div className="chart-overlay">
              <span className="loader" aria-hidden="true" />
              {status === 'live' ? (
                'Loading symbols…'
              ) : (
                <span>
                  Waiting for the backend — run <code>uvicorn main:app</code> in{' '}
                  <code>backend/</code>
                </span>
              )}
            </div>
          </section>
        )}
        <AnomalyLog onJump={handleJump} filter={logFilter} onFilter={setLogFilter} />
      </main>

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        tickers={tickers}
        theme={theme}
        logFilter={logFilter}
        onSelectSymbol={setSelected}
        onToggleTheme={toggleTheme}
        onLogFilter={setLogFilter}
      />
      <Toaster theme={theme} position="bottom-right" visibleToasts={3} gap={10} />
    </div>
  )
}
