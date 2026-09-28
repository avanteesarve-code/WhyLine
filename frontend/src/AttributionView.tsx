import { fmtLocalTime } from './format'
import type { Attribution, NewsItem } from './types'

// Presentational rendering of a backend Attribution. Receives everything
// through props; never touches the market store. Wording stays associative
// ("related", "associated") — attribution is semantic context, not proof
// that a headline moved the market. Headline tone is not a price signal.

interface AttributionViewProps {
  attribution: Attribution | null | undefined
  anomalyTime: number // UNIX seconds, same convention as published_at
  variant: 'tooltip' | 'log'
}

const TOOLTIP_LIMIT = 2

function fmtOffset(publishedAt: number, anomalyTime: number): string {
  const diffMin = Math.round((publishedAt - anomalyTime) / 60)
  if (diffMin === 0) return 'at the anomaly candle'
  const side = diffMin < 0 ? 'before' : 'after'
  const absMin = Math.abs(diffMin)
  if (absMin >= 120) return `${Math.round(absMin / 60)} h ${side}`
  return `${absMin} min ${side}`
}

/** Backend error codes are never shown raw; map them to friendly notes. */
function errorNotes(attribution: Attribution): string[] {
  const notes: string[] = []
  const err = attribution.error ?? ''
  if (err.includes('sentiment_')) notes.push('Headline sentiment unavailable.')
  if (err.includes('news_')) {
    if (attribution.is_fallback && err.includes('news_no_api_key')) {
      notes.push('News provider not configured.')
    } else if (attribution.is_fallback && err.includes('news_no_match')) {
      notes.push('No matching provider news found.')
    } else {
      notes.push('News provider unavailable.')
    }
  }
  return notes
}

function ToneChip({
  label,
  score,
}: {
  label: NonNullable<NewsItem['sentiment_label']>
  score: NewsItem['sentiment_score']
}): React.JSX.Element {
  const text =
    typeof score === 'number'
      ? `tone: ${label} ${score >= 0 ? '+' : ''}${score.toFixed(2)}`
      : `tone: ${label}`
  return (
    <span
      className="ctx-tone"
      title="FinBERT headline tone: P(positive) − P(negative), range −1 to +1. Not a price signal."
    >
      {text}
    </span>
  )
}

function Headline({
  item,
  allowLink,
}: {
  item: NewsItem
  allowLink: boolean
}): React.JSX.Element {
  const url = item.url
  if (allowLink && typeof url === 'string' && /^https?:\/\//i.test(url)) {
    return (
      <a
        className="ctx-headline ctx-link"
        href={url}
        target="_blank"
        rel="noopener noreferrer"
      >
        {item.headline}
      </a>
    )
  }
  return <span className="ctx-headline">{item.headline}</span>
}

export default function AttributionView({
  attribution,
  anomalyTime,
  variant,
}: AttributionViewProps): React.JSX.Element | null {
  // Historical anomalies (or disabled attribution) carry no attribution.
  if (!attribution) {
    if (variant === 'log') return null
    return (
      <p className="ctx-hint">Related news is gathered for live anomalies only.</p>
    )
  }

  if (attribution.status === 'pending') {
    return <p className="ctx-hint">Loading related context...</p>
  }

  if (
    attribution.status === 'no_news' ||
    (attribution.status === 'ok' && attribution.items.length === 0)
  ) {
    return <p className="ctx-hint">No relevant news found for this anomaly.</p>
  }

  if (attribution.status === 'error') {
    return (
      <div className={`ctx-section ctx-${variant}`}>
        <p className="ctx-hint">Unable to load attribution.</p>
        {errorNotes(attribution).map((note) => (
          <p key={note} className="ctx-note">
            {note}
          </p>
        ))}
      </div>
    )
  }

  // status === 'ok' with at least one item.
  const fallback = attribution.is_fallback
  const items =
    variant === 'tooltip'
      ? attribution.items.slice(0, TOOLTIP_LIMIT)
      : attribution.items
  const extra = attribution.items.length - items.length
  // Links only in the log, only for real provider news with http(s) URLs —
  // never for fallback/sample context.
  const allowLink = variant === 'log' && !fallback

  return (
    <section
      className={`ctx-section ctx-${variant}${fallback ? ' ctx-fallback' : ''}`}
    >
      <div className="ctx-heading-row">
        <h4 className="ctx-heading">
          {fallback ? 'Fallback context' : 'Related news'}
        </h4>
        {fallback && <span className="ctx-badge">SAMPLE</span>}
      </div>
      {fallback ? (
        <p className="ctx-note">Sample headlines, not real news</p>
      ) : (
        <p className="ctx-caption">Semantic context — association, not causation</p>
      )}
      <ul className="ctx-list">
        {items.map((item, i) => (
          <li key={`${i}-${item.headline}`} className="ctx-item">
            <Headline item={item} allowLink={allowLink} />
            <div className="ctx-meta">
              {item.source && <span>{item.source}</span>}
              {typeof item.published_at === 'number' && (
                <span>
                  {fmtLocalTime(item.published_at, true)} ·{' '}
                  {fmtOffset(item.published_at, anomalyTime)}
                </span>
              )}
              {item.sentiment_label !== null && (
                <ToneChip
                  label={item.sentiment_label}
                  score={item.sentiment_score}
                />
              )}
            </div>
          </li>
        ))}
      </ul>
      {extra > 0 && (
        <p className="ctx-hint">{`+${extra} more in the anomaly log`}</p>
      )}
      {errorNotes(attribution).map((note) => (
        <p key={note} className="ctx-note">
          {note}
        </p>
      ))}
    </section>
  )
}
