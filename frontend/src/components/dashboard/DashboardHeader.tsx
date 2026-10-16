import {
  BarChart3,
  Bell,
  Calendar,
  Layers,
  Search,
  Sparkles,
  TrendingUp,
  Trophy,
  Wallet,
} from 'lucide-react'
import { useAppStore } from '@/store/useAppStore'
import { Badge } from '@/components/ui/Badge'

export type ActiveView = 'dashboard' | 'simulator' | 'parlay' | 'cards' | 'ev'

interface DashboardHeaderProps {
  onOpenView: (view: ActiveView) => void
  onOpenBankroll: () => void
  hasLiveGames?: boolean
}

export function DashboardHeader({
  onOpenView,
  onOpenBankroll,
  hasLiveGames = true,
}: DashboardHeaderProps) {
  const { activeSport, setActiveSport, bankroll } = useAppStore()

  return (
    <header className="macos-toolbar" role="banner">
      {/* ─── Brand & Sport Segmented Control ───────────────────────────────── */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2.5">
          <div className="grid h-8 w-8 place-items-center rounded-lg border border-white/10 bg-white/[0.06] text-slate-200 shadow-sm">
            <Sparkles className="h-4 w-4 text-apple-blue" />
          </div>
          <div className="hidden sm:block">
            <div className="flex items-center gap-1.5 leading-none">
              <span className="text-xs font-semibold tracking-wide text-white">AI Sports</span>
              <span className="rounded bg-white/[0.08] px-1.5 py-0.5 text-[9px] font-mono font-medium text-slate-300">
                PRO
              </span>
            </div>
            <p className="text-[10px] text-slate-400 mt-0.5">Quantitative Analytics</p>
          </div>
        </div>

        {/* macOS Style Segmented Control (NBA / NFL) */}
        <div
          className="flex items-center rounded-lg border border-white/[0.08] bg-black/40 p-0.5"
          aria-label="Seleccionar deporte"
        >
          {(['NBA', 'NFL'] as const).map((sport) => (
            <button
              key={sport}
              type="button"
              aria-pressed={activeSport === sport}
              onClick={() => setActiveSport(sport)}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-all duration-150 ${
                activeSport === sport
                  ? 'bg-neutral-200 text-slate-900 font-semibold shadow-macos-btn'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {sport}
            </button>
          ))}
        </div>
      </div>

      {/* ─── Navigation Segmented Control & Toolbar Items ─────────────────── */}
      <nav className="hidden lg:flex items-center gap-2">
        <div className="flex items-center rounded-xl border border-white/[0.08] bg-black/35 p-1 backdrop-blur-md">
          <button
            type="button"
            onClick={() => onOpenView('dashboard')}
            className="flex items-center gap-1.5 rounded-lg bg-white/[0.14] px-3 py-1 text-xs font-medium text-white shadow-macos-btn border border-white/[0.08]"
          >
            <Layers className="h-3.5 w-3.5 text-apple-blue" />
            <span>Dashboard</span>
          </button>


          <button
            type="button"
            onClick={() => onOpenView('parlay')}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-medium text-slate-400 hover:text-white transition-colors"
          >
            <Trophy className="h-3.5 w-3.5 text-slate-400" />
            <span>Parlay EV+</span>
          </button>

          <button
            type="button"
            onClick={() => onOpenView('cards')}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-medium text-slate-400 hover:text-white transition-colors"
          >
            <Sparkles className="h-3.5 w-3.5 text-slate-400" />
            <span>Sports Cards</span>
          </button>
        </div>

        {/* macOS Toolbar Quick Action Icons */}
        <div className="flex items-center gap-1 border-l border-white/[0.08] pl-2">
          <button
            type="button"
            onClick={() => onOpenView('ev')}
            title="Calculadora EV+ y Kelly"
            className="macos-btn-icon"
          >
            <TrendingUp className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            title="Estadísticas avanzadas"
            className="macos-btn-icon"
          >
            <BarChart3 className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            title="Calendario"
            className="macos-btn-icon"
          >
            <Calendar className="h-3.5 w-3.5" />
          </button>
        </div>
      </nav>

      {/* ─── Right Controls: Live status, Search, Notifications & Bankroll ─── */}
      <div className="flex items-center gap-2">
        {hasLiveGames && (
          <Badge variant="live" className="hidden sm:inline-flex">
            En Vivo
          </Badge>
        )}

        <button
          type="button"
          title="Buscar partidos, equipos o jugadores"
          className="macos-btn-icon"
        >
          <Search className="h-3.5 w-3.5" />
        </button>

        <button
          type="button"
          title="Notificaciones"
          className="macos-btn-icon relative"
        >
          <Bell className="h-3.5 w-3.5" />
          <span className="absolute top-2 right-2 h-1.5 w-1.5 rounded-full bg-apple-blue" />
        </button>

        {/* User Bankroll Pill (macOS Status Style) */}
        <button
          type="button"
          onClick={onOpenBankroll}
          title="Gestionar Bankroll"
          className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.05] py-1 pl-1.5 pr-3 transition-colors hover:bg-white/[0.1]"
        >
          <div className="grid h-6 w-6 place-items-center rounded-lg bg-white/[0.08] text-slate-300">
            <Wallet className="h-3 w-3" />
          </div>
          <div className="text-left">
            <p className="text-[9px] uppercase font-semibold text-slate-400 leading-none">Bankroll</p>
            <p className="font-mono text-xs font-semibold text-white leading-tight">
              ${bankroll.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
            </p>
          </div>
        </button>
      </div>
    </header>
  )
}
