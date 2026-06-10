import { listAgents } from '../api/agents'

const KEY = 'rapport.activeAgentId'

export const getActiveAgentId = () => localStorage.getItem(KEY)
export const setActiveAgentId = (id) => localStorage.setItem(KEY, id)

/** localStorage id first; otherwise newest agent from the API (and remember it). */
export async function resolveActiveAgentId() {
  const stored = getActiveAgentId()
  if (stored) return stored
  const agents = await listAgents().catch(() => [])
  if (agents.length > 0) {
    setActiveAgentId(agents[0].id)
    return agents[0].id
  }
  return null
}
