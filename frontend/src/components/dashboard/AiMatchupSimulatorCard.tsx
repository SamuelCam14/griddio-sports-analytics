import { Cpu } from 'lucide-react'
import type { ScheduledMatch } from '@/types/dashboard'
import type { ExternalGameForecast } from '@/services/api'
import { formatMatchDate, formatMatchTime, formatPercentage } from '@/utils/dashboard'

interface AiMatchupSimulatorCardProps {
  match?: ScheduledMatch
  sport: 'NBA' | 'NFL'
  externalForecast?: ExternalGameForecast
}

export function AiMatchupSimulatorCard({ match, sport }: AiMatchupSimulatorCardProps) {
  const odds = match?.odds
  const prediction = match?.prediction
  const hasVerifiedOdds = odds?.home_ml != null && odds.away_ml != null

  return (
    <section className="macos-card p-4 sm:p-5" aria-label="Estado de datos e inferencia">
      <div className="flex items-center gap-2 border-b border-white/[0.08] pb-3">
        <Cpu className="h-4 w-4 text-apple-blue" />
        <div>
          <h3 className="text-sm font-semibold tracking-tight text-white">Datos del partido</h3>
          <p className="text-[10px] text-slate-400">{sport} · valores observados y publicados</p>
        </div>
      </div>

      {match ? (
        <div className="mt-4 space-y-3 text-xs">
          <p className="font-medium text-white">{odds?.away_team} @ {odds?.home_team}</p>
          <p className="text-slate-400">{odds?.commence_time ? `${formatMatchDate(odds.commence_time)} · ${formatMatchTime(odds.commence_time)}` : 'Fecha no disponible en el mercado.'}</p>
          <div className="grid grid-cols-2 gap-2">
            <Fact label="Momio visitante" value={odds?.away_ml != null ? `${odds.away_ml > 0 ? '+' : ''}${odds.away_ml}` : null} />
            <Fact label="Momio local" value={odds?.home_ml != null ? `${odds.home_ml > 0 ? '+' : ''}${odds.home_ml}` : null} />
            <Fact label="Prob. implícita visitante" value={odds?.away_implied_prob != null ? formatPercentage(odds.away_implied_prob) : null} />
            <Fact label="Prob. implícita local" value={odds?.home_implied_prob != null ? formatPercentage(odds.home_implied_prob) : null} />
          </div>
          <p className="text-[11px] text-slate-400">
            {hasVerifiedOdds && odds?.best_bookmaker
              ? `Fuente de cuotas: ${odds.best_bookmaker}${odds.h2h_observed_at ? ` · observadas ${odds.h2h_observed_at}` : ''}.`
              : 'Cuotas o procedencia de mercado incompletas.'}
          </p>
          <div className={`rounded-lg border p-3 text-slate-300 ${prediction ? 'border-emerald-500/20 bg-emerald-500/[0.06]' : 'border-white/[0.08] bg-white/[0.02]'}`}>
            {prediction ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-emerald-400">Ensamble de Producción (nfl_temporal_v1)</span>
                  <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-mono text-emerald-300">{prediction.tier}</span>
                </div>
                <p>Probabilidad de victoria: <span className="font-medium text-white">{odds?.home_team} {formatPercentage(prediction.home_win_prob)}</span> · <span className="font-medium text-white">{odds?.away_team} {formatPercentage(prediction.away_win_prob)}</span></p>
                {prediction.best_bet && (
                  <div className="rounded border border-emerald-500/20 bg-black/40 p-2 text-xs">
                    <p className="font-medium text-white">Recomendación con ventaja: {prediction.best_bet.selection}</p>
                    <p className="text-[11px] text-emerald-400">Probabilidad: {formatPercentage(prediction.best_bet.win_probability)} · EV: +${prediction.best_bet.ev_dollars.toFixed(1)} / $100</p>
                    {prediction.best_bet.reason && <p className="mt-1 text-[10px] text-slate-400">{prediction.best_bet.reason}</p>}
                  </div>
                )}
                {prediction.spread?.status === 'available' && prediction.spread.projected_margin != null && (
                  <p className="text-[11px] text-slate-300">
                    Margen proyectado: <span className="font-mono text-white">{prediction.spread.projected_margin > 0 ? '+' : ''}{prediction.spread.projected_margin.toFixed(1)} pts</span> ({prediction.spread.projected_home_score?.toFixed(1)} - {prediction.spread.projected_away_score?.toFixed(1)}) · Prob. cubrir: {formatPercentage(prediction.spread.cover_probability)}
                  </p>
                )}
                <p className="text-[10px] text-slate-400 italic">
                  Reporte preliminar: Las listas oficiales de inactivos se confirman a 90 min del kickoff.
                </p>
              </div>
            ) : (
              <div className="space-y-1">
                <p className="font-medium text-slate-300">Pronóstico no disponible</p>
                <p className="text-[11px] text-slate-400">
                  {!hasVerifiedOdds
                    ? 'Esperando publicación de cuotas de mercado vigentes para este evento.'
                    : 'Sin ventaja matemática calculable (+EV con cuota ≥ 1.40). El ensamble no fuerza recomendaciones sin ventaja real.'}
                </p>
                <p className="text-[10px] text-slate-500">
                  Cero datos simulados. No se aplican penalizaciones fijas ni valores imputados.
                </p>
              </div>
            )}
          </div>
        </div>
      ) : (
        <p className="mt-4 text-xs text-slate-400" role="status">No hay un partido seleccionado o asociado a cuotas canónicas.</p>
      )}

      <div className="mt-3 border-t border-white/[0.08] pt-3 text-[10px] text-slate-500">
        <span>{prediction ? 'Modelo: Producción validado' : 'Modelo: Sin recomendación con ventaja'}</span>
        <span className="float-right">Datos: Verificados (ESPN & NFLverse)</span>
      </div>
    </section>
  )
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return <div className="rounded-lg bg-white/[0.03] p-2"><p className="text-[10px] text-slate-500">{label}</p><p className="mt-1 font-mono text-slate-200">{value ?? 'Sin dato'}</p></div>
}
