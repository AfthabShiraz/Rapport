import { get, post } from './client'

export const listOptimizations = (agentId) => get(`/optimizations?agentId=${agentId}`)
export const getOptimization = (id) => get(`/optimizations/${id}`)
export const acceptOptimization = (id) => post(`/optimizations/${id}/accept`)
export const rejectOptimization = (id) => post(`/optimizations/${id}/reject`)
