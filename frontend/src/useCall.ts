import { useCallback, useEffect, useRef, useState } from 'react'
import { PipecatClient, RTVIEvent } from '@pipecat-ai/client-js'
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport'
import type { CallEvent, CallTrace, TranscriptLine } from './types'

const EMPTY: CallTrace = { status: 'idle', visited: [], taken: {}, transcript: [], tools: [] }

/** Places a browser test call against an agent and folds the bot's events into a trace. */
export function useCall(edgeIndex: (source: string, fn: string) => number) {
  const [trace, setTrace] = useState<CallTrace>(EMPTY)
  const client = useRef<PipecatClient | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const indexRef = useRef(edgeIndex)
  indexRef.current = edgeIndex

  const onEvent = useCallback((e: CallEvent) => {
    setTrace((t) => {
      switch (e.type) {
        case 'node_entered':
          return { ...t, activeNode: e.node, visited: t.visited.includes(e.node) ? t.visited : [...t.visited, e.node] }
        case 'edge_taken': {
          const key = `${e.source}>${indexRef.current(e.source, e.function)}`
          return { ...t, taken: { ...t.taken, [key]: (t.taken[key] ?? 0) + 1 } }
        }
        case 'tool_started':
          return { ...t, tools: [...t.tools, { id: e.id, node: e.node, tool: e.tool, args: e.args, startedAt: performance.now() }] }
        case 'tool_finished':
          return {
            ...t,
            tools: t.tools.map((c) => (c.id === e.id ? { ...c, ms: e.ms, rows: e.rows, ok: e.ok, result: e.result } : c)),
          }
      }
    })
  }, [])

  const say = useCallback((who: TranscriptLine['who'], text: string, final: boolean) => {
    setTrace((t) => {
      const last = t.transcript[t.transcript.length - 1]
      // The caller stays on one line until the agent answers: a short pause commits a segment,
      // and the next one is added after it. Partials only replace the uncommitted tail.
      if (last && who === 'caller' && last.who === 'caller') {
        if (!text.trim()) return t
        const committed = last.final ? last.text : (last.committed ?? '')
        const merged = `${committed} ${text}`.trim()
        const line = { ...last, text: merged, final, committed: final ? merged : committed }
        return { ...t, transcript: [...t.transcript.slice(0, -1), line] }
      }
      // Agent output appends to its turn until the turn closes.
      if (last && who === 'agent' && last.who === 'agent' && !last.final) {
        return { ...t, transcript: [...t.transcript.slice(0, -1), { ...last, text: `${last.text} ${text}`.trim() }] }
      }
      if (!text.trim()) return t
      return { ...t, transcript: [...t.transcript, { id: crypto.randomUUID(), who, text, final }] }
    })
  }, [])

  const closeAgentTurn = useCallback(() => {
    setTrace((t) => {
      const last = t.transcript[t.transcript.length - 1]
      if (!last || last.who !== 'agent' || last.final) return t
      return { ...t, transcript: [...t.transcript.slice(0, -1), { ...last, final: true }] }
    })
  }, [])

  const start = useCallback(
    async (agentId: string) => {
      setTrace({ ...EMPTY, status: 'connecting' })
      const pc = new PipecatClient({
        transport: new SmallWebRTCTransport(),
        enableMic: true,
        enableCam: false,
        callbacks: {
          onServerMessage: (data: unknown) => onEvent(data as CallEvent),
          onUserTranscript: (d) => say('caller', d.text, d.final),
          onBotOutput: (d) => {
            // Each sentence arrives once as "new", then again as "in-progress"/"completed"
            // while the words are spoken. Take it the first time only.
            if (d.spoken_status && d.spoken_status !== 'new') return
            if (d.aggregated_by === 'sentence' || !d.aggregated_by) say('agent', d.text, false)
          },
          onBotStoppedSpeaking: closeAgentTurn,
          onTrackStarted: (track, participant) => {
            if (participant?.local || track.kind !== 'audio') return
            if (!audio.current) audio.current = new Audio()
            audio.current.srcObject = new MediaStream([track])
            void audio.current.play()
          },
          onDisconnected: () => setTrace((t) => ({ ...t, status: t.status === 'error' ? 'error' : 'ended' })),
        },
      })
      pc.on(RTVIEvent.BotReady, () => setTrace((t) => ({ ...t, status: 'live' })))
      client.current = pc
      try {
        await pc.startBotAndConnect({ endpoint: '/start', requestData: { transport: 'webrtc', body: { agent: agentId } } })
      } catch (err) {
        const message =
          err instanceof DOMException && err.name === 'NotAllowedError'
            ? 'Microphone access was blocked. Allow it in the browser and try again.'
            : `Couldn't connect to the voice backend: ${err instanceof Error ? err.message : String(err)}`
        setTrace((t) => ({ ...t, status: 'error', error: message }))
        await pc.disconnect().catch(() => {})
      }
    },
    [onEvent, say, closeAgentTurn],
  )

  const stop = useCallback(async () => {
    await client.current?.disconnect()
    client.current = null
  }, [])

  const reset = useCallback(() => setTrace(EMPTY), [])

  useEffect(() => () => void client.current?.disconnect(), [])

  return { trace, start, stop, reset }
}
