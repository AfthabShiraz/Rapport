import { get, patch, post } from './client'

export const createAgent = (data) => post('/agents', data)
export const listAgents = () => get('/agents')
export const getAgent = (id) => get(`/agents/${id}`)
export const updateAgent = (id, data) => patch(`/agents/${id}`, data)
