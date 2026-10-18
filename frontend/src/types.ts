// The agent JSON contract — mirrors backend/agent_builder/schema.py.

export type JsonSchemaProps = Record<string, { type?: string; description?: string; enum?: string[] }>

export interface Edge {
  function: string
  description: string
  target: string
  properties: JsonSchemaProps
  required: string[]
}

export interface TaskMessage {
  role: string
  content: string
}

export interface AgentNode {
  name: string
  task_messages: TaskMessage[]
  role_message?: string | null
  edges: Edge[]
  tools?: string[]
  pre_actions?: unknown[]
  post_actions?: unknown[]
  end?: boolean
  ui?: { position?: { x: number; y: number } }
}

export interface Agent {
  name: string
  initial_node: string
  persona: string
  voice_id: string
  model: string
  nodes: AgentNode[]
}

export interface AgentSummary {
  id: string
  name: string
  nodes: number
}

export interface PickerOption {
  id: string
  label: string
  note: string
}

export interface ToolInfo {
  name: string
  description: string
  parameters: string[]
}

// Events the bot sends over the call's RTVI channel (see builder.py).
export type CallEvent =
  | { type: 'node_entered'; node: string }
  | { type: 'edge_taken'; source: string; target: string; function: string; args: Record<string, unknown> }
  | { type: 'tool_started'; id: string; node: string; tool: string; args: Record<string, unknown> }
  | {
      type: 'tool_finished'
      id: string
      node: string
      tool: string
      ms: number
      rows: number | null
      ok: boolean
      result: unknown
    }

export type Selection = { kind: 'agent' } | { kind: 'node'; name: string } | { kind: 'edge'; source: string; index: number }

export type CallStatus = 'idle' | 'connecting' | 'live' | 'ended' | 'error'

export interface TranscriptLine {
  id: string
  who: 'caller' | 'agent'
  text: string
  final: boolean
  committed?: string // caller only: the finished segments of this line, before the live partial
}

export interface ToolCall {
  id: string
  node: string
  tool: string
  args: Record<string, unknown>
  startedAt: number
  ms?: number
  rows?: number | null
  ok?: boolean
  result?: unknown
}

export interface CallTrace {
  status: CallStatus
  error?: string
  activeNode?: string
  visited: string[]
  // "source>index" -> times taken this call
  taken: Record<string, number>
  transcript: TranscriptLine[]
  tools: ToolCall[]
}
