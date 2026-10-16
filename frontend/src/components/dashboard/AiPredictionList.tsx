import type { ScheduledMatch } from '@/types/dashboard'
import type { ExternalGameForecast } from '@/services/api'
import { formatPercentage } from '@/utils/dashboard'

interface AiPredictionListProps {
  matches: ScheduledMatch[]
  sport: 'NBA' | 'NFL'
  externalForecast?: ExternalGameForecast
  externalForecastError?: string
  externalForecastLoading?: boolean
  experimentalStatusMessage?: string
  onSelectMatch?: (match: ScheduledMatch) => void
}

export function AiPredictionList({ matches, sport, externalForecast, externalForecastError, externalForecastLoading, experimentalStatusMessage, onSelectMatch }: AiPredictionListProps) {
  const predictions = matches.filter((match) => match.prediction)
  const marketReferences = matches.filter((match) =>
    match.odds.home_implied_prob != null && match.odds.away_implied_prob != null,
  )

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold tracking-tight text-white">Pronósticos y señales</h3>
        <p className="mt-1 text-[11px] text-slate-400">El mercado es una referencia observada; solo un modelo aprobado puede generar picks.</p>
      </div>

      {marketReferences.length > 0 && (
        <div className="rounded-xl border border-sky-400/15 bg-sky-400/[0.04] p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-sky-300">Referencia de mercado sin vig · no es modelo ni pick</p>
          <div className="mt-2 space-y-2">
            {marketReferences.slice(0, 5).map((match) => (
              <button
                key={`market-${match.game.id}`}
                type="button"
                onClick={() => onSelectMatch?.(match)}
                className="flex w-full items-center justify-between gap-2 text-left text-xs text-slate-300 hover:text-white"
              >
                <span className="min-w-0">
                  <span className="block truncate">{match.odds.away_team} @ {match.odds.home_team}</span>
                  <span className="block text-[10px] text-slate-500">
                    {match.odds.markets_provenance?.moneyline?.bookmaker ?? match.odds.h2h_bookmaker ?? 'Casa sin identificar'}
                    {' · '}
                    {match.odds.markets_provenance?.moneyline?.observed_at ?? match.odds.h2h_observed_at ?? 'Hora no disponible'}
                  </span>
                </span>
                <span className="shrink-0 font-mono">{match.odds.away_team} {formatPercentage(match.odds.away_implied_prob)} · {match.odds.home_team} {formatPercentage(match.odds.home_implied_prob)}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {sport === 'NFL' && externalForecast?.status === 'available' && (
        <div className="rounded-xl border border-blue-400/15 bg-blue-400/[0.04] p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-blue-300">Pronóstico externo · {externalForecast.source}</p>
          <p className="mt-2 text-xs text-slate-200">
            {externalForecast.away_team} {formatPercentage(externalForecast.away_win_probability)} · {externalForecast.home_team} {formatPercentage(externalForecast.home_win_probability)}
          </p>
          <p className="mt-1 text-[10px] text-slate-500">
            Consultado {externalForecast.retrieved_at_utc ?? 'hora no disponible'} · no es modelo Griddio ni recomendación de apuesta.
          </p>
        </div>
      )}
      {sport === 'NFL' && (externalForecastError || externalForecast?.status === 'unavailable') && (
        <p className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 text-[11px] text-slate-400" role="status">
          Pronóstico externo no disponible: {externalForecastError ?? externalForecast?.reason ?? 'la fuente no entregó un resultado verificable.'}
        </p>
      )}
      {sport === 'NFL' && externalForecastLoading && (
        <p className="text-[10px] text-slate-500" role="status">Consultando fuente externa de pronóstico…</p>
      )}

      {predictions.length === 0 ? (
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-xs text-slate-400" role="status">
          No hay predicciones de producción disponibles para {sport}. No se muestran probabilidades experimentales como picks.
        </div>
      ) : (
        <div className="space-y-1.5">
          {predictions.slice(0, 5).map((match) => (
            <button
              key={match.game.id}
              type="button"
              onClick={() => onSelectMatch?.(match)}
              className="flex w-full items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.03] p-2.5 text-left hover:bg-white/[0.07]"
            >
              <span className="truncate text-xs font-medium text-slate-200">{match.odds.away_team} @ {match.odds.home_team}</span>
              <span className="ml-3 shrink-0 font-mono text-xs text-slate-200">Local {formatPercentage(match.prediction?.home_win_prob)}</span>
            </button>
          ))}
        </div>
      )}

      {sport === 'NFL' && matches.some((match) => match.experimentalPrediction) && (
        <div className="rounded-lg border border-amber-500/15 bg-amber-500/[0.04] p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-amber-300">Probabilidades experimentales · sin pick ni EV</p>
          <div className="mt-2 space-y-2">
            {matches.filter((match) => match.experimentalPrediction).slice(0, 5).map((match) => (
              <button
                key={`experimental-${match.game.id}`}
                type="button"
                onClick={() => onSelectMatch?.(match)}
                className="flex w-full items-center justify-between gap-2 text-left text-xs text-slate-300 hover:text-white"
              >
                <span className="truncate">{match.odds.away_team} @ {match.odds.home_team}</span>
                <span className="shrink-0 font-mono">L {formatPercentage(match.experimentalPrediction?.home_win_probability)} · V {formatPercentage(match.experimentalPrediction?.away_win_probability)}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {sport === 'NFL' && experimentalStatusMessage && (
        <p className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 text-[11px] text-slate-400" role="status">
          Estado experimental: {experimentalStatusMessage}
        </p>
      )}
    </div>
  )
}
