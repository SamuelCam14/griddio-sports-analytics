import type { ScheduledMatch } from '@/types/dashboard'
import type { ExternalGameForecast } from '@/services/api'
import { formatPercentage } from '@/utils/dashboard'

interface AiPredictionListProps {
  matches: ScheduledMatch[]
  sport: 'NBA' | 'NFL'
  externalForecast?: ExternalGameForecast
  externalForecastLoading?: boolean
  onSelectMatch?: (match: ScheduledMatch) => void
}

export function AiPredictionList({ matches, sport, externalForecast, externalForecastLoading, onSelectMatch }: AiPredictionListProps) {
  const predictions = matches.filter((match) => match.prediction)
  const marketReferences = matches.filter((match) =>
    match.odds.home_implied_prob != null && match.odds.away_implied_prob != null,
  )

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold tracking-tight text-white">Pronósticos de Producción (+EV)</h3>
        <p className="mt-1 text-[11px] text-slate-400">Picks con confluencia del ensamble, cuota ≥ 1.40 y valor esperado positivo.</p>
      </div>

      {predictions.length === 0 ? (
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-xs text-slate-400" role="status">
          No hay recomendaciones de producción para este corte en {sport}. El ensamble solo publica picks cuando existe confluencia y ventaja matemática real (+EV).
        </div>
      ) : (
        <div className="space-y-2">
          {predictions.map((match) => {
            const pred = match.prediction!
            const bet = pred.best_bet
            return (
              <button
                key={match.game.id}
                type="button"
                onClick={() => onSelectMatch?.(match)}
                className="flex w-full items-center justify-between rounded-xl border border-emerald-500/15 bg-emerald-500/[0.03] p-2.5 text-left transition-colors hover:bg-emerald-500/[0.08]"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-xs font-medium text-slate-100">{match.odds.away_team} @ {match.odds.home_team}</span>
                    <span className="rounded bg-emerald-500/20 px-1.5 py-0.2 font-mono text-[9px] text-emerald-300 uppercase">{pred.tier}</span>
                  </div>
                  <p className="mt-0.5 truncate text-[11px] text-slate-400">
                    {bet?.selection ?? pred.pick_team_name}
                    {bet?.odds_american != null ? ` (${bet.odds_american > 0 ? '+' : ''}${bet.odds_american})` : ''}
                  </p>
                </div>
                <div className="ml-3 shrink-0 text-right font-mono">
                  <span className="block text-xs font-semibold text-emerald-400">
                    +${(bet?.ev_dollars ?? pred.ev_dolares ?? 0).toFixed(1)} EV
                  </span>
                  <span className="block text-[10px] text-slate-400">
                    {formatPercentage(bet?.win_probability ?? pred.prob_modelo)}
                  </span>
                </div>
              </button>
            )
          })}
        </div>
      )}

      {marketReferences.length > 0 && (
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Referencia de mercado sin vig (casas observadas)</p>
          <div className="mt-2 space-y-2">
            {marketReferences.slice(0, 4).map((match) => (
              <button
                key={`market-${match.game.id}`}
                type="button"
                onClick={() => onSelectMatch?.(match)}
                className="flex w-full items-center justify-between gap-2 text-left text-xs text-slate-300 hover:text-white"
              >
                <span className="min-w-0">
                  <span className="block truncate">{match.odds.away_team} @ {match.odds.home_team}</span>
                  <span className="block text-[10px] text-slate-500">
                    {match.odds.markets_provenance?.moneyline?.bookmaker ?? match.odds.h2h_bookmaker ?? 'Bookmaker'}
                  </span>
                </span>
                <span className="shrink-0 font-mono text-[11px] text-slate-400">{match.odds.away_team} {formatPercentage(match.odds.away_implied_prob)} · {match.odds.home_team} {formatPercentage(match.odds.home_implied_prob)}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {externalForecast?.status === 'available' && (
        <div className="rounded-lg border border-white/[0.05] bg-white/[0.015] p-2.5 text-xs text-slate-400">
          <div className="flex items-center justify-between text-[10px] uppercase text-slate-500">
            <span>Benchmark externo ({externalForecast.source ?? 'ESPN'})</span>
            <span>{externalForecast.retrieved_at_utc ? `Observado ${externalForecast.retrieved_at_utc.slice(11, 16)} UTC` : ''}</span>
          </div>
          <p className="mt-1 font-mono text-slate-300">
            {externalForecast.away_team} {formatPercentage(externalForecast.away_win_probability)} · {externalForecast.home_team} {formatPercentage(externalForecast.home_win_probability)}
          </p>
        </div>
      )}
      {externalForecastLoading && (
        <p className="text-[10px] text-slate-500" role="status">Consultando referencia externa…</p>
      )}
    </div>
  )
}
