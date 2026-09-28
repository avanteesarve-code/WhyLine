// Regression test for the frontend marketStore attribution merge (TEST 5).
//
// Exercises the REAL frontend/src/marketStore.ts (transpiled, not a copy)
// through the exact WebSocket/REST sequences of the live-attribution flow:
//   anomaly(pending) on a duplicate id must merge (not be dropped),
//   attribution(ok) must update the existing anomaly (not duplicate it),
//   a stale history snapshot must never undo pending/final attribution.
//
// Run:  node scripts/store-merge-test.mjs   (from frontend/)
// Uses only the already-installed `typescript` devDependency for the
// transpile step. No new dependencies, no test framework.

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'

const here = dirname(fileURLToPath(import.meta.url))
const frontendDir = join(here, '..')
const require = createRequire(join(frontendDir, 'package.json'))
const ts = require('typescript')

// Minimal browser shims needed by marketStore.ts.
globalThis.window = globalThis

const src = readFileSync(join(frontendDir, 'src', 'marketStore.ts'), 'utf8')
// import.meta.env.DEV only guards a dev-only debug handle; force it off.
const testable = src.replaceAll('import.meta.env.DEV', 'false')
const js = ts.transpileModule(testable, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText

const dir = mkdtempSync(join(tmpdir(), 'whyline-store-'))
const bundled = join(dir, 'marketStore.mjs')
writeFileSync(bundled, js)
const { marketStore } = await import(pathToFileURL(bundled).href)

let failures = 0
function check(name, cond, extra = '') {
  if (cond) {
    console.log(`ok   ${name}`)
  } else {
    failures += 1
    console.log(`FAIL ${name} ${extra}`)
  }
}

const SYMBOL = 'BTCUSDT'
const ANOMALY_ID = `${SYMBOL}-1700001200`

function anomaly(attribution) {
  return {
    id: ANOMALY_ID,
    symbol: SYMBOL,
    time: 1700001200,
    price: 100,
    direction: 'down',
    methods: ['return_z'],
    return_z: -3.5,
    volume_z: null,
    iforest_score: null,
    pct_change: -0.18,
    vol_ratio: 1.7,
    explanation: 'Price dropped 0.18% in one candle (z -3.5 vs recent baseline)',
    attribution,
  }
}
const pendingAttr = { status: 'pending', is_fallback: false, items: [], error: null }
const okAttr = {
  status: 'ok',
  is_fallback: true,
  items: [
    {
      headline: 'Sample headline',
      source: 'WhyLine sample data',
      url: null,
      published_at: null,
      sentiment_label: 'positive',
      sentiment_score: 0.5,
    },
  ],
  error: 'news_no_api_key',
}

// History payload currently served by the backend (driven per step below).
let historyAnomalies = []
globalThis.fetch = async () => ({
  ok: true,
  json: async () => ({
    symbol: SYMBOL,
    interval: '1m',
    candles: [],
    anomalies: historyAnomalies,
  }),
})

const tick = (ms = 50) => new Promise((r) => setTimeout(r, ms))

// --- Step 1: connect; history has the anomaly with attribution null
// (e.g. snapshot serialized in the old race window), then the live
// anomaly(pending) event arrives for the same id. ---------------------------
historyAnomalies = [anomaly(null)]
marketStore.handleMessage({ type: 'hello', symbols: [SYMBOL], interval: '1m' })
await tick()
marketStore.handleMessage({ type: 'anomaly', anomaly: anomaly({ ...pendingAttr }) })
await tick()

let stored = marketStore.getSymbolData(SYMBOL).anomalies.find((a) => a.id === ANOMALY_ID)
check('duplicate anomaly(pending) merges instead of being dropped', stored?.attribution?.status === 'pending', JSON.stringify(stored?.attribution))
check('duplicate anomaly does not duplicate the feed entry', marketStore.getFeed().filter((a) => a.id === ANOMALY_ID).length === 1)
check('duplicate anomaly does not duplicate stored anomalies', marketStore.getSymbolData(SYMBOL).anomalies.filter((a) => a.id === ANOMALY_ID).length === 1)

// --- Step 2: attribution(ok) updates the existing anomaly in place. --------
marketStore.handleMessage({
  type: 'attribution',
  anomaly_id: ANOMALY_ID,
  symbol: SYMBOL,
  attribution: okAttr,
})
await tick()
stored = marketStore.getSymbolData(SYMBOL).anomalies.find((a) => a.id === ANOMALY_ID)
check('attribution(ok) merges into the existing anomaly', stored?.attribution?.status === 'ok', JSON.stringify(stored?.attribution))
check('attribution(ok) carries news items', stored?.attribution?.items?.length === 1 && stored.attribution.items[0].headline === 'Sample headline')
check('attribution update does not create a duplicate anomaly', marketStore.getSymbolData(SYMBOL).anomalies.filter((a) => a.id === ANOMALY_ID).length === 1)
const feedEntry = marketStore.getFeed().find((a) => a.id === ANOMALY_ID)
check('feed entry reflects the final attribution', feedEntry?.attribution?.status === 'ok')

// --- Step 3: a stale history snapshot (null) must not undo final. ---------
historyAnomalies = [anomaly(null)]
marketStore.handleMessage({ type: 'hello', symbols: [SYMBOL], interval: '1m' })
await tick()
stored = marketStore.getSymbolData(SYMBOL).anomalies.find((a) => a.id === ANOMALY_ID)
check('stale history(null) never undoes final attribution', stored?.attribution?.status === 'ok', JSON.stringify(stored?.attribution))

// --- Step 4: a stale history snapshot (pending) must not undo final. ------
historyAnomalies = [anomaly({ ...pendingAttr })]
marketStore.handleMessage({ type: 'hello', symbols: [SYMBOL], interval: '1m' })
await tick()
stored = marketStore.getSymbolData(SYMBOL).anomalies.find((a) => a.id === ANOMALY_ID)
check('stale history(pending) never undoes final attribution', stored?.attribution?.status === 'ok', JSON.stringify(stored?.attribution))

// --- Step 5: pending must survive a history reload carrying stale null. ----
historyAnomalies = [anomaly({ ...pendingAttr })]
marketStore.handleMessage({ type: 'hello', symbols: ['ETHUSDT'], interval: '1m' })
await tick()
// Seed a pending anomaly on the fresh symbol via the live path.
const ETH_ID = 'ETHUSDT-1700001300'
globalThis.fetch = async () => ({ ok: true, json: async () => ({ symbol: 'ETHUSDT', interval: '1m', candles: [], anomalies: [] }) })
marketStore.handleMessage({ type: 'hello', symbols: ['ETHUSDT'], interval: '1m' })
await tick()
marketStore.handleMessage({
  type: 'anomaly',
  anomaly: { ...anomaly({ ...pendingAttr }), id: ETH_ID, symbol: 'ETHUSDT', time: 1700001300 },
})
await tick()
// Now a reconnect history arrives with a stale null for that id.
globalThis.fetch = async () => ({
  ok: true,
  json: async () => ({
    symbol: 'ETHUSDT', interval: '1m', candles: [],
    anomalies: [{ ...anomaly(null), id: ETH_ID, symbol: 'ETHUSDT', time: 1700001300 }],
  }),
})
marketStore.handleMessage({ type: 'hello', symbols: ['ETHUSDT'], interval: '1m' })
await tick()
stored = marketStore.getSymbolData('ETHUSDT').anomalies.find((a) => a.id === ETH_ID)
check('history(null) never downgrades known pending', stored?.attribution?.status === 'pending', JSON.stringify(stored?.attribution))

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`)
  process.exit(1)
}
console.log('\nall store merge checks passed')
