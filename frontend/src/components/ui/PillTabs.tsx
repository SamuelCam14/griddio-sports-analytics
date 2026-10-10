export interface TabOption<T extends string = string> {
  id: T
  label: string
  badge?: number | string
}

interface PillTabsProps<T extends string = string> {
  tabs: TabOption<T>[]
  activeTab: T
  onChange: (id: T) => void
  size?: 'sm' | 'md'
  className?: string
}

export function PillTabs<T extends string = string>({
  tabs,
  activeTab,
  onChange,
  size = 'sm',
  className = '',
}: PillTabsProps<T>) {
  const padClass = size === 'sm' ? 'px-2.5 py-1 text-[11px]' : 'px-3.5 py-1.5 text-xs'

  return (
    <div
      role="tablist"
      className={`inline-flex items-center gap-1 rounded-full border border-white/10 bg-black/30 p-1 backdrop-blur-md ${className}`}
    >
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id
        return (
          <button
            key={tab.id}
            role="tab"
            type="button"
            aria-selected={isActive}
            onClick={() => onChange(tab.id)}
            className={`rounded-full font-semibold transition-all duration-200 ${padClass} ${
              isActive
                ? 'bg-blue-600 text-white shadow-btn-glow'
                : 'text-slate-400 hover:text-white hover:bg-white/5'
            }`}
          >
            <span>{tab.label}</span>
            {tab.badge != null && (
              <span
                className={`ml-1.5 rounded-full px-1.5 py-0.2 text-[9px] ${
                  isActive ? 'bg-white/20 text-white' : 'bg-white/10 text-slate-300'
                }`}
              >
                {tab.badge}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
