import { useSyncExternalStore } from 'react'
import AttributionView from './AttributionView'
import { marketStore } from './marketStore'
import { fmtLocalTime, fmtPct } from './format'

interface Props {
  onJump: (symbol: string, time: number) => void
}

const METHOD_TAGS: Record<string, string> = {
  return_z: 'z',
  volume_z: 'vol',
  isolation_forest: 'IF',
}

export default function AnomalyLog({ onJump }: Props) {
  const feed = useSyncExternalStore(marketStore.subscribeFeed, marketStore.getFeed)

  return (
    <section className="panel anomaly-log">
      <header className="panel-title">
        Anomaly log <span className="panel-sub">{feed.length} events · click to inspect</span>
      </header>
      <ul>
        {feed.map((a) => (
          <li key={a.id}>
            <button className="log-row" onClick={() => onJump(a.symbol, a.time)}>
              <div className="log-top">
                <span className="log-symbol">{a.symbol.replace(/USDT$/, '')}</span>
                <span className={`dir dir-${a.direction}`}>
                  {a.direction === 'up' ? '▲' : '▼'} {fmtPct(a.pct_change)}
                </span>
                <span className="log-tags">
                  {a.methods.map((m) => (
                    <i key={m}>{METHOD_TAGS[m] ?? m}</i>
                  ))}
                </span>
                <span className="log-time">{fmtLocalTime(a.time, true)}</span>
              </div>
              <p className="log-text">{a.explanation}</p>
            </button>
            <AttributionView
              attribution={a.attribution}
              anomalyTime={a.time}
              variant="log"
            />
          </li>
        ))}
        {feed.length === 0 && (
          <li className="log-empty">
            no anomalies yet — markers appear here as detectors fire
          </li>
        )}
      </ul>
    </section>
  )
}
