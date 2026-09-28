import { useSyncExternalStore } from 'react'
import Tip from './Tip'
import { marketStore } from './marketStore'
import { fmtPct, fmtPrice } from './format'

interface Props {
  selected: string | null
  onSelect: (symbol: string) => void
}

const PULSE_WINDOW_MS = 90_000

function Sparkline({ values, dir }: { values: number[]; dir: string }) {
  if (values.length < 2) return <span className="spark" />
  const min = Math.min(...values)
  const range = Math.max(...values) - min || 1
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * 60
      const y = 19 - ((v - min) / range) * 18
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  return (
    <svg className={`spark ${dir}`} viewBox="0 0 60 20" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={`0,20 ${points} 60,20`} className="spark-fill" />
      <polyline points={points} className="spark-line" />
    </svg>
  )
}

/** ↑/↓ move the selection through the list, like a trading terminal. */
function handleKeyDown(e: React.KeyboardEvent<HTMLUListElement>) {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
  const rows = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('.watch-row')]
  const i = rows.indexOf(document.activeElement as HTMLButtonElement)
  if (i < 0) return
  e.preventDefault()
  const next = rows[(i + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length]
  next.focus()
  next.click()
}

export default function Watchlist({ selected, onSelect }: Props) {
  const tickers = useSyncExternalStore(
    marketStore.subscribeTickers,
    marketStore.getTickers,
  )
  const now = Date.now()

  return (
    <section className="panel watchlist">
      <header className="panel-title">
        <h2>Watchlist</h2>
        <span className="panel-sub">1h change</span>
      </header>
      <ul onKeyDown={handleKeyDown}>
        {tickers.map((t) => {
          const pulsing =
            t.lastAnomalyAt !== null && now - t.lastAnomalyAt < PULSE_WINDOW_MS
          const dir =
            t.changePct === null ? '' : t.changePct >= 0 ? 'dir-up' : 'dir-down'
          return (
            <li key={t.symbol}>
              <button
                className={[
                  'watch-row',
                  t.symbol === selected ? 'active' : '',
                  pulsing ? 'pulsing' : '',
                ].join(' ')}
                onClick={() => onSelect(t.symbol)}
                aria-pressed={t.symbol === selected}
              >
                <span className="watch-name">
                  <b>{t.symbol.replace(/USDT$/, '')}</b>
                  <small>/USDT</small>
                </span>
                <Sparkline values={t.spark} dir={dir} />
                <span className="watch-price">
                  {t.price !== null ? fmtPrice(t.price) : '—'}
                </span>
                <Tip content={`${t.anomalyCount} anomalies detected in the loaded history`}>
                  <span className="watch-badge" hidden={t.anomalyCount === 0}>
                    <i aria-hidden="true" />
                    {t.anomalyCount}
                    <span className="sr-only"> anomalies</span>
                  </span>
                </Tip>
                <span className={`watch-change ${dir}`}>
                  {t.changePct !== null ? fmtPct(t.changePct, 2) : ''}
                </span>
              </button>
            </li>
          )
        })}
        {tickers.length === 0 &&
          Array.from({ length: 8 }, (_, i) => (
            <li key={i} className="watch-skeleton" aria-hidden="true">
              <span />
              <span />
            </li>
          ))}
      </ul>
    </section>
  )
}
