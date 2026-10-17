import { useCallback, useEffect, useMemo, useState } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import { Phone, PhoneOff, Plus, Save, SquarePlus } from 'lucide-react'
import { api } from './api'
import { Graph } from './Graph'
import { Inspector } from './Inspector'
import { CallPanel } from './CallPanel'
import { useCall } from './useCall'
import { validate } from './validate'
import prosperLogo from './assets/prosper-logo.svg'
import type { Agent, AgentNode, AgentSummary, Edge, Selection, ToolInfo } from './types'

const BLANK_AGENT: Agent = {
  name: 'Untitled agent',
  initial_node: 'greeting',
  persona: 'You are a helpful voice assistant. Your replies are spoken aloud, so keep them short and avoid lists or symbols.',
  voice_id: '21m00Tcm4TlvDq8ikWAM',
  model: 'gpt-4o',
  nodes: [
    {
      name: 'greeting',
      task_messages: [{ role: 'developer', content: 'Greet the caller and ask how you can help.' }],
      edges: [],
      end: true,
      ui: { position: { x: 0, y: 0 } },
    },
  ],
}

export default function App() {
  const [agents, setAgents] = useState<AgentSummary[]>([])
  const [agentId, setAgentId] = useState<string | null>(null)
  const [agent, setAgent] = useState<Agent | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [selection, setSelection] = useState<Selection>({ kind: 'agent' })

  const edgeIndex = useCallback(
    (source: string, fn: string) => agent?.nodes.find((n) => n.name === source)?.edges.findIndex((e) => e.function === fn) ?? -1,
    [agent],
  )
  const { trace, start, stop, reset } = useCall(edgeIndex)
  const inCall = trace.status === 'connecting' || trace.status === 'live'
  const showTrace = trace.status !== 'idle'

  const refreshList = useCallback(async () => {
    const list = await api.listAgents()
    setAgents(list)
    return list
  }, [])

  const open = useCallback(async (id: string) => {
    const a = await api.getAgent(id)
    setAgentId(id)
    setAgent(a)
    setDirty(false)
    setSelection({ kind: 'agent' })
  }, [])

  useEffect(() => {
    Promise.all([refreshList(), api.tools()])
      .then(([list, t]) => {
        setTools(t)
        const first = list.find((a) => a.id === 'clinic-scheduler') ?? list[0]
        if (first) return open(first.id)
      })
      .catch(() => setLoadError('The backend isn’t answering. Start it with “make run” and reload.'))
  }, [refreshList, open])

  const problems = useMemo(() => (agent ? validate(agent, tools.map((t) => t.name)) : []), [agent, tools])
  const flagged = useMemo(() => new Set(problems.flatMap((p) => (p.node ? [p.node] : []))), [problems])

  const edit = useCallback((fn: (a: Agent) => Agent) => {
    setAgent((a) => (a ? fn(a) : a))
    setDirty(true)
  }, [])

  const patchNode = useCallback(
    (name: string, patch: Partial<AgentNode>) => edit((a) => ({ ...a, nodes: a.nodes.map((n) => (n.name === name ? { ...n, ...patch } : n)) })),
    [edit],
  )

  const handlers = {
    onAgent: (patch: Partial<Agent>) => edit((a) => ({ ...a, ...patch })),
    onNode: patchNode,
    onRenameNode: (from: string, to: string) => {
      edit((a) => ({
        ...a,
        initial_node: a.initial_node === from ? to : a.initial_node,
        nodes: a.nodes.map((n) => ({
          ...n,
          name: n.name === from ? to : n.name,
          edges: n.edges.map((e) => (e.target === from ? { ...e, target: to } : e)),
        })),
      }))
      setSelection({ kind: 'node', name: to })
    },
    onEdge: (source: string, index: number, patch: Partial<Edge>) =>
      edit((a) => ({
        ...a,
        nodes: a.nodes.map((n) => (n.name === source ? { ...n, edges: n.edges.map((e, i) => (i === index ? { ...e, ...patch } : e)) } : n)),
      })),
    onDeleteNode: (name: string) => {
      edit((a) => ({
        ...a,
        nodes: a.nodes.filter((n) => n.name !== name).map((n) => ({ ...n, edges: n.edges.filter((e) => e.target !== name) })),
      }))
      setSelection({ kind: 'agent' })
    },
    onDeleteEdge: (source: string, index: number) => {
      edit((a) => ({ ...a, nodes: a.nodes.map((n) => (n.name === source ? { ...n, edges: n.edges.filter((_, i) => i !== index) } : n)) }))
      setSelection({ kind: 'node', name: source })
    },
  }

  const onMove = useCallback(
    (name: string, position: { x: number; y: number }) => patchNode(name, { ui: { position: { x: Math.round(position.x), y: Math.round(position.y) } } }),
    [patchNode],
  )

  const onConnect = useCallback(
    (source: string, target: string) => {
      let index = 0
      edit((a) => ({
        ...a,
        nodes: a.nodes.map((n) => {
          if (n.name !== source) return n
          index = n.edges.length
          let fn = `go_to_${target}`
          for (let k = 2; n.edges.some((e) => e.function === fn); k++) fn = `go_to_${target}_${k}`
          return { ...n, end: false, edges: [...n.edges, { function: fn, description: `Move on to ${target}.`, target, properties: {}, required: [] }] }
        }),
      }))
      setSelection({ kind: 'edge', source, index })
    },
    [edit],
  )

  const addNode = () => {
    if (!agent) return
    let name = 'new_node'
    for (let k = 2; agent.nodes.some((n) => n.name === name); k++) name = `new_node_${k}`
    const xs = agent.nodes.map((n) => n.ui?.position?.x ?? 0)
    const position = { x: (xs.length ? Math.max(...xs) : -340) + 340, y: 0 }
    edit((a) => ({
      ...a,
      nodes: [...a.nodes, { name, task_messages: [{ role: 'developer', content: '' }], edges: [], end: true, tools: [], ui: { position } }],
    }))
    setSelection({ kind: 'node', name })
  }

  const save = useCallback(async () => {
    if (!agent || !agentId) return
    setSaving(true)
    setSaveError(null)
    try {
      await api.saveAgent(agentId, agent)
      setDirty(false)
      await refreshList()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }, [agent, agentId, refreshList])

  const newAgent = async () => {
    if (dirty && !confirm('Discard unsaved changes to this agent?')) return
    let id = 'untitled-agent'
    for (let k = 2; agents.some((a) => a.id === id); k++) id = `untitled-agent-${k}`
    await api.saveAgent(id, BLANK_AGENT)
    await refreshList()
    await open(id)
    setSelection({ kind: 'node', name: 'greeting' })
  }

  const switchTo = async (id: string) => {
    if (id === agentId || inCall) return
    if (dirty && !confirm('Discard unsaved changes to this agent?')) return
    reset()
    await open(id)
  }

  const call = async () => {
    if (!agentId) return
    if (inCall) return stop()
    if (dirty) await save()
    setSelection({ kind: 'agent' })
    await start(agentId)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault()
        if (!inCall) void save()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [save, inCall])

  const canCall = !!agent && problems.length === 0 && !saving

  return (
    <div className="shell" data-call={trace.status}>
      <nav className="rail" aria-label="Agents">
        <div className="brand">
          <img className="brand__logo" src={prosperLogo} alt="Prosper" />
          <span className="brand__sub">Agent builder</span>
        </div>

        <button type="button" className="call-btn" data-live={inCall || undefined} onClick={call} disabled={!inCall && !canCall}>
          {inCall ? <PhoneOff size={17} strokeWidth={2} aria-hidden /> : <Phone size={17} strokeWidth={2} aria-hidden />}
          {trace.status === 'connecting' ? 'Connecting…' : inCall ? 'Hang up' : 'Test call'}
        </button>
        {!inCall && agent && problems.length > 0 && <p className="rail__hint">Fix {problems.length === 1 ? 'the check' : `${problems.length} checks`} before calling.</p>}

        <div className="rail__group">
          <h2 className="rail__label">Agents</h2>
          <ul className="agent-list">
            {agents.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  className="agent-item"
                  aria-current={a.id === agentId || undefined}
                  onClick={() => switchTo(a.id)}
                  disabled={inCall && a.id !== agentId}
                >
                  <span className="agent-item__name">{a.name}</span>
                  <span className="agent-item__meta">{a.nodes} nodes</span>
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="rail-btn" onClick={newAgent} disabled={inCall}>
            <Plus size={15} strokeWidth={2} aria-hidden />
            New agent
          </button>
        </div>
      </nav>

      <main className="stage">
        <header className="toolbar">
          <h1 className="toolbar__title">{agent?.name ?? 'Loading…'}</h1>
          <span className="toolbar__id mono">{agentId}.json</span>
          <span className="toolbar__state" data-dirty={dirty || undefined}>
            {saveError ? <span className="error">{saveError}</span> : saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'Saved'}
          </span>
          <div className="toolbar__actions">
            <button type="button" className="btn" onClick={addNode} disabled={!agent || inCall}>
              <SquarePlus size={15} strokeWidth={1.75} aria-hidden />
              Add node
            </button>
            <button type="button" className="btn btn--solid" onClick={save} disabled={!dirty || saving || inCall}>
              <Save size={15} strokeWidth={1.75} aria-hidden />
              Save
            </button>
          </div>
        </header>
        <div className="canvas">
          {loadError ? (
            <p className="canvas__empty">{loadError}</p>
          ) : agent ? (
            <ReactFlowProvider>
              <Graph
                key={agentId}
                agent={agent}
                trace={trace}
                selection={selection}
                flagged={flagged}
                locked={inCall}
                onSelect={setSelection}
                onMove={onMove}
                onConnect={onConnect}
                onDeleteNode={handlers.onDeleteNode}
                onDeleteEdge={handlers.onDeleteEdge}
              />
            </ReactFlowProvider>
          ) : (
            <p className="canvas__empty">Loading agents…</p>
          )}
        </div>
      </main>

      <aside className="panel" aria-label={showTrace ? 'Test call' : 'Inspector'}>
        {showTrace ? (
          <CallPanel trace={trace} onDismiss={reset} />
        ) : agent ? (
          <Inspector
            agent={agent}
            selection={selection}
            tools={tools}
            problems={problems}
            locked={inCall}
            onSelect={setSelection}
            {...handlers}
          />
        ) : null}
      </aside>
    </div>
  )
}
