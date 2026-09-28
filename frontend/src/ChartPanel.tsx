import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  HistogramSeries,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts'
import type {
  CandlestickData,
  HistogramData,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  MouseEventParams,
  SeriesMarker,
  Time,
} from 'lightweight-charts'
import { marketStore } from './marketStore'
import AttributionView from './AttributionView'
import { chartColors, chartOptions } from './theme'
import type { ChartColors } from './theme'
import {
  fmtChartTime,
  fmtLocalTime,
  fmtPct,
  fmtPrice,
  fmtVolume,
  priceDecimals,
  toChartTime,
} from './format'
import type { Anomaly, Candle, ThemeName } from './types'

export interface JumpRequest {
  symbol: string
  time: number
  seq: number // distinguishes repeated clicks on the same anomaly
}

interface Props {
  symbol: string
  theme: ThemeName
  jump: JumpRequest | null
}

interface HoverInfo {
  anomaly: Anomaly
  x: number
  y: number
}

const METHOD_LABELS: Record<string, string> = {
  return_z: 'price z-score',
  volume_z: 'volume z-score',
  isolation_forest: 'Isolation Forest',
}

// Tallest the hover tooltip may grow (attribution content included); the
// CSS max-height on .anomaly-tooltip matches this value.
const TOOLTIP_MAX_HEIGHT = 340

function toCandleData(c: Candle): CandlestickData {
  return {
    time: toChartTime(c.time),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }
}

function toVolumeData(c: Candle, colors: ChartColors): HistogramData {
  return {
    time: toChartTime(c.time),
    value: c.volume,
    color: c.close >= c.open ? colors.volUp : colors.volDown,
  }
}

function toMarker(a: Anomaly, colors: ChartColors): SeriesMarker<Time> {
  const up = a.direction === 'up'
  return {
    time: toChartTime(a.time),
    position: up ? 'belowBar' : 'aboveBar',
    shape: up ? 'arrowUp' : 'arrowDown',
    color: colors.anomaly,
    id: a.id,
    text: Math.abs(a.pct_change) >= 0.05 ? fmtPct(a.pct_change) : 'vol',
  }
}

export default function ChartPanel({ symbol, theme, jump }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candlesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  // Anomalies keyed by *chart* (shifted) time, for crosshair lookup.
  const anomalyIndexRef = useRef<Map<number, Anomaly>>(new Map())
  const themeRef = useRef<ThemeName>(theme)

  const [loaded, setLoaded] = useState(false)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const [crosshairCandle, setCrosshairCandle] = useState<Candle | null>(null)

  // --- chart lifecycle (created once per mount) ---------------------------
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(el, { autoSize: true, ...chartOptions(themeRef.current) })
    const colors = chartColors(themeRef.current)
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: colors.up,
      downColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
      borderVisible: false,
    })
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: 'volume',
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    })
    chart
      .priceScale('volume')
      .applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    const markers = createSeriesMarkers(candles, [])

    chartRef.current = chart
    candlesRef.current = candles
    volumeRef.current = volume
    markersRef.current = markers

    const onMove = (param: MouseEventParams) => {
      if (param.time === undefined || !param.point) {
        setHover(null)
        setCrosshairCandle(null)
        return
      }
      const t = param.time as number
      const bar = param.seriesData.get(candles) as CandlestickData | undefined
      const vol = param.seriesData.get(volume) as HistogramData | undefined
      setCrosshairCandle(
        bar
          ? {
              time: t,
              open: bar.open,
              high: bar.high,
              low: bar.low,
              close: bar.close,
              volume: vol?.value ?? 0,
            }
          : null,
      )
      const anomaly = anomalyIndexRef.current.get(t)
      setHover(anomaly ? { anomaly, x: param.point.x, y: param.point.y } : null)
    }
    chart.subscribeCrosshairMove(onMove)

    return () => {
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
      chartRef.current = null
      candlesRef.current = null
      volumeRef.current = null
      markersRef.current = null
    }
  }, [])

  // --- data for the selected symbol + live updates ------------------------
  useEffect(() => {
    setLoaded(false)
    setHover(null)
    setCrosshairCandle(null)

    const paint = () => {
      const d = marketStore.getSymbolData(symbol)
      const chart = chartRef.current
      if (!d?.loaded || !chart) return
      const colors = chartColors(themeRef.current)
      const lastClose = d.candles[d.candles.length - 1]?.close ?? 1
      candlesRef.current!.applyOptions({
        priceFormat: {
          type: 'price',
          precision: priceDecimals(lastClose),
          minMove: 10 ** -priceDecimals(lastClose),
        },
      })
      candlesRef.current!.setData(d.candles.map(toCandleData))
      volumeRef.current!.setData(d.candles.map((c) => toVolumeData(c, colors)))
      anomalyIndexRef.current = new Map(
        d.anomalies.map((a) => [toChartTime(a.time) as number, a]),
      )
      markersRef.current!.setMarkers(d.anomalies.map((a) => toMarker(a, colors)))
      chart.timeScale().setVisibleLogicalRange({
        from: d.candles.length - 160,
        to: d.candles.length + 6,
      })
      setLoaded(true)
    }

    paint()
    const unsubscribe = marketStore.subscribeChart(symbol, (ev) => {
      if (ev.type === 'reset') {
        paint()
        return
      }
      if (!candlesRef.current) return
      const colors = chartColors(themeRef.current)
      if (ev.type === 'candle') {
        candlesRef.current.update(toCandleData(ev.candle))
        volumeRef.current!.update(toVolumeData(ev.candle, colors))
      } else if (ev.type === 'anomaly') {
        const d = marketStore.getSymbolData(symbol)
        if (!d) return
        anomalyIndexRef.current.set(
          toChartTime(ev.anomaly.time) as number,
          ev.anomaly,
        )
        markersRef.current!.setMarkers(d.anomalies.map((a) => toMarker(a, colors)))
      } else if (ev.type === 'attribution') {
        // Update-only: refresh the indexed anomaly (and the open tooltip)
        // without touching markers.
        anomalyIndexRef.current.set(
          toChartTime(ev.anomaly.time) as number,
          ev.anomaly,
        )
        setHover((prev) =>
          prev && prev.anomaly.id === ev.anomaly.id
            ? { ...prev, anomaly: ev.anomaly }
            : prev,
        )
      }
    })
    return unsubscribe
  }, [symbol])

  // --- theme switching -----------------------------------------------------
  useEffect(() => {
    themeRef.current = theme
    const chart = chartRef.current
    if (!chart) return
    chart.applyOptions(chartOptions(theme))
    const colors = chartColors(theme)
    const d = marketStore.getSymbolData(symbol)
    if (d?.loaded) {
      markersRef.current!.setMarkers(d.anomalies.map((a) => toMarker(a, colors)))
    }
  }, [theme, symbol])

  // --- jump-to-anomaly from the log ----------------------------------------
  useEffect(() => {
    if (!jump || jump.symbol !== symbol || !loaded) return
    const d = marketStore.getSymbolData(symbol)
    const chart = chartRef.current
    if (!d?.loaded || !chart) return
    const idx = d.candles.findIndex((c) => c.time === jump.time)
    if (idx < 0) return
    chart.timeScale().setVisibleLogicalRange({ from: idx - 75, to: idx + 75 })
  }, [jump, symbol, loaded])

  const el = containerRef.current
  const tooltipLeft = hover
    ? Math.min(hover.x + 16, (el?.clientWidth ?? 600) - 300)
    : 0
  const tooltipTop = hover
    ? Math.min(
        Math.max(hover.y - 40, 8),
        (el?.clientHeight ?? 400) - TOOLTIP_MAX_HEIGHT,
      )
    : 0

  return (
    <section className="panel chart-panel">
      <div className="chart-header">
        <div className="chart-title">
          <span className="chart-symbol">{symbol.replace(/USDT$/, '')}</span>
          <span className="chart-quote">/ USDT · {marketStore.interval}</span>
        </div>
        <OhlcReadout symbol={symbol} hovered={crosshairCandle} />
        <button
          className="live-button"
          onClick={() => chartRef.current?.timeScale().scrollToRealTime()}
          title="Scroll to the newest candle"
        >
          ⏵ Live
        </button>
      </div>
      <div className="chart-body">
        <div ref={containerRef} className="chart-container" />
        {!loaded && <div className="chart-overlay">loading history…</div>}
        {hover && (
          <div
            className="anomaly-tooltip"
            style={{ left: tooltipLeft, top: tooltipTop }}
          >
            <div className="tooltip-head">
              <span className={`dir dir-${hover.anomaly.direction}`}>
                {hover.anomaly.direction === 'up' ? '▲' : '▼'}{' '}
                {fmtPct(hover.anomaly.pct_change)}
              </span>
              <span className="tooltip-time">
                {fmtLocalTime(hover.anomaly.time, true)}
              </span>
            </div>
            <p className="tooltip-text">{hover.anomaly.explanation}</p>
            <div className="tooltip-stats">
              {hover.anomaly.return_z !== null && (
                <span>price z {hover.anomaly.return_z.toFixed(1)}</span>
              )}
              {hover.anomaly.volume_z !== null && (
                <span>vol z {hover.anomaly.volume_z.toFixed(1)}</span>
              )}
              <span>vol ×{hover.anomaly.vol_ratio.toFixed(1)}</span>
              {hover.anomaly.iforest_score !== null && (
                <span>IF {hover.anomaly.iforest_score.toFixed(3)}</span>
              )}
            </div>
            <div className="tooltip-methods">
              {hover.anomaly.methods.map((m) => (
                <span key={m} className="method-tag">
                  {METHOD_LABELS[m] ?? m}
                </span>
              ))}
            </div>
            <AttributionView
              attribution={hover.anomaly.attribution}
              anomalyTime={hover.anomaly.time}
              variant="tooltip"
            />
          </div>
        )}
      </div>
    </section>
  )
}

/** OHLCV readout: shows the hovered candle, or ticks with the live one.
 *  Isolated so its per-second updates re-render only this small block. */
function OhlcReadout({
  symbol,
  hovered,
}: {
  symbol: string
  hovered: Candle | null
}) {
  const [last, setLast] = useState<Candle | null>(null)

  useEffect(() => {
    const d = marketStore.getSymbolData(symbol)
    setLast(d?.candles[d.candles.length - 1] ?? null)
    return marketStore.subscribeChart(symbol, (ev) => {
      if (ev.type === 'candle') setLast(ev.candle)
      else if (ev.type === 'reset') {
        const fresh = marketStore.getSymbolData(symbol)
        setLast(fresh?.candles[fresh.candles.length - 1] ?? null)
      }
    })
  }, [symbol])

  const candle = hovered ?? last
  if (!candle) return <div className="ohlc" />
  const changePct =
    candle.open > 0 ? ((candle.close - candle.open) / candle.open) * 100 : 0
  const dirClass = candle.close >= candle.open ? 'dir-up' : 'dir-down'
  // Hovered candles carry chart-shifted times; live ones carry raw UNIX time.
  const timeLabel = hovered
    ? fmtChartTime(candle.time, true)
    : fmtLocalTime(candle.time, true)
  return (
    <div className="ohlc">
      <span className="ohlc-time">{timeLabel}</span>
      <span>
        O <b>{fmtPrice(candle.open)}</b>
      </span>
      <span>
        H <b>{fmtPrice(candle.high)}</b>
      </span>
      <span>
        L <b>{fmtPrice(candle.low)}</b>
      </span>
      <span>
        C <b className={dirClass}>{fmtPrice(candle.close)}</b>
      </span>
      <span>
        Vol <b>{fmtVolume(candle.volume)}</b>
      </span>
      <span className={dirClass}>{fmtPct(changePct)}</span>
    </div>
  )
}
