import { expect, test, type Page } from '@playwright/test'

// New features: ask the market, theme members, per-theme refresh, AI theme filter and a refresh
// button on every page. Demo stack: every figure is synthetic. Screenshots are written to
// ../analysis/proof/ as evidence.
const SHOTS = '../analysis/proof'

async function ready(page: Page) {
  await expect
    .poll(async () => (await (await page.request.get('/api/v1/overview')).json()).universe?.instruments ?? 0, { timeout: 150_000, intervals: [2000] })
    .toBeGreaterThan(0)
}

test.beforeEach(async ({ page }) => {
  await ready(page)
})

test('overview loads quickly and answers a screen question from the database', async ({ page }) => {
  const t0 = Date.now()
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Ask the market' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Universe' })).toBeVisible()
  expect(Date.now() - t0).toBeLessThan(10_000)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/01-overview.png`, fullPage: true })

  await page.getByLabel('Your question').fill('Which stocks have the lowest volatility?')
  await page.getByTestId('ask-submit').click()
  const result = page.getByTestId('ask-result')
  await expect(result).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('ask-answer')).toContainText('instruments match')
  await expect(result.getByRole('table')).toBeVisible()
  await expect(result).toContainText('Understood by rules')
  await expect(result).toContainText('The language model was not used')
  await result.scrollIntoViewIfNeeded()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/02-ask-screen-question.png`, fullPage: true })
})

test('ask about one stock uses the live MCP quote and stored metrics', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Your question').fill('How is DEMOIT01 doing?')
  await page.getByTestId('ask-submit').click()
  const q1 = page.getByTestId('evidence-Q1')
  await expect(q1).toBeVisible({ timeout: 30_000 })
  await expect(q1).toContainText('Live · MCP')
  await expect(page.getByTestId('evidence-M1')).toContainText('Stored · database')
  await expect(page.getByTestId('ask-answer')).toContainText('DEMOIT01')
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/03-ask-single-stock.png`, fullPage: true })
})

test('ask about the market and sectors', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'How is the market today?' }).click()
  await expect(page.getByTestId('ask-result')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('evidence-L1')).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/04-ask-market.png`, fullPage: true })
})

test('themes list shows members for momentum and low volatility, and a theme card refreshes with the latest data', async ({ page }) => {
  await page.goto('/themes')
  const momentum = page.locator('li').filter({ has: page.getByRole('link', { name: 'Price momentum', exact: true }) })
  await expect(momentum).toContainText(/\d+ members/)
  const lowVol = page.locator('li').filter({ has: page.getByRole('link', { name: 'Low realized volatility', exact: true }) })
  await expect(lowVol).toContainText(/\d+ members/)
  await expect(lowVol).not.toContainText('none qualify')
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/05-themes-with-members.png`, fullPage: false })

  await momentum.getByTestId('theme-refresh').click()
  await expect(momentum.getByRole('status')).toContainText(/members, prices updated for \d+ of \d+/, { timeout: 60_000 })
  await momentum.scrollIntoViewIfNeeded()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/06-theme-card-refreshed.png` })
})

test('theme detail: refresh fetches latest data and AI filter narrows the members', async ({ page }) => {
  await page.goto('/themes/low-volatility')
  await expect(page.getByRole('heading', { name: 'Low realized volatility' })).toBeVisible()
  await expect(page.getByRole('tab', { name: /Members \(\d+\)/ })).toBeVisible()
  await page.getByTestId('theme-refresh').first().click()
  await expect(page.getByText(/Fetched the latest data and re-ranked/)).toBeVisible({ timeout: 60_000 })

  await page.getByLabel('How should the theme be narrowed?').fill('only banks')
  await page.getByTestId('ai-filter-submit').click()
  const res = page.getByTestId('ai-filter-result')
  await expect(res).toBeVisible({ timeout: 30_000 })
  await expect(res).toContainText('Financial Services')
  await expect(res).toContainText('Filters from rules')
  const sectors = await res.locator('tbody tr td:nth-child(2) div').allTextContents()
  expect(sectors.length).toBeGreaterThan(0)
  expect(sectors.every((s) => s === 'Financial Services')).toBe(true)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/07-theme-ai-filter.png`, fullPage: true })
})

const PAGES: Array<[string, string, string]> = [
  ['/', 'Overview', '08-refresh-overview'],
  ['/screener', 'Screener', '09-refresh-screener'],
  ['/themes', 'Themes', '10-refresh-themes'],
  ['/rotation', 'Sector rotation', '11-refresh-rotation'],
  ['/research', 'Research board', '12-refresh-research'],
  ['/concalls', 'Concall follow-through', '13-refresh-concalls'],
  ['/watchlists', 'Watchlists and alerts', '14-refresh-watchlists'],
  ['/status', 'Data status', '15-refresh-status'],
]

for (const [path, title, shot] of PAGES) {
  test(`refresh button on ${title} reloads its data`, async ({ page }) => {
    await page.goto(path)
    await expect(page.getByRole('heading', { name: title, level: 1 })).toBeVisible()
    const reloads: string[] = []
    page.on('request', (r) => r.url().includes('/api/v1/') && reloads.push(r.url()))
    const btn = page.getByTestId('page-refresh').last()
    await btn.click()
    await expect(page.getByText(/^Updated \d/).first()).toBeVisible({ timeout: 30_000 })
    expect(reloads.length).toBeGreaterThan(0)
    await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/${shot}.png` })
  })
}

test('instrument page fetches the latest prices for one instrument', async ({ page }) => {
  await page.goto('/instrument/NSE:DEMOIT02')
  await expect(page.getByRole('heading', { name: /DEMOIT02/ })).toBeVisible()
  await page.getByTestId('page-refresh').first().click()
  await expect(page.getByText(/^Updated \d/).first()).toBeVisible({ timeout: 60_000 })
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/16-refresh-instrument.png` })
})

test('dark theme and mobile layout @mobile', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Ask the market' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/17-overview-${test.info().project.name}.png`, fullPage: false })
  if (test.info().project.name === 'desktop') {
    await page.getByRole('button', { name: /Switch to (dark|light) theme/ }).click()
    await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${SHOTS}/18-overview-dark.png`, fullPage: false })
  }
})
