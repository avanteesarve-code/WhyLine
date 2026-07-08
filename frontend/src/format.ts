import type { UTCTimestamp } from 'lightweight-charts'

// Lightweight Charts renders timestamps as UTC. To show the viewer's local
// clock we shift each timestamp by the local offset before handing it to the
// chart (the approach recommended by the library's time-zone docs), and
// format shifted values with timeZone:'UTC' so the two cancel out.
export function toChartTime(unixSeconds: number): UTCTimestamp {
  const offsetMin = new Date(unixSeconds * 1000).getTimezoneOffset()
  return (unixSeconds - offsetMin * 60) as UTCTimestamp
}

export function fmtChartTime(shifted: number, withDate = false): string {
  const d = new Date(shifted * 1000)
  const time = d.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  })
  if (!withDate) return time
  const day = d.toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
  return `${day} ${time}`
}

// For values that never enter the chart (anomaly log, tooltips) the raw UNIX
// time is formatted directly; the browser applies the local timezone.
export function fmtLocalTime(unixSeconds: number, withDate = false): string {
  const d = new Date(unixSeconds * 1000)
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (!withDate) return time
  const day = d.toLocaleDateString([], { month: 'short', day: 'numeric' })
  return `${day} ${time}`
}

export function priceDecimals(price: number): number {
  const p = Math.abs(price)
  if (p >= 100) return 2
  if (p >= 10) return 3
  if (p >= 0.1) return 4
  return 5
}

export function fmtPrice(price: number): string {
  return price.toLocaleString(undefined, {
    minimumFractionDigits: priceDecimals(price),
    maximumFractionDigits: priceDecimals(price),
  })
}

export function fmtPct(pct: number, digits = 2): string {
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toFixed(digits)}%`
}

export function fmtVolume(volume: number): string {
  if (volume >= 1_000_000) return `${(volume / 1_000_000).toFixed(2)}M`
  if (volume >= 1_000) return `${(volume / 1_000).toFixed(1)}K`
  return volume.toFixed(2)
}
