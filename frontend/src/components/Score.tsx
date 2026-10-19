import { useState } from 'react'
import { fmtMetric, fmtNum } from '../lib/format'
import { Badge, Button, Dialog, Missing, Notice, RiskBadge, StateBadge } from './ui'

export const PILLARS = ['fundamentals', 'valuation', 'momentum', 'risk', 'context'] as const
const SHORT: Record<string, string> = { fundamentals: 'Fundamentals', valuation: 'Valuation', momentum: 'Momentum', risk: 'Risk', context: 'Context' }

/** Five bars, one per pillar, in fixed order. A hollow outlined bar means the pillar has no score. */
export function PillarGlyph({ scores, size = 22, label }: { scores: Array<number | null | undefined>; size?: number; label?: string }) {
  const w = 4
  const gap = 2
  const text = PILLARS.map((p, i) => `${SHORT[p]} ${scores[i] == null ? 'not scored' : Math.round(scores[i] as number)}`).join(', ')
  return (
    <svg width={PILLARS.length * (w + gap) - gap} height={size} role="img" aria-label={label ?? `Pillar scores: ${text}`} className="shrink-0">
      <title>{text}</title>
      {PILLARS.map((p, i) => {
        const v = scores[i]
        const x = i * (w + gap)
        if (v == null) return <rect key={p} x={x + 0.5} y={size - 6.5} width={w - 1} height={6} rx={1} fill="none" stroke="var(--glyph-empty)" />
        const h = Math.max(2, (v / 100) * size)
        return (
          <g key={p}>
            <rect x={x} y={0} width={w} height={size} rx={1.5} fill="var(--glyph-empty)" opacity={0.45} />
            <rect x={x} y={size - h} width={w} height={h} rx={1.5} fill="var(--glyph)" />
          </g>
        )
      })}
    </svg>
  )
}

export function pillarScores(values: Record<string, number | null | undefined>) {
  return PILLARS.map((p) => values[`${p}_score`])
}

/** Overall score with rounding and an explicit incomplete state. */
export function ScoreFigure({ score, className }: { score: any; className?: string }) {
  if (!score) return <Missing reason="No metrics have been computed for this instrument." />
  const v = score.complete ? score.overall : score.partial_overall
  if (v == null) return <span className={className}><Missing reason={score.disclosure} status="missing" /></span>
  return (
    <span className={className}>
      <span className="num font-display text-[34px] font-semibold leading-none">{Math.round(v)}</span>
      <span className="ml-1 text-ink-2">/ 100{!score.complete && ' partial'}</span>
    </span>
  )
}

export function ScoreBreakdown({ score, versions, asOf }: { score: any; versions?: Record<string, string>; asOf?: string }) {
  const [how, setHow] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  if (!score) return null
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 px-4 py-4">
        <div>
          <div className="text-xs text-ink-2">Overall research score</div>
          <ScoreFigure score={score} />
          {score.band && <div className="text-xs text-ink-2">{score.band} of the tracked universe</div>}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <StateBadge state={score.stance} label={`Stance: ${score.stance}`} />
          <RiskBadge label={score.risk?.label} />
          <StateBadge state={score.confidence} label={`Confidence: ${score.confidence}`} />
          <Badge>Data completeness {fmtNum(score.completeness_pct, 0)}%</Badge>
        </div>
        <Button variant="quiet" className="ml-auto" onClick={() => setHow(true)}>
          How this is calculated
        </Button>
      </div>
      {!score.complete && (
        <div className="px-4 pb-3">
          <Notice tone="warn" title="Incomplete score.">
            {score.disclosure}
          </Notice>
        </div>
      )}
      <ul className="m-0 list-none border-t border-rule p-0">
        {PILLARS.map((p) => {
          const d = score.pillars?.[p]
          if (!d) return null
          const isOpen = open === p
          return (
            <li key={p} className="border-b border-rule last:border-0">
              <button className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-2 sm:grid-cols-[220px_minmax(0,1fr)_120px_auto]" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : p)}>
                <span className="font-medium">{d.label}</span>
                <span className="hidden h-2 overflow-hidden rounded-full bg-[var(--glyph-empty)] sm:block" aria-hidden>
                  {d.score != null && <span className="block h-full rounded-full bg-[var(--glyph)]" style={{ width: `${d.score}%` }} />}
                </span>
                <span className="num text-right">
                  {d.score == null ? <Missing reason={`Only ${d.metrics_used} of ${d.metrics_total} metrics available`} status="missing" /> : <strong>{Math.round(d.score)}</strong>}
                  <span className="ml-1 text-xs text-ink-2">
                    {d.metrics_used}/{d.metrics_total} metrics
                  </span>
                </span>
                <span className="text-ink-3" aria-hidden>
                  {isOpen ? '▾' : '▸'}
                </span>
              </button>
              {isOpen && (
                <div className="scroll-x bg-surface-2 px-4 pb-3">
                  <table className="text-[13px]">
                    <thead>
                      <tr>
                        <th>Metric</th>
                        <th className="r">Value</th>
                        <th className="r">Percentile score</th>
                        <th>Direction</th>
                        <th>Ranked against</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.metrics.map((m: any) => (
                        <tr key={m.name}>
                          <td>{m.label}</td>
                          <td className="r num">{m.value == null ? <Missing status={m.peer_scope === 'not_applicable' ? 'not_applicable' : 'missing'} /> : fmtMetric(m.name, m.value, undefined)}</td>
                          <td className="r num">{m.score == null ? <Missing reason="Excluded from the pillar average" /> : Math.round(m.score)}</td>
                          <td className="text-ink-2">{m.higher_is_better ? 'Higher is better' : 'Lower is better'}</td>
                          <td className="text-ink-2">{m.peer_scope === 'sector' ? 'Sector peers' : m.peer_scope === 'universe' ? 'Whole universe' : m.peer_scope === 'not_applicable' ? 'Not applicable to this sector' : 'Not ranked'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-2 text-xs text-ink-2">
                    Pillar score is the plain average of the available metric percentiles. This pillar carries {Math.round(d.weight * 100)}% of the overall score
                    {d.contribution != null && `, contributing ${fmtNum(d.contribution, 1)} points`}.
                  </p>
                </div>
              )}
            </li>
          )
        })}
      </ul>
      <Dialog open={how} title="How the research score is calculated" onClose={() => setHow(false)}>
        <ol className="m-0 list-decimal space-y-2 pl-5">
          <li>Each metric becomes a 0–100 percentile rank within the tracked universe (valuation metrics within sector peers when there are at least five), oriented so a higher number is more favourable.</li>
          <li>A pillar score is the plain average of its available metric percentiles. Missing or inapplicable metrics are left out; they are never counted as zero. A pillar needs at least half of its applicable metrics.</li>
          <li>The overall score is the average of the five pillars, 20% each. If any pillar lacks coverage the overall score is withheld and a clearly labelled partial score is shown instead.</li>
        </ol>
        <dl className="mt-3 space-y-2 text-[13px]">
          <div>
            <dt className="font-semibold">Stance</dt>
            <dd className="m-0 text-ink-2">{score.stance_rule}</dd>
          </div>
          <div>
            <dt className="font-semibold">Confidence</dt>
            <dd className="m-0 text-ink-2">{score.confidence_rule}</dd>
          </div>
          <div>
            <dt className="font-semibold">Risk label</dt>
            <dd className="m-0 text-ink-2">{score.risk?.criteria}</dd>
          </div>
        </dl>
        <p className="mt-3 text-xs text-ink-2">
          Peer universe: {score.peer_universe?.size} instruments on {score.peer_universe?.exchange}. Metrics as of {asOf}. Formula versions: {versions ? Object.entries(versions).map(([k, v]) => `${k} ${v}`).join(', ') : 'n/a'}.
          The score is a general research classification, not a prediction or personalised advice.
        </p>
      </Dialog>
    </div>
  )
}

export function EntrySetup({ setup }: { setup: any }) {
  if (!setup) return null
  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <StateBadge state={setup.state} label={setup.state} />
        <span className="text-ink-2">A checklist of measurable conditions. It is not an instruction to buy.</span>
      </div>
      <ul className="m-0 list-none border-t border-rule p-0">
        {setup.conditions.map((c: any) => (
          <li key={c.key} className="grid grid-cols-[24px_minmax(0,1fr)] gap-x-2 border-b border-rule px-4 py-2 last:border-0">
            <span aria-hidden className={c.passed === true ? 'text-gain' : c.passed === false ? 'text-loss' : 'text-ink-3'}>
              {c.passed === true ? '✓' : c.passed === false ? '✕' : '?'}
            </span>
            <span>
              <span className="font-medium">{c.label}</span> <span className="sr-only">{c.passed === true ? 'passed' : c.passed === false ? 'not met' : 'cannot be evaluated'}</span>
              <span className="block text-[13px] text-ink-2">
                Rule: {c.rule}. Now: {c.detail}.
              </span>
            </span>
          </li>
        ))}
      </ul>
      <div className="space-y-2 px-4 py-3 text-[13px]">
        {setup.wait_for?.length > 0 && (
          <div>
            <div className="font-semibold">Conditions to wait for</div>
            <ul className="m-0 mt-1 list-disc pl-5 text-ink-2">
              {setup.wait_for.map((w: string) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        )}
        {setup.invalidation && (
          <p className="m-0">
            <span className="font-semibold">What would weaken it: </span>
            {setup.invalidation.condition}. <span className="text-ink-2">Method: {setup.invalidation.method}.</span>
          </p>
        )}
        <p className="m-0 text-ink-2">
          <span className="font-semibold text-ink">Entry zone: </span>
          {setup.entry_zone_reason}
        </p>
        <p className="m-0 text-xs text-ink-2">{setup.disclaimer}</p>
      </div>
    </div>
  )
}
