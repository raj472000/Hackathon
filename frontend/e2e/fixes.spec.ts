import { expect, test, type Page } from '@playwright/test'

// Fixes from the 15 Oct 2026 review, on the demo stack (synthetic data). Broker and NSE-filings
// responses that the demo stack cannot produce are supplied with route interception and say so.
const SHOTS = '../analysis/proof'

/** The page reads the Kite session from GET /sources/status (re-confirmed with Kite by the API). The demo broker is
 * logged in, so tests of the login flow present the state they need there, as the real API would report it. */
async function kiteStatus(page: Page, state: () => any) {
  await page.route('**/api/v1/sources/status', async (r) => {
    const res = await r.fetch()
    const body = await res.json()
    body.connectors = body.connectors.map((c: any) => (c.key === 'kite_mcp' ? { ...c, auth: state() } : c))
    await r.fulfill({ response: res, json: body })
  })
}

async function ready(page: Page) {
  await expect
    .poll(async () => (await (await page.request.get('/api/v1/overview')).json()).universe?.instruments ?? 0, { timeout: 150_000, intervals: [2000] })
    .toBeGreaterThan(0)
}

test.beforeEach(async ({ page }) => {
  await ready(page)
  // Keep the start-of-session broker prompt out of tests that are not about it.
  await page.addInitScript(() => sessionStorage.setItem('broker-login-dismissed', '1'))
})

test('ask: bullish over the last 3 days answers with a ranked 3-session movers table', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Your question').fill('strong bullish stocks in last 3 days')
  await page.getByTestId('ask-submit').click()
  const table = page.getByTestId('ask-movers')
  await expect(table).toBeVisible({ timeout: 30_000 })
  await expect(table.locator('thead')).toContainText('3 sessions')
  const pct = (await table.locator('tbody tr td:nth-child(5)').allTextContents()).map((t) => parseFloat(t.replace(/[^\d.-]/g, '')))
  expect(pct.length).toBeGreaterThan(3)
  expect(pct).toEqual([...pct].sort((a, b) => b - a))
  await expect(page.getByTestId('ask-answer')).toContainText('Over the last 3 sessions')
  await expect(page.getByTestId('evidence-P1')).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/20-ask-movers-3-days.png`, fullPage: true })
})

test('rotation: every sector is plotted and the tail slider shortens the trails', async ({ page }) => {
  await page.goto('/rotation')
  const cov = page.getByText(/\d+ of \d+ sectors plotted/)
  await expect(cov).toBeVisible({ timeout: 30_000 })
  const [plotted, total] = ((await cov.textContent()) ?? '').match(/\d+/g)!.map(Number)
  expect(plotted).toBe(total)
  const slider = page.getByTestId('rrg-tail')
  const points = async () => (await page.locator('svg polyline').first().getAttribute('points'))!.trim().split(/\s+/).length
  await slider.fill('2')
  await expect(page.getByText('2 weeks', { exact: true })).toBeVisible()
  expect(await points()).toBe(2)
  await page.screenshot({ path: `${SHOTS}/21-rotation-tail-2-weeks.png` })
  const max = Number(await slider.getAttribute('max'))
  await slider.fill(String(max))
  expect(await points()).toBe(max)
  await slider.fill('4')
  expect(await points()).toBe(4)
  await page.screenshot({ path: `${SHOTS}/22-rotation-tail-4-weeks.png` })
})

test('broker login: asked at start, the link is requested fresh on click, approval is confirmed', async ({ page, context }) => {
  await page.addInitScript(() => sessionStorage.removeItem('broker-login-dismissed'))
  let approved = false
  let linksIssued = 0
  await kiteStatus(page, () => (approved ? { state: 'authorized', user_id: 'AB1234' } : { state: 'login_required' }))
  // Kite needs a real Zerodha account, so the broker responses are simulated here; the backend tests cover the MCP flow.
  await page.route('**/api/v1/sources/broker/login', (r) =>
    r.fulfill({ json: { kite_mcp: approved ? { state: 'authorized', user_id: 'AB1234', backfill_job: 42 } : { state: 'login_required', login_url: 'http://127.0.0.1:5174/STALE-LINK' } } }),
  )
  await page.route('**/api/v1/sources/broker/login-link', (r) => {
    linksIssued += 1
    return r.fulfill({ json: { kite_mcp: { state: 'login_required', login_url: `http://127.0.0.1:5174/kite-authorize?fresh=${linksIssued}`, issued_at: new Date().toISOString() } } })
  })
  await page.goto('/')
  const dlg = page.getByTestId('broker-login')
  await expect(dlg).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Connect Zerodha Kite for broker data' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/23-kite-login-prompt.png` })
  const [tab] = await Promise.all([context.waitForEvent('page'), page.getByTestId('broker-login-link').click()])
  await tab.waitForURL(/kite-authorize\?fresh=1/)
  expect(tab.url()).not.toContain('STALE-LINK')
  expect(linksIssued).toBe(1)
  await tab.close()
  // A second click gets another new link, never the earlier one.
  const [tab2] = await Promise.all([context.waitForEvent('page'), page.getByTestId('broker-login-link').click()])
  await tab2.waitForURL(/fresh=2/)
  await tab2.close()
  approved = true
  await page.getByTestId('broker-check').click()
  await expect(page.getByRole('heading', { name: 'Zerodha Kite is connected' })).toBeVisible()
  await expect(dlg).toContainText('Signed in as AB1234')
  await expect(dlg).toContainText('job 42')
  await page.screenshot({ path: `${SHOTS}/24-kite-connected.png` })
})

test('broker login: when Kite ends the session, the prompt and the status panel offer "Log in again"', async ({ page, context }) => {
  // Reported 19 Oct 2026: the login was lost and there was no way to log in again on the screen.
  await page.addInitScript(() => sessionStorage.setItem('broker-login-dismissed', '1')) // dismissed earlier
  let kite: any = { state: 'authorized', user_id: 'AB1234', checked_at: new Date().toISOString() }
  await kiteStatus(page, () => kite)
  await page.route('**/api/v1/sources/broker/login', (r) => r.fulfill({ json: { kite_mcp: kite } }))
  let links = 0
  await page.route('**/api/v1/sources/broker/login-link', (r) => {
    links += 1
    return r.fulfill({ json: { kite_mcp: { ...kite, login_url: `http://127.0.0.1:5174/kite-authorize?fresh=${links}` } } })
  })
  await page.goto('/status')
  const panel = page.getByTestId('kite-session')
  await expect(panel).toContainText('Logged in')
  await expect(panel).toContainText('AB1234')
  // Kite ends the session; the next status poll reports it
  kite = { state: 'login_required', checked_at: new Date().toISOString(), reason: 'Kite ended the previous session, so its approval no longer applies. Approve access again.' }
  await page.getByTestId('page-refresh').last().click()
  await expect(page.getByRole('heading', { name: 'Kite logged you out' })).toBeVisible()
  await expect(page.getByTestId('broker-login')).toContainText('Kite ended the previous session')
  await page.getByTestId('broker-skip').click()
  await expect(panel).toContainText('Logged out by Kite')
  await expect(page.getByTestId('broker-connect')).toHaveText('Kite: log in again')
  const [tab] = await Promise.all([context.waitForEvent('page'), panel.getByRole('button', { name: 'Log in again' }).click()])
  await tab.waitForURL(/kite-authorize\?fresh=1/)
  await tab.close()
  kite = { state: 'authorized', user_id: 'AB1234', checked_at: new Date().toISOString() }
  await panel.getByTestId('kite-check').click()
  await expect(page.getByRole('heading', { name: 'Zerodha Kite is connected' })).toBeVisible()
  await page.getByRole('button', { name: 'Done' }).click()
  await expect(panel).toContainText('Logged in')
})

test('broker login: continuing without broker data leaves a Connect Kite button', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.removeItem('broker-login-dismissed'))
  await page.route('**/api/v1/sources/broker/login', (r) => r.fulfill({ json: { kite_mcp: { state: 'login_required' } } }))
  await kiteStatus(page, () => ({ state: 'login_required' }))
  await page.goto('/')
  await page.getByTestId('broker-skip').click()
  await expect(page.getByTestId('broker-login')).toBeHidden()
  await page.getByTestId('broker-connect').click()
  await expect(page.getByTestId('broker-login')).toBeVisible()
})

test('concalls: opening a company fetches its calls from NSE and scores them without an upload', async ({ page }) => {
  let started = 0
  // The demo stack has no NSE filings (synthetic symbols), so the filings state and the job are simulated;
  // the analysis shown afterwards is the stack's own stored result.
  await page.route('**/api/v1/instruments/*/concalls', async (r) => {
    if (r.request().method() !== 'GET') return r.continue()
    const res = await r.fetch()
    const body = await res.json()
    return r.fulfill({ json: { ...body, latest: started ? body.latest : null, nse_filings: { available: true, reason: null } } })
  })
  await page.route('**/api/v1/instruments/*/concalls/auto', (r) => {
    started += 1
    return r.fulfill({ status: 202, json: { job_id: 4242, status: 'running' } })
  })
  await page.route('**/api/v1/jobs/4242', (r) =>
    r.fulfill({
      json: {
        job_id: 4242, kind: 'concall_auto', status: 'succeeded', error: null,
        detail: { fetched: { found: 4, stored: [{ fetched: true }, { fetched: true }, { fetched: true }, { fetched: true }], skipped: [] }, previous: 'Q1 FY2027', current: 'Q2 FY2027', score: 34.6, label: 'Weak follow-through' },
      },
    }),
  )
  await page.goto('/concalls/NSE:DEMOIT01')
  await expect(page.getByText('Scored Q1 FY2027 → Q2 FY2027.')).toBeVisible({ timeout: 30_000 })
  expect(started).toBe(1) // started by opening the page, no button and no upload
  await expect(page.getByText('4 new transcripts fetched from NSE')).toBeVisible()
  await expect(page.getByTestId('concall-auto')).toHaveText(/Fetch latest from NSE and re-score|Fetch from NSE and analyse/)
  await expect(page.locator('details').filter({ hasText: 'Add a call that NSE does not have' })).not.toHaveAttribute('open', '')
  await page.screenshot({ path: `${SHOTS}/25-concall-auto-demo.png`, fullPage: true })
})
