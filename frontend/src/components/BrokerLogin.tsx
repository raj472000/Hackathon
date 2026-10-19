import { useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError, post } from '../api/client'
import { ago } from '../lib/format'
import { Button, Dialog, Notice, StateBadge } from './ui'

// Broker (Zerodha Kite MCP) login. Kite binds the approval to the API's MCP session; the API keeps that session across
// restarts and re-confirms it every few minutes, so a login normally lasts until Kite's own daily expiry. When Kite does
// end it, every page offers "Log in again" and the prompt reopens, even if it was dismissed earlier.
const DISMISS_KEY = 'broker-login-dismissed'

export type KiteState = {
  state: string
  login_url?: string | null
  reason?: string
  user_id?: string
  backfill_job?: number
  note?: string
  checked_at?: string
  session_lost_at?: string | null
}
type Ctx = {
  state: KiteState | null
  lost: boolean
  checking: boolean
  error: string | null
  open: () => void
  login: () => Promise<void>
  check: (afterApproval?: boolean) => Promise<void>
  disconnect: () => Promise<void>
}
const BrokerCtx = createContext<Ctx>({
  state: null,
  lost: false,
  checking: false,
  error: null,
  open: () => {},
  login: async () => {},
  check: async () => {},
  disconnect: async () => {},
})
export const useBroker = () => useContext(BrokerCtx)

/** Opens Kite's login with a link issued at this moment (Kite rejects old links as "invalid authorize session").
 * The tab is opened synchronously in the click so pop-up blockers allow it, then pointed at the fresh link. */
export async function openKiteLogin(): Promise<KiteState> {
  const tab = window.open('', '_blank')
  try {
    const r = await post('/sources/broker/login-link')
    const k: KiteState = r.kite_mcp
    if (k.login_url) {
      if (tab) {
        tab.opener = null
        tab.location.href = k.login_url
      } else window.open(k.login_url, '_blank', 'noopener')
    } else tab?.close()
    return k
  } catch (e) {
    tab?.close()
    throw e
  }
}

function readDismissed() {
  try {
    return sessionStorage.getItem(DISMISS_KEY) === '1'
  } catch {
    return false
  }
}

function writeDismissed(on: boolean) {
  try {
    if (on) sessionStorage.setItem(DISMISS_KEY, '1')
    else sessionStorage.removeItem(DISMISS_KEY)
  } catch {
    /* storage unavailable: the prompt simply returns on the next load */
  }
}

/** `auth` is the Kite session state from GET /sources/status, which the layout polls every minute and which the API
 * re-confirms with Kite on each poll, so a login done in another tab or a session Kite ended shows up here. */
export function BrokerLoginProvider({ enabled, auth, children }: { enabled: boolean; auth?: KiteState | null; children: ReactNode }) {
  const qc = useQueryClient()
  const [state, setState] = useState<KiteState | null>(null)
  const [dismissed, setDismissed] = useState(readDismissed)
  const [justConnected, setJustConnected] = useState(false)
  const [lost, setLost] = useState(false)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const prev = useRef<string | undefined>(undefined)

  const apply = (k: KiteState, fromApproval = false) => {
    const was = prev.current
    prev.current = k.state
    setState(k)
    if (k.state === 'authorized') {
      setLost(false)
      if (fromApproval || (was && was !== 'authorized')) setJustConnected(true)
    } else if (k.state === 'login_required' && was === 'authorized') {
      // The session ended while the app was in use: say so and offer the login again, even if it was dismissed.
      setLost(true)
      setDismissed(false)
      writeDismissed(false)
    }
  }

  useEffect(() => {
    if (auth?.state && auth.state !== 'unknown' && auth.state !== prev.current) apply(auth)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth?.state, auth?.checked_at])

  const check = async (afterApproval = false) => {
    setChecking(true)
    setError(null)
    try {
      const r = await post('/sources/broker/login')
      const k: KiteState = r.kite_mcp
      apply(k, afterApproval)
      if (afterApproval && k.state === 'login_required') setError('Kite has not confirmed the approval yet. Finish it in the Kite tab, then check again.')
      qc.invalidateQueries({ queryKey: ['sources'] })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not reach the broker source.')
    } finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    if (enabled && !auth?.state) check()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled])

  const login = async () => {
    setError(null)
    try {
      const k = await openKiteLogin()
      apply(k)
      if (k.state !== 'authorized' && !k.login_url) setError(`Kite did not return a login link${k.reason ? ` (${k.reason})` : ''}. Try again in a moment.`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not get a login link from Kite.')
    }
  }

  const disconnect = async () => {
    if (!window.confirm('Disconnect Zerodha Kite? Broker data stops until you log in again.')) return
    setError(null)
    try {
      const r = await post('/sources/broker/logout')
      prev.current = undefined // a deliberate logout is not a lost session
      setLost(false)
      setState(r.kite_mcp)
      prev.current = r.kite_mcp.state
      qc.invalidateQueries({ queryKey: ['sources'] })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not disconnect from Kite.')
    }
  }

  const close = () => {
    setJustConnected(false)
    if (state?.state === 'login_required') {
      setDismissed(true)
      writeDismissed(true)
    }
  }
  const reopen = () => {
    setDismissed(false)
    writeDismissed(false)
    if (state?.state !== 'login_required') check()
  }
  const show = enabled && ((state?.state === 'login_required' && !dismissed) || justConnected)

  return (
    <BrokerCtx.Provider value={{ state, lost, checking, error, open: reopen, login, check, disconnect }}>
      {children}
      <Dialog
        open={show}
        title={justConnected ? 'Zerodha Kite is connected' : lost ? 'Kite logged you out' : 'Connect Zerodha Kite for broker data'}
        onClose={close}
        footer={
          justConnected ? (
            <Button variant="primary" onClick={close}>
              Done
            </Button>
          ) : (
            <>
              <Button variant="quiet" onClick={close} data-testid="broker-skip">
                Continue without broker data
              </Button>
              <Button variant="primary" onClick={() => check(true)} busy={checking} data-testid="broker-check">
                I have approved it
              </Button>
            </>
          )
        }
      >
        <div data-testid="broker-login">
          {justConnected ? (
            <div className="space-y-2 text-[14px]">
              <p className="m-0">
                Signed in{state?.user_id ? ` as ${state.user_id}` : ''}. Broker data now fills what NSE does not provide: index quotes and price history beyond NSE&apos;s window.
              </p>
              <p className="m-0 text-ink-2">The login is kept when the app restarts. Kite itself ends it once a day, and you are asked again then.</p>
              {state?.backfill_job && (
                <p className="m-0 text-ink-2">Fetching full history for instruments with short or stale prices in the background (job {state.backfill_job}). Scores and the rotation graph update when it finishes.</p>
              )}
            </div>
          ) : (
            <div className="space-y-3 text-[14px]">
              {lost ? (
                <Notice tone="warn" title="The Kite session has ended.">
                  {state?.reason ?? 'Kite no longer accepts the earlier approval (it expires once a day).'} Log in again to keep broker data flowing.
                </Notice>
              ) : (
                <p className="m-0">
                  Kite fills the gaps in NSE&apos;s data: index quotes, and price history NSE does not cover (some stocks have only a few months there).
                </p>
              )}
              <ol className="m-0 list-decimal space-y-1 pl-5">
                <li>
                  <button type="button" onClick={login} className="font-semibold text-link underline-offset-2 hover:underline" data-testid="broker-login-link">
                    Open the Kite login page
                  </button>{' '}
                  and sign in with your Zerodha account. A new link is requested each time, so it is always valid.
                </li>
                <li>Approve access, then come back and choose “I have approved it”.</li>
              </ol>
              <p className="m-0 text-[12.5px] text-ink-2">
                This app never sees your password and cannot place, modify or cancel orders; only market-data tools are allowed.
              </p>
              {error && <Notice tone="warn">{error}</Notice>}
            </div>
          )}
        </div>
      </Dialog>
    </BrokerCtx.Provider>
  )
}

/** Header button, on every screen size, whenever broker data needs a login. */
export function BrokerConnectButton() {
  const { state, lost, open } = useBroker()
  if (state?.state !== 'login_required') return null
  return (
    <button onClick={open} className="whitespace-nowrap rounded-full border border-accent px-2.5 py-0.5 text-xs font-medium text-ink hover:bg-accent-soft" data-testid="broker-connect">
      {lost ? 'Kite: log in again' : 'Log in to Kite'}
    </button>
  )
}

/** The Kite session on the Data status page: always shows the current state and the action that fits it. */
export function KiteSessionPanel() {
  const { state, lost, checking, error, login, check, disconnect } = useBroker()
  const s = state?.state
  return (
    <div className="space-y-2.5 px-4 py-3" data-testid="kite-session">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        {s ? <StateBadge state={s} label={s === 'authorized' ? 'Logged in' : s === 'login_required' ? (lost ? 'Logged out by Kite' : 'Login needed') : s.replace(/_/g, ' ')} /> : <span className="text-ink-2">Checking…</span>}
        {state?.user_id && <span>as {state.user_id}</span>}
        {state?.checked_at && <span className="text-ink-3">checked {ago(state.checked_at)}</span>}
      </div>
      {state?.reason && s !== 'authorized' && <p className="m-0 text-[13px] text-ink-2">{state.reason}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {s === 'authorized' ? (
          <>
            <Button onClick={() => check()} busy={checking}>
              Check session
            </Button>
            <Button variant="quiet" onClick={disconnect} data-testid="kite-disconnect">
              Disconnect Kite
            </Button>
          </>
        ) : (
          <>
            <Button variant="primary" onClick={login} data-testid="kite-login">
              {lost ? 'Log in again' : 'Log in to Kite'}
            </Button>
            <Button onClick={() => check(true)} busy={checking} data-testid="kite-check">
              I have approved it
            </Button>
          </>
        )}
      </div>
      <p className="m-0 text-xs text-ink-3">
        The login is kept across app restarts and re-confirmed every few minutes. Kite ends it once a day; this panel and the header then offer “Log in again”.
        Read-only market data only; orders are blocked.
      </p>
      {error && <Notice tone="warn">{error}</Notice>}
    </div>
  )
}
