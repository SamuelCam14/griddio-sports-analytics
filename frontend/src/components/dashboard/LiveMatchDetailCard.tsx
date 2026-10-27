import { useState } from 'react'
import { Maximize2 } from 'lucide-react'
import { PillTabs, type TabOption } from '@/components/ui/PillTabs'
import type { ScheduledMatch } from '@/types/dashboard'
import { formatMatchDate, formatMatchTime, formatPercentage, formatNumber, formatEvDollars } from '@/utils/dashboard'

interface LiveMatchDetailCardProps {
  match?: ScheduledMatch
  sport: 'NBA' | 'NFL'
  onOpenDeepDive: () => void
}

type TabType = 'market' | 'lineup' | 'stats' | 'insights'
const TABS: TabOption<TabType>[] = [
  { id: 'market', label: 'Mercado' },
  { id: 'lineup', label: 'Bajas' },
  { id: 'stats', label: 'Stats' },
  { id: 'insights', label: 'Modelo' },
]

export function LiveMatchDetailCard({ match, sport, onOpenDeepDive }: LiveMatchDetailCardProps) {
  const [activeTab, setActiveTab] = useState<TabType>('market')
  const game = match?.game
  const odds = match?.odds
  const prediction = match?.prediction
  const home = odds?.home_team ?? 'Local sin identificar'
  const away = odds?.away_team ?? 'Visitante sin identificar'
  const kickoff = odds?.commence_time ?? game?.game_date
  const isScheduled = !game || game.status === 'scheduled'
  const statusLabel = !game ? 'Estado no disponible' : game.status === 'live' ? 'EN VIVO' : game.status === 'final' ? 'FINAL' : 'PROGRAMADO'

  return (
    <div className="macos-card p-4 sm:p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold tracking-tight text-white">Estado del partido</h3>
          <p className="mt-0.5 text-[11px] text-slate-400">{sport} · {kickoff ? formatMatchDate(kickoff) : 'fecha sin verificar'}</p>
        </div>
        <button type="button" onClick={onOpenDeepDive} disabled={!game} title="Abrir contexto del partido" className="macos-btn-icon disabled:opacity-40">
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3.5 rounded-xl border border-white/[0.08] bg-[#12151e]/60 p-4">
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 text-center">
          <div><p className="text-xs font-semibold text-white">{away}</p><p className="text-[10px] text-slate-400">Visitante</p></div>
          <div className="font-mono text-xl font-bold text-white">
            {isScheduled ? '— vs —' : `${game?.away_score ?? '—'} - ${game?.home_score ?? '—'}`}
            <p className="mt-1 text-[10px] font-medium text-slate-400">{statusLabel}{kickoff && isScheduled ? ` · ${formatMatchTime(kickoff)}` : ''}</p>
          </div>
          <div><p className="text-xs font-semibold text-white">{home}</p><p className="text-[10px] text-slate-400">Local</p></div>
        </div>
        <div className="mt-3 border-t border-white/[0.08] pt-3 text-center text-xs text-slate-400">
          {prediction
            ? `Probabilidad local ${formatPercentage(prediction.home_win_prob)} · visitante ${formatPercentage(prediction.away_win_prob)}`
            : 'Pronóstico en espera de cuotas o lista de inactivos pre-partido'}
        </div>
      </div>

      <div className="mt-3.5 flex justify-center"><PillTabs tabs={TABS} activeTab={activeTab} onChange={setActiveTab} size="sm" /></div>
      <div className="mt-3 min-h-[115px] rounded-xl border border-white/[0.06] bg-[#12151e]/40 p-3 text-xs">
        {activeTab === 'market' && (
          <div className="space-y-2">
            <MarketFact
              label="Moneyline · visita / local"
              value={odds?.home_ml != null && odds?.away_ml != null ? `${formatOdds(odds.away_ml)} / ${formatOdds(odds.home_ml)}` : null}
              bookmaker={odds?.markets_provenance?.moneyline?.bookmaker ?? odds?.best_bookmaker}
              observedAt={odds?.markets_provenance?.moneyline?.observed_at ?? odds?.h2h_observed_at}
              stale={odds?.markets_provenance?.moneyline?.is_stale ?? odds?.is_stale}
              staleReason={odds?.markets_provenance?.moneyline?.staleness_reason ?? odds?.staleness_reason}
            />
            <MarketFact
              label="Hándicap · local / visita"
              value={odds?.home_spread != null ? `${odds.home_spread > 0 ? '+' : ''}${odds.home_spread} (${formatOddsNullable(odds.home_spread_ml)}) / ${odds.away_spread != null ? `${odds.away_spread > 0 ? '+' : ''}${odds.away_spread}` : '—'} (${formatOddsNullable(odds.away_spread_ml)})` : null}
              bookmaker={odds?.markets_provenance?.spread?.bookmaker ?? odds?.spread_bookmaker}
              observedAt={odds?.markets_provenance?.spread?.observed_at ?? odds?.spread_observed_at}
              stale={odds?.markets_provenance?.spread?.is_stale}
              staleReason={odds?.markets_provenance?.spread?.staleness_reason}
            />
            <MarketFact
              label="Total · over / under"
              value={odds?.total_line != null ? `${odds.total_line} · ${formatOddsNullable(odds.over_ml)} / ${formatOddsNullable(odds.under_ml)}` : null}
              bookmaker={odds?.markets_provenance?.total?.bookmaker ?? odds?.total_bookmaker}
              observedAt={odds?.markets_provenance?.total?.observed_at ?? odds?.total_observed_at}
              stale={odds?.markets_provenance?.total?.is_stale}
              staleReason={odds?.markets_provenance?.total?.staleness_reason}
            />
            <Fact label="Probabilidad de mercado sin vig · local / visita" value={odds?.home_implied_prob != null && odds?.away_implied_prob != null ? `${formatPercentage(odds.home_implied_prob)} / ${formatPercentage(odds.away_implied_prob)}` : null} />
            <p className="pt-1 text-[10px] text-slate-500">Las probabilidades sin vig describen el mercado; no son probabilidades del modelo ni implican EV.</p>
          </div>
        )}
        {activeTab === 'lineup' && <p className="text-slate-400">El reporte de lesiones no está disponible en la cartelera. Abre el contexto del partido para consultar cobertura y fuente.</p>}
        {activeTab === 'stats' && game && (
          <div className="space-y-2">
            <Fact label="Win rate" value={game.team_stats?.home?.wpct != null && game.team_stats?.away?.wpct != null ? `${formatPercentage(game.team_stats.away.wpct)} / ${formatPercentage(game.team_stats.home.wpct)}` : null} />
            <Fact label="Rating neto" value={game.team_stats?.home?.net_rtg != null && game.team_stats?.away?.net_rtg != null ? `${formatNumber(game.team_stats.away.net_rtg)} / ${formatNumber(game.team_stats.home.net_rtg)}` : null} />
            <Fact label="Ritmo" value={game.team_stats?.home?.pace != null && game.team_stats?.away?.pace != null ? `${formatNumber(game.team_stats.away.pace)} / ${formatNumber(game.team_stats.home.pace)}` : null} />
          </div>
        )}
        {activeTab === 'stats' && !game && <p className="text-slate-400">No hay un registro canónico de partido para leer estadísticas.</p>}
        {activeTab === 'insights' && (
          <div className="space-y-2 text-slate-300">
            {prediction ? (
              <>
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="font-semibold text-emerald-400">Modelo de producción ({prediction.model_version ?? `${sport.toLowerCase()}_temporal_v1`})</p>
                  {prediction.pick_status === 'official_cohort' ? (
                    <span className="rounded border border-emerald-500/40 bg-emerald-500/15 px-1.5 py-0.5 font-mono text-[9px] text-emerald-300">
                      Pick Oficial Confirmado
                    </span>
                  ) : (
                    <span className="rounded border border-amber-500/40 bg-amber-500/15 px-1.5 py-0.5 font-mono text-[9px] text-amber-300">
                      Early Lean (Preview)
                    </span>
                  )}
                </div>
                {prediction.status_reason && (
                  <p className="text-[10px] text-slate-400">{prediction.status_reason}</p>
                )}
                <p>Probabilidad estimada: Local {formatPercentage(prediction.home_win_prob)} / Visita {formatPercentage(prediction.away_win_prob)}</p>
                <p>
                  EV y selección:{' '}
                  {prediction.best_bet
                    ? `${prediction.best_bet.selection} · EV ${formatEvDollars(prediction.best_bet.ev_dollars, 1, { suffix: ' por cada $100' })}`
                    : 'Sin ventaja matemática calculable en este mercado.'}
                </p>
                {prediction.spread?.status === 'available' && prediction.spread.projected_margin != null && (
                  <p className="text-slate-300">
                    Hándicap proyectado: {prediction.spread.projected_margin > 0 ? '+' : ''}{prediction.spread.projected_margin.toFixed(1)} pts · Prob. cubrir {formatPercentage(prediction.spread.cover_probability)}
                  </p>
                )}
                {prediction.key_factors?.map((factor, index) => <p key={`${factor}-${index}`} className="text-slate-400">{factor}</p>)}
              </>
            ) : (
              <div className="space-y-1">
                <p className="font-medium text-slate-300">Sin recomendación para este partido</p>
                <p className="text-slate-400">
                  El ensamble requiere confluencia de modelos, cuotas verificadas (≥ 1.40) y valor esperado positivo (+EV).
                </p>
                <p className="text-[10px] text-slate-500">
                  Las listas oficiales de inactivos se confirman a 90 min del kickoff. Cero datos simulados.
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string | number | null | undefined }) {
  return <div className="flex justify-between gap-3 border-b border-white/[0.04] pb-1 text-slate-400"><span>{label}</span><span className="text-right font-mono text-slate-200">{value ?? 'Sin dato verificado'}</span></div>
}

function MarketFact({
  label,
  value,
  bookmaker,
  observedAt,
  stale,
  staleReason,
}: {
  label: string
  value: string | null
  bookmaker?: string | null
  observedAt?: string | null
  stale?: boolean
  staleReason?: string | null
}) {
  return (
    <div className="border-b border-white/[0.04] pb-1">
      <div className="flex justify-between gap-3 text-slate-400"><span>{label}</span><span className="text-right font-mono text-slate-200">{value ?? 'Sin línea observada'}</span></div>
      <p className={`mt-0.5 text-right text-[10px] ${stale ? 'text-amber-300' : 'text-slate-500'}`}>
        {bookmaker ?? 'Sin casa'} · {observedAt ?? 'sin hora de observación'}{stale ? ` · obsoleto${staleReason ? `: ${staleReason}` : ''}` : ''}
      </p>
      {stale && <p className="text-right text-[10px] text-amber-300">No usar para calcular EV ni recomendar.</p>}
    </div>
  )
}

function formatOdds(value: number): string {
  return `${value > 0 ? '+' : ''}${value}`
}

function formatOddsNullable(value?: number | null): string {
  return value == null ? 'sin momio' : formatOdds(value)
}
