import { get, post } from './client'

export const createCall = (agentId, prospectName) =>
  post('/calls', { agent_id: agentId, prospect_name: prospectName })
export const listCalls = (agentId) => get(`/calls?agentId=${agentId}`)
export const getCall = (id) => get(`/calls/${id}`)
export const startCall = (id) => post(`/calls/${id}/start`)
export const endCall = (id, outcome) => post(`/calls/${id}/end`, outcome ? { outcome } : {})
export const getCallOptimization = (id) => get(`/calls/${id}/optimization`)
