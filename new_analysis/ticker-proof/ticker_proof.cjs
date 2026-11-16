// Loads the built frontend in a real browser engine (no server: every request is answered by page.route),
// with a SYNTHETIC test fixture for /markets/nifty50, and measures whether the ticker moves and fits the page.
const path = require('path')
const fs = require('fs')
const [, , dist, outDir, engine = 'webkit', width = '1000'] = process.argv
const pw = require(path.join(dist, '..', 'node_modules', 'playwright'))

const SYMS = ['ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'AXISBANK', 'BAJAJ-AUTO', 'BAJFINANCE', 'BEL', 'BHARTIARTL', 'CIPLA',
  'COALINDIA', 'DRREDDY', 'EICHERMOT', 'GRASIM', 'HCLTECH', 'HDFCBANK', 'HINDALCO', 'ICICIBANK', 'INFY', 'ITC', 'TCS']
const nifty50 = {
  state: 'degraded', priced: SYMS.length - 1, total: SYMS.length, sources: { nse: { priced: 10 }, nse_close: { priced: 10 } },
  items: SYMS.map((s, i) => (s === 'TCS' ? { symbol: s, last_price: null, change_pct: null, source: null }
    : { symbol: s, last_price: 100 + i * 37.5, change_pct: ((i % 5) - 2) * 0.37, source: 'TEST FIXTURE', as_of: '2026-10-30T07:00:00Z' })),
  index: { value: 24000.5, change_pct: 0.3, source: 'TEST FIXTURE' },
}
const TYPES = { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.html': 'text/html' }

;(async () => {
  const browser = await pw[engine].launch()
  const page = await browser.newPage({ viewport: { width: +width, height: 800 }, deviceScaleFactor: 2 })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.route('**/*', async (route) => {
    const u = new URL(route.request().url())
    if (u.pathname.startsWith('/api/v1/markets/nifty50')) return route.fulfill({ json: nifty50 })
    if (u.pathname.startsWith('/api/') || u.pathname.startsWith('/health')) return route.fulfill({ status: 404, json: {} })
    const f = path.join(dist, u.pathname)
    if (u.pathname !== '/' && fs.existsSync(f) && fs.statSync(f).isFile())
      return route.fulfill({ body: fs.readFileSync(f), contentType: TYPES[path.extname(f)] || 'application/octet-stream' })
    return route.fulfill({ body: fs.readFileSync(path.join(dist, 'index.html')), contentType: 'text/html' })
  })
  await page.goto('http://app.test/')
  const t = page.getByTestId('nifty50-ticker')
  await t.waitFor({ timeout: 15000 })
  await page.mouse.move(0, 790) // keep the pointer off the ticker so it is not paused
  const shift = () => t.evaluate((el) => {
    const tr = el.querySelector('[data-ticker-track]') || el.querySelector('div > div')
    return new DOMMatrixReadOnly(getComputedStyle(tr).transform).m41
  })
  const geo = await t.evaluate((el) => {
    const r = (n) => { const b = n.getBoundingClientRect(); return { left: Math.round(b.left), right: Math.round(b.right), width: Math.round(b.width) } }
    const btns = el.querySelectorAll('button')
    return { window: innerWidth, docScrollWidth: document.documentElement.scrollWidth, section: r(el), viewport: r(el.children[1]),
      left_arrow: r(btns[0]), right_arrow: r(btns[1]), text: el.innerText.slice(0, 400) }
  })
  await t.screenshot({ path: path.join(outDir, `${engine}-${width}-t0.png`) })
  const s0 = await shift()
  await page.waitForTimeout(3000)
  const s3 = await shift()
  await t.screenshot({ path: path.join(outDir, `${engine}-${width}-t3.png`) })
  await t.hover() // hover pauses
  await page.waitForTimeout(300)
  const h0 = await shift()
  await page.waitForTimeout(1500)
  const h1 = await shift()
  await t.getByRole('button', { name: 'Scroll Nifty 50 ticker right' }).click()
  await page.waitForTimeout(1200)
  const a1 = await shift()
  console.log(JSON.stringify({ engine, version: browser.version(), geo, shift_t0: s0, shift_t3s: s3, moved_px_in_3s: +(s0 - s3).toFixed(1),
    hover_moved_px_in_1_5s: +(h0 - h1).toFixed(1), right_arrow_moved_px: +(h1 - a1).toFixed(1),
    shows_TCS_unpriced: geo.text.includes('TCS'), shows_dash: geo.text.includes('—'), page_errors: errors }, null, 1))
  await browser.close()
})().catch((e) => { console.error(e); process.exit(1) })
