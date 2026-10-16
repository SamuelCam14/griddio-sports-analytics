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
  CheckCircle2
} from 'lucide-react'

import { gamesApi, type Game, type MatchupContext, type OfficialInjury } from '@/services/api'
import { formatNumber, formatPercentage } from '@/utils/dashboard'

interface TeamMetricsPanelProps {
  game: Game
  homeName: string
  awayName: string
}

type TabType = 'h2h' | 'injuries' | 'stats' | 'calendar'

export function TeamMetricsPanel({ game, homeName, awayName }: TeamMetricsPanelProps) {
  const [activeTab, setActiveTab] = useState<TabType>('h2h')

  const { data: context, isLoading, isError } = useQuery<MatchupContext>({
    queryKey: ['gameContext', game.id],
    queryFn: () => gamesApi.getContext(game.id).then((res) => res.data),
    staleTime: 5 * 60_000,
  })

  const homeInjuriesCount = context?.injuries.home.length ?? 0
  const awayInjuriesCount = context?.injuries.away.length ?? 0
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
                    {context.h2h.total_games} enfrentamientos registrados
                  </span>
                </div>

                <div className="mt-4 grid grid-cols-3 items-center gap-2 text-center">
                  <div className="rounded-lg bg-white/[0.02] p-2.5">
                    <p className="text-[11px] text-slate-400 truncate">{awayName}</p>
                    <p className="mt-1 font-mono text-xl font-bold text-cyan-300">{context.h2h.away_wins}</p>
                    <p className="text-[10px] text-slate-500">Victorias</p>
                  </div>
                  <div className="rounded-lg bg-white/[0.02] p-2.5">
                    <p className="text-[11px] text-slate-400">Empates</p>
                    <p className="mt-1 font-mono text-xl font-bold text-slate-300">{context.h2h.draws}</p>
                    <p className="text-[10px] text-slate-500">Igualdades</p>
                  </div>
                  <div className="rounded-lg bg-white/[0.02] p-2.5">
                    <p className="text-[11px] text-slate-400 truncate">{homeName}</p>
                    <p className="mt-1 font-mono text-xl font-bold text-emerald-300">{context.h2h.home_wins}</p>
                    <p className="text-[10px] text-slate-500">Victorias</p>
                  </div>
                </div>

                {/* Barra de Proporción H2H */}
                {context.h2h.total_games > 0 && (
                  <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-white/5">
                    <div
                      style={{ width: `${(context.h2h.away_wins / context.h2h.total_games) * 100}%` }}
                      className="bg-cyan-400 transition-all duration-500"
                      title={`${awayName}: ${context.h2h.away_wins}`}
                    />
                    <div
                      style={{ width: `${(context.h2h.draws / context.h2h.total_games) * 100}%` }}
                      className="bg-slate-500 transition-all duration-500"
                      title={`Empates: ${context.h2h.draws}`}
                    />
                    <div
                      style={{ width: `${(context.h2h.home_wins / context.h2h.total_games) * 100}%` }}
                      className="bg-emerald-400 transition-all duration-500"
                      title={`${homeName}: ${context.h2h.home_wins}`}
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
                    streak={context.recent_form.away.streak}
                    games={context.recent_form.away.games}
                  />
                  {/* Forma Local */}
                  <TeamFormCard
                    teamName={homeName}
                    isAway={false}
                    streak={context.recent_form.home.streak}
                    games={context.recent_form.home.games}
                  />
                </div>
              </div>

              {/* Últimos Duelos Directos Detallados */}
              {context.h2h.recent_clashes.length > 0 && (
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
                {context.injury_source.status === 'unavailable'
                  ? 'No se pudo verificar el reporte de ESPN.'
                  : context.injury_source.status === 'not_applicable_past_game'
                    ? 'El reporte actual no aplica a un partido pasado.'
                    : `Fuente: ${context.injury_source.source ?? 'desconocida'} · captura ${context.injury_source.captured_at_utc ?? 'sin timestamp'}${context.injury_source.is_from_cache ? ` · caché de ${Math.round((context.injury_source.cache_age_seconds ?? 0) / 60)} min` : ' · respuesta en vivo'}`}
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
                          {formatPercentage(context.injuries.away_impact.ppr_lost_pct)}
                        </span>
                      </div>
                      <div className="flex justify-between text-slate-400">
                        <span>Snaps Ofensivos Perdidos</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries.away_impact.off_snaps_lost_pct)}
                        </span>
                      </div>
                      <div className="flex justify-between text-slate-400">
                        <span>Snaps Defensivos Perdidos</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries.away_impact.def_snaps_lost_pct)}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-lg bg-white/[0.02] p-3">
                    <p className="text-xs font-semibold text-emerald-300 truncate">{homeName}</p>
                    <div className="mt-2 space-y-1.5 text-xs">
                      <div className="flex justify-between text-slate-400">
                        <span>Pérdida Ofensiva (PPR)</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries.home_impact.ppr_lost_pct)}
                        </span>
                      </div>
                      <div className="flex justify-between text-slate-400">
                        <span>Snaps Ofensivos Perdidos</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries.home_impact.off_snaps_lost_pct)}
                        </span>
                      </div>
                      <div className="flex justify-between text-slate-400">
                        <span>Snaps Defensivos Perdidos</span>
                        <span className="font-mono text-slate-200">
                          {formatPercentage(context.injuries.home_impact.def_snaps_lost_pct)}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>}

              {/* Listas Detalladas de Bajas */}
              <div className="grid gap-4 md:grid-cols-2">
                <InjuriesListCard
                  teamName={awayName}
                  isAway
                  injuries={context.injuries.away}
                  coverageState={context.injuries.away_impact.coverage_state}
                />
                <InjuriesListCard
                  teamName={homeName}
                  isAway={false}
                  injuries={context.injuries.home}
                  coverageState={context.injuries.home_impact.coverage_state}
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
                    awayVal={context.comparison_stats.away.ppg}
                    homeVal={context.comparison_stats.home.ppg}
                    awayName={awayName}
                    homeName={homeName}
                  />

                  <DuelComparisonBar
                    label="Puntos Permitidos por Partido"
                    awayVal={context.comparison_stats.away.opp_ppg}
                    homeVal={context.comparison_stats.home.opp_ppg}
                    awayName={awayName}
                    homeName={homeName}
                    inverted
                  />

                  <DuelComparisonBar
                    label="Diferencial de Puntos (+/-)"
                    awayVal={context.comparison_stats.away.avg_margin}
                    homeVal={context.comparison_stats.home.avg_margin}
                    awayName={awayName}
                    homeName={homeName}
                  />

                  <DuelComparisonBar
                    label="Días de Descanso"
                    awayVal={context.comparison_stats.away.days_rest}
                    homeVal={context.comparison_stats.home.days_rest}
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
  streak,
  games
}: {
  teamName: string
  isAway: boolean
  streak: Array<'W' | 'L' | 'T'>
  games: any[]
}) {
  return (
    <div className="rounded-xl border border-white/8 bg-white/[0.025] p-3.5">
      <div className="flex items-center justify-between">
        <p className={`text-xs font-semibold truncate ${isAway ? 'text-cyan-300' : 'text-emerald-300'}`}>
          {teamName}
        </p>
        <div className="flex items-center gap-1">
          {streak.length > 0 ? (
            streak.map((result, idx) => (
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
        {games.map((g, idx) => (
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

function InjuriesListCard({
  teamName,
  isAway,
  injuries,
  coverageState,
}: {
  teamName: string
  isAway: boolean
  injuries: OfficialInjury[]
  coverageState?: string
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
            const isOut = item.status.toUpperCase() === 'OUT' || item.status.toUpperCase().includes('IR')
            const isQuest = item.status.toUpperCase().includes('QUESTIONABLE')
            return (
              <div
                key={idx}
                className="flex items-center justify-between gap-2 rounded-lg bg-white/[0.02] p-2 text-xs"
              >
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
  const metrics = [
    { label: 'Porcentaje de victorias', home: formatPercentage(game.team_stats.home.wpct), away: formatPercentage(game.team_stats.away.wpct) },
    { label: 'Rating neto', home: formatNumber(game.team_stats.home.net_rtg), away: formatNumber(game.team_stats.away.net_rtg) },
    { label: 'Rating ofensivo', home: formatNumber(game.team_stats.home.off_rtg), away: formatNumber(game.team_stats.away.off_rtg) },
    { label: 'Rating defensivo', home: formatNumber(game.team_stats.home.def_rtg), away: formatNumber(game.team_stats.away.def_rtg) },
    { label: 'Ritmo', home: formatNumber(game.team_stats.home.pace), away: formatNumber(game.team_stats.away.pace) },
    { label: 'Descanso (días)', home: game.team_stats.home.days_rest == null ? '—' : `${game.team_stats.home.days_rest}`, away: game.team_stats.away.days_rest == null ? '—' : `${game.team_stats.away.days_rest}` },
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
