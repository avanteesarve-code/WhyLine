import { useSyncExternalStore } from 'react'
import { marketStore } from './marketStore'
import { fmtPct, fmtPrice } from './format'

interface Props {
  selected: string | null
  onSelect: (symbol: string) => void
}

const PULSE_WINDOW_MS = 90_000

export default function Watchlist({ selected, onSelect }: Props) {
  const tickers = useSyncExternalStore(
    marketStore.subscribeTickers,
    marketStore.getTickers,
  )
  const now = Date.now()

  return (
    <section className="panel watchlist">
      <header className="panel-title">
        Watchlist <span className="panel-sub">{tickers.length} symbols · 1h Δ</span>
      </header>
      <ul>
        {tickers.map((t) => {
          const pulsing =
            t.lastAnomalyAt !== null && now - t.lastAnomalyAt < PULSE_WINDOW_MS
          return (
            <li key={t.symbol}>
              <button
                className={[
                  'watch-row',
                  t.symbol === selected ? 'active' : '',
                  pulsing ? 'pulsing' : '',
                ].join(' ')}
                onClick={() => onSelect(t.symbol)}
              >
                <span className="watch-name">
                  <b>{t.symbol.replace(/USDT$/, '')}</b>
                  <small>USDT</small>
                </span>
                <span className="watch-price">
                  {t.price !== null ? fmtPrice(t.price) : '—'}
                </span>
                <span
                  className={
                    'watch-change ' +
                    (t.changePct === null
                      ? ''
                      : t.changePct >= 0
                        ? 'dir-up'
                        : 'dir-down')
                  }
                >
                  {t.changePct !== null ? fmtPct(t.changePct, 2) : ''}
                </span>
                {t.anomalyCount > 0 && (
                  <span className="watch-badge" title="anomalies in loaded history">
                    ⚡{t.anomalyCount}
                  </span>
                )}
              </button>
            </li>
          )
        })}
        {tickers.length === 0 && (
          <li className="watch-empty">waiting for backend…</li>
        )}
      </ul>
    </section>
  )
}
