import { Link, useLocation } from 'react-router-dom'

const items = [
  { label: 'Setup', to: '/setup' },
  { label: 'New call', to: '/setup?focus=start' },
  { label: 'Analytics', to: '/analytics' },
]

export default function Sidebar() {
  const { pathname } = useLocation()
  return (
    <aside className="w-[212px] shrink-0 border-r border-line bg-surface px-3 py-5 flex flex-col gap-1">
      <div className="px-3 pb-5">
        <div className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-coral text-surface text-base font-bold leading-none">
            R
          </span>
          <span className="text-lg font-bold tracking-tight text-ink">Rapport</span>
        </div>
        <div className="mt-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
          Overmind sales agent
        </div>
      </div>
      {items.map((it) => {
        const active = it.to.startsWith(pathname) && pathname !== '/'
        const isActive = active && it.label === 'Setup'
        return (
          <Link
            key={it.label}
            to={it.to}
            className={`group flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              isActive
                ? 'bg-coral-soft text-coral-ink'
                : 'text-ink-soft hover:bg-cream-deep hover:text-ink'
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full transition-colors ${
                isActive ? 'bg-coral' : 'bg-line-strong group-hover:bg-ink-faint'
              }`}
            />
            {it.label}
          </Link>
        )
      })}
      <div className="mt-auto px-3 pt-4">
        <div className="rounded-xl border border-line bg-cream px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold text-ink">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            Agent live
          </div>
          <div className="mt-0.5 text-[11px] text-ink-soft leading-snug">
            Self-optimizing after every call
          </div>
        </div>
      </div>
    </aside>
  )
}
