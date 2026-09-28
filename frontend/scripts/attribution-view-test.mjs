// Render tests for frontend/src/AttributionView.tsx (TEST: no SAMPLE UI).
//
// Compiles the REAL component with the repo's own tsc (which natively
// handles .tsx) into a scratch dir inside frontend/ (so bare 'react'
// imports resolve), server-renders it (react-dom/server), and asserts on
// the markup: fallback/sample attribution must never render article cards
// or sample wording; real news must render normally.
//
// Run:  node scripts/attribution-view-test.mjs   (from frontend/)
// Uses only already-installed deps: typescript, react, react-dom.

import { execFileSync } from 'node:child_process'
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const frontendDir = join(here, '..')
const workdir = join(frontendDir, '.tmp-views-test')

// Fresh scratch dir (also cleans up after a previously failed run).
rmSync(workdir, { recursive: true, force: true })
mkdirSync(workdir, { recursive: true })
for (const f of ['AttributionView.tsx', 'Tip.tsx', 'format.ts', 'types.ts']) {
  cpSync(join(frontendDir, 'src', f), join(workdir, f))
}
writeFileSync(
  join(workdir, 'tsconfig.json'),
  JSON.stringify({
    compilerOptions: {
      target: 'es2020',
      module: 'esnext',
      moduleResolution: 'bundler',
      jsx: 'react-jsx',
      strict: false,
      skipLibCheck: true,
      outDir: 'out',
    },
    files: ['AttributionView.tsx'],
  }),
)

let failures = 0
try {
  execFileSync(
    process.execPath,
    [join(frontendDir, 'node_modules', 'typescript', 'bin', 'tsc'), '-p', join(workdir, 'tsconfig.json')],
    { stdio: 'pipe' },
  )

  // tsc does not rewrite extensionless relative imports for node ESM.
  const emittedView = join(workdir, 'out', 'AttributionView.js')
  writeFileSync(
    emittedView,
    readFileSync(emittedView, 'utf8').replaceAll("from './format'", "from './format.js'")
    .replaceAll("from './Tip'", "from './Tip.js'"),
  )

  const { default: AttributionView } = await import(
    pathToFileURL(emittedView).href
  )
  const React = (await import('react')).default
  const { renderToStaticMarkup } = await import('react-dom/server')

  const T = 1700001200
  const render = (attribution, variant = 'log') =>
    renderToStaticMarkup(React.createElement(AttributionView, { attribution, anomalyTime: T, variant }))

  const realItem = (headline, url) => ({
    headline,
    source: 'CoinDesk',
    url,
    published_at: T - 600,
    sentiment_label: 'positive',
    sentiment_score: 0.5,
  })
  const sampleItem = (headline) => ({
    headline,
    source: 'WhyLine sample data',
    url: null,
    published_at: null,
    sentiment_label: null,
    sentiment_score: null,
  })
  const realOk = {
    status: 'ok', is_fallback: false,
    items: [realItem('Bitcoin jumps on ETF inflows', 'https://www.coindesk.com/real-btc')],
    error: null,
  }
  const fallbackOk = {
    status: 'ok', is_fallback: true,
    items: [sampleItem('Sample headline one')],
    error: 'news_no_api_key',
  }
  const fallbackError = { status: 'error', is_fallback: true, items: [], error: 'news_no_api_key' }
  const realError = { status: 'error', is_fallback: false, items: [], error: 'news_http_error' }
  const realEmpty = { status: 'ok', is_fallback: false, items: [], error: null }
  const pending = { status: 'pending', is_fallback: false, items: [], error: null }

  const BANNED = ['FALLBACK CONTEXT', 'Fallback context', 'SAMPLE', 'Sample headlines', 'WhyLine sample data', 'News provider not configured.']
  const check = (name, cond, extra = '') => {
    if (cond) console.log(`ok   ${name}`)
    else { failures += 1; console.log(`FAIL ${name} ${extra}`) }
  }
  const hasNone = (html, strs) => strs.every((s) => !html.includes(s))

  for (const variant of ['log', 'tooltip']) {
    const html = render(fallbackOk, variant)
    check(`fallback ok (${variant}) renders no sample`, hasNone(html, [...BANNED, 'Sample headline one']))
    check(`fallback ok (${variant}) shows no-news state`, html.includes('No related news found.'))
  }
  {
    const html = render(realOk)
    check('real ok renders headlines', html.includes('Bitcoin jumps on ETF inflows'))
    check('real ok renders article link', html.includes('href="https://www.coindesk.com/real-btc"'))
    check('real ok shows heading', html.includes('Related news'))
    check('real ok has no banned text', hasNone(html, BANNED))
  }
  {
    const html = render(pending)
    check('pending renders loading state', html.includes('Finding related news...'))
  }
  for (const variant of ['log', 'tooltip']) {
    const html = render(null, variant)
    check(`null attribution (${variant}) renders nothing`, html === '')
  }
  {
    const html = render(realError)
    check('real error shows unavailable', html.includes('Related news unavailable.'))
    check('real error has no banned text', hasNone(html, BANNED))
    const fhtml = render(fallbackError)
    check('fallback error shows unavailable', fhtml.includes('Related news unavailable.'))
    check('fallback error hides provider-config text', hasNone(fhtml, BANNED))
  }
  {
    const html = render(realEmpty)
    check('real empty shows no-news state', html.includes('No related news found.'))
    check('real empty has no banned text', hasNone(html, BANNED))
  }
} finally {
  rmSync(workdir, { recursive: true, force: true })
}

if (failures > 0) { console.error(`\n${failures} check(s) failed`); process.exit(1) }
console.log('\nall attribution view checks passed')
