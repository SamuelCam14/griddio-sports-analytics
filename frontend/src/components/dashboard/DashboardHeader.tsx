import { useAppStore } from '@/store/useAppStore'

export function DashboardHeader() {
  const { activeSport, setActiveSport } = useAppStore()

  return (
    <header className="dashboard-header">
      <div className="flex min-w-0 items-center gap-3">
        <div className="brand-mark" aria-hidden="true">SA</div>
        <div>
          <p className="text-sm font-semibold tracking-wide text-white">SPORTS ANALYTICS</p>
          <p className="text-xs text-slate-400">Centro de análisis · NBA y NFL</p>
        </div>
      </div>
      <div className="flex items-center gap-2 rounded-full border border-white/10 bg-black/20 p-1" aria-label="Seleccionar deporte">
        {(['NBA', 'NFL'] as const).map((sport) => (
          <button
            key={sport}
            type="button"
            aria-pressed={activeSport === sport}
            onClick={() => setActiveSport(sport)}
            className={`sport-switch ${activeSport === sport ? 'sport-switch-active' : ''}`}
          >
            {sport}
          </button>
        ))}
      </div>
    </header>
  )
}
