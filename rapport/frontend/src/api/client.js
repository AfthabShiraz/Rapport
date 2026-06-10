async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (res.status === 204) return null
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${options.method || 'GET'} ${path} → ${res.status} ${body.slice(0, 200)}`)
  }
  return res.json()
}

export const get = (path) => request(path)
export const post = (path, body) =>
  request(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined })
export const patch = (path, body) =>
  request(path, { method: 'PATCH', body: JSON.stringify(body) })
