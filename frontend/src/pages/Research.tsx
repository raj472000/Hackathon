import { useQueryClient } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { post, useAction, useApi } from '../api/client'
import { InstrumentSearch } from '../components/InstrumentSearch'
import { EntrySetup, PILLARS, PillarGlyph, ScoreFigure } from '../components/Score'
import { Async, Badge, Button, EmptyState, ErrorState, Notice, PageHeader, Panel, RefreshButton, RiskBadge, StateBadge } from '../components/ui'
import { PROVIDER_LABEL, fmtDate, fmtDateTime, fmtNum } from '../lib/format'
import { QuoteHeader } from './Instrument'

const KIND: Record<string, { label: string; tone: string }> = {
  source: { label: 'Sourced fact', tone: 'info' },
  calculation: { label: 'Calculated', tone: 'neutral' },
  quote: { label: 'Quote', tone: 'neutral' },
}

/** One claim with its kind (sourced, calculated, interpretation) and links to the evidence it cites. */
function Point({ p, evidence, interpreted }: { p: any; evidence: Record<string, any>; interpreted: boolean }) {
  if (!p) return <p className="m-0 text-ink-2">Not enough evidence to say anything here.</p>
  return (
    <div>
      <p className="m-0">{p.text}</p>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
        {interpreted && <Badge tone="accent">Model interpretation</Badge>}
        {p.kinds?.map((k: string) => (
          <Badge key={k} tone={KIND[k]?.tone}>
            {KIND[k]?.label ?? k}
          </Badge>
        ))}
        {p.evidence?.map((id: string) => {
          const e = evidence[id]
          if (!e) return null
          return e.kind === 'source' ? (
            <a key={id} href={e.url} target="_blank" rel="noopener noreferrer nofollow">
              [{id}] {e.title.slice(0, 40)}
            </a>
          ) : (
            <a key={id} href={`#ev-${id}`} className="text-ink-2">
              {e.label} {typeof e.value === 'number' ? fmtNum(e.value) : ''}
            </a>
          )
        })}
        {(!p.evidence || p.evidence.length === 0) && <span className="text-ink-3">Rule outcome, no metric cited</span>}
      </div>
    </div>
  )
}

function List({ title, items, evidence, interpreted, empty }: { title: string; items: any[]; evidence: Record<string, any>; interpreted: boolean; empty: ReactNode }) {
  return (
    <Panel title={title}>
      {items.length === 0 ? (
        <p className="m-0 px-4 py-3 text-ink-2">{empty}</p>
      ) : (
        <ul className="m-0 list-none p-0">
          {items.map((p, i) => (
            <li key={i} className="border-b border-rule px-4 py-2.5 last:border-0">
              <Point p={p} evidence={evidence} interpreted={interpreted} />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

function Board({ d, onRegenerate, busy }: { d: any; onRegenerate: () => void; busy: boolean }) {
  const r = d.report
  const ev = d.evidence as Record<string, any>
  const interpreted = d.mode === 'llm'
  const sources = Object.entries(ev).filter(([, e]) => e.kind === 'source')
  const calcs = Object.entries(ev).filter(([, e]) => e.kind !== 'source')
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink-2">
        <Badge tone={interpreted ? 'accent' : 'neutral'}>{interpreted ? `Written by ${d.llm.model_id} from the evidence below` : 'Rule-based summary, no language model'}</Badge>
        <span>Generated {fmtDateTime(d.generated_at)}</span>
        <span>Metrics as of {fmtDate(d.metrics_as_of)}</span>
        <Button className="ml-auto" onClick={onRegenerate} busy={busy}>
          Regenerate
        </Button>
      </div>
      {!interpreted && d.llm.reason && (
        <Notice tone="info" title="Language model not used.">
          {d.llm.reason}
        </Notice>
      )}
      {!d.web.available && (
        <Notice tone="warn" title="Web research unavailable.">
          {d.web.reason} News, filings and catalysts are therefore empty rather than guessed.
        </Notice>
      )}
      {d.grounding?.points_dropped > 0 && (
        <Notice tone="warn" title={`${d.grounding.points_dropped} model statement${d.grounding.points_dropped === 1 ? ' was' : 's were'} removed.`}>
          They cited evidence that was not retrieved or stated numbers absent from the cited evidence.
        </Notice>
      )}

      <Panel title="Summary">
        <div className="grid gap-4 px-4 py-4 md:grid-cols-[auto_minmax(0,1fr)]">
          <div className="space-y-2">
            <ScoreFigure score={d.score} />
            {d.score && <PillarGlyph size={34} scores={PILLARS.map((p) => d.score.pillars?.[p]?.score)} />}
            <div className="flex flex-wrap gap-1.5">
              <StateBadge state={d.labels.stance} label={`Stance: ${d.labels.stance ?? 'Insufficient data'}`} />
              <RiskBadge label={d.labels.risk} />
              <StateBadge state={d.labels.confidence} label={`Confidence: ${d.labels.confidence ?? 'Low'}`} />
            </div>
            {d.score && <div className="text-xs text-ink-2">Data completeness {fmtNum(d.score.completeness_pct, 0)}%</div>}
          </div>
          <div className="max-w-[75ch]">
            <Point p={r.summary} evidence={ev} interpreted={interpreted} />
            {r.uncertainty && <p className="mb-0 mt-2 text-[13px] text-ink-2">Uncertainty: {r.uncertainty}</p>}
          </div>
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <List title="Supporting evidence" items={r.pros} evidence={ev} interpreted={interpreted} empty="No metric ranks clearly in this instrument's favour." />
        <List title="Counter-evidence and risks" items={[...r.cons, ...r.risks]} evidence={ev} interpreted={interpreted} empty="No clear negatives were found in the available metrics." />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {(['valuation_context', 'technical_picture', 'sector_context'] as const).map((k) => (
          <Panel key={k} title={{ valuation_context: 'Valuation', technical_picture: 'Price action', sector_context: 'Sector context' }[k]}>
            <div className="px-4 py-3">
              <Point p={r[k]} evidence={ev} interpreted={interpreted} />
            </div>
          </Panel>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <List title="Events and news" items={r.news} evidence={ev} interpreted={interpreted} empty={d.web.available ? 'The search returned nothing relevant.' : 'Web research was unavailable, so nothing was retrieved.'} />
        <List title="Catalysts" items={r.catalysts} evidence={ev} interpreted={interpreted} empty="None identified from retrieved sources." />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Entry setup">
          <EntrySetup setup={d.entry_setup} />
        </Panel>
        <div className="space-y-4">
          <List title="What would weaken the thesis" items={r.invalidation} evidence={ev} interpreted={interpreted} empty="No invalidation condition could be derived (needs a 200-day average)." />
          <Panel title="Missing data">
            {r.missing_data.length === 0 ? (
              <p className="m-0 px-4 py-3 text-ink-2">Every metric used on this board is available.</p>
            ) : (
              <ul className="m-0 list-disc px-4 py-3 pl-9 text-[13px] text-ink-2">
                {r.missing_data.map((m: string) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>

      <Panel title="Sources and data quality" aside={<span>{sources.length} web sources, {calcs.length} calculated values</span>}>
        {sources.length > 0 && (
          <div className="scroll-x">
            <table className="text-[13px]">
              <thead>
                <tr>
                  <th>Ref</th>
                  <th>Source</th>
                  <th>Type</th>
                  <th>Published</th>
                  <th>Retrieved</th>
                </tr>
              </thead>
              <tbody>
                {sources.map(([id, e]) => (
                  <tr key={id} id={`ev-${id}`}>
                    <td>{id}</td>
                    <td>
                      <a href={e.url} target="_blank" rel="noopener noreferrer nofollow">
                        {e.title}
                      </a>
                      <div className="text-xs text-ink-2">{e.snippet}</div>
                    </td>
                    <td>
                      <Badge tone={e.source_type === 'primary' ? 'good' : 'neutral'}>{e.source_type === 'primary' ? 'Primary' : 'Secondary'}</Badge>
                    </td>
                    <td>{e.published_at ? fmtDate(e.published_at) : 'Not supplied'}</td>
                    <td>{fmtDateTime(e.retrieved_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <details className="px-4 py-3">
          <summary className="cursor-pointer font-medium">Calculated values and quotes given as evidence ({calcs.length})</summary>
          <div className="scroll-x mt-2">
            <table className="text-[13px]">
              <thead>
                <tr>
                  <th>Value</th>
                  <th className="r">Figure</th>
                  <th>Formula or source</th>
                  <th>As of</th>
                </tr>
              </thead>
              <tbody>
                {calcs.map(([id, e]) => (
                  <tr key={id} id={`ev-${id}`}>
                    <td>{e.label}</td>
                    <td className="r num">{typeof e.value === 'number' ? fmtNum(e.value) : e.value}</td>
                    <td className="text-ink-2">{e.kind === 'quote' ? `${PROVIDER_LABEL[e.provider] ?? e.provider} quote (${e.status})` : e.formula}</td>
                    <td className="text-ink-2">{e.as_of ? fmtDate(e.as_of) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </Panel>
      <p className="m-0 max-w-[90ch] text-xs text-ink-2">{d.disclaimer}</p>
    </div>
  )
}

export function Research() {
  const { ref } = useParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const refId = ref ? encodeURIComponent(ref) : ''
  const inst = useApi<any>(['instrument', ref], ref ? `/instruments/${refId}` : null, { quote: false })
  const latest = useApi<any>(['research', ref], ref ? `/instruments/${refId}/research` : null)
  const gen = useAction<void, any>(() => post('/research/stock', { instrument: ref }), [])
  const run = async () => {
    const d = await gen.mutateAsync()
    qc.setQueryData(['research', ref, null], d)
  }
  return (
    <>
      {!ref && <PageHeader title="Research board" lede="Evidence-backed positives, negatives, risks and conditions for one instrument. Every statement links to the value or source it rests on." refresh={<RefreshButton keys={[['research'], ['sources']]} hint="Reload research reports and source status" />} />}
      <div className="mb-4 max-w-md">
        <InstrumentSearch equitiesOnly placeholder="Choose an instrument to research" onPick={(i) => nav(`/research/${i.exchange}:${encodeURIComponent(i.symbol)}`)} />
      </div>
      {!ref ? (
        <Panel>
          <EmptyState title="Pick an instrument to start">Search by symbol or company name above. Exchange and symbol are always shown so the choice is unambiguous.</EmptyState>
        </Panel>
      ) : (
        <>
          <Async q={inst} rows={3}>
            {(d) => <QuoteHeader d={d} />}
          </Async>
          <p className="mb-3 text-[13px]">
            <Link to={`/instrument/${ref}`}>Instrument detail</Link>
          </p>
          {gen.error && <ErrorState error={gen.error} retry={run} />}
          <Async q={latest} rows={10}>
            {(d) =>
              d.report == null ? (
                <Panel>
                  <EmptyState
                    title="No research generated yet"
                    action={
                      <Button variant="primary" onClick={run} busy={gen.isPending}>
                        Generate research
                      </Button>
                    }
                  >
                    This assembles current quotes, computed metrics and any retrieved web sources into a structured board.
                  </EmptyState>
                </Panel>
              ) : (
                <Board d={d} onRegenerate={run} busy={gen.isPending} />
              )
            }
          </Async>
        </>
      )}
    </>
  )
}
