import {
  Activity,
  BarChart3,
  Bell,
  Calendar,
  Layers,
  Search,
  Settings,
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
    <header className="glass-header" role="banner">
      {/* ─── Brand & Sport Switcher ────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2.5">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-emerald-500/20 via-cyan-500/10 to-transparent border border-emerald-400/30 text-emerald-300 shadow-emerald-glow">
            <Sparkles className="h-5 w-5" />
          </div>
          <div className="hidden sm:block">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-bold tracking-wider text-white">AI SPORTS</span>
              <span className="rounded bg-emerald-400/20 px-1 py-0.2 text-[9px] font-mono font-semibold text-emerald-300">
                PRO
              </span>
            </div>
            <p className="text-[10px] text-slate-400">Quantitative Intelligence</p>
          </div>
        </div>

        {/* Sport switcher pills */}
        <div
          className="flex items-center rounded-full border border-white/10 bg-black/40 p-1"
          aria-label="Seleccionar deporte"
        >
          {(['NBA', 'NFL'] as const).map((sport) => (
            <button
              key={sport}
              type="button"
              aria-pressed={activeSport === sport}
              onClick={() => setActiveSport(sport)}
              className={`rounded-full px-3 py-1 text-xs font-bold transition-all duration-200 ${
                activeSport === sport
                  ? 'bg-emerald-400 text-[#041410] shadow-[0_2px_12px_rgba(52,211,153,0.35)]'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {sport}
            </button>
          ))}
        </div>
      </div>

      {/* ─── Navigation Pills & Quick Action Icons ──────────────────────────── */}
      <nav className="hidden lg:flex items-center gap-2">
        <button
          type="button"
          onClick={() => onOpenView('dashboard')}
          className="glass-pill glass-pill-active"
        >
          <Layers className="h-3.5 w-3.5" />
          <span>Dashboard</span>
        </button>

        <button
          type="button"
          onClick={() => onOpenView('simulator')}
          className="glass-pill"
        >
          <Activity className="h-3.5 w-3.5 text-cyan-400" />
          <span>Simulador</span>
        </button>

        <button
          type="button"
          onClick={() => onOpenView('parlay')}
          className="glass-pill"
        >
          <Trophy className="h-3.5 w-3.5 text-amber-400" />
          <span>Parlay EV+</span>
        </button>

        <button
          type="button"
          onClick={() => onOpenView('cards')}
          className="glass-pill"
        >
          <Sparkles className="h-3.5 w-3.5 text-purple-400" />
          <span>Sports Cards</span>
        </button>

        {/* Quick action frosted circle buttons */}
        <div className="ml-2 flex items-center gap-1.5 border-l border-white/10 pl-2">
          <button
            type="button"
            onClick={() => onOpenView('ev')}
            title="Calculadora Kelly / EV+"
            className="btn-icon-glass text-slate-300 hover:text-emerald-300"
          >
            <TrendingUp className="h-4 w-4" />
          </button>
          <button
            type="button"
            title="Estadísticas y Análisis"
            className="btn-icon-glass text-slate-300 hover:text-cyan-300"
          >
            <BarChart3 className="h-4 w-4" />
          </button>
          <button
            type="button"
            title="Calendario deportivo"
            className="btn-icon-glass text-slate-300 hover:text-white"
          >
            <Calendar className="h-4 w-4" />
          </button>
        </div>
      </nav>

      {/* ─── Right Controls: Live status, Notifications & Profile ────────────── */}
      <div className="flex items-center gap-2.5">
        {/* Live Match badge like reference image */}
        {hasLiveGames && (
          <Badge variant="live" className="hidden sm:inline-flex">
            En Vivo
          </Badge>
        )}

        <button
          type="button"
          title="Buscar partidos, equipos o jugadores"
          className="btn-icon-glass"
        >
          <Search className="h-4 w-4" />
        </button>

        <button
          type="button"
          title="Notificaciones de alertas y +EV"
          className="btn-icon-glass relative"
        >
          <Bell className="h-4 w-4" />
          <span className="absolute top-2 right-2 h-2 w-2 rounded-full bg-emerald-400" />
        </button>

        {/* User Bankroll pill */}
        <button
          type="button"
          onClick={onOpenBankroll}
          title="Gestionar Bankroll"
          className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 py-1 pl-1 pr-3 transition-colors hover:border-emerald-400/30 hover:bg-white/10"
        >
          <div className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-emerald-400 to-cyan-500 font-bold text-xs text-[#061813] shadow-sm">
            <Wallet className="h-3.5 w-3.5" />
          </div>
          <div className="text-left">
            <p className="text-[10px] uppercase font-semibold text-slate-400 leading-none">Bankroll</p>
            <p className="font-mono text-xs font-bold text-emerald-300 leading-tight">
              ${bankroll.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
            </p>
          </div>
        </button>
      </div>
    </header>
  )
}
