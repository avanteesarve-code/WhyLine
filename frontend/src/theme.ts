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

const COLORS: Record<ThemeName, ChartColors> = {
  dark: {
    surface: '#1a1a19',
    text: '#898781',
    grid: '#2c2c2a',
    border: '#383835',
    up: '#0ca30c',
    down: '#d03b3b',
    volUp: 'rgba(12, 163, 12, 0.45)',
    volDown: 'rgba(208, 59, 59, 0.45)',
    anomaly: '#fab219',
  },
  light: {
    surface: '#fcfcfb',
    text: '#898781',
    grid: '#e1e0d9',
    border: '#c3c2b7',
    up: '#0ca30c',
    down: '#d03b3b',
    volUp: 'rgba(12, 163, 12, 0.45)',
    volDown: 'rgba(208, 59, 59, 0.45)',
    anomaly: '#c98500',
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
      fontFamily:
        'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    },
    grid: {
      vertLines: { color: c.grid },
      horzLines: { color: c.grid },
    },
    crosshair: { mode: CrosshairMode.Normal },
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
