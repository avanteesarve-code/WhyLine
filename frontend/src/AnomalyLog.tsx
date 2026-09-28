import { useSyncExternalStore } from 'react'
import AttributionView from './AttributionView'
import { marketStore } from './marketStore'
import { fmtLocalTime, fmtPct } from './format'

type Filter = 'all' | 'live'

interface Props {
  onJump: (symbol: string, time: number) => void
  filter: Filter
  onFilter: (filter: Filter) => void
}

const METHOD_TAGS: Record<string, string> = {
  return_z: 'Z',
  volume_z: 'VOL',
  isolation_forest: 'IF',
}

export default function AnomalyLog({ onJump, filter, onFilter: setFilter }: Props) {
  const feed = useSyncExternalStore(marketStore.subscribeFeed, marketStore.getFeed)
  const liveCount = feed.filter((a) => a.live).length
  const rows = filter === 'live' ? feed.filter((a) => a.live) : feed

  return (
    <section className="panel anomaly-log">
      <header className="panel-title">
        <h2>Anomaly log</h2>
        <div className="segmented" role="group" aria-label="Filter anomalies">
          <button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
            All <span>{feed.length}</span>
          </button>
          <button aria-pressed={filter === 'live'} onClick={() => setFilter('live')}>
            Live <span>{liveCount}</span>
          </button>
        </div>
      </header>
      <ul>
        {rows.map((a) => (
          <li key={a.id} className={`log-item log-${a.direction}`}>
            <button
              className="log-row"
              onClick={() => onJump(a.symbol, a.time)}
              title="Show on chart"
            >
              <div className="log-top">
                <span className="log-symbol">{a.symbol.replace(/USDT$/, '')}</span>
                <span className={`dir dir-${a.direction}`}>
                  {a.direction === 'up' ? '▲' : '▼'} {fmtPct(a.pct_change)}
                </span>
                <span className="log-tags">
                  {a.methods.map((m) => (
                    <i key={m} className={`tag tag-${m}`}>
                      {METHOD_TAGS[m] ?? m}
                    </i>
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
        {rows.length === 0 && (
          <li className="log-empty">
            {filter === 'live' ? (
              <>
                <b>No live anomalies yet</b>
                New detections appear here with related news.
              </>
            ) : (
              <>
                <b>No anomalies yet</b>
                Detections will appear here.
              </>
            )}
          </li>
        )}
      </ul>
    </section>
  )
}
