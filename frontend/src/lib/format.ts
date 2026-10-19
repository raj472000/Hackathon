// Formatting helpers. A missing value renders as an em dash with a reason, never as 0.

export const MISSING = '—'

const nf = (min: number, max: number) => new Intl.NumberFormat('en-IN', { minimumFractionDigits: min, maximumFractionDigits: max })

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return MISSING
  return nf(digits, digits).format(v)
}

/** Value with its unit. Scores are rounded to whole numbers to avoid false precision. */
export function fmtUnit(v: number | null | undefined, unit?: string | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return MISSING
  switch (unit) {
    case 'pct':
      return `${nf(1, 1).format(v)}%`
    case 'x':
      return `${nf(2, 2).format(v)}x`
    case 'days':
      return `${nf(0, 0).format(v)} d`
    case 'score':
      return nf(0, 0).format(v)
    case 'count':
      return nf(0, 0).format(v)
    case 'price':
      return `₹${nf(2, 2).format(v)}`
    case 'currency':
      return fmtCrore(v)
    default:
      return nf(2, 2).format(v)
  }
}

/** Statement values are stored in INR crore. Traded value arrives in rupees. */
export function fmtCrore(v: number | null | undefined): string {
  if (v === null || v === undefined) return MISSING
  if (Math.abs(v) >= 1e7) return `₹${nf(1, 1).format(v / 1e7)} cr`
  return `₹${nf(0, 1).format(v)} cr`
}

export function fmtSigned(v: number | null | undefined, digits = 1, suffix = '%'): string {
  if (v === null || v === undefined || Number.isNaN(v)) return MISSING
  const arrow = v > 0 ? '▲' : v < 0 ? '▼' : '■'
  return `${arrow} ${nf(digits, digits).format(Math.abs(v))}${suffix}`
}

export function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return 'text-ink-2'
  return v > 0 ? 'text-gain' : 'text-loss'
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return MISSING
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function fmtDateTime(iso?: string | null): string {
  if (!iso) return MISSING
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}, ${d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })}`
}

export function ago(iso?: string | null): string {
  if (!iso) return MISSING
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

export const PROVIDER_LABEL: Record<string, string> = {
  nse_mcp: 'NSE MCP',
  kite_mcp: 'Kite MCP',
  fundamentals_file: 'Statements file',
  demo_generator: 'Demo generator',
}

export const STATUS_LABEL: Record<string, string> = {
  missing: 'Not available',
  not_meaningful: 'Not meaningful',
  not_applicable: 'Not applicable',
  insufficient_history: 'Not enough history',
  not_supported: 'Not supported by source',
  stale: 'Stale',
  delayed: 'Delayed',
  unavailable: 'Source unavailable',
  login_required: 'Broker login required',
  not_configured: 'Not configured',
  conflict: 'Sources disagree',
  ok: 'OK',
}

const RUPEE_METRICS = new Set(['avg_traded_value'])

/** Format a catalog metric. Traded value is in rupees; statement aggregates are in INR crore. */
export function fmtMetric(key: string, v: number | null | undefined, unit?: string | null): string {
  if (v === null || v === undefined) return MISSING
  if (RUPEE_METRICS.has(key)) return `₹${nf(1, 1).format(v / 1e7)} cr`
  if (unit === 'currency') return `₹${nf(0, 0).format(v)} cr`
  return fmtUnit(v, unit)
}
