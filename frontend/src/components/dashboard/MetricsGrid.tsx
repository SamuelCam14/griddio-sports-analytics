import { Calendar, ShieldCheck, Target, TrendingUp } from 'lucide-react'

interface MetricsGridProps {
  totalMatches: number
  upcomingCount: number
  sport: 'NBA' | 'NFL'
  avgPace?: number
  upsetsCount?: number | null
  predictionsEnabled: boolean
  marketTotalLine?: number | null
  activeMatchupLabel?: string | null
}

export function MetricsGrid({
  totalMatches,
  upcomingCount,
  sport,
  upsetsCount = null,
  predictionsEnabled,
  marketTotalLine,
  activeMatchupLabel,
}: MetricsGridProps) {
  const isNba = sport === 'NBA'

  return (
    <div className="grid grid-cols-2 gap-2.5">
      {/* Metric 1: Matches */}
      <div className="macos-metric-tile">
        <div className="flex items-center justify-between">
          <span className="macos-metric-label">Partidos</span>
          <Calendar className="h-3.5 w-3.5 text-slate-400" />
        </div>
        <div className="mt-2">
          <p className="macos-metric-value">{totalMatches} <span className="text-xs font-normal text-slate-400">con cuotas asociadas</span></p>
          <p className="macos-metric-sub">{upcomingCount} próximos en el calendario consultado</p>
        </div>
      </div>

      {/* Metric 2: Scoring / Totals */}
      <div className="macos-metric-tile">
        <div className="flex items-center justify-between">
          <span className="macos-metric-label">{isNba ? 'Mercado NBA' : 'Línea Total NFL'}</span>
          <Target className="h-3.5 w-3.5 text-apple-blue" />
        </div>
        <div className="mt-2">
          <p className="macos-metric-value">
            {marketTotalLine != null ? `${marketTotalLine}` : '—'}{' '}
            <span className="text-xs font-normal text-slate-400">{sport === 'NFL' ? 'O/U' : 'Líneas'}</span>
          </p>
          <p className="macos-metric-sub">
            {activeMatchupLabel ?? (totalMatches > 0 ? 'Líneas observadas' : 'Sin partidos')}
          </p>
        </div>
      </div>

      {/* Metric 3: Detected Upsets */}
      <div className="macos-metric-tile">
        <div className="flex items-center justify-between">
          <span className="macos-metric-label">Sorpresas EV+</span>
          <TrendingUp className="h-3.5 w-3.5 text-apple-orange" />
        </div>
        <div className="mt-2">
          <p className="macos-metric-value">{upsetsCount ?? '—'} <span className="text-xs font-normal text-slate-400">selecciones</span></p>
          <p className="macos-metric-sub">
            {!predictionsEnabled ? 'Sin modelo aprobado; no calculado' : upsetsCount ? 'Con ventaja publicada por el backend' : 'Sin selecciones EV+ publicadas'}
          </p>
        </div>
      </div>

      {/* Metric 4: Model Governance */}
      <div className="macos-metric-tile">
        <div className="flex items-center justify-between">
          <span className="macos-metric-label">Estado del modelo</span>
          <ShieldCheck className="h-3.5 w-3.5 text-apple-purple" />
        </div>
        <div className="mt-2">
          <p className="macos-metric-value">
            {predictionsEnabled ? 'Aprobado' : sport === 'NFL' ? 'Temporal v1 (Exp)' : 'Temporal v1 (Exp)'}
          </p>
          <p className="macos-metric-sub">
            {predictionsEnabled ? 'Gate de producción: abierto' : 'Gate de producción: cerrado'}
          </p>
        </div>
      </div>
    </div>
  )
}
