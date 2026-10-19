import { useEffect, useId, useState } from 'react'
import { useApi } from '../api/client'

export type InstrumentLite = { id: number; exchange: string; symbol: string; name: string | null; sector: string | null; instrument_type: string }

/** Combobox over the instrument master. Results always show exchange and symbol, so a pick is unambiguous. */
export function InstrumentSearch({ onPick, placeholder, equitiesOnly }: { onPick: (i: InstrumentLite) => void; placeholder?: string; equitiesOnly?: boolean }) {
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()
  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 200)
    return () => clearTimeout(t)
  }, [text])
  const res = useApi<{ items: InstrumentLite[] }>(['instrument-search'], q.length >= 1 ? '/instruments' : null, { q, page_size: 8, instrument_type: equitiesOnly ? 'EQ' : undefined })
  const items = res.data?.items ?? []
  const pick = (i: InstrumentLite) => {
    onPick(i)
    setText('')
    setOpen(false)
  }
  return (
    <div className="relative">
      <input
        role="combobox"
        aria-expanded={open && items.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-label={placeholder ?? 'Find an instrument'}
        className="field w-full"
        placeholder={placeholder ?? 'Find an instrument'}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
          setActive(0)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setActive((a) => Math.min(a + 1, items.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((a) => Math.max(a - 1, 0))
          } else if (e.key === 'Enter' && items[active]) {
            e.preventDefault()
            pick(items[active])
          } else if (e.key === 'Escape') setOpen(false)
        }}
      />
      {open && q.length >= 1 && (
        <ul id={listId} role="listbox" className="absolute z-30 mt-1 max-h-80 w-full list-none overflow-auto rounded-md border border-rule-strong bg-surface p-1 shadow-lg">
          {res.isLoading && <li className="px-3 py-2 text-ink-2">Searching…</li>}
          {!res.isLoading && items.length === 0 && <li className="px-3 py-2 text-ink-2">No instrument matches “{q}” in the tracked universe.</li>}
          {items.map((i, n) => (
            <li
              key={i.id}
              role="option"
              aria-selected={n === active}
              onMouseDown={(e) => {
                e.preventDefault()
                pick(i)
              }}
              onMouseEnter={() => setActive(n)}
              className={`flex cursor-pointer items-baseline justify-between gap-3 rounded px-3 py-1.5 ${n === active ? 'bg-accent-soft' : ''}`}
            >
              <span>
                <span className="font-semibold">{i.symbol}</span> <span className="text-ink-2">{i.name}</span>
              </span>
              <span className="whitespace-nowrap text-xs text-ink-3">
                {i.exchange} · {i.instrument_type === 'INDEX' ? 'Index' : (i.sector ?? 'Equity')}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
