import { get } from './client'

export const getAnalytics = (agentId) => get(`/analytics/${agentId}`)
