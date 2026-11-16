// Checks that the Nifty 50 ticker starts moving again after someone interacts with it: clicks an arrow, clicks the
// strip, taps it on a touch screen, or hovers and leaves. Measures movement over 2 s with the pointer moved away.
// Usage: node ticker_interaction_check.cjs <frontend dir> <url | local> [webkit|chromium]
// "local" loads <frontend dir>/dist with no server (requests answered by page.route) and a labelled TEST FIXTURE of prices.
const path = require('path')
const fs = require('fs')
const [, , frontend, url, engine = 'webkit'] = process.argv
const pw = require(path.join(frontend, 'node_modules', 'playwright'))

const pos = (t) => t.evaluate((el) => {
  const tr = el.children[1].firstElementChild
  return -new DOMMatrixReadOnly(getComputedStyle(tr).transform).m41
})

async function movedAfter(page, t, act, touch) {
  await act()
  if (!touch) await page.mouse.move(2, 890) // the pointer leaves the ticker, as a person would
  await page.waitForTimeout(1500) // let an arrow nudge finish
  const a = await pos(t)
  await page.waitForTimeout(2000)
  let d = (await pos(t)) - a
  if (d < 0) d += await t.evaluate((el) => el.children[1].firstElementChild.firstElementChild.offsetWidth) // wrapped
  return +d.toFixed(1)
}

async function serveLocal(page) {
  const dist = path.join(frontend, 'dist')
  const items = ['ADANIENT', 'AXISBANK', 'BEL', 'CIPLA', 'DRREDDY', 'HDFCBANK', 'INFY', 'ITC', 'LT', 'SBIN', 'TCS', 'WIPRO']
    .map((s, i) => ({ symbol: s, last_price: 100 + i * 50, change_pct: (i % 3) - 1, source: 'TEST FIXTURE' }))
  const types = { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }
  await page.route('**/*', (route) => {
    const u = new URL(route.request().url())
    if (u.pathname.startsWith('/api/v1/markets/nifty50'))
      return route.fulfill({ json: { state: 'ok', priced: items.length, total: items.length, sources: { nse: { priced: items.length } }, items, index: { value: null } } })
    if (u.pathname.startsWith('/api/') || u.pathname.startsWith('/health')) return route.fulfill({ status: 404, json: {} })
    const f = path.join(dist, u.pathname)
    if (u.pathname !== '/' && fs.existsSync(f) && fs.statSync(f).isFile()) return route.fulfill({ body: fs.readFileSync(f), contentType: types[path.extname(f)] })
    return route.fulfill({ body: fs.readFileSync(path.join(dist, 'index.html')), contentType: 'text/html' })
  })
}

;(async () => {
  const browser = await pw[engine].launch()
  const out = { engine, url }
  for (const touch of [false, true]) {
    const page = await browser.newPage({ viewport: { width: touch ? 390 : 1000, height: 900 }, hasTouch: touch, isMobile: touch && engine !== 'webkit' ? true : undefined })
    if (url === 'local') await serveLocal(page)
    await page.goto(url === 'local' ? 'http://app.test/' : url, { waitUntil: 'domcontentloaded', timeout: 60000 })
    const t = page.getByTestId('nifty50-ticker')
    await t.waitFor({ timeout: 60000 })
    if (!touch) await page.mouse.move(2, 890)
    await page.waitForTimeout(1000)
    const right = t.getByRole('button', { name: 'Scroll Nifty 50 ticker right' })
    const strip = t.locator(':scope > div')
    if (!touch) {
      out.untouched = await movedAfter(page, t, async () => {}, false)
      out.after_hover_and_leave = await movedAfter(page, t, () => t.hover(), false)
      out.after_arrow_click = await movedAfter(page, t, () => right.click(), false)
      out.after_click_on_strip_gap = await movedAfter(page, t, () => strip.click({ position: { x: 2, y: 2 } }), false)
      out.focused_element = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') || document.activeElement?.tagName)
    } else {
      out.touch_untouched = await movedAfter(page, t, async () => {}, true)
      out.touch_after_arrow_tap = await movedAfter(page, t, () => right.tap(), true)
      out.touch_after_tap_on_strip = await movedAfter(page, t, () => strip.tap({ position: { x: 2, y: 2 } }), true)
    }
    await page.close()
  }
  console.log(JSON.stringify(out, null, 1), '\n(px moved in 2 s; 80 = running normally, 0 = stuck)')
  await browser.close()
})().catch((e) => { console.error(e); process.exit(1) })
