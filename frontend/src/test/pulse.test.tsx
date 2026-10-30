import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { IndexStrip } from '../components/IndexStrip'
import { Nifty50Ticker } from '../components/Nifty50Ticker'
import { TickertapeSessionPanel } from '../components/TickertapeConnect'
import { Derivatives } from '../pages/Derivatives'
import { News } from '../pages/News'

function mockApi(routes: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const path = String(url).split('?')[0].replace('/api/v1', '')
    const body = routes[path]
    return body === undefined ? new Response('{}', { status: 404 }) : new Response(JSON.stringify(body), { status: 200 })
  }))
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>)
}

afterEach(() => vi.unstubAllGlobals())

const NEWS = {
  state: 'ok', sentiment_counts: { Bullish: 1, Bearish: 1, Neutral: 0 }, window: { from: '2026-10-26', to: '2026-10-26', days: 1 }, routine_hidden: 2, computed_at: '2026-10-26T06:30:00Z',
  sources: { nse_announcements: { state: 'ok' }, web_search: { state: 'ok' }, quotes: { state: 'ok', live: 2, end_of_day: 0 } },
  news: [
    { id: 'n1', kind: 'exchange_filing', source: 'NSE corporate announcement', source_type: 'primary', headline: 'Aaa Ltd: Acquisition', category: 'Acquisition',
      summary: 'Aaa Ltd has informed the Exchange about the acquisition of Bcd Pvt Ltd.', url: 'https://nsearchives.nseindia.com/x.pdf', published_at: '2026-10-26T10:00:00+05:30', symbols: ['AAA'] },
    { id: 'n2', kind: 'news', source: 'ddgs', source_type: 'secondary', headline: 'Zzz shares slide', category: 'News', summary: null, url: 'https://news.example/z',
      published_at: '2026-10-26', symbols: ['ZZZ'] },
  ],
  stocks: [
    { symbol: 'AAA', name: 'Aaa Ltd', sector: null, in_universe: true, news_ids: ['n1'], categories: ['Acquisition'], first_news_at: '2026-10-26T10:00:00+05:30',
      last_price: 110, day_change_pct: 4, quote_as_of: null, quote_basis: 'live (delayed)', ref_price: 100, ref_date: null, ref_basis: 'previous close', move_pct: 10,
      sentiment: { label: 'Bullish', score: 2, confidence: 'moderate', basis: ['news reads positive (acquisition)', 'price up 10.00% since the news'] } },
    { symbol: 'ZZZ', name: null, sector: null, in_universe: false, news_ids: ['n2'], categories: ['News'], first_news_at: '2026-10-26', last_price: null,
      day_change_pct: null, quote_as_of: null, quote_basis: null, ref_price: null, ref_date: null, move_pct: null, move_reason: 'no NSE quote for this symbol',
      sentiment: { label: 'Bearish', score: -1, confidence: 'low', basis: ['news reads negative (negative wording)', 'no price move since the news yet'] } },
  ],
}

describe('market news', () => {
  it('lists news on the left, the day’s stocks on the right, and opens a short description on click', async () => {
    mockApi({ '/markets/news': NEWS })
    wrap(<News />)
    const headline = await screen.findByRole('button', { name: /Aaa Ltd: Acquisition/ })
    expect(screen.queryByText(/acquisition of Bcd/)).not.toBeInTheDocument()
    await userEvent.click(headline)
    expect(headline).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/acquisition of Bcd Pvt Ltd/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the filing' })).toHaveAttribute('href', 'https://nsearchives.nseindia.com/x.pdf')
    const stocks = screen.getByRole('heading', { name: 'Stocks in the news' }).closest('section')!
    expect(within(stocks).getByText('▲ 10.00%', { selector: 'td' })).toBeInTheDocument()
    // a stock with no quote shows a reasoned gap, never zero
    expect(within(stocks).getAllByText('no NSE quote for this symbol', { exact: false }).length).toBeGreaterThan(0)
  })
  it('filters the news to one stock', async () => {
    mockApi({ '/markets/news': NEWS })
    wrap(<News />)
    await screen.findByText('Zzz shares slide')
    const stocks = screen.getByRole('heading', { name: 'Stocks in the news' }).closest('section')!
    await userEvent.click(within(stocks).getAllByRole('button', { name: '1 item' })[0])
    expect(screen.queryByText('Zzz shares slide')).not.toBeInTheDocument()
    expect(screen.getByText(/Showing AAA only/)).toBeInTheDocument()
  })
})

describe('news sentiment', () => {
  it('shows each stock’s sentiment with its reasons and filters by it', async () => {
    mockApi({ '/markets/news': NEWS })
    wrap(<News />)
    const stocks = (await screen.findByRole('heading', { name: 'Stocks in the news' })).closest('section')!
    expect(within(stocks).getByText(/1 bullish/)).toBeInTheDocument()
    expect(within(stocks).getByTitle(/^Bullish \(moderate confidence\): news reads positive/)).toBeInTheDocument()
    expect(within(stocks).getByText(/news reads positive \(acquisition\)/)).toBeInTheDocument() // reasons for screen readers
    await userEvent.selectOptions(screen.getByTestId('sentiment-filter'), 'Bearish')
    expect(within(stocks).queryByText('AAA')).not.toBeInTheDocument()
    expect(within(stocks).getByText('ZZZ')).toBeInTheDocument()
  })
})

describe('index strip', () => {
  it('shows Nifty, Sensex and Nifty Bank with change, and a reason when a value is missing', async () => {
    mockApi({ '/markets/indices': { indices: [
      { key: 'NIFTY 50', label: 'Nifty 50', exchange: 'NSE', state: 'delayed', value: 22562.6, prev_close: 22600, change: -37.4, change_pct: -0.165, high: 22706, low: 22387, as_of: '2026-10-26T11:41:00+05:30', source: 'NSE CM market MCP' },
      { key: 'SENSEX', label: 'Sensex', exchange: 'BSE', state: 'not_configured', value: null, reason: 'MACRO_CHART_BASE_URL is not set' },
      { key: 'NIFTY BANK', label: 'Nifty Bank', exchange: 'NSE', state: 'delayed', value: 54003, prev_close: 54287.35, change: -284.35, change_pct: -0.52, as_of: null, source: 'NSE CM market MCP' },
    ] } })
    wrap(<IndexStrip />)
    const nifty = await screen.findByTestId('index-NIFTY 50')
    expect(nifty).toHaveTextContent('22,562.60')
    expect(nifty).toHaveTextContent('▼ 0.17%')
    expect(screen.getByTestId('index-SENSEX')).toHaveTextContent('MACRO_CHART_BASE_URL is not set')
    expect(screen.getByTestId('index-NIFTY BANK')).toHaveTextContent('54,003.00')
  })
})

describe('F&O and open interest', () => {
  it('switches between top gainers and the buildup views', async () => {
    const row = { symbol: 'ABC', kind: 'stock', near_expiry: '2026-10-27', close: 102, price_change_pct: 2, open_interest: 1000, oi_change: 50, oi_change_pct: 5.26, volume_contracts: 20, buildup: 'long_buildup' }
    mockApi({
      '/derivatives/movers': { state: 'delayed', gainers: [{ symbol: 'DRREDDY', ltp: 1283.8, prev_close: 1229.5, change_pct: 4.42, volume: 1, turnover_lakh: 1 }], losers: [], as_of: null, source: 'NSE', retrieved_at: '2026-10-26T06:00:00Z' },
      '/derivatives/oi': { state: 'ok', date: '2026-10-23', requested: '2026-10-26', source: 'NSE F&O bhavcopy (end of day)', underlyings: 1, oi_gainers: [row], oi_losers: [],
        buildups: { long_buildup: [row], short_buildup: [], short_covering: [], long_unwinding: [] }, buildup_counts: { long_buildup: 1, short_buildup: 0, short_covering: 0, long_unwinding: 0 },
        buildup_labels: { long_buildup: 'Long buildup', short_buildup: 'Short buildup', short_covering: 'Short covering', long_unwinding: 'Long unwinding' }, index_futures: [], method: 'rules' },
    })
    wrap(<Derivatives />)
    expect(await screen.findByText('DRREDDY')).toBeInTheDocument()
    for (const tab of ['Top losers', 'OI gainers', 'OI losers', 'Short buildup', 'Short covering', 'Long unwinding']) {
      expect(screen.getByRole('tab', { name: tab })).toBeInTheDocument()
    }
    await userEvent.click(screen.getByRole('tab', { name: 'Long buildup' }))
    expect(await screen.findByText(/latest published on or before/)).toBeInTheDocument()
    expect(screen.getByText('ABC')).toBeInTheDocument()
    expect(screen.getByText('Price up and open interest up: new long positions.', { selector: 'p' })).toBeInTheDocument()
  })
})

describe('Tickertape session', () => {
  it('shows a connected session with a Disconnect button that logs out after confirmation', async () => {
    const calls: string[] = []
    const status = (state: string) => ({ data_mode: 'live', is_demo: false, retrieved_at: '2026-10-26T10:00:00Z', coverage: { instruments: 1, last_bar: null }, capabilities: {},
      connectors: [{ key: 'tickertape_mcp', label: 'Tickertape MCP', state: state === 'authorized' ? 'ok' : 'login_required', tool_count: 33,
        blocked_tools: ['place_order'], auth: state === 'authorized' ? { state, scope: 'market_data', authorised_at: '2026-10-26T09:30:00Z' } : { state, reason: 'Connect Tickertape' } }] })
    let current = 'authorized'
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      const path = String(url).split('?')[0].replace('/api/v1', '')
      calls.push(`${init?.method ?? 'GET'} ${path}`)
      if (path === '/sources/tickertape/logout') { current = 'login_required'; return new Response(JSON.stringify({ tickertape: { state: current } })) }
      return new Response(JSON.stringify(status(current)))
    }))
    vi.stubGlobal('confirm', () => true)
    wrap(<TickertapeSessionPanel />)
    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.getByText(/33 read-only tools, 1 blocked/)).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('tickertape-disconnect'))
    expect(calls).toContain('POST /sources/tickertape/logout')
    expect(await screen.findByTestId('tickertape-login')).toHaveTextContent('Connect Tickertape')
  })
})

describe('Nifty 50 ticker', () => {
  it('lists each constituent with price and change, links to the instrument, and ends with the index', async () => {
    mockApi({ '/markets/nifty50': { state: 'degraded', priced: 2, total: 3, sources: { kite: { priced: 2 } },
      items: [{ symbol: 'ITC', last_price: 260.5, change_pct: 1.01, source: 'Zerodha Kite MCP (get_quotes)' },
              { symbol: 'MARUTI', last_price: 11220, change_pct: -0.09, source: 'Zerodha Kite MCP (get_quotes)' },
              { symbol: 'TCS', last_price: null, change_pct: null, source: null }],
      index: { value: 22753.2, change_pct: 0.68, source: 'Zerodha Kite MCP (get_quotes)' } } })
    wrap(<Nifty50Ticker />)
    const t = await screen.findByTestId('nifty50-ticker')
    const itc = within(t).getAllByRole('link', { name: /ITC/ })[0]
    expect(itc).toHaveAttribute('href', '/instrument/NSE:ITC')
    expect(itc).toHaveTextContent('260.50')
    expect(itc).toHaveTextContent('1.01% up')
    expect(within(t).getAllByRole('link', { name: /MARUTI/ })[0]).toHaveTextContent('0.09% down')
    expect(within(t).queryByRole('link', { name: /TCS/ })).toBeNull() // no price from any source: left out, never shown blank
    expect(within(t).getAllByText('NIFTY 50')[0].parentElement).toHaveTextContent('22,753.20')
    expect(within(t).getByRole('button', { name: 'Scroll Nifty 50 ticker right' })).toBeInTheDocument()
  })

  it('moves the track with a sub-pixel transform every frame, loops, and never draws an unpriced stock', async () => {
    const frames: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb))
    vi.stubGlobal('cancelAnimationFrame', () => {})
    vi.spyOn(performance, 'now').mockReturnValue(0)
    const width = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(1000)
    mockApi({ '/markets/nifty50': { state: 'degraded', priced: 1, total: 2, sources: { nse_close: { priced: 1 } },
      items: [{ symbol: 'ITC', last_price: 260.5, change_pct: 1.01, source: 'NSE bhavcopy MCP (latest close)' },
              { symbol: 'TCS', last_price: null, change_pct: null, source: null }],
      index: { value: null, reason: 'down' } } })
    wrap(<Nifty50Ticker />)
    const t = await screen.findByTestId('nifty50-ticker')
    expect(t).not.toHaveTextContent('TCS')
    expect(t).not.toHaveTextContent('NIFTY 50') // the index had no value: left out, not shown as a dash
    expect(t).not.toHaveTextContent('—')
    const trackEl = within(t).getAllByRole('link', { name: /ITC/ })[0].parentElement!.parentElement as HTMLElement
    const run = (ms: number) => act(() => { const cb = frames.shift()!; cb(ms) })
    run(16) // 16 ms at 40 px/s = 0.64 px: below one pixel, which scrollLeft rounding used to swallow
    expect(-parseFloat(trackEl.style.transform.slice('translate3d('.length))).toBeCloseTo(0.64, 5)
    for (let ms = 116; ms <= 25_116; ms += 100) run(ms) // 25 s = 1000 px, a full copy of the list
    const shift = -parseFloat(trackEl.style.transform.slice('translate3d('.length))
    expect(shift).toBeGreaterThanOrEqual(0)
    expect(shift).toBeLessThan(1000) // wrapped to the start instead of running off the end
    width.mockRestore()
  })
})
