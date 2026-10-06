import { useEffect, useRef, useState } from 'react'
import { ChevronRight, CircleAlert, X } from 'lucide-react'
import type { CallTrace, ToolCall } from './types'

interface Props {
  trace: CallTrace
  onDismiss: () => void
}

const STATUS: Record<CallTrace['status'], string> = {
  idle: 'Idle',
  connecting: 'Connecting',
  live: 'Live',
  ended: 'Call ended',
  error: 'Call failed',
}

export function CallPanel({ trace, onDismiss }: Props) {
  const transcriptEnd = useRef<HTMLLIElement>(null)
  const traceEnd = useRef<HTMLLIElement>(null)
  useEffect(() => transcriptEnd.current?.scrollIntoView({ block: 'nearest' }), [trace.transcript])
  useEffect(() => traceEnd.current?.scrollIntoView({ block: 'nearest' }), [trace.tools.length])

  const done = trace.status === 'ended' || trace.status === 'error'
  return (
    <div className="call">
      <header className="call__head" data-status={trace.status}>
        <span className="lamp" aria-hidden />
        <span className="call__status" role="status">
          {STATUS[trace.status]}
        </span>
        {trace.activeNode && trace.status === 'live' && <span className="call__node mono">{trace.activeNode}</span>}
        {done && (
          <button type="button" className="icon-btn" onClick={onDismiss} aria-label="Clear call trace">
            <X size={15} strokeWidth={1.75} />
          </button>
        )}
      </header>

      {trace.error && (
        <p className="call__error">
          <CircleAlert size={15} strokeWidth={1.75} aria-hidden />
          {trace.error}
        </p>
      )}

      <section className="call__section call__transcript" aria-label="Transcript">
        <h2>Transcript</h2>
        {trace.transcript.length === 0 ? (
          <p className="quiet">{trace.status === 'connecting' ? 'Opening the line…' : 'Say hello. The agent speaks first.'}</p>
        ) : (
          <ol className="lines">
            {trace.transcript.map((l) => (
              <li key={l.id} className="line" data-who={l.who} data-final={l.final || undefined}>
                <span className="line__who">{l.who === 'caller' ? 'Caller' : 'Agent'}</span>
                <span className="line__text">{l.text}</span>
              </li>
            ))}
            <li ref={transcriptEnd} aria-hidden className="sentinel" />
          </ol>
        )}
      </section>

      <section className="call__section call__trace" aria-label="Tool calls">
        <h2>
          Tool calls <span className="count">{trace.tools.length}</span>
        </h2>
        {trace.tools.length === 0 ? (
          <p className="quiet">Lookups appear here as the agent runs them.</p>
        ) : (
          <ol className="directory">
            <li className="directory__cols" aria-hidden>
              <span>Tool</span>
              <span>Node</span>
              <span className="num">Rows</span>
              <span className="num">ms</span>
            </li>
            {trace.tools.map((c) => (
              <ToolRow key={c.id} call={c} />
            ))}
            <li ref={traceEnd} aria-hidden className="sentinel" />
          </ol>
        )}
      </section>
    </div>
  )
}

function ToolRow({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false)
  const pending = call.ms === undefined
  const state = pending ? 'pending' : call.ok ? 'ok' : 'failed'
  return (
    <li className="entry" data-state={state}>
      <button type="button" className="entry__row" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="entry__tool">
          <ChevronRight size={12} strokeWidth={2} className="entry__chev" aria-hidden />
          <span className="mono">{call.tool}</span>
        </span>
        <span className="entry__node mono">{call.node}</span>
        <span className="num">{pending ? '·' : call.rows ?? '—'}</span>
        <span className="num">{pending ? <Ticker since={call.startedAt} /> : <Readout to={call.ms!} />}</span>
      </button>
      <p className="entry__args mono">{summarize(call.args)}</p>
      {!pending && !call.ok && <p className="entry__why">{reason(call.result)}</p>}
      {open && (
        <pre className="entry__json mono">{JSON.stringify({ args: call.args, result: call.result }, null, 2)}</pre>
      )}
    </li>
  )
}

function summarize(args: Record<string, unknown>) {
  const parts = Object.entries(args).map(([k, v]) => `${k}=${typeof v === 'string' ? `"${v}"` : JSON.stringify(v)}`)
  return parts.length ? parts.join('  ') : 'no arguments'
}

function reason(result: unknown) {
  const r = result as { error?: string; problems?: string[] } | undefined
  return r?.problems?.join(' ') ?? r?.error ?? 'Failed.'
}

/** Counts up while a lookup is in flight. */
function Ticker({ since }: { since: number }) {
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    let raf = 0
    const tick = () => {
      setNow(performance.now())
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])
  return <>{Math.max(0, now - since).toFixed(0)}</>
}

/** Rolls the readout up to the measured server time once the result lands. */
function Readout({ to }: { to: number }) {
  const [shown, setShown] = useState(0)
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setShown(to)
      return
    }
    const start = performance.now()
    let raf = 0
    const step = (t: number) => {
      const p = Math.min(1, (t - start) / 320)
      setShown(to * (1 - Math.pow(1 - p, 3)))
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [to])
  return <>{shown < 10 ? shown.toFixed(2) : shown.toFixed(1)}</>
}
