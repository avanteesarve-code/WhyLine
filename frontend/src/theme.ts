import { ColorType, CrosshairMode } from 'lightweight-charts'
import type { DeepPartial, ChartOptions } from 'lightweight-charts'
import type { ThemeName } from './types'

// Palette per docs/arch + dataviz reference: status green/red for candle
// polarity (validated: deutan dE 12.4, >=3:1 on both surfaces), amber
// "warning" accent for anomaly markers (direction is also encoded by arrow
// shape + text label, so color never carries meaning alone).
export interface ChartColors {
  surface: string
  text: string
  grid: string
  border: string
  up: string
  down: string
  volUp: string
  volDown: string
  anomaly: string
}

// Must stay in sync with the matching tokens in index.css.
const COLORS: Record<ThemeName, ChartColors> = {
  dark: {
    surface: '#101315',
    text: '#7d8784',
    grid: '#1a1f22',
    border: '#262c30',
    up: '#22b573',
    down: '#ef5a4c',
    volUp: 'rgba(34, 181, 115, 0.32)',
    volDown: 'rgba(239, 90, 76, 0.32)',
    anomaly: '#f5b83d',
  },
  light: {
    surface: '#fbf9f4',
    text: '#6b7072',
    grid: '#ece8df',
    border: '#d9d4c8',
    up: '#0f8a52',
    down: '#cf3b30',
    volUp: 'rgba(15, 138, 82, 0.28)',
    volDown: 'rgba(207, 59, 48, 0.28)',
    anomaly: '#b8740a',
  },
}

export function chartColors(theme: ThemeName): ChartColors {
  return COLORS[theme]
}

export function chartOptions(theme: ThemeName): DeepPartial<ChartOptions> {
  const c = COLORS[theme]
  return {
    layout: {
      background: { type: ColorType.Solid, color: c.surface },
      textColor: c.text,
      fontFamily: 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif',
      fontSize: 11,
    },
    grid: {
      vertLines: { color: c.grid },
      horzLines: { color: c.grid },
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: c.text, labelBackgroundColor: c.border },
      horzLine: { color: c.text, labelBackgroundColor: c.border },
    },
    rightPriceScale: {
      borderColor: c.border,
      scaleMargins: { top: 0.08, bottom: 0.22 }, // keep clear of volume pane
    },
    timeScale: {
      borderColor: c.border,
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 5,
    },
  }
}
