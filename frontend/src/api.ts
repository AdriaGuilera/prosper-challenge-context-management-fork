import type { Agent, AgentSummary, PickerOption, ToolInfo } from './types'

/** Fill the fields the JSON may omit (the backend schema defaults them too). */
function normalize(a: Agent): Agent {
  return {
    ...a,
    persona: a.persona ?? '',
    nodes: a.nodes.map((n) => ({
      ...n,
      task_messages: n.task_messages ?? [],
      edges: (n.edges ?? []).map((e) => ({ ...e, properties: e.properties ?? {}, required: e.required ?? [] })),
      tools: n.tools ?? [],
    })),
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json()
}

export const api = {
  listAgents: () => request<AgentSummary[]>('/api/agents'),
  getAgent: (id: string) => request<Agent>(`/api/agents/${id}`).then(normalize),
  saveAgent: (id: string, agent: Agent) =>
    request<{ id: string }>(`/api/agents/${id}`, { method: 'PUT', body: JSON.stringify(agent) }),
  deleteAgent: (id: string) => request<{ ok: true }>(`/api/agents/${id}`, { method: 'DELETE' }),
  tools: () => request<ToolInfo[]>('/api/tools'),
  models: () => request<PickerOption[]>('/api/models'),
  voices: () => request<PickerOption[]>('/api/voices'),
}
