import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Swords,
  ShieldAlert,
  BarChart3,
  SlidersHorizontal,
  Activity,
  TrendingUp,
  Loader2,
  CheckCircle2,
  Zap,
  ChevronDown,
  ChevronUp,
  Info
} from 'lucide-react'

import { gamesApi, type Game, type MatchupContext, type OfficialInjury, type AffectedPlayerProduction } from '@/services/api'
import { formatNumber, formatPercentage } from '@/utils/dashboard'

interface TeamMetricsPanelProps {
  game: Game
  homeName: string
  awayName: string
}

type TabType = 'h2h' | 'injuries' | 'stats' | 'calendar'

export function TeamMetricsPanel({ game, homeName, awayName }: TeamMetricsPanelProps) {
  const [activeTab, setActiveTab] = useState<TabType>('h2h')
  const [showFantasyBreakdown, setShowFantasyBreakdown] = useState(true)

  const { data: context, isLoading, isError } = useQuery<MatchupContext>({
    queryKey: ['gameContext', game.id],
    queryFn: () => gamesApi.getContext(game.id).then((res) => res.data),
    staleTime: 5 * 60_000,
  })

  const homeInjuriesCount = context?.injuries?.home?.length ?? 0
  const awayInjuriesCount = context?.injuries?.away?.length ?? 0
  const totalInjuriesCount = homeInjuriesCount + awayInjuriesCount

  return (
    <section className="surface-card" aria-label="Centro de contexto deportivo estilo SofaScore">
      <div className="section-heading flex-wrap items-center justify-between border-b border-white/8 pb-4">
        <div>
          <p className="eyebrow">MATCHUP HUB · SOFASCORE INTELLIGENCE</p>
          <h2 className="section-title">Contexto deportivo integral</h2>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 pt-2 sm:pt-0">
          <button
            type="button"
            onClick={() => setActiveTab('h2h')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
              activeTab === 'h2h'
                ? 'bg-emerald-400/15 text-emerald-300 ring-1 ring-emerald-400/30'
                : 'text-slate-400 hover:bg-white/5 hover:text-white'
            }`}
          >
            <Swords className="h-3.5 w-3.5" />
            <span>H2H y Forma</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('injuries')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
              activeTab === 'injuries'
                ? 'bg-emerald-400/15 text-emerald-300 ring-1 ring-emerald-400/30'
                : 'text-slate-400 hover:bg-white/5 hover:text-white'
            }`}
          >
            <ShieldAlert className="h-3.5 w-3.5" />
            <span>Bajas y Lesiones</span>
            {totalInjuriesCount > 0 && (
              <span className="ml-1 rounded-full bg-rose-500/20 px-1.5 py-0.2 text-[10px] font-mono text-rose-300">
                {totalInjuriesCount}
              </span>
            )}
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('stats')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
              activeTab === 'stats'
                ? 'bg-emerald-400/15 text-emerald-300 ring-1 ring-emerald-400/30'
                : 'text-slate-400 hover:bg-white/5 hover:text-white'
            }`}
          >
            <BarChart3 className="h-3.5 w-3.5" />
            <span>Duelo Estadístico</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('calendar')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
              activeTab === 'calendar'
                ? 'bg-emerald-400/15 text-emerald-300 ring-1 ring-emerald-400/30'
                : 'text-slate-400 hover:bg-white/5 hover:text-white'
            }`}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            <span>Indicadores Liga</span>
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="flex min-h-56 flex-col items-center justify-center gap-3 py-8 text-slate-400">
          <Loader2 className="h-6 w-6 animate-spin text-emerald-400" />
          <p className="text-xs">Sincronizando métricas históricas, bajas y precedentes H2H…</p>
        </div>
      )}

      {isError && !isLoading && (
        <div className="my-6 rounded-xl border border-rose-500/20 bg-rose-500/10 p-4 text-xs text-rose-200">
          No se pudieron cargar todos los antecedentes del partido. Mostrando datos disponibles del calendario.
        </div>
      )}

      {!isLoading && context && (
        <AnimatePresence mode="wait">
          {activeTab === 'h2h' && (
            <motion.div
              key="tab-h2h"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
              className="mt-5 space-y-6"
            >
              {/* Resumen General H2H */}
              <div className="rounded-xl border border-white/8 bg-white/[0.025] p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Swords className="h-4 w-4 text-emerald-400" />
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                      Balance Histórico Directo (H2H)
                    </h3>
                  </div>
                  <span className="font-mono text-xs text-slate-400">
                    {context.h2h?.total_games ?? 0} enfrentamientos registrados
                  </span>
                </div>

                <div className="mt-4 grid grid-cols-3 items-center gap-2 text-center">
                  <div className="rounded-lg bg-white/[0.02] p-2.5">
                    <p className="text-[11px] text-slate-400 truncate">{awayName}</p>
                    <p className="mt-1 font-mono text-xl font-bold text-cyan-300">{context.h2h?.away_wins ?? 0}</p>
                    <p className="text-[10px] text-slate-500">Victorias</p>
                  </div>
                  <div className="rounded-lg bg-white/[0.02] p-2.5">
                    <p className="text-[11px] text-slate-400">Empates</p>
                    <p className="mt-1 font-mono text-xl font-bold text-slate-300">{context.h2h?.draws ?? 0}</p>
                    <p className="text-[10px] text-slate-500">Igualdades</p>
                  </div>
                  <div className="rounded-lg bg-white/[0.02] p-2.5">
                    <p className="text-[11px] text-slate-400 truncate">{homeName}</p>
                    <p className="mt-1 font-mono text-xl font-bold text-emerald-300">{context.h2h?.home_wins ?? 0}</p>
                    <p className="text-[10px] text-slate-500">Victorias</p>
                  </div>
                </div>

                {/* Barra de Proporción H2H */}
                {(context.h2h?.total_games ?? 0) > 0 && (
                  <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-white/5">
                    <div
                      style={{ width: `${((context.h2h?.away_wins ?? 0) / context.h2h.total_games) * 100}%` }}
                      className="bg-cyan-400 transition-all duration-500"
                      title={`${awayName}: ${context.h2h?.away_wins ?? 0}`}
                    />
                    <div
                      style={{ width: `${((context.h2h?.draws ?? 0) / context.h2h.total_games) * 100}%` }}
                      className="bg-slate-500 transition-all duration-500"
                      title={`Empates: ${context.h2h?.draws ?? 0}`}
                    />
                    <div
                      style={{ width: `${((context.h2h?.home_wins ?? 0) / context.h2h.total_games) * 100}%` }}
                      className="bg-emerald-400 transition-all duration-500"
                      title={`${homeName}: ${context.h2h?.home_wins ?? 0}`}
                    />
                  </div>
                )}
              </div>

              {/* Forma Reciente (Últimos 5 Partidos) */}
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <TrendingUp className="h-4 w-4 text-emerald-400" />
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                      Forma Reciente (Últimos 5 encuentros)
                    </h3>
                  </div>
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                  {/* Forma Visitante */}
                  <TeamFormCard
                    teamName={awayName}
                    isAway
                    streak={context.recent_form?.away?.streak ?? []}
                    games={context.recent_form?.away?.games ?? []}
                  />
                  {/* Forma Local */}
                  <TeamFormCard
                    teamName={homeName}
                    isAway={false}
                    streak={context.recent_form?.home?.streak ?? []}
                    games={context.recent_form?.home?.games ?? []}
                  />
                </div>
              </div>

              {/* Últimos Duelos Directos Detallados */}
              {(context.h2h?.recent_clashes?.length ?? 0) > 0 && (
                <div className="rounded-xl border border-white/8 bg-white/[0.025] p-4">
                  <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-300">
                    Últimos enfrentamientos cara a cara
                  </h3>
                  <div className="divide-y divide-white/5">
                    {context.h2h.recent_clashes.map((clash, idx) => (
                      <div key={idx} className="flex items-center justify-between py-2.5 text-xs">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-slate-500">{clash.date}</span>
                          <span className="text-slate-300">
                            {clash.away_team} <span className="text-slate-500">@</span> {clash.home_team}
                          </span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="font-mono font-semibold text-slate-100">
                            {clash.away_score} — {clash.home_score}
                          </span>
                          <span className="rounded bg-white/5 px-2 py-0.5 font-mono text-[10px] text-emerald-300">
                            Ganó {clash.winner}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </motion.div>
          )}

          {activeTab === 'injuries' && (
            <motion.div
              key="tab-injuries"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
              className="mt-5 space-y-6"
            >
              <div className="rounded-lg border border-white/[0.06] bg-white/[0.025] p-3 text-[11px] text-slate-400" role="status">
                {context.injury_source?.status === 'unavailable'
                  ? 'No se pudo verificar el reporte de ESPN.'
                  : context.injury_source?.status === 'not_applicable_past_game'
                    ? 'El reporte actual no aplica a un partido pasado.'
                    : `Fuente: ${context.injury_source?.source ?? 'desconocida'} · captura ${context.injury_source?.captured_at_utc ?? 'sin timestamp'}${context.injury_source?.is_from_cache ? ` · caché de ${Math.round((context.injury_source.cache_age_seconds ?? 0) / 60)} min` : ' · respuesta en vivo'}`}
              </div>

              {game.sport === 'NFL' && <div className="rounded-xl border border-white/8 bg-white/[0.025] p-4">
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-emerald-400" />
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                    Impacto de producción (requiere cobertura y roster verificados)
                  </h3>
                </div>
                <p className="mt-1 text-[11px] text-slate-400">
                  Los porcentajes se muestran solo cuando existen denominadores y cobertura verificables.
                </p>

                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <div className="rounded-lg bg-white/[0.02] p-3">
                    <p className="text-xs font-semibold text-cyan-300 truncate">{awayName}</p>
                    <div className="mt-2 space-y-1.5 text-xs">
                      <div className="flex justify-between text-slate-400">
                        <span>Pérdida Ofensiva (PPR)</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries?.away_impact?.ppr_lost_pct)}
                        </span>
                      </div>
                      <div className="flex justify-between text-slate-400">
                        <span>Snaps Ofensivos Perdidos</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries?.away_impact?.off_snaps_lost_pct)}
                        </span>
                      </div>
                      <div className="flex justify-between text-slate-400">
                        <span>Snaps Defensivos Perdidos</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries?.away_impact?.def_snaps_lost_pct)}
                        </span>
                      </div>
                      {context.injuries?.away_impact?.qb_starter_injured && (
                        <div className="flex justify-between text-amber-300 font-medium">
                          <span>QB vs reemplazo de liga (EPA)</span>
                          <span className="font-mono">
                            {context.injuries.away_impact.qb_lost_expected_epa == null
                              ? 'No estimable'
                              : `${context.injuries.away_impact.qb_lost_expected_epa >= 0 ? '−' : '+'}${Math.abs(context.injuries.away_impact.qb_lost_expected_epa).toFixed(1)} EPA`}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-lg bg-white/[0.02] p-3">
                    <p className="text-xs font-semibold text-emerald-300 truncate">{homeName}</p>
                    <div className="mt-2 space-y-1.5 text-xs">
                      <div className="flex justify-between text-slate-400">
                        <span>Pérdida Ofensiva (PPR)</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries?.home_impact?.ppr_lost_pct)}
                        </span>
                      </div>
                      <div className="flex justify-between text-slate-400">
                        <span>Snaps Ofensivos Perdidos</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries?.home_impact?.off_snaps_lost_pct)}
                        </span>
                      </div>
                      <div className="flex justify-between text-slate-400">
                        <span>Snaps Defensivos Perdidos</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries?.home_impact?.def_snaps_lost_pct)}
                        </span>
                      </div>
                      {context.injuries?.home_impact?.qb_starter_injured && (
                        <div className="flex justify-between text-amber-300 font-medium">
                          <span>QB vs reemplazo de liga (EPA)</span>
                          <span className="font-mono">
                            {context.injuries.home_impact.qb_lost_expected_epa == null
                              ? 'No estimable'
                              : `${context.injuries.home_impact.qb_lost_expected_epa >= 0 ? '−' : '+'}${Math.abs(context.injuries.home_impact.qb_lost_expected_epa).toFixed(1)} EPA`}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* Reporte Detallado de Puntos Fantasy y Snaps Perdidos */}
                {((context.injuries?.away_impact?.affected_players?.length ?? 0) > 0 ||
                  (context.injuries?.home_impact?.affected_players?.length ?? 0) > 0) && (
                  <div className="mt-4 border-t border-white/5 pt-3.5">
                    <button
                      type="button"
                      onClick={() => setShowFantasyBreakdown((prev) => !prev)}
                      className="flex w-full items-center justify-between text-left group"
                    >
                      <div className="flex items-center gap-2">
                        <Zap className="h-4 w-4 text-amber-400" />
                        <span className="text-xs font-semibold text-slate-200 group-hover:text-amber-300 transition-colors">
                          Reporte de Puntos Fantasy y Snaps Perdidos por Jugador (nflverse SSOT)
                        </span>
                      </div>
                      <div className="flex items-center gap-1 text-[11px] text-slate-400 group-hover:text-slate-300">
                        <span>{showFantasyBreakdown ? 'Ocultar' : 'Ver detalle'}</span>
                        {showFantasyBreakdown ? (
                          <ChevronUp className="h-3.5 w-3.5" />
                        ) : (
                          <ChevronDown className="h-3.5 w-3.5" />
                        )}
                      </div>
                    </button>

                    {showFantasyBreakdown && (
                      <div className="mt-3 space-y-3">
                        <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 border border-amber-500/20 p-2.5 text-[11px] text-amber-200/90">
                          <Info className="h-4 w-4 shrink-0 text-amber-400 mt-0.5" />
                          <p>
                            <strong>Cálculo Real:</strong> Los puntos fantasy PPR (promedio histórico reciente) y volumen de snaps son obtenidos desde estadísticas reales de <em>nflverse</em>. Las ausencias confirmadas (OUT) se descuentan al 100%, mientras que las bajas cuestionables aplican la probabilidad base del 25%. El modelo de Machine Learning penaliza la probabilidad final del equipo en base a este impacto directo.
                          </p>
                        </div>

                        <div className="grid gap-3 md:grid-cols-2">
                          {/* Columna Visitante */}
                          <div className="rounded-lg bg-white/[0.015] border border-white/5 p-3">
                            <p className="text-[11px] font-semibold text-cyan-300 mb-2 truncate">
                              Bajas con impacto · {awayName}
                            </p>
                            {(!context.injuries?.away_impact?.affected_players ||
                              context.injuries.away_impact.affected_players.length === 0) ? (
                              <p className="text-[11px] text-slate-500">Sin penalizaciones de producción registradas.</p>
                            ) : (
                              <div className="space-y-2">
                                {context.injuries.away_impact.affected_players.map((p, idx) => (
                                  <PlayerProductionImpactRow key={idx} player={p} />
                                ))}
                              </div>
                            )}
                          </div>

                          {/* Columna Local */}
                          <div className="rounded-lg bg-white/[0.015] border border-white/5 p-3">
                            <p className="text-[11px] font-semibold text-emerald-300 mb-2 truncate">
                              Bajas con impacto · {homeName}
                            </p>
                            {(!context.injuries?.home_impact?.affected_players ||
                              context.injuries.home_impact.affected_players.length === 0) ? (
                              <p className="text-[11px] text-slate-500">Sin penalizaciones de producción registradas.</p>
                            ) : (
                              <div className="space-y-2">
                                {context.injuries.home_impact.affected_players.map((p, idx) => (
                                  <PlayerProductionImpactRow key={idx} player={p} />
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>}

              {/* Listas Detalladas de Bajas */}
              <div className="grid gap-4 md:grid-cols-2">
                <InjuriesListCard
                  teamName={awayName}
                  isAway
                  injuries={context.injuries?.away ?? []}
                  coverageState={context.injuries?.away_impact?.coverage_state}
                  affectedPlayers={context.injuries?.away_impact?.affected_players}
                />
                <InjuriesListCard
                  teamName={homeName}
                  isAway={false}
                  injuries={context.injuries?.home ?? []}
                  coverageState={context.injuries?.home_impact?.coverage_state}
                  affectedPlayers={context.injuries?.home_impact?.affected_players}
                />
              </div>
            </motion.div>
          )}

          {activeTab === 'stats' && (
            <motion.div
              key="tab-stats"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
              className="mt-5 space-y-5"
            >
              <div className="rounded-xl border border-white/8 bg-white/[0.025] p-4">
                <div className="flex items-center gap-2">
                  <BarChart3 className="h-4 w-4 text-emerald-400" />
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                    Duelo Estadístico Comparativo (SofaScore Duel Bars)
                  </h3>
                </div>
                <p className="mt-1 text-[11px] text-slate-400">
                  Rendimiento real por partido en los últimos 10 encuentros disputados de cada escuadra.
                </p>

                <div className="mt-6 space-y-5">
                  <DuelComparisonBar
                    label="Puntos por Partido (PPG)"
                    awayVal={context.comparison_stats?.away?.ppg ?? null}
                    homeVal={context.comparison_stats?.home?.ppg ?? null}
                    awayName={awayName}
                    homeName={homeName}
                  />

                  <DuelComparisonBar
                    label="Puntos Permitidos por Partido"
                    awayVal={context.comparison_stats?.away?.opp_ppg ?? null}
                    homeVal={context.comparison_stats?.home?.opp_ppg ?? null}
                    awayName={awayName}
                    homeName={homeName}
                    inverted
                  />

                  <DuelComparisonBar
                    label="Diferencial de Puntos (+/-)"
                    awayVal={context.comparison_stats?.away?.avg_margin ?? null}
                    homeVal={context.comparison_stats?.home?.avg_margin ?? null}
                    awayName={awayName}
                    homeName={homeName}
                  />

                  <DuelComparisonBar
                    label="Días de Descanso"
                    awayVal={context.comparison_stats?.away?.days_rest ?? null}
                    homeVal={context.comparison_stats?.home?.days_rest ?? null}
                    awayName={awayName}
                    homeName={homeName}
                  />
                </div>
              </div>
            </motion.div>
          )}

          {activeTab === 'calendar' && (
            <motion.div
              key="tab-calendar"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
              className="mt-5"
            >
              <LeagueMetricsSection game={game} homeName={homeName} awayName={awayName} />
            </motion.div>
          )}
        </AnimatePresence>
      )}

      {/* Fallback si no hay contexto disponible */}
      {!isLoading && !context && (
        <div className="mt-5">
          <LeagueMetricsSection game={game} homeName={homeName} awayName={awayName} />
        </div>
      )}
    </section>
  )
}

function TeamFormCard({
  teamName,
  isAway,
  streak = [],
  games = [],
}: {
  teamName: string
  isAway: boolean
  streak?: Array<'W' | 'L' | 'T'>
  games?: any[]
}) {
  const safeStreak = streak ?? []
  const safeGames = games ?? []

  return (
    <div className="rounded-xl border border-white/8 bg-white/[0.025] p-3.5">
      <div className="flex items-center justify-between">
        <p className={`text-xs font-semibold truncate ${isAway ? 'text-cyan-300' : 'text-emerald-300'}`}>
          {teamName}
        </p>
        <div className="flex items-center gap-1">
          {safeStreak.length > 0 ? (
            safeStreak.map((result, idx) => (
              <span
                key={idx}
                className={`flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold ${
                  result === 'W'
                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                    : result === 'L'
                    ? 'bg-rose-500/20 text-rose-400 border border-rose-500/40'
                    : 'bg-zinc-700/30 text-zinc-300 border border-zinc-600/30'
                }`}
              >
                {result}
              </span>
            ))
          ) : (
            <span className="text-[11px] text-slate-500">Sin registro</span>
          )}
        </div>
      </div>

      <div className="mt-3 divide-y divide-white/5">
        {safeGames.map((g, idx) => (
          <div key={idx} className="flex items-center justify-between py-1.5 text-xs">
            <span className="font-mono text-slate-500 text-[11px]">{g.date}</span>
            <span className="text-slate-300">
              {g.is_home ? 'vs' : '@'} {g.opponent}
            </span>
            <div className="flex items-center gap-2">
              <span className="font-mono text-slate-200">
                {g.team_score} - {g.opponent_score}
              </span>
              <span
                className={`w-4 text-center font-bold text-[10px] ${
                  g.result === 'W'
                    ? 'text-emerald-400'
                    : g.result === 'L'
                    ? 'text-rose-400'
                    : 'text-slate-400'
                }`}
              >
                {g.result}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function PlayerProductionImpactRow({ player }: { player: AffectedPlayerProduction }) {
  const statusUpper = (player.status ?? '').toUpperCase()
  const isOut = statusUpper === 'OUT' || (player.p_absence ?? 0) >= 0.99
  const hasPpr = (player.rolling_ppr ?? 0) > 0 || (player.lost_ppr ?? 0) > 0
  const hasOffSnaps = (player.rolling_off_pct ?? 0) > 0 || (player.lost_off_snaps ?? 0) > 0
  const hasDefSnaps = (player.rolling_def_pct ?? 0) > 0 || (player.lost_def_snaps ?? 0) > 0

  return (
    <div className="rounded-md bg-white/[0.02] border border-white/5 p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="font-medium text-slate-200 truncate">{player.player_name}</span>
          <span className="rounded bg-white/5 px-1 py-0.2 font-mono text-[9px] text-slate-400">
            {player.position}
          </span>
        </div>
        <span
          className={`shrink-0 rounded px-1.5 py-0.2 font-mono text-[10px] font-semibold ${
            isOut
              ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
              : 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
          }`}
        >
          {player.status} ({Math.round(player.p_absence * 100)}% ausente)
        </span>
      </div>

      <div className="mt-1.5 space-y-1 font-mono text-[10px]">
        {hasPpr ? (
          <div className="flex items-center justify-between text-slate-400">
            <span>Puntos Fantasy (PPR):</span>
            <span className="text-slate-200">
              {(player.rolling_ppr ?? 0).toFixed(1)} habitual · <strong className="text-rose-400">-{(player.lost_ppr ?? 0).toFixed(1)} pts</strong>
            </span>
          </div>
        ) : (
          <div className="flex items-center justify-between text-slate-500">
            <span>Puntos Fantasy (PPR):</span>
            <span>0.0 (Posición de trinchera / defensa)</span>
          </div>
        )}

        {hasOffSnaps && (
          <div className="flex items-center justify-between text-slate-400">
            <span>Snaps Ofensivos:</span>
            <span className="text-slate-200">
              {Math.round(player.rolling_off_pct * 100)}% habitual · <strong className="text-amber-400">-{Math.round(player.lost_off_snaps * 100)}% en campo</strong>
            </span>
          </div>
        )}

        {hasDefSnaps && (
          <div className="flex items-center justify-between text-slate-400">
            <span>Snaps Defensivos:</span>
            <span className="text-slate-200">
              {Math.round(player.rolling_def_pct * 100)}% habitual · <strong className="text-cyan-400">-{Math.round(player.lost_def_snaps * 100)}% en campo</strong>
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

function InjuriesListCard({
  teamName,
  isAway,
  injuries,
  coverageState,
  affectedPlayers,
}: {
  teamName: string
  isAway: boolean
  injuries: OfficialInjury[]
  coverageState?: string
  affectedPlayers?: AffectedPlayerProduction[]
}) {
  return (
    <div className="rounded-xl border border-white/8 bg-white/[0.025] p-3.5">
      <div className="flex items-center justify-between border-b border-white/5 pb-2.5">
        <p className={`text-xs font-semibold truncate ${isAway ? 'text-cyan-300' : 'text-emerald-300'}`}>
          {teamName}
        </p>
        <span className="font-mono text-[11px] text-slate-400">
          {injuries.length} baja(s)
        </span>
      </div>

      {injuries.length === 0 ? (
        <div className="flex min-h-28 flex-col items-center justify-center text-center text-slate-500">
          <CheckCircle2 className="h-5 w-5 text-emerald-500/50 mb-1" />
          <p className="text-xs">{coverageState === 'covered_healthy' ? 'Reporte verificado sin lesiones' : coverageState === 'no_injuries_listed_in_capture' ? 'Equipo ausente en el reporte; salud no confirmada' : 'Sin registros de lesiones verificables'}</p>
        </div>
      ) : (
        <div className="mt-2.5 max-h-72 space-y-2 overflow-y-auto pr-1">
          {injuries.map((item, idx) => {
            const statusUpper = (item.status ?? '').toUpperCase()
            const isOut = statusUpper === 'OUT' || statusUpper.includes('IR')
            const isQuest = statusUpper.includes('QUESTIONABLE')
            const itemName = (item.player_name ?? '').toLowerCase().trim()
            const impact = affectedPlayers?.find((ap) => {
              const apName = (ap.player_name ?? '').toLowerCase().trim()
              if (!apName || !itemName) return false
              return apName === itemName || apName.includes(itemName) || itemName.includes(apName)
            })
            return (
              <div
                key={idx}
                className="rounded-lg bg-white/[0.02] p-2 text-xs border border-white/5"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate font-medium text-slate-200">{item.player_name}</span>
                      <span className="rounded bg-white/5 px-1 py-0.2 font-mono text-[9px] text-slate-400">
                        {item.position}
                      </span>
                    </div>
                    <p className="mt-0.5 truncate text-[10px] text-slate-500">
                      {item.injury !== '—' ? item.injury : 'No especificado'}
                    </p>
                  </div>
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                      isOut
                        ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                        : isQuest
                        ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                        : 'bg-zinc-700/30 text-zinc-300 border border-zinc-600/30'
                    }`}
                  >
                    {item.status}
                  </span>
                </div>

                {/* Métricas individuales de Fantasy Points y Snaps */}
                {impact && (impact.lost_ppr > 0 || impact.lost_off_snaps > 0 || impact.lost_def_snaps > 0) && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 font-mono text-[9px] border-t border-white/5 pt-1">
                    {impact.lost_ppr > 0 && (
                      <span className="rounded bg-rose-500/15 px-1 py-0.5 text-rose-300 border border-rose-500/30">
                        -{(impact.lost_ppr ?? 0).toFixed(1)} pts PPR ({(impact.rolling_ppr ?? 0).toFixed(1)} hab.)
                      </span>
                    )}
                    {impact.lost_off_snaps > 0 && (
                      <span className="rounded bg-amber-500/15 px-1 py-0.5 text-amber-300 border border-amber-500/30">
                        -{Math.round(impact.lost_off_snaps * 100)}% snaps OF
                      </span>
                    )}
                    {impact.lost_def_snaps > 0 && (
                      <span className="rounded bg-cyan-500/15 px-1 py-0.5 text-cyan-300 border border-cyan-500/30">
                        -{Math.round(impact.lost_def_snaps * 100)}% snaps DEF
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function DuelComparisonBar({
  label,
  awayVal,
  homeVal,
  awayName: _awayName,
  homeName: _homeName,
  inverted = false
}: {
  label: string
  awayVal: number | null
  homeVal: number | null
  awayName: string
  homeName: string
  inverted?: boolean
}) {
  if (awayVal == null || homeVal == null) {
    return <div className="flex items-center justify-between gap-3 text-xs text-slate-400"><span>{label}</span><span>Sin datos suficientes</span></div>
  }
  const sum = Math.abs(awayVal) + Math.abs(homeVal) || 1
  const awayPct = Math.min(100, Math.max(0, (Math.abs(awayVal) / sum) * 100))
  const homePct = 100 - awayPct

  const awayWins = inverted ? awayVal < homeVal : awayVal > homeVal
  const homeWins = inverted ? homeVal < awayVal : homeVal > awayVal

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span
          className={`font-mono font-semibold ${
            awayWins ? 'text-cyan-300' : 'text-slate-400'
          }`}
        >
          {awayVal}
        </span>
        <span className="text-[11px] font-medium uppercase tracking-wider text-slate-400">
          {label}
        </span>
        <span
          className={`font-mono font-semibold ${
            homeWins ? 'text-emerald-300' : 'text-slate-400'
          }`}
        >
          {homeVal}
        </span>
      </div>

      <div className="flex h-1.5 overflow-hidden rounded-full bg-white/5">
        <div
          style={{ width: `${awayPct}%` }}
          className={`transition-all duration-500 ${
            awayWins ? 'bg-cyan-400' : 'bg-cyan-400/40'
          }`}
        />
        <div
          style={{ width: `${homePct}%` }}
          className={`transition-all duration-500 ${
            homeWins ? 'bg-emerald-400' : 'bg-emerald-400/40'
          }`}
        />
      </div>
    </div>
  )
}

function LeagueMetricsSection({
  game,
  homeName,
  awayName
}: {
  game: Game
  homeName: string
  awayName: string
}) {
  const homeStats = game?.team_stats?.home
  const awayStats = game?.team_stats?.away

  const metrics = [
    { label: 'Porcentaje de victorias', home: formatPercentage(homeStats?.wpct), away: formatPercentage(awayStats?.wpct) },
    { label: 'Rating neto', home: formatNumber(homeStats?.net_rtg), away: formatNumber(awayStats?.net_rtg) },
    { label: 'Rating ofensivo', home: formatNumber(homeStats?.off_rtg), away: formatNumber(awayStats?.off_rtg) },
    { label: 'Rating defensivo', home: formatNumber(homeStats?.def_rtg), away: formatNumber(awayStats?.def_rtg) },
    { label: 'Ritmo', home: formatNumber(homeStats?.pace), away: formatNumber(awayStats?.pace) },
    { label: 'Descanso (días)', home: homeStats?.days_rest == null ? '—' : `${homeStats.days_rest}`, away: awayStats?.days_rest == null ? '—' : `${awayStats.days_rest}` },
  ]

  return (
    <div>
      <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] gap-2 border-b border-white/8 px-2 pb-3 text-xs font-medium text-slate-400">
        <span className="truncate">{awayName}</span>
        <span className="text-center">Métrica</span>
        <span className="truncate text-right">{homeName}</span>
      </div>
      <div className="divide-y divide-white/5">
        {metrics.map((metric) => (
          <div key={metric.label} className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 px-2 py-3 text-sm">
            <span className="font-mono text-slate-200">{metric.away}</span>
            <span className="text-center text-xs text-slate-500">{metric.label}</span>
            <span className="text-right font-mono text-slate-200">{metric.home}</span>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[11px] text-slate-500">
        Métricas basadas solo en partidos completados antes de la fecha objetivo. Los campos sin cobertura se indican como —.
      </p>
    </div>
  )
}
