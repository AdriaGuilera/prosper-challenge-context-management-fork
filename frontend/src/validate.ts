import type { Agent } from './types'

export interface Problem {
  node?: string
  message: string
}

/** Mirrors AgentBuilder._validate, plus the checks only an editor cares about. */
export function validate(agent: Agent, tools: string[]): Problem[] {
  const problems: Problem[] = []
  const names = new Set<string>()
  for (const n of agent.nodes) {
    if (!n.name.trim()) problems.push({ message: 'A node has no name.' })
    else if (names.has(n.name)) problems.push({ node: n.name, message: `Two nodes are named “${n.name}”.` })
    names.add(n.name)
  }
  if (!agent.nodes.length) problems.push({ message: 'Add a first node to start the conversation.' })
  else if (!names.has(agent.initial_node)) problems.push({ message: 'Pick a start node.' })

  for (const n of agent.nodes) {
    const fns = new Set<string>()
    for (const e of n.edges) {
      if (!names.has(e.target)) problems.push({ node: n.name, message: `“${e.function}” leads to a node that doesn't exist.` })
      if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(e.function))
        problems.push({ node: n.name, message: `“${e.function}” isn't a valid function name (letters, digits, underscores).` })
      if (fns.has(e.function)) problems.push({ node: n.name, message: `Two transitions are called “${e.function}”.` })
      fns.add(e.function)
    }
    for (const t of n.tools ?? []) {
      if (!tools.includes(t)) problems.push({ node: n.name, message: `Unknown tool “${t}”.` })
    }
    if (!n.end && !n.edges.length) problems.push({ node: n.name, message: `“${n.name}” has no way out. Add a transition or mark it as an end node.` })
  }

  // Reachability from the start node.
  if (names.has(agent.initial_node)) {
    const byName = new Map(agent.nodes.map((n) => [n.name, n]))
    const seen = new Set([agent.initial_node])
    const queue = [agent.initial_node]
    while (queue.length) {
      for (const e of byName.get(queue.shift()!)?.edges ?? []) {
        if (byName.has(e.target) && !seen.has(e.target)) {
          seen.add(e.target)
          queue.push(e.target)
        }
      }
    }
    for (const n of agent.nodes) {
      if (n.name && !seen.has(n.name)) problems.push({ node: n.name, message: `Nothing leads to “${n.name}”.` })
    }
  }
  return problems
}
