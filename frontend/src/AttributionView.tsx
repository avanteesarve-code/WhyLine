import Tip from './Tip'
import { fmtLocalTime } from './format'
import type { Attribution, NewsItem } from './types'

// Presentational rendering of a backend Attribution. Receives everything
// through props; never touches the market store. Only REAL provider news is
// ever rendered: fallback/sample items (is_fallback) are never shown as
// news — they collapse to a minimal "no news" / "unavailable" state.
// Wording stays associative ("related", "associated") — attribution is
// semantic context, not proof that a headline moved the market. Headline
// tone is not a price signal.

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

/** Backend error codes are never shown raw; map them to friendly notes.
 * Provider-failure wording is deliberately minimal: sample/fallback data is
 * never rendered, so there is nothing to explain beyond unavailability. */
function errorNotes(attribution: Attribution): string[] {
  const notes: string[] = []
  const err = attribution.error ?? ''
  if (err.includes('sentiment_')) notes.push('Headline sentiment unavailable.')
  if (err.includes('news_broad_market')) {
    notes.push('Broad crypto-market context — no asset-specific articles found.')
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
  const hasScore = typeof score === 'number'
  const clamped = hasScore ? Math.max(-1, Math.min(1, score)) : 0
  // Diverging meter centred on 0: fills left (negative) or right (positive).
  const fill = {
    left: `${50 + Math.min(clamped, 0) * 50}%`,
    width: `${Math.abs(clamped) * 50}%`,
  }
  return (
    <Tip content="FinBERT headline tone: P(positive) − P(negative), from −1 to +1. Describes the headline, not the price.">
      <span className={`ctx-tone tone-${label}`} tabIndex={0}>
        {hasScore && (
          <span className="tone-meter" aria-hidden="true">
            <span style={fill} />
          </span>
        )}
        {label}
        {hasScore && ` ${score >= 0 ? '+' : ''}${score.toFixed(2)}`}
      </span>
    </Tip>
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
  // Historical anomalies (or disabled attribution) carry no attribution:
  // render nothing so the card stays clean.
  if (!attribution) {
    return null
  }

  if (attribution.status === 'pending') {
    return <p className="ctx-hint ctx-pending">Finding related news...</p>
  }

  // Fallback/sample items are never rendered as news. Collapse them to the
  // same minimal states as genuinely having no real articles.
  if (attribution.is_fallback) {
    if (attribution.status === 'error') {
      return <p className="ctx-hint">Related news unavailable.</p>
    }
    return <p className="ctx-hint">No related news found.</p>
  }

  if (
    attribution.status === 'no_news' ||
    (attribution.status === 'ok' && attribution.items.length === 0)
  ) {
    return <p className="ctx-hint">No related news found.</p>
  }

  if (attribution.status === 'error') {
    return <p className="ctx-hint">Related news unavailable.</p>
  }

  // status === 'ok' with at least one REAL item.
  const items =
    variant === 'tooltip'
      ? attribution.items.slice(0, TOOLTIP_LIMIT)
      : attribution.items
  const extra = attribution.items.length - items.length
  // Links only in the log, only for real provider news with http(s) URLs.
  const allowLink = variant === 'log'

  return (
    <section className={`ctx-section ctx-${variant}`}>
      <div className="ctx-heading-row">
        <h4 className="ctx-heading">Related news</h4>
        <span className="ctx-caption">association, not causation</span>
      </div>
      <ul className="ctx-list">
        {items.map((item, i) => (
          <li key={`${i}-${item.headline}`} className="ctx-item">
            <Headline item={item} allowLink={allowLink} />
            <div className="ctx-meta">
              {item.source && <span className="ctx-source">{item.source}</span>}
              {typeof item.published_at === 'number' && (
                <span title={fmtLocalTime(item.published_at, true)}>
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
