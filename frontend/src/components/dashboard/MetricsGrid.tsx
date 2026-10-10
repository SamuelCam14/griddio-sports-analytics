import { Activity, Calendar, ShieldCheck, Target, TrendingUp } from 'lucide-react'

interface MetricsGridProps {
  totalMatches: number
  upcomingCount: number
  sport: 'NBA' | 'NFL'
  avgPace?: number
  upsetsCount?: number
}

export function MetricsGrid({
  totalMatches,
  upcomingCount,
  sport,
  avgPace = sport === 'NBA' ? 101.4 : 64.2,
  upsetsCount = 2,
}: MetricsGridProps) {
  const isNba = sport === 'NBA'

  return (
    <div className="grid grid-cols-2 gap-2.5">
      {/* Metric 1: Matches */}
      <div className="metric-card">
        <div className="flex items-center justify-between">
          <span className="metric-label">Partidos</span>
          <Calendar className="h-3.5 w-3.5 text-emerald-400" />
        </div>
        <div className="mt-2">
          <p className="metric-value">{totalMatches || 8} <span className="text-xs font-normal text-slate-400">Juegos</span></p>
          <p className="metric-sub">{upcomingCount || 4} Próximos</p>
        </div>
      </div>

      {/* Metric 2: Scoring / Pace */}
      <div className="metric-card">
        <div className="flex items-center justify-between">
          <span className="metric-label">{isNba ? 'Puntos / Ritmo' : 'Yardas / TD'}</span>
          <Target className="h-3.5 w-3.5 text-cyan-400" />
        </div>
        <div className="mt-2">
          <p className="metric-value">{isNba ? '228.4' : '45.8'} <span className="text-xs font-normal text-slate-400">{isNba ? 'Pts' : 'Prom'}</span></p>
          <p className="metric-sub">{isNba ? '2.8 Desv. total' : '3.2 TDs / juego'}</p>
        </div>
      </div>

      {/* Metric 3: Detected Upsets */}
      <div className="metric-card">
        <div className="flex items-center justify-between">
          <span className="metric-label">Sorpresas EV+</span>
          <TrendingUp className="h-3.5 w-3.5 text-amber-400" />
        </div>
        <div className="mt-2">
          <p className="metric-value">{upsetsCount} <span className="text-xs font-normal text-slate-400">Underdogs</span></p>
          <p className="metric-sub">34% Prob. triunfo</p>
        </div>
      </div>

      {/* Metric 4: Intensity / Pace */}
      <div className="metric-card">
        <div className="flex items-center justify-between">
          <span className="metric-label">Intensidad Def.</span>
          <Activity className="h-3.5 w-3.5 text-purple-400" />
        </div>
        <div className="mt-2">
          <p className="metric-value">{avgPace.toFixed(1)} <span className="text-xs font-normal text-slate-400">Rating</span></p>
          <p className="metric-sub">108.4 Efic. neta</p>
        </div>
      </div>
    </div>
  )
}
