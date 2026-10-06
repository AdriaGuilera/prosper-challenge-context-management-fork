import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { api } from './api'
import type { Agent, AgentNode, Edge, PickerOption, Selection, ToolInfo } from './types'
import type { Problem } from './validate'

interface Props {
  agent: Agent
  selection: Selection
  tools: ToolInfo[]
  problems: Problem[]
  locked: boolean
  onAgent: (patch: Partial<Agent>) => void
  onNode: (name: string, patch: Partial<AgentNode>) => void
  onRenameNode: (from: string, to: string) => void
  onEdge: (source: string, index: number, patch: Partial<Edge>) => void
  onDeleteNode: (name: string) => void
  onDeleteEdge: (source: string, index: number) => void
  onSelect: (s: Selection) => void
}

export function Inspector(props: Props) {
  const { agent, selection } = props
  if (selection.kind === 'node') {
    const node = agent.nodes.find((n) => n.name === selection.name)
    if (node) return <NodeForm {...props} node={node} />
  }
  if (selection.kind === 'edge') {
    const edge = agent.nodes.find((n) => n.name === selection.source)?.edges[selection.index]
    if (edge) return <EdgeForm {...props} source={selection.source} index={selection.index} edge={edge} />
  }
  return <AgentForm {...props} />
}

function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="panel-section">
      <header className="panel-section__head">
        <h2>{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  )
}

// Fetched once per page load; the lists don't change while editing.
const pickerCache = new Map<string, Promise<PickerOption[]>>()
function usePicker(kind: 'models' | 'voices') {
  const [options, setOptions] = useState<PickerOption[] | null>(null)
  useEffect(() => {
    if (!pickerCache.has(kind)) pickerCache.set(kind, api[kind]().catch(() => []))
    let alive = true
    void pickerCache.get(kind)!.then((o) => alive && setOptions(o))
    return () => {
      alive = false
    }
  }, [kind])
  return options
}

function Picker({ value, options, onChange }: { value: string; options: PickerOption[] | null; onChange: (v: string) => void }) {
  // Keep a value the list doesn't know (hand-edited JSON, a custom voice) selectable.
  const known = options?.some((o) => o.id === value)
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} disabled={!options}>
      {!options && <option value={value}>Loading…</option>}
      {options && !known && <option value={value}>{value || 'Choose…'}</option>}
      {options?.map((o) => (
        <option key={o.id} value={o.id}>
          {o.note ? `${o.label} (${o.note})` : o.label}
        </option>
      ))}
    </select>
  )
}

function AgentForm({ agent, problems, locked, onAgent, onSelect }: Props) {
  const models = usePicker('models')
  const voices = usePicker('voices')
  return (
    <div className="inspector">
      <Section title="Agent">
        <fieldset disabled={locked} className="fields">
          <label className="field">
            <span>Name</span>
            <input value={agent.name} onChange={(e) => onAgent({ name: e.target.value })} />
          </label>
          <label className="field">
            <span>Persona</span>
            <textarea rows={7} value={agent.persona} onChange={(e) => onAgent({ persona: e.target.value })} />
            <small>Applied to every node unless a node overrides it.</small>
          </label>
          <label className="field">
            <span>Start node</span>
            <select value={agent.initial_node} onChange={(e) => onAgent({ initial_node: e.target.value })}>
              {!agent.nodes.some((n) => n.name === agent.initial_node) && <option value="">Choose…</option>}
              {agent.nodes.map((n) => (
                <option key={n.name} value={n.name}>
                  {n.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>LLM model</span>
            <Picker value={agent.model} options={models} onChange={(model) => onAgent({ model })} />
          </label>
          <label className="field">
            <span>ElevenLabs voice</span>
            <Picker value={agent.voice_id} options={voices} onChange={(voice_id) => onAgent({ voice_id })} />
          </label>
        </fieldset>
      </Section>
      <Section title="Checks" aside={<span className="count">{problems.length || 'All clear'}</span>}>
        {problems.length === 0 ? (
          <p className="quiet">The graph compiles: every transition lands on a node and every node is reachable.</p>
        ) : (
          <ul className="problems">
            {problems.map((p, i) => (
              <li key={i}>
                {p.node ? (
                  <button type="button" className="link" onClick={() => onSelect({ kind: 'node', name: p.node! })}>
                    {p.message}
                  </button>
                ) : (
                  p.message
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  )
}

function NodeForm({ agent, node, tools, problems, locked, onNode, onRenameNode, onDeleteNode, onAgent, onSelect }: Props & { node: AgentNode }) {
  const [name, setName] = useState(node.name)
  useEffect(() => {
    setName(node.name)
  }, [node.name])
  const task = node.task_messages[0]?.content ?? ''
  const mine = problems.filter((p) => p.node === node.name)
  const commitName = () => {
    const next = name.trim().replace(/\s+/g, '_')
    if (next && next !== node.name && !agent.nodes.some((n) => n.name === next)) onRenameNode(node.name, next)
    else setName(node.name)
  }
  return (
    <div className="inspector">
      <Section
        title="Node"
        aside={
          <button type="button" className="icon-btn" onClick={() => onDeleteNode(node.name)} disabled={locked} aria-label={`Delete node ${node.name}`}>
            <Trash2 size={15} strokeWidth={1.75} />
          </button>
        }
      >
        <fieldset disabled={locked} className="fields">
          <label className="field">
            <span>Name</span>
            <input
              value={name}
              className="mono"
              onChange={(e) => setName(e.target.value)}
              onBlur={commitName}
              onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
            />
          </label>
          <label className="field">
            <span>Task</span>
            <textarea
              rows={6}
              value={task}
              onChange={(e) =>
                onNode(node.name, { task_messages: [{ role: 'developer', content: e.target.value }, ...node.task_messages.slice(1)] })
              }
            />
            <small>What the agent should do while the call is on this node.</small>
          </label>
          <label className="field">
            <span>Persona override</span>
            <textarea
              rows={3}
              value={node.role_message ?? ''}
              placeholder="Uses the agent persona"
              onChange={(e) => onNode(node.name, { role_message: e.target.value || null })}
            />
          </label>
          <div className="checks">
            <label className="check">
              <input
                type="checkbox"
                checked={agent.initial_node === node.name}
                onChange={(e) => e.target.checked && onAgent({ initial_node: node.name })}
              />
              Start node
            </label>
            <label className="check">
              <input type="checkbox" checked={!!node.end} onChange={(e) => onNode(node.name, { end: e.target.checked })} />
              Ends the call
            </label>
          </div>
        </fieldset>
      </Section>
      <Section title="Tools" aside={<span className="count">{(node.tools ?? []).length} of {tools.length}</span>}>
        <p className="quiet">Catalog lookups the agent can run here without leaving the node.</p>
        <fieldset disabled={locked} className="tool-list">
          {tools.map((t) => {
            const on = (node.tools ?? []).includes(t.name)
            return (
              <label key={t.name} className="tool-option" data-on={on || undefined}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={(e) =>
                    onNode(node.name, {
                      tools: e.target.checked ? [...(node.tools ?? []), t.name] : (node.tools ?? []).filter((x) => x !== t.name),
                    })
                  }
                />
                <span>
                  <span className="mono">{t.name}</span>
                  <small>{t.description}</small>
                </span>
              </label>
            )
          })}
        </fieldset>
      </Section>
      <Section title="Transitions" aside={<span className="count">{node.edges.length}</span>}>
        {node.edges.length === 0 ? (
          <p className="quiet">Drag from the node's right edge to another node to add one.</p>
        ) : (
          <ul className="rows">
            {node.edges.map((e, i) => (
              <li key={i}>
                <button type="button" className="row-btn" onClick={() => onSelect({ kind: 'edge', source: node.name, index: i })}>
                  <span className="mono">{e.function}</span>
                  <span className="row-btn__to">→ {e.target}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {mine.length > 0 && (
        <Section title="Checks" aside={<span className="count count--warn">{mine.length}</span>}>
          <ul className="problems">
            {mine.map((p, i) => (
              <li key={i}>{p.message}</li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  )
}

function EdgeForm({ agent, source, index, edge, locked, onEdge, onDeleteEdge, onSelect }: Props & { source: string; index: number; edge: Edge }) {
  const [schema, setSchema] = useState(() => JSON.stringify(edge.properties, null, 2))
  const [schemaError, setSchemaError] = useState<string | null>(null)
  useEffect(() => {
    setSchema(JSON.stringify(edge.properties, null, 2))
    setSchemaError(null)
  }, [source, index, edge.properties])

  const commitSchema = (text: string) => {
    setSchema(text)
    try {
      const parsed = JSON.parse(text || '{}')
      if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) throw new Error('Must be a JSON object of properties.')
      setSchemaError(null)
      onEdge(source, index, { properties: parsed, required: edge.required.filter((r) => r in parsed) })
    } catch (err) {
      setSchemaError(err instanceof Error ? err.message : 'Invalid JSON')
    }
  }

  const props = Object.keys(edge.properties)
  return (
    <div className="inspector">
      <Section
        title="Transition"
        aside={
          <button type="button" className="icon-btn" onClick={() => onDeleteEdge(source, index)} disabled={locked} aria-label={`Delete transition ${edge.function}`}>
            <Trash2 size={15} strokeWidth={1.75} />
          </button>
        }
      >
        <p className="route">
          <button type="button" className="link mono" onClick={() => onSelect({ kind: 'node', name: source })}>
            {source}
          </button>
          <span aria-hidden>→</span>
          <button type="button" className="link mono" onClick={() => onSelect({ kind: 'node', name: edge.target })}>
            {edge.target}
          </button>
        </p>
        <fieldset disabled={locked} className="fields">
          <label className="field">
            <span>Function name</span>
            <input className="mono" value={edge.function} onChange={(e) => onEdge(source, index, { function: e.target.value.replace(/\s+/g, '_') })} />
            <small>The tool the LLM calls to take this transition.</small>
          </label>
          <label className="field">
            <span>When to take it</span>
            <textarea rows={3} value={edge.description} onChange={(e) => onEdge(source, index, { description: e.target.value })} />
          </label>
          <label className="field">
            <span>Goes to</span>
            <select value={edge.target} onChange={(e) => onEdge(source, index, { target: e.target.value })}>
              {agent.nodes.map((n) => (
                <option key={n.name} value={n.name}>
                  {n.name}
                </option>
              ))}
            </select>
          </label>
        </fieldset>
      </Section>
      <Section title="Collected fields" aside={<span className="count">{props.length}</span>}>
        <fieldset disabled={locked} className="fields">
          <label className="field">
            <span>Properties (JSON Schema)</span>
            <textarea
              rows={8}
              className="mono code"
              spellCheck={false}
              value={schema}
              aria-invalid={!!schemaError}
              onChange={(e) => commitSchema(e.target.value)}
            />
            {schemaError ? <small className="error">{schemaError}</small> : <small>Saved to agent state when the transition is taken.</small>}
          </label>
          {props.length > 0 && (
            <div className="checks">
              {props.map((p) => (
                <label key={p} className="check mono">
                  <input
                    type="checkbox"
                    checked={edge.required.includes(p)}
                    onChange={(e) =>
                      onEdge(source, index, { required: e.target.checked ? [...edge.required, p] : edge.required.filter((r) => r !== p) })
                    }
                  />
                  {p} required
                </label>
              ))}
            </div>
          )}
        </fieldset>
      </Section>
    </div>
  )
}
