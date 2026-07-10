import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import AnomalyLog from './AnomalyLog'
import ChartPanel from './ChartPanel'
import type { JumpRequest } from './ChartPanel'
import Watchlist from './Watchlist'
import { marketStore } from './marketStore'
import { useLiveSocket } from './useLiveSocket'
import type { ThemeName } from './types'

const STATUS_LABEL = {
  connecting: 'connecting',
  live: 'live',
  reconnecting: 'reconnecting',
} as const

export default function App() {
  const status = useLiveSocket()
  const tickers = useSyncExternalStore(
    marketStore.subscribeTickers,
    marketStore.getTickers,
  )
  const [theme, setTheme] = useState<ThemeName>(
    () => (localStorage.getItem('whyline-theme') as ThemeName) || 'dark',
  )
  const [selected, setSelected] = useState<string | null>(null)
  const [jump, setJump] = useState<JumpRequest | null>(null)
  const jumpSeq = useRef(0)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('whyline-theme', theme)
  }, [theme])

  const active = selected ?? tickers[0]?.symbol ?? null

  const handleJump = (symbol: string, time: number) => {
    setSelected(symbol)
    jumpSeq.current += 1
    setJump({ symbol, time, seq: jumpSeq.current })
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <img className="brand-mark" src="/logo_rm.png" alt="WhyLine logo" />
          <div>
            <h1>WhyLine</h1>
            <p>real-time anomaly detection · Binance {marketStore.interval} klines</p>
          </div>
        </div>
        <div className="header-right">
          <span className={`status-pill status-${status}`}>
            <i className="status-dot" />
            {STATUS_LABEL[status]}
          </span>
          <button
            className="theme-toggle"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title="Toggle dark / light theme"
          >
            {theme === 'dark' ? '☀ light' : '☾ dark'}
          </button>
        </div>
      </header>

      <main className="layout">
        <Watchlist selected={active} onSelect={setSelected} />
        {active ? (
          <ChartPanel symbol={active} theme={theme} jump={jump} />
        ) : (
          <section className="panel chart-panel">
            <div className="chart-overlay">
              {status === 'live'
                ? 'loading symbols…'
                : 'connecting to backend — run `uvicorn main:app` in backend/'}
            </div>
          </section>
        )}
        <AnomalyLog onJump={handleJump} />
      </main>
    </div>
  )
}
