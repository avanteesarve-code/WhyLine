import { useEffect, useState } from 'react'
import { marketStore } from './marketStore'
import type { ConnectionStatus, ServerMessage } from './types'

/** Connect to the backend /ws feed (via the Vite proxy), auto-reconnecting
 *  with capped exponential backoff. Messages flow into the market store;
 *  the hook only surfaces connection status for the header pill. */
export function useLiveSocket(): ConnectionStatus {
  const [status, setStatus] = useState<ConnectionStatus>('connecting')

  useEffect(() => {
    let ws: WebSocket | null = null
    let disposed = false
    let attempts = 0
    let timer: number | undefined

    const url =
      (location.protocol === 'https:' ? 'wss://' : 'ws://') +
      location.host +
      '/ws'

    const connect = () => {
      ws = new WebSocket(url)
      ws.onopen = () => {
        attempts = 0
        setStatus('live')
      }
      ws.onmessage = (event) => {
        try {
          marketStore.handleMessage(JSON.parse(event.data) as ServerMessage)
        } catch {
          // malformed frame; ignore
        }
      }
      ws.onclose = () => {
        if (disposed) return
        setStatus('reconnecting')
        const delay = Math.min(1000 * 2 ** attempts, 10_000)
        attempts += 1
        timer = window.setTimeout(connect, delay)
      }
      ws.onerror = () => ws?.close()
    }

    connect()
    return () => {
      disposed = true
      window.clearTimeout(timer)
      ws?.close()
    }
  }, [])

  return status
}
