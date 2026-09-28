import { Command } from 'cmdk'
import { fmtPct, fmtPrice } from './format'
import type { TickerRow } from './marketStore'
import type { ThemeName } from './types'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  tickers: TickerRow[]
  theme: ThemeName
  logFilter: 'all' | 'live'
  onSelectSymbol: (symbol: string) => void
  onToggleTheme: () => void
  onLogFilter: (filter: 'all' | 'live') => void
}

export default function CommandPalette({
  open,
  onOpenChange,
  tickers,
  theme,
  logFilter,
  onSelectSymbol,
  onToggleTheme,
  onLogFilter,
}: Props) {
  const run = (fn: () => void) => () => {
    fn()
    onOpenChange(false)
  }

  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Command menu"
      overlayClassName="cmdk-overlay"
      contentClassName="cmdk"
    >
      <div className="cmdk-input-row">
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <circle cx="9" cy="9" r="5.5" />
          <path d="m13 13 4 4" />
        </svg>
        <Command.Input placeholder="Jump to a symbol or run a command…" />
        <kbd>esc</kbd>
      </div>
      <Command.List>
        <Command.Empty>No matching symbol or command.</Command.Empty>
        <Command.Group heading="Symbols">
          {tickers.map((t) => {
            const base = t.symbol.replace(/USDT$/, '')
            const dir =
              t.changePct === null ? '' : t.changePct >= 0 ? 'dir-up' : 'dir-down'
            return (
              <Command.Item
                key={t.symbol}
                value={t.symbol}
                keywords={[base]}
                onSelect={run(() => onSelectSymbol(t.symbol))}
              >
                <span className="cmdk-symbol">
                  <b>{base}</b>
                  <small>/USDT</small>
                </span>
                {t.anomalyCount > 0 && (
                  <span className="cmdk-count">
                    <i aria-hidden="true" />
                    {t.anomalyCount}
                  </span>
                )}
                <span className="cmdk-price">
                  {t.price !== null ? fmtPrice(t.price) : '—'}
                </span>
                <span className={`cmdk-change ${dir}`}>
                  {t.changePct !== null ? fmtPct(t.changePct) : ''}
                </span>
              </Command.Item>
            )
          })}
        </Command.Group>
        <Command.Group heading="Actions">
          <Command.Item value="theme toggle" onSelect={run(onToggleTheme)}>
            Switch to {theme === 'dark' ? 'light' : 'dark'} theme
          </Command.Item>
          <Command.Item
            value="anomaly log filter live all"
            onSelect={run(() => onLogFilter(logFilter === 'all' ? 'live' : 'all'))}
          >
            {logFilter === 'all'
              ? 'Show only live anomalies in the log'
              : 'Show all anomalies in the log'}
          </Command.Item>
        </Command.Group>
      </Command.List>
      <div className="cmdk-footer">
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd> navigate
        </span>
        <span>
          <kbd>↵</kbd> select
        </span>
      </div>
    </Command.Dialog>
  )
}
