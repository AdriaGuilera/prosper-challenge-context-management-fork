import { memo, useCallback, useEffect, useMemo, useRef } from 'react'
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  Position,
  ReactFlow,
  getSmoothStepPath,
  useReactFlow,
  type Connection,
  type EdgeProps,
  type Node as FlowNode,
  type Edge as FlowEdge,
  type NodeChange,
  type NodeProps,
} from '@xyflow/react'
import { Wrench } from 'lucide-react'
import type { Agent, CallTrace, Selection } from './types'

type Lamp = 'idle' | 'live' | 'visited' | 'next'

type CircuitData = {
  name: string
  task: string
  tools: string[]
  isStart: boolean
  isEnd: boolean
  lamp: Lamp
  flagged: boolean
}

type WireData = {
  fn: string
  taken: number
  next: boolean
  live: boolean
  lane: number // back wires only: which loop above the nodes, so two never share one
}

// A wire that runs right to left (a "go back" transition) would fold onto its forward
// twin, label on label. It loops over the top of both nodes instead, one lane per wire.
function loopPath(sx: number, sy: number, tx: number, ty: number, lane: number): [string, number, number] {
  const out = 28
  const y = Math.min(sy, ty) - 110 - lane * 26
  const path = `M ${sx} ${sy} H ${sx + out} V ${y} H ${tx - out} V ${ty} H ${tx}`
  return [path, (sx + tx) / 2, y]
}

const CircuitNode = memo(function CircuitNode({ data, selected }: NodeProps<FlowNode<CircuitData>>) {
  return (
    <div className="circuit" data-lamp={data.lamp} data-selected={selected || undefined} data-flagged={data.flagged || undefined}>
      <Handle type="target" position={Position.Left} className="jack" />
      <header className="circuit__head">
        <span className="lamp" aria-hidden />
        <span className="circuit__name">{data.name || 'untitled'}</span>
        {data.isStart && <span className="mark">Start</span>}
        {data.isEnd && <span className="mark mark--end">End</span>}
      </header>
      <p className="circuit__task">{data.task || 'No instructions yet.'}</p>
      {data.tools.length > 0 && (
        <ul className="circuit__tools" aria-label="Tools">
          {data.tools.map((t) => (
            <li key={t}>
              <Wrench size={11} strokeWidth={1.75} aria-hidden />
              {t}
            </li>
          ))}
        </ul>
      )}
      <span className="sr-only">
        {data.lamp === 'live' ? 'Live now.' : data.lamp === 'visited' ? 'Visited this call.' : data.lamp === 'next' ? 'Possible next step.' : ''}
      </span>
      <Handle type="source" position={Position.Right} className="jack" />
    </div>
  )
})

const WireEdge = memo(function WireEdge(props: EdgeProps<FlowEdge<WireData>>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected, markerEnd } = props
  const [path, labelX, labelY] =
    sourceX > targetX
      ? loopPath(sourceX, sourceY, targetX, targetY, data?.lane ?? 0)
      : getSmoothStepPath({
          sourceX,
          sourceY,
          targetX,
          targetY,
          sourcePosition,
          targetPosition,
          borderRadius: 0,
          offset: 28,
        })
  const taken = data?.taken ?? 0
  const state = taken > 0 ? 'taken' : data?.next ? 'next' : 'idle'
  // Fixed weight ramp: each repeat traversal thickens the wire, capped at 4.
  const width = taken > 0 ? Math.min(1.5 + taken, 4) : 1
  return (
    <>
      <BaseEdge
        path={path}
        markerEnd={markerEnd}
        className="wire"
        data-state={state}
        data-selected={selected || undefined}
        style={{ strokeWidth: width }}
      />
      <EdgeLabelRenderer>
        <div
          className="wire__label nodrag nopan"
          data-state={state}
          data-selected={selected || undefined}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        >
          {data?.fn}
          {taken > 1 && <span className="wire__count">×{taken}</span>}
        </div>
      </EdgeLabelRenderer>
    </>
  )
})

const nodeTypes = { circuit: CircuitNode }
const edgeTypes = { wire: WireEdge }

interface Props {
  agent: Agent
  trace: CallTrace
  selection: Selection
  flagged: Set<string>
  locked: boolean
  onSelect: (s: Selection) => void
  onMove: (name: string, pos: { x: number; y: number }) => void
  onConnect: (source: string, target: string) => void
  onDeleteNode: (name: string) => void
  onDeleteEdge: (source: string, index: number) => void
}

export function Graph({ agent, trace, selection, flagged, locked, onSelect, onMove, onConnect, onDeleteNode, onDeleteEdge }: Props) {
  const live = trace.status === 'live' || trace.status === 'connecting'
  const active = trace.activeNode
  const activeNode = agent.nodes.find((n) => n.name === active)
  const nextTargets = new Set(live ? activeNode?.edges.map((e) => e.target) ?? [] : [])
  // Nodes are rebuilt from the agent on every edit. Without their measured size React Flow
  // treats each rebuilt node as new, hides it and drops its handles until it re-measures,
  // which made the whole graph blink while dragging. Keep the sizes and hand them back.
  const sizes = useRef(new Map<string, { width: number; height: number }>())

  const nodes: FlowNode<CircuitData>[] = useMemo(
    () =>
      agent.nodes.map((n, i) => {
        let lamp: Lamp = 'idle'
        if (n.name === active && live) lamp = 'live'
        else if (trace.visited.includes(n.name)) lamp = 'visited'
        if (lamp !== 'live' && nextTargets.has(n.name)) lamp = 'next'
        return {
          id: n.name,
          type: 'circuit',
          position: n.ui?.position ?? { x: (i % 4) * 340, y: Math.floor(i / 4) * 240 },
          measured: sizes.current.get(n.name),
          selected: selection.kind === 'node' && selection.name === n.name,
          draggable: !locked,
          data: {
            name: n.name,
            task: n.task_messages[0]?.content ?? '',
            tools: n.tools ?? [],
            isStart: n.name === agent.initial_node,
            isEnd: !!n.end,
            lamp,
            flagged: flagged.has(n.name),
          },
        }
      }),
    // nextTargets is derived from agent + active + live
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agent, active, live, trace.visited, selection, flagged, locked],
  )

  const edges: FlowEdge<WireData>[] = useMemo(() => {
    // number the back wires, shortest first, so nested loops don't cross
    const x = (name: string) => agent.nodes.find((n) => n.name === name)?.ui?.position?.x ?? 0
    const back = agent.nodes
      .flatMap((n) => n.edges.map((e, i) => ({ id: `${n.name}>${i}`, span: x(n.name) - x(e.target) })))
      .filter((b) => b.span > 0)
      .sort((a, b) => a.span - b.span)
    const lanes = new Map(back.map((b, i) => [b.id, i]))
    return agent.nodes.flatMap((n) =>
        n.edges.map((e, i) => ({
          id: `${n.name}>${i}`,
          source: n.name,
          target: e.target,
          type: 'wire',
          selected: selection.kind === 'edge' && selection.source === n.name && selection.index === i,
          data: {
            fn: e.function,
            taken: trace.taken[`${n.name}>${i}`] ?? 0,
            next: live && n.name === active,
            live,
            lane: lanes.get(`${n.name}>${i}`) ?? 0,
          },
        })),
      )
  }, [agent, trace.taken, selection, live, active])

  const handleNodesChange = useCallback(
    (changes: NodeChange<FlowNode<CircuitData>>[]) => {
      for (const c of changes) {
        if (c.type === 'dimensions' && c.dimensions) sizes.current.set(c.id, c.dimensions)
        else if (c.type === 'position' && c.position) onMove(c.id, c.position)
      }
    },
    [onMove],
  )

  const handleConnect = useCallback((c: Connection) => c.source && c.target && onConnect(c.source, c.target), [onConnect])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={handleNodesChange}
      onConnect={handleConnect}
      onNodeClick={(_, n) => onSelect({ kind: 'node', name: n.id })}
      onEdgeClick={(_, e) => {
        const [source, index] = e.id.split('>')
        onSelect({ kind: 'edge', source, index: Number(index) })
      }}
      onPaneClick={() => onSelect({ kind: 'agent' })}
      onNodesDelete={(ns) => !locked && ns.forEach((n) => onDeleteNode(n.id))}
      onEdgesDelete={(es) =>
        !locked &&
        [...es]
          .sort((a, b) => Number(b.id.split('>')[1]) - Number(a.id.split('>')[1]))
          .forEach((e) => {
            const [source, index] = e.id.split('>')
            onDeleteEdge(source, Number(index))
          })
      }
      nodesConnectable={!locked}
      elementsSelectable
      deleteKeyCode={locked ? null : ['Backspace', 'Delete']}
      fitView
      fitViewOptions={{ padding: 0.2 }}
      minZoom={0.3}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
      defaultEdgeOptions={{ type: 'wire' }}
      connectionLineStyle={{ stroke: 'var(--signal)', strokeWidth: 1.5 }}
    >
      <Background variant={BackgroundVariant.Lines} gap={24} color="var(--grid)" />
      <Controls showInteractive={false} position="bottom-left" />
      <FollowLive node={live ? active : undefined} />
    </ReactFlow>
  )
}

/** Keeps the live node in view without jumping: only pans when it leaves the viewport. */
function FollowLive({ node }: { node?: string }) {
  const rf = useReactFlow()
  useEffect(() => {
    if (!node) return
    const n = rf.getNode(node)
    if (!n) return
    const { x, y, zoom } = rf.getViewport()
    const el = document.querySelector('.react-flow') as HTMLElement | null
    if (!el) return
    const w = n.measured?.width ?? 260
    const h = n.measured?.height ?? 120
    const left = n.position.x * zoom + x
    const top = n.position.y * zoom + y
    const inView = left > 0 && top > 0 && left + w * zoom < el.clientWidth && top + h * zoom < el.clientHeight
    if (!inView) void rf.setCenter(n.position.x + w / 2, n.position.y + h / 2, { zoom, duration: 600 })
  }, [node, rf])
  return null
}
