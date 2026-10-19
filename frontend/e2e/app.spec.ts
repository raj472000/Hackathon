import { expect, test, type Page } from '@playwright/test'

// Demo stack: every figure on screen is synthetic.
async function ready(page: Page) {
  // The first start runs an initial refresh; wait until the API reports scored instruments.
  await expect
    .poll(async () => (await (await page.request.get('/api/v1/overview')).json()).universe?.instruments ?? 0, { timeout: 150_000, intervals: [2000] })
    .toBeGreaterThan(0)
}

/** The metric chooser has a search box (about 100 metrics). Picks by typing, as a user would. */
async function pickMetric(page: Page, search: string, label: string, n = 0) {
  await page.getByRole('button', { name: 'Metric' }).nth(n).click()
  await page.getByLabel('Search metrics').fill(search)
  await page.getByRole('option', { name: new RegExp(`^${label}`) }).first().click()
}

test.beforeEach(async ({ page }) => {
  await ready(page)
})

test('overview shows the demo banner, freshness and sector table @mobile', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('status').filter({ hasText: 'Demo mode' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  await expect(page.getByLabel('Data sources and freshness')).toContainText('Prices to')
  await expect(page.getByRole('heading', { name: 'Sector performance' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Information Technology', exact: true })).toBeVisible()
  // no horizontal page scroll at this viewport
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
})

test('screener: add a filter, sort, see missing-value markers and export link', async ({ page }) => {
  await page.goto('/screener')
  await expect(page.getByRole('heading', { name: /of 48 instruments/ })).toBeVisible()
  await page.getByRole('button', { name: 'Add filter' }).click()
  await pickMetric(page, 'roe', 'ROE')
  await page.getByLabel('Condition').selectOption('gte')
  await page.getByLabel('Value', { exact: true }).fill('15')
  await expect(page.getByRole('heading', { name: /of 48 instruments/ })).not.toHaveText('48 of 48 instruments')
  await page.getByRole('button', { name: 'ROE' }).click()
  await expect(page.getByRole('columnheader', { name: /ROE/ })).toHaveAttribute('aria-sort', 'descending')
  const read = () =>
    page.evaluate(() => {
      const heads = Array.from(document.querySelectorAll('thead th')).map((h) => h.textContent ?? '')
      const col = heads.findIndex((h) => h.startsWith('ROE'))
      return Array.from(document.querySelectorAll('tbody tr')).map((r) => parseFloat(r.children[col].textContent ?? ''))
    })
  // the table keeps the previous page on screen until the sorted one arrives
  await expect.poll(async () => { const n = await read(); return n.length > 0 && [...n].sort((x, y) => y - x).join() === n.join() }).toBe(true)
  expect((await read()).every((n) => n >= 15)).toBe(true)
  await expect(page.getByRole('link', { name: 'Export CSV' })).toHaveAttribute('href', /export\.csv\?.*roe/)
  // a filter on a metric nobody has excludes everything and says why
  await pickMetric(page, 'return 5y', 'Return 5Y')
  await expect(page.getByText(/left out because a filtered metric is not available/)).toBeVisible()
  await expect(page.getByText('No instrument matches these filters')).toBeVisible()
})

test('screener: search the filtered results by symbol, and open a stock without waiting for its quote', async ({ page }) => {
  await page.goto('/screener')
  await expect(page.getByRole('heading', { name: /of 48 instruments/ })).toBeVisible()
  await page.getByRole('button', { name: 'Add filter' }).click()
  await pickMetric(page, 'volatility', 'Volatility 1Y')
  await page.getByLabel('Condition').selectOption('lt')
  await page.getByLabel('Value', { exact: true }).fill('100')
  // the search box sits on the results and searches within the filters
  const search = page.getByTestId('screener-search')
  await expect(search).toHaveAttribute('placeholder', 'Search within the filtered results…')
  await search.fill('DEMOIT')
  await expect(page.getByText('Showing matches for “DEMOIT” within the filters')).toBeVisible()
  await expect.poll(async () => (await page.locator('tbody tr td:first-child a').allTextContents()).every((s) => s.startsWith('DEMOIT'))).toBe(true)
  const first = page.locator('tbody tr td:first-child a').first()
  const sym = (await first.textContent()) ?? ''
  await first.hover() // loads the stock page's stored data in advance
  await first.click()
  await expect(page.getByRole('heading', { level: 1 })).toContainText(sym)
  await expect(page.getByText('Overall research score')).toBeVisible()
})

test('themes: browse, open a theme, see ranked members and methodology', async ({ page }) => {
  await page.goto('/themes')
  await page.getByRole('button', { name: 'Low risk and stability' }).click()
  await page.getByRole('link', { name: 'Low realized volatility' }).click()
  await expect(page.getByRole('heading', { name: 'Low realized volatility' })).toBeVisible()
  await expect(page.getByText(/Snapshot \d{4}-\d{2} revision/)).toBeVisible()
  await expect(page.locator('tbody tr').first().locator('td').first()).toHaveText('1')
  await page.getByRole('tab', { name: 'Methodology' }).click()
  await expect(page.getByText('Volatility 1Y < 25')).toBeVisible()
  await page.getByRole('tab', { name: /Excluded/ }).click()
  await expect(page.getByText(/failed: volatility_252d/).first()).toBeVisible()
})

test('themes: an unavailable theme says which data it needs', async ({ page }) => {
  await page.goto('/themes/earnings-momentum')
  await expect(page.getByText('This theme cannot be calculated yet')).toBeVisible()
  await expect(page.getByText(/analyst_estimates/)).toBeVisible()
})

test('custom theme: interpret, review assumptions, confirm and save', async ({ page }) => {
  await page.goto('/themes')
  await page.getByRole('button', { name: 'Create custom theme' }).click()
  await page.getByLabel('Theme name (optional)').fill(`E2E quality ${Date.now()}`)
  await page.getByRole('button', { name: 'Interpret' }).click()
  await expect(page.getByText('Check these assumptions.')).toBeVisible()
  await expect(page.getByText(/'low debt' read as debt\/equity < 0.5x/)).toBeVisible()
  const save = page.getByRole('button', { name: 'Save theme' })
  await expect(save).toBeDisabled()
  await page.getByLabel('I have reviewed the filters and thresholds above.').check()
  await save.click()
  await expect(page.getByRole('heading', { name: /E2E quality/ })).toBeVisible()
  await expect(page.getByText('No snapshot yet', { exact: true })).toBeVisible()
  await page.getByTestId('theme-refresh').click()
  await expect(page.getByText(/Fetched the latest data and re-ranked: snapshot .* revision 1/)).toBeVisible({ timeout: 60_000 })
})

test('instrument: score breakdown, entry setup, price provenance and conflicts', async ({ page }) => {
  await page.goto('/instrument/NSE:DEMOAUTO01')
  // stored data renders first; the live quote arrives separately
  await expect(page.getByText('Overall research score')).toBeVisible()
  await expect(page.getByText('Delayed quote')).toBeVisible()
  await expect(page.getByText(/NSE MCP, as of/)).toBeVisible()
  await expect(page.getByText('Overall research score')).toBeVisible()
  await page.getByRole('button', { name: 'How this is calculated' }).click()
  await expect(page.getByText(/20% each/)).toBeVisible()
  await page.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByText('A checklist of measurable conditions. It is not an instruction to buy.')).toBeVisible()
  await expect(page.getByText(/No backtested entry-zone method/)).toBeVisible()
  await page.getByRole('tab', { name: 'Price and provenance' }).click()
  await expect(page.getByRole('img', { name: /Daily price chart/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Source disagreements' })).toBeVisible()
  await expect(page.locator('td.text-loss', { hasText: '2.00%' })).toHaveCount(2)
  await page.getByRole('tab', { name: 'All metrics' }).click()
  await page.getByRole('button', { name: 'ROE', exact: true }).click()
  await expect(page.getByText(/Formula: PAT \/ average shareholders' equity/)).toBeVisible()
  await page.getByRole('tab', { name: 'Financial statements' }).click()
  await expect(page.getByText(/Synthetic demo figures/)).toBeVisible()
})

test('research board states honestly that the model and web search were not used', async ({ page }) => {
  await page.goto('/research/NSE:DEMOIT02')
  const gen = page.getByRole('button', { name: /Generate research|Regenerate/ })
  await gen.click()
  await expect(page.getByText('Rule-based summary, no language model')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Web research unavailable.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Supporting evidence' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'What would weaken the thesis' })).toBeVisible()
  await expect(page.getByText(/not personalised investment advice/)).toBeVisible()
})

test('sector rotation: chart, help panel, controls and table', async ({ page }) => {
  await page.goto('/rotation')
  await expect(page.getByRole('img', { name: /Relative rotation graph of 10 sectors/ })).toBeVisible()
  await expect(page.getByText(/Snapshot computed/)).toBeVisible()
  await page.getByRole('button', { name: 'How to read this' }).click()
  await expect(page.getByText(/do not predict future performance/)).toBeVisible()
  await page.getByRole('button', { name: 'Close' }).click()
  await page.getByLabel('Weeks computed').selectOption('4')
  await expect(page.getByText('Computed on demand, not stored')).toBeVisible()
  await expect(page.getByText(/trail 4 weeks/)).toBeVisible()
  await page.getByRole('row', { name: /^Chemicals/ }).getByRole('button', { name: 'Chemicals' }).click()
  await expect(page.getByLabel('Highlighted sectors')).toContainText('Chemicals')
})

test('sector rotation: a short tail by default that can be lengthened, shortened and stepped back', async ({ page }) => {
  await page.goto('/rotation')
  const tail = page.getByTestId('rrg-tail')
  await expect(tail).toHaveValue('2')
  await page.getByTestId('rrg-tail-more').click()
  await expect(tail).toHaveValue('3')
  await page.getByTestId('rrg-tail-less').click()
  await page.getByTestId('rrg-tail-less').click()
  await expect(tail).toHaveValue('1')
  await expect(page.getByTestId('rrg-tail-less')).toBeDisabled()
  const latest = (await page.getByTestId('rrg-as-of').textContent()) ?? ''
  expect(latest).toMatch(/^Latest week · week ending \d{4}-\d{2}-\d{2}$/)
  await page.getByTestId('rrg-back').click()
  await page.getByTestId('rrg-back').click()
  await expect(page.getByTestId('rrg-as-of')).toContainText('2 weeks back')
  expect(await page.getByTestId('rrg-as-of').textContent()).not.toBe(latest)
  await page.getByTestId('rrg-forward').click()
  await expect(page.getByTestId('rrg-as-of')).toContainText('1 week back')
  await page.getByRole('button', { name: 'Latest' }).click()
  await expect(page.getByTestId('rrg-as-of')).toHaveText(latest)
  // stepping back to the first computed week offers more history
  for (let i = 0; i < 12 && (await page.getByTestId('rrg-back').isEnabled()); i++) await page.getByTestId('rrg-back').click()
  await page.getByTestId('rrg-more-history').click()
  await expect(page.getByText('Computed on demand, not stored')).toBeVisible()
  await expect(page.getByText(/trail 12 weeks/)).toBeVisible()
})

test('concall follow-through: run the analysis and see commitment verdicts', async ({ page }) => {
  await page.goto('/concalls/NSE:DEMOIT01')
  await page.getByRole('button', { name: 'Score stored calls' }).click()
  await expect(page.getByText('Follow-through score')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Keyword heuristic, no language model')).toBeVisible()
  await expect(page.getByText(/Heuristic mode/)).toBeVisible()
  await expect(page.getByText('Deferred').first()).toBeVisible()
  await expect(page.getByText(/has been delayed to Q4 FY2027/)).toBeVisible()
  await expect(page.getByText('Synthetic demo transcript').first()).toBeVisible()
})

test('watchlist and alert lifecycle with confirmation on delete', async ({ page }) => {
  const name = `E2E ${Date.now()}`
  await page.goto('/watchlists')
  await page.getByRole('textbox', { name: 'New watchlist' }).fill(name)
  await page.getByRole('button', { name: 'Create watchlist' }).click()
  await expect(page.getByRole('heading', { name })).toBeVisible()
  await page.getByPlaceholder(`Add an instrument to ${name}`).fill('DEMOFMCG01')
  await page.getByRole('option', { name: /DEMOFMCG01/ }).click()
  await expect(page.getByRole('link', { name: 'DEMOFMCG01' })).toBeVisible()
  await page.getByRole('button', { name: 'Delete list' }).first().click()
  await expect(page.getByRole('heading', { name: 'Delete this watchlist?' })).toBeVisible()
  await page.getByRole('button', { name: 'Delete watchlist' }).click()
  await expect(page.getByRole('heading', { name })).toHaveCount(0)
  await expect(page.getByText(/never place orders/)).toBeVisible()
})

test('data status: connectors, capability matrix, blocked order tools and schedule', async ({ page }) => {
  await page.goto('/status')
  await expect(page.getByRole('cell', { name: /^NSE bhavcopy MCP/ })).toBeVisible()
  await expect(page.getByText('4 blocked')).toBeVisible()
  await expect(page.getByRole('row', { name: /Order placement/ })).toContainText('blocked by policy')
  await expect(page.getByRole('row', { name: /Financial statements/ }).first()).toContainText('No')
  await expect(page.getByText(/The broker fills a value only when NSE has none/)).toBeVisible()
  await expect(page.getByText('LLM_API_KEY is not set', { exact: false }).or(page.getByText('LLM_ENABLED is false', { exact: false })).first()).toBeVisible()
  // the Kite session panel is at the top and shows the state without a click
  const kite = page.getByTestId('kite-session')
  await expect(kite).toContainText('Logged in')
  await expect(kite.getByRole('button', { name: 'Disconnect Kite' })).toBeVisible()
})

test('keyboard: skip link and instrument search work without a mouse', async ({ page }) => {
  await page.goto('/')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
  const search = page.getByRole('combobox', { name: 'Find an instrument by symbol or name' })
  await search.focus()
  await page.keyboard.type('DEMOCHEM')
  await expect(page.getByRole('option').first()).toBeVisible()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/instrument\/NSE:DEMOCHEM0\d/)
})
