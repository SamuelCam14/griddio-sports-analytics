import { Cpu } from 'lucide-react'
import type { ScheduledMatch } from '@/types/dashboard'
import type { ExternalGameForecast } from '@/services/api'
import { formatMatchDate, formatMatchTime, formatPercentage } from '@/utils/dashboard'

interface AiMatchupSimulatorCardProps {
  match?: ScheduledMatch
  sport: 'NBA' | 'NFL'
  externalForecast?: ExternalGameForecast
}

export function AiMatchupSimulatorCard({ match, sport, externalForecast }: AiMatchupSimulatorCardProps) {
  const odds = match?.odds
  const prediction = match?.prediction
  const experimental = match?.experimentalPrediction
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
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] p-3 text-slate-300">
            {prediction
              ? `Probabilidades del modelo de producción: local ${formatPercentage(prediction.home_win_prob)} · visitante ${formatPercentage(prediction.away_win_prob)}.`
              : externalForecast?.status === 'available'
                ? `Pronóstico externo ${externalForecast.source}: local ${formatPercentage(externalForecast.home_win_probability)} · visitante ${formatPercentage(externalForecast.away_win_probability)}. No es el modelo Griddio ni una recomendación de apuesta.`
                : experimental
                ? `Probabilidades experimentales (${experimental.model_version}): local ${formatPercentage(experimental.home_win_probability)} · visitante ${formatPercentage(experimental.away_win_probability)}. Mayor probabilidad: ${experimental.home_win_probability >= experimental.away_win_probability ? odds?.home_team : odds?.away_team}. Corte ${experimental.run_as_of}. No es pick ni calcula EV.`
                : 'No hay predicción disponible para este evento. No se derivan probabilidades ni EV de las cuotas.'}
          </div>
        </div>
      ) : (
        <p className="mt-4 text-xs text-slate-400" role="status">No hay un partido asociado a cuotas y calendario canónicos.</p>
      )}

      <div className="mt-3 border-t border-white/[0.08] pt-3 text-[10px] text-slate-500">
        <span>{prediction ? 'Modelo de producción' : externalForecast?.status === 'available' ? `Pronóstico externo: ${externalForecast.source}` : experimental ? 'Inferencia experimental verificada por hash' : 'Sin inferencia vinculada'}</span>
        <span className="float-right">Gate: {prediction ? 'aprobado' : 'cerrado'}</span>
      </div>
    </section>
  )
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return <div className="rounded-lg bg-white/[0.03] p-2"><p className="text-[10px] text-slate-500">{label}</p><p className="mt-1 font-mono text-slate-200">{value ?? 'Sin dato'}</p></div>
}
