// Opens a running deployment of the app in a real browser engine and records, frame by frame, how far the
// Nifty 50 ticker moves: it handles both the old scrollLeft ticker and the new transform ticker.
// Usage: node ticker_live_check.cjs <frontend dir> <url> [webkit|chromium] [width] [seconds] [out dir]
const path = require('path')
const [, , frontend, url, engine = 'webkit', width = '1000', seconds = '10', outDir = '.'] = process.argv
const pw = require(path.join(frontend, 'node_modules', 'playwright'))

;(async () => {
  const browser = await pw[engine].launch()
  const page = await browser.newPage({ viewport: { width: +width, height: 900 }, deviceScaleFactor: 2 })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)))
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  const t = page.getByTestId('nifty50-ticker')
  await t.waitFor({ timeout: 60000 })
  await page.mouse.move(2, 890) // pointer off the ticker so it is not paused
  await page.waitForTimeout(1500)
  await t.screenshot({ path: path.join(outDir, `live-${engine}-${width}-t0.png`) })
  const r = await t.evaluate((el, ms) => new Promise((done) => {
    const vp = el.children[1]
    const tr = vp.firstElementChild
    const isNew = !!tr && tr.style.transform !== undefined && getComputedStyle(tr).transform !== 'none'
    // Position along the tape: transform for the new ticker, scrollLeft for the old one.
    const at = () => (isNew ? -new DOMMatrixReadOnly(getComputedStyle(tr).transform).m41 : vp.scrollLeft)
    const loop = vp.scrollWidth / 2
    const samples = []
    const start = performance.now()
    const tick = (now) => {
      samples.push([now - start, at()])
      if (now - start < ms) requestAnimationFrame(tick)
      else done({ isNew, loop, samples, items: el.querySelectorAll('a').length, text: el.innerText.replace(/\s+/g, ' ').slice(0, 160),
        blanks: (el.innerText.match(/—/g) || []).length })
    }
    requestAnimationFrame(tick)
  }), +seconds * 1000)
  await t.screenshot({ path: path.join(outDir, `live-${engine}-${width}-t${seconds}.png`) })

  // Moved distance per frame; a wrap back to the start counts as forward motion.
  const deltas = []
  for (let i = 1; i < r.samples.length; i++) {
    let d = r.samples[i][1] - r.samples[i - 1][1]
    if (d < -r.loop / 2) d += r.loop
    deltas.push(d)
  }
  const total = deltas.reduce((a, b) => a + b, 0)
  const elapsed = r.samples.at(-1)[0] / 1000
  let longestStill = 0, run = 0
  for (let i = 0; i < deltas.length; i++) {
    run = Math.abs(deltas[i]) < 0.01 ? run + (r.samples[i + 1][0] - r.samples[i][0]) : 0
    longestStill = Math.max(longestStill, run)
  }
  console.log(JSON.stringify({
    engine, browser_version: browser.version(), url, width: +width,
    ticker_code: r.isNew ? 'new (transform)' : 'old (scrollLeft)',
    frames: deltas.length, fps: +(deltas.length / elapsed).toFixed(1),
    moved_px: +total.toFixed(1), speed_px_per_s: +(total / elapsed).toFixed(1),
    frames_without_movement: deltas.filter((d) => Math.abs(d) < 0.01).length,
    longest_still_ms: Math.round(longestStill), went_backwards: deltas.some((d) => d < -0.01),
    links: r.items, dashes_shown: r.blanks, first_text: r.text, page_errors: errors,
  }, null, 1))
  await browser.close()
})().catch((e) => { console.error(e); process.exit(1) })
