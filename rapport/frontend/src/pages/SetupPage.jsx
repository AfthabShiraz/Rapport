import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { createAgent, getAgent, updateAgent } from '../api/agents'
import { listCalls } from '../api/calls'
import { listOptimizations } from '../api/optimizations'
import StartCallControl from '../components/StartCallControl'
import { resolveActiveAgentId, setActiveAgentId } from '../state/activeAgent'

const DEFAULTS = {
  brand_name: 'Lumio Solar',
  product: 'Home solar + battery packages',
  elevator_pitch:
    'We help homeowners cut their electricity bills by up to 70% with fully-installed solar and battery packages — no upfront cost options and a 25-year performance guarantee.',
  target_persona: 'UK homeowner, 35–60, owns their home, rising energy bills, south-facing roof',
  objections: [
    'Too expensive',
    'Not enough sun in the UK',
    'Need to think about it',
    'Already got quotes',
  ],
}

function Section({ title, children }) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-5 shadow-card">
      <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-soft mb-3.5">
        {title}
      </h2>
      {children}
    </section>
  )
}

const inputCls =
  'w-full rounded-xl border border-line-strong bg-surface px-3.5 py-2.5 text-sm text-ink placeholder:text-ink-faint transition focus:outline-none focus:border-coral focus:ring-2 focus:ring-coral/25'

export default function SetupPage() {
  const [agentId, setAgentId] = useState(null)
  const [form, setForm] = useState(DEFAULTS)
  const [newObjection, setNewObjection] = useState('')
  const [status, setStatus] = useState(null) // {calls, applied}
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState(null)
  const [searchParams] = useSearchParams()
  const startInputRef = useRef(null)

  useEffect(() => {
    ;(async () => {
      const id = await resolveActiveAgentId()
      if (!id) return
      try {
        const agent = await getAgent(id)
        setAgentId(agent.id)
        setForm({
          brand_name: agent.brand_name,
          product: agent.product,
          elevator_pitch: agent.elevator_pitch,
          target_persona: agent.target_persona,
          objections: agent.objections,
        })
        refreshStatus(agent.id)
      } catch {
        /* stale id; keep defaults */
      }
    })()
  }, [])

  useEffect(() => {
    if (searchParams.get('focus') === 'start') {
      startInputRef.current?.focus()
      startInputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [searchParams])

  const refreshStatus = async (id) => {
    try {
      const [calls, opts] = await Promise.all([listCalls(id), listOptimizations(id)])
      setStatus({
        calls: calls.length,
        applied: opts.filter((o) => o.status === 'accepted').length,
      })
    } catch {
      setStatus(null)
    }
  }

  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value })

  const addObjection = () => {
    const v = newObjection.trim()
    if (v && !form.objections.includes(v)) {
      setForm({ ...form, objections: [...form.objections, v] })
    }
    setNewObjection('')
  }

  const save = async () => {
    setSaving(true)
    try {
      const agent = agentId ? await updateAgent(agentId, form) : await createAgent(form)
      setAgentId(agent.id)
      setActiveAgentId(agent.id)
      setSavedAt(Date.now())
      refreshStatus(agent.id)
    } catch (e) {
      alert(`Save failed: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-10 space-y-4">
        <div className="mb-6">
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-coral mb-1.5">
            Configure agent
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight text-ink">Manager setup</h1>
          <p className="mt-1.5 text-sm text-ink-soft">
            Onboard your brand, shape the pitch, and put your AI agent on the phone.
          </p>
        </div>

        <Section title="Brand & product">
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-ink-soft">Brand name</label>
              <input className={inputCls} value={form.brand_name} onChange={set('brand_name')} />
            </div>
            <div>
              <label className="text-xs font-medium text-ink-soft">Product</label>
              <input className={inputCls} value={form.product} onChange={set('product')} />
            </div>
            <div>
              <label className="text-xs font-medium text-ink-soft">Elevator pitch</label>
              <textarea
                rows={2}
                className={inputCls}
                value={form.elevator_pitch}
                onChange={set('elevator_pitch')}
              />
            </div>
          </div>
        </Section>

        <Section title="Ideal customer profile">
          <label className="text-xs font-medium text-ink-soft">Target persona</label>
          <input className={inputCls} value={form.target_persona} onChange={set('target_persona')} />
        </Section>

        <Section title="Common objections">
          <div className="flex flex-wrap gap-2">
            {form.objections.map((o) => (
              <span
                key={o}
                className="inline-flex items-center gap-1.5 rounded-full border border-line bg-cream px-3 py-1 text-sm text-ink"
              >
                {o}
                <button
                  onClick={() =>
                    setForm({ ...form, objections: form.objections.filter((x) => x !== o) })
                  }
                  className="text-ink-faint hover:text-coral transition-colors"
                  aria-label={`remove ${o}`}
                >
                  ✕
                </button>
              </span>
            ))}
            <input
              value={newObjection}
              onChange={(e) => setNewObjection(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addObjection()}
              onBlur={addObjection}
              placeholder="+ Add"
              className="rounded-full border border-dashed border-line-strong bg-transparent px-3 py-1 text-sm w-28 text-ink placeholder:text-ink-faint focus:outline-none focus:border-coral focus:ring-2 focus:ring-coral/25"
            />
          </div>
        </Section>

        <Section title="Learning status">
          <div className="flex items-center gap-2 text-sm">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 text-emerald-300 px-2.5 py-0.5 text-xs font-semibold">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              active
            </span>
            <span className="text-ink-soft">
              {status
                ? `${status.calls} calls traced · ${status.applied} optimizations applied`
                : 'no agent deployed yet'}
            </span>
            {agentId && (
              <button
                onClick={() => refreshStatus(agentId)}
                className="text-ink-faint hover:text-coral transition-colors"
                title="refresh"
              >
                ⟳
              </button>
            )}
          </div>
        </Section>

        <div className="flex items-center justify-between pt-2 pb-10">
          <div className="flex items-center gap-3">
            <button
              onClick={save}
              disabled={saving}
              className="rounded-xl bg-ink px-4 py-2.5 text-sm font-semibold text-surface transition hover:bg-ink/90 disabled:opacity-40"
            >
              {saving ? 'Saving…' : 'Save & deploy agent'}
            </button>
            {savedAt && <span className="text-xs font-medium text-emerald-400">deployed ✓</span>}
          </div>
          <StartCallControl agentId={agentId} inputRef={startInputRef} defaultName="James Thornton" />
        </div>
      </div>
    </div>
  )
}
