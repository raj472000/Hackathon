import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { RotationSchema, ScreenerSchema } from '../api/schemas'
import { RRGChart } from '../components/Charts'
import { EntrySetup, PillarGlyph, ScoreBreakdown, ScoreFigure } from '../components/Score'
import { Async, ErrorState, Missing } from '../components/ui'
import { fmtMetric, fmtSigned, fmtUnit, MISSING } from '../lib/format'
import { FilterBuilder, toApiFilters } from '../pages/Screener'

describe('formatting', () => {
  it('never renders a missing value as zero', () => {
    expect(fmtUnit(null, 'pct')).toBe(MISSING)
    expect(fmtUnit(undefined, 'x')).toBe(MISSING)
    expect(fmtMetric('roe', null, 'pct')).toBe(MISSING)
    expect(fmtSigned(null)).toBe(MISSING)
    expect(fmtUnit(0, 'pct')).toBe('0.0%')
  })
  it('formats units and shows direction with a symbol, not colour alone', () => {
    expect(fmtUnit(18.42, 'pct')).toBe('18.4%')
    expect(fmtUnit(24.1, 'x')).toBe('24.10x')
    expect(fmtUnit(71.6, 'score')).toBe('72')
    expect(fmtSigned(2.34)).toBe('▲ 2.3%')
    expect(fmtSigned(-2.34)).toBe('▼ 2.3%')
    expect(fmtMetric('avg_traded_value', 250_000_000, 'currency')).toBe('₹25.0 cr')
    expect(fmtMetric('market_cap', 52000, 'currency')).toBe('₹52,000 cr')
  })
})

describe('missing and error states', () => {
  it('explains why a value is missing to screen readers', () => {
    render(<Missing status="insufficient_history" reason="126 bars available, 253 required" />)
    expect(screen.getByText('Not enough history: 126 bars available, 253 required')).toBeInTheDocument()
  })
  it('shows an actionable error with the request id and retries', async () => {
    const retry = vi.fn()
    render(<ErrorState error={new ApiError(429, 'rate_limited', 'Too many research requests. Try again in 12 seconds.', 'abc123')} retry={retry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Too many requests')
    expect(screen.getByText(/Try again in 12 seconds/)).toBeInTheDocument()
    expect(screen.getByText('Request id abc123')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalledOnce()
  })
  it('renders a skeleton while loading and content when ready', () => {
    const { rerender } = render(<Async q={{ isLoading: true, error: null, refetch: () => {} }}>{() => <p>ready</p>}</Async>)
    expect(screen.getByRole('status', { name: 'Loading' })).toBeInTheDocument()
    rerender(<Async q={{ isLoading: false, error: null, data: 1, refetch: () => {} }}>{() => <p>ready</p>}</Async>)
    expect(screen.getByText('ready')).toBeInTheDocument()
  })
})

const score = {
  overall: 61.4, partial_overall: null, complete: true, disclosure: 'All five pillars have sufficient coverage; each contributes 20%.', band: 'Upper-middle', stance: 'Mixed',
  confidence: 'High', completeness_pct: 100, stance_rule: 'Favorable: score >= 65.', confidence_rule: 'High: completeness >= 85%.', risk: { label: 'Moderate', criteria: 'Worst bucket across three measures.' },
  peer_universe: { size: 48, exchange: 'NSE' },
  pillars: Object.fromEntries(['fundamentals', 'valuation', 'momentum', 'risk', 'context'].map((p, i) => [p, { label: p, score: 40 + i * 10, sufficient: true, weight: 0.2, contribution: 8 + i * 2, metrics_used: 4, metrics_total: 4,
    metrics: [{ name: 'roe', label: 'ROE', value: 18.4, score: 72, higher_is_better: true, peer_scope: 'universe' }, { name: 'debt_equity', label: 'Debt / equity', value: null, score: null, higher_is_better: false, peer_scope: 'not_applicable' }] }])),
}

describe('score display', () => {
  it('pillar glyph describes all five pillars and marks unscored ones', () => {
    render(<PillarGlyph scores={[53, 12, null, 38, 82]} />)
    expect(screen.getByRole('img')).toHaveAccessibleName('Pillar scores: Fundamentals 53, Valuation 12, Momentum not scored, Risk 38, Context 82')
  })
  it('rounds the overall score and labels a partial score', () => {
    const { rerender } = render(<ScoreFigure score={score} />)
    expect(screen.getByText('61')).toBeInTheDocument()
    rerender(<ScoreFigure score={{ ...score, complete: false, overall: null, partial_overall: 57.8 }} />)
    expect(screen.getByText('58')).toBeInTheDocument()
    expect(screen.getByText(/partial/)).toBeInTheDocument()
  })
  it('shows an incomplete warning, pillar detail and the how-calculated view', async () => {
    render(<ScoreBreakdown score={{ ...score, complete: false, overall: null, partial_overall: 57, disclosure: 'Incomplete: insufficient coverage for valuation.' }} versions={{ score: 'score-v1' }} asOf="09 Oct 2026" />)
    expect(screen.getByText(/insufficient coverage for valuation/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /fundamentals/ }))
    expect(screen.getByText('Not applicable to this sector')).toBeInTheDocument()
    expect(screen.getByText('Higher is better')).toBeInTheDocument()
    expect(screen.getByText(/carries 20% of the overall score/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'How this is calculated' })).toBeInTheDocument()
  })
  it('entry setup lists every rule, what to wait for, and never says buy', () => {
    render(<EntrySetup setup={{ state: 'Watch', conditions: [{ key: 'trend', label: 'Price trend', rule: 'close > SMA50 > SMA200', passed: true, detail: 'close 110' }, { key: 'valuation', label: 'Valuation versus peers', rule: 'valuation pillar score >= 40', passed: false, detail: 'valuation score 12' }, { key: 'quality', label: 'Fundamental quality', rule: 'fundamentals pillar score >= 50', passed: null, detail: 'n/a' }],
      wait_for: ['Valuation versus peers: needs valuation pillar score >= 40'], invalidation: { condition: 'A daily close below SMA200 (95.00) would weaken the setup', method: '200-day SMA' }, entry_zone_reason: 'No backtested entry-zone method is configured.', disclaimer: 'Not personalised advice.' }} />)
    expect(screen.getByText('Watch')).toBeInTheDocument()
    expect(screen.getByText('not met')).toBeInTheDocument()
    expect(screen.getByText('cannot be evaluated')).toBeInTheDocument()
    expect(screen.getByText(/Conditions to wait for/)).toBeInTheDocument()
    expect(screen.getByText(/No backtested entry-zone method/)).toBeInTheDocument()
    expect(screen.queryByText(/buy now/i)).not.toBeInTheDocument()
  })
})

describe('screener filters', () => {
  const metrics = [{ key: 'roe', label: 'ROE', unit: 'pct', group: 'fundamentals' }, { key: 'pe', label: 'P/E (TTM)', unit: 'x', group: 'valuation' }]
  it('sends only complete numeric filters', () => {
    expect(toApiFilters([{ metric: 'roe', op: 'gte', value: '15' }, { metric: 'pe', op: 'lt', value: '' }, { metric: 'pe', op: 'between', value: '5', value2: '' }, { metric: 'pe', op: 'between', value: '5', value2: '20' }, { metric: 'roe', op: 'gt', value: 'abc' }]))
      .toEqual([{ metric: 'roe', op: 'gte', value: 15 }, { metric: 'pe', op: 'between', value: 5, value2: 20 }])
  })
  it('adds, edits and removes filter rows with labelled controls', async () => {
    const onChange = vi.fn()
    const { rerender } = render(<FilterBuilder rows={[]} metrics={metrics} onChange={onChange} />)
    expect(screen.getByText(/No filters/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Add filter' }))
    expect(onChange).toHaveBeenLastCalledWith([{ metric: 'roe', op: 'gte', value: '' }])
    rerender(<FilterBuilder rows={[{ metric: 'roe', op: 'between', value: '10', value2: '20' }]} metrics={metrics} onChange={onChange} />)
    expect(screen.getByLabelText('Upper value')).toHaveValue('20')
    expect(screen.getByText('percent')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Remove filter 1' }))
    expect(onChange).toHaveBeenLastCalledWith([])
  })
})

describe('rotation chart', () => {
  const trail = (dx: number, dy: number) => [1, 2, 3].map((i) => ({ date: `2026-09-${10 + i * 7}`, rs_ratio: 100 + dx * i, rs_momentum: 100 + dy * i }))
  const sectors = { IT: { status: 'ok', quadrant: 'Leading', trail: trail(1, 1) }, FMCG: { status: 'ok', quadrant: 'Lagging', trail: trail(-1, -1) }, Power: { status: 'insufficient_history', trail: [] } }
  it('labels quadrants and plotted sectors directly, skips sectors without history, and selects on click', async () => {
    const onToggle = vi.fn()
    render(<RRGChart sectors={sectors} selected={['IT']} onToggle={onToggle} visible={new Set(['IT', 'FMCG', 'Power'])} />)
    expect(screen.getByRole('img', { name: /2 sectors/ })).toBeInTheDocument()
    for (const q of ['Leading', 'Weakening', 'Lagging', 'Improving']) expect(screen.getByText(q)).toBeInTheDocument()
    expect(screen.getAllByText('IT').length).toBeGreaterThan(0)
    expect(screen.queryByText('Power')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('FMCG'))
    expect(onToggle).toHaveBeenCalledWith('FMCG')
    expect(screen.getByLabelText('Highlighted sectors')).toHaveTextContent('IT')
  })
})

describe('response contracts', () => {
  it('rejects a screener payload with a numeric value given as a string', () => {
    const base = { data_mode: 'demo', is_demo: true, retrieved_at: 'x', total: 1, universe: 1, page: 1, page_size: 25, columns: [], as_of: null, computed_at: null, excluded_for_missing_filter_metric: 0 }
    const item = { instrument_id: 1, symbol: 'A', name: null, sector: null, values: { roe: null }, missing: { roe: { status: 'missing', reason: 'x' } } }
    expect(ScreenerSchema.safeParse({ ...base, items: [item] }).success).toBe(true)
    expect(ScreenerSchema.safeParse({ ...base, items: [{ ...item, values: { roe: '18' } }] }).success).toBe(false)
  })
  it('accepts a rotation payload with a sector that has no trail', () => {
    expect(RotationSchema.safeParse({ data_mode: 'live', is_demo: false, retrieved_at: 'x', snapshot_id: null, sectors: { Power: { status: 'insufficient_history', reason: 'x' } } }).success).toBe(true)
  })
})
