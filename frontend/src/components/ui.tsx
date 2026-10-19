import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { ApiError, waitForJob } from '../api/client'
import { MISSING, STATUS_LABEL } from '../lib/format'

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

export function Button({ variant = 'secondary', busy, className, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'quiet' | 'danger' | 'accent'; busy?: boolean }) {
  const styles = {
    primary: 'bg-rail text-white border-rail hover:bg-rail-2',
    accent: 'bg-accent text-rail border-accent font-semibold hover:brightness-105',
    secondary: 'bg-surface text-ink border-rule-strong hover:border-ink-3',
    quiet: 'bg-transparent text-link border-transparent hover:bg-surface-2',
    danger: 'bg-surface text-loss border-loss/50 hover:bg-danger-soft',
  }[variant]
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      aria-busy={busy || undefined}
      className={cx('inline-flex min-h-[34px] items-center justify-center gap-2 rounded-[7px] border px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50', styles, className)}
    >
      {busy && <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />}
      {children}
    </button>
  )
}

const TONES: Record<string, string> = {
  neutral: 'bg-surface-2 text-ink-2 border-rule',
  good: 'bg-good-soft text-gain border-transparent',
  warn: 'bg-warn-soft text-warn border-transparent',
  bad: 'bg-danger-soft text-loss border-transparent',
  info: 'bg-info-soft text-link border-transparent',
  accent: 'bg-accent-soft text-ink border-accent/40',
}

export function Badge({ tone = 'neutral', children, title }: { tone?: keyof typeof TONES | string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cx('inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs font-medium', TONES[tone] ?? TONES.neutral)}>
      {children}
    </span>
  )
}

/** Labels are words plus a shape, so state never depends on colour alone. */
export function stateTone(state?: string | null): { tone: string; mark: string } {
  switch (state) {
    case 'ok':
    case 'authorized':
    case 'succeeded':
    case 'fresh':
    case 'Favorable':
    case 'Setup favorable':
    case 'High':
      return { tone: 'good', mark: '●' }
    case 'degraded':
    case 'delayed':
    case 'stale':
    case 'login_required':
    case 'running':
    case 'Mixed':
    case 'Watch':
    case 'Medium':
    case 'skipped':
      return { tone: 'warn', mark: '◐' }
    case 'unavailable':
    case 'failed':
    case 'Unfavorable':
    case 'conflict':
      return { tone: 'bad', mark: '▲' }
    default:
      return { tone: 'neutral', mark: '○' }
  }
}

export function StateBadge({ state, label }: { state?: string | null; label?: string }) {
  const { tone, mark } = stateTone(state)
  return (
    <Badge tone={tone}>
      <span aria-hidden>{mark}</span>
      {label ?? STATUS_LABEL[state ?? ''] ?? (state ? state.replace(/_/g, ' ') : 'Unknown')}
    </Badge>
  )
}

export function RiskBadge({ label }: { label?: string | null }) {
  const tone = label === 'Low' ? 'good' : label === 'Moderate' ? 'info' : label === 'High' ? 'warn' : label === 'Very High' ? 'bad' : 'neutral'
  return <Badge tone={tone}>{label ? `${label} risk` : 'Risk unknown'}</Badge>
}

/** A missing value with its reason available on hover and to screen readers. */
export function Missing({ reason, status }: { reason?: string | null; status?: string | null }) {
  const label = STATUS_LABEL[status ?? 'missing'] ?? 'Not available'
  const text = reason ? `${label}: ${reason}` : label
  return (
    <span className="cursor-help text-ink-3" title={text}>
      <span aria-hidden>{MISSING}</span>
      <span className="sr-only">{text}</span>
    </span>
  )
}

export function Panel({ title, aside, children, className, id }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string; id?: string }) {
  return (
    <section id={id} className={cx('panel rounded-[10px] border border-rule bg-surface', className)}>
      {(title || aside) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-rule px-4 py-3">
          {title && <h2 className="text-[15.5px] font-semibold tracking-[-0.01em]">{title}</h2>}
          {aside && <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function PageHeader({ title, lede, actions, refresh }: { title: string; lede?: ReactNode; actions?: ReactNode; refresh?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3 border-b border-rule pb-4">
      <div className="max-w-[72ch]">
        <h1 className="text-[30px] font-semibold leading-[1.1] tracking-[-0.02em]">{title}</h1>
        {lede && <p className="mt-1.5 text-[14.5px] text-ink-2">{lede}</p>}
      </div>
      {(actions || refresh) && (
        <div className="flex flex-wrap items-center gap-2">
          {actions}
          {refresh}
        </div>
      )}
    </div>
  )
}

type RefreshState = { phase: 'idle' | 'job' | 'reload' | 'done' | 'error'; message?: string; at?: Date }

/**
 * One refresh control for every page. `job` (optional) asks the server to fetch from the sources
 * and returns a background job id; the button waits for it. `action` (optional) is any other call
 * to make first. Then every query under `keys` is reloaded from the API.
 */
export function useRefresh({ keys, job, action, onDone }: { keys: unknown[][]; job?: () => Promise<{ job_id: number }>; action?: () => Promise<unknown>; onDone?: (j: any) => void }) {
  const qc = useQueryClient()
  const [st, setSt] = useState<RefreshState>({ phase: 'idle' })
  const run = async () => {
    try {
      let detail: any = null
      if (job) {
        setSt({ phase: 'job', message: 'Fetching latest data from the sources' })
        const { job_id } = await job()
        detail = await waitForJob(job_id)
        if (detail.status === 'failed') throw new ApiError(0, 'job_failed', detail.error ?? 'The refresh job failed. See Data status for details.')
      }
      if (action) {
        setSt({ phase: 'job', message: 'Checking live sources' })
        await action()
      }
      setSt({ phase: 'reload', message: 'Reloading' })
      await Promise.all(keys.map((k) => qc.invalidateQueries({ queryKey: k })))
      onDone?.(detail)
      setSt({ phase: 'done', at: new Date() })
    } catch (e) {
      setSt({ phase: 'error', message: e instanceof Error ? e.message : 'Refresh failed' })
    }
  }
  return { ...st, busy: st.phase === 'job' || st.phase === 'reload', run }
}

export function RefreshButton({ keys, job, action, label = 'Refresh', onDone, hint }: { keys: unknown[][]; job?: () => Promise<{ job_id: number }>; action?: () => Promise<unknown>; label?: string; onDone?: (j: any) => void; hint?: string }) {
  const r = useRefresh({ keys, job, action, onDone })
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-ink-2" role="status" aria-live="polite">
        {(r.phase === 'job' || r.phase === 'reload') && r.message}
        {r.phase === 'done' && r.at && `Updated ${r.at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`}
        {r.phase === 'error' && <span className="text-loss">{r.message}</span>}
      </span>
      <Button onClick={r.run} busy={r.busy} title={hint} data-testid="page-refresh">
        {!r.busy && (
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" />
            <path d="M13.8 2.5v3.2h-3.2" />
          </svg>
        )}
        {label}
      </Button>
    </div>
  )
}

export function Skeleton({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cx('space-y-2 p-4', className)} role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton h-5" style={{ width: `${92 - ((i * 13) % 35)}%` }} />
      ))}
    </div>
  )
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="px-4 py-8 text-center">
      <p className="font-medium">{title}</p>
      {children && <p className="mx-auto mt-1 max-w-[60ch] text-ink-2">{children}</p>}
      {action && <div className="mt-3 flex justify-center gap-2">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const e = error instanceof ApiError ? error : null
  return (
    <div role="alert" className="m-4 rounded-md border border-loss/40 bg-danger-soft px-4 py-3">
      <p className="font-medium text-loss">{e?.status === 429 ? 'Too many requests' : e?.status === 401 ? 'Sign-in needed' : 'This could not be loaded'}</p>
      <p className="mt-0.5">{e?.message ?? 'An unexpected error occurred.'}</p>
      {e?.status === 401 && <p className="mt-1 text-ink-2">Enter the API token on the Data status page.</p>}
      <div className="mt-2 flex items-center gap-3">
        {retry && (
          <Button onClick={retry} variant="secondary">
            Try again
          </Button>
        )}
        {e?.requestId && <span className="text-xs text-ink-2">Request id {e.requestId}</span>}
      </div>
    </div>
  )
}

/** Standard loading / error / content switch for a query. */
export function Async<T>({ q, children, rows }: { q: { data?: T; error: unknown; isLoading: boolean; refetch: () => unknown }; children: (data: T) => ReactNode; rows?: number }) {
  if (q.isLoading) return <Skeleton rows={rows} />
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />
  if (q.data === undefined) return null
  return <>{children(q.data)}</>
}

export function Notice({ tone = 'info', children, title }: { tone?: 'info' | 'warn' | 'bad' | 'good'; title?: string; children: ReactNode }) {
  const c = { info: 'bg-info-soft border-link/25', warn: 'bg-warn-soft border-warn/30', bad: 'bg-danger-soft border-loss/30', good: 'bg-good-soft border-gain/30' }[tone]
  return (
    <div className={cx('rounded-md border px-3 py-2 text-[13px]', c)} role={tone === 'bad' ? 'alert' : 'note'}>
      {title && <span className="font-semibold">{title} </span>}
      {children}
    </div>
  )
}

export function Dialog({ open, title, children, onClose, footer }: { open: boolean; title: string; children: ReactNode; onClose: () => void; footer?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  const id = useId()
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal?.()
    if (!open && d.open) d.close?.()
  }, [open])
  return (
    <dialog
      ref={ref}
      aria-labelledby={id}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className="m-auto w-[min(92vw,640px)] rounded-lg border border-rule-strong bg-surface p-0 text-ink backdrop:bg-black/40"
    >
      {open && (
        <>
          <header className="flex items-center justify-between border-b border-rule px-4 py-3">
            <h2 id={id} className="text-base font-semibold">
              {title}
            </h2>
            <button onClick={onClose} aria-label="Close" className="rounded px-2 py-1 text-ink-2 hover:bg-surface-2">
              ✕
            </button>
          </header>
          <div className="max-h-[65vh] overflow-y-auto px-4 py-3">{children}</div>
          {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-rule px-4 py-3">{footer}</footer>}
        </>
      )}
    </dialog>
  )
}

export function Confirm({ open, title, body, confirmLabel, onConfirm, onClose, busy, danger }: { open: boolean; title: string; body: ReactNode; confirmLabel: string; onConfirm: () => void; onClose: () => void; busy?: boolean; danger?: boolean }) {
  return (
    <Dialog
      open={open}
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} busy={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-ink-2">{body}</div>
    </Dialog>
  )
}

export function Tabs({ tabs, value, onChange, label }: { tabs: Array<{ id: string; label: string }>; value: string; onChange: (id: string) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-rule">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cx('-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium', value === t.id ? 'border-accent text-ink' : 'border-transparent text-ink-2 hover:text-ink')}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Kv({ k, children, hint }: { k: string; children: ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-rule py-1.5 last:border-0">
      <dt className="text-ink-2" title={hint}>
        {k}
      </dt>
      <dd className="num m-0 text-right font-medium">{children}</dd>
    </div>
  )
}
