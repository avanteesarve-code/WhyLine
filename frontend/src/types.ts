// Shapes mirror the backend's pydantic models (backend/models.py).

export interface Candle {
  time: number // candle open time, UNIX seconds (UTC)
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface Anomaly {
  id: string
  symbol: string
  time: number
  price: number
  direction: 'up' | 'down'
  methods: string[] // subset of "return_z" | "volume_z" | "isolation_forest"
  return_z: number | null
  volume_z: number | null
  iforest_score: number | null
  pct_change: number
  vol_ratio: number
  explanation: string
  attribution?: Attribution | null
  live?: boolean // detected on the live stream, not the startup history scan
}

export interface NewsItem {
  headline: string
  source: string | null
  url: string | null
  published_at: number | null
  sentiment_label: 'positive' | 'negative' | 'neutral' | null
  sentiment_score: number | null
}

export interface Attribution {
  status: 'pending' | 'ok' | 'no_news' | 'error'
  is_fallback: boolean
  items: NewsItem[]
  error: string | null
}

export interface HistoryResponse {
  symbol: string
  interval: string
  candles: Candle[]
  anomalies: Anomaly[]
}

export type ServerMessage =
  | { type: 'hello'; symbols: string[]; interval: string }
  | { type: 'candle'; symbol: string; closed: boolean; candle: Candle }
  | { type: 'anomaly'; anomaly: Anomaly }
  | { type: 'attribution'; anomaly_id: string; symbol: string; attribution: Attribution }

export type ConnectionStatus = 'connecting' | 'live' | 'reconnecting'

export type ThemeName = 'dark' | 'light'
