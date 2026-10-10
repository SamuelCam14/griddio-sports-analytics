import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Maximize2, Sparkles, TrendingUp, Users, ShieldAlert, BarChart2 } from 'lucide-react'
import { TeamLogo } from '@/components/ui/TeamLogo'
import { PillTabs, type TabOption } from '@/components/ui/PillTabs'
import type { ScheduledMatch } from '@/types/dashboard'
import { formatMatchDate, formatMatchTime } from '@/utils/dashboard'

interface LiveMatchDetailCardProps {
  match?: ScheduledMatch
  sport: 'NBA' | 'NFL'
  onOpenDeepDive: () => void
}

type TabType = 'timeline' | 'lineup' | 'stats' | 'insights'

const TABS: TabOption<TabType>[] = [
  { id: 'timeline', label: 'Momios' },
  { id: 'lineup', label: 'Bajas/Props' },
  { id: 'stats', label: 'Stats' },
  { id: 'insights', label: 'Insights AI' },
]

export function LiveMatchDetailCard({
  match,
  sport,
  onOpenDeepDive,
}: LiveMatchDetailCardProps) {
  const [activeTab, setActiveTab] = useState<TabType>('timeline')
  const isNba = sport === 'NBA'

  const homeTeam = match?.odds.home_team ?? (isNba ? 'Boston Celtics' : 'Kansas City Chiefs')
  const awayTeam = match?.odds.away_team ?? (isNba ? 'Los Angeles Lakers' : 'San Francisco 49ers')
  const homeScore = match?.game.home_score ?? (isNba ? 108 : 27)
  const awayScore = match?.game.away_score ?? (isNba ? 104 : 24)
  const isLive = match?.game.status === 'live'
  const isFinal = match?.game.status === 'final'
  const clockText = isLive ? 'Q4 03:45' : isFinal ? 'FINAL' : formatMatchTime(match?.odds.commence_time ?? new Date().toISOString())

  const homeProb = match?.prediction?.home_win_prob ?? match?.odds.home_implied_prob ?? 0.6
  const awayProb = match?.prediction?.away_win_prob ?? match?.odds.away_implied_prob ?? 0.4
  const homePct = Math.round(homeProb * 100)
  const awayPct = Math.round(awayProb * 100)

  return (
    <div className="glass-panel p-4 sm:p-5 flex flex-col justify-between">
      <div>
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h3 className="section-title-sm text-white">Live Matches</h3>
            <p className="mt-0.5 text-[11px] text-slate-400">
              {match ? `${sport} · ${formatMatchDate(match.odds.commence_time)}` : `${sport} · Cartelera Estelar`}
            </p>
          </div>
          <button
            type="button"
            onClick={onOpenDeepDive}
            title="Expandir centro de análisis"
            className="btn-icon-glass"
          >
            <Maximize2 className="h-3.5 w-3.5 text-slate-300" />
          </button>
        </div>

        {/* Scoreboard banner */}
        <div className="mt-4 rounded-2xl border border-white/10 bg-black/30 p-4 backdrop-blur-md">
          {/* Teams and Score */}
          <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
            {/* Away Team */}
            <div className="flex flex-col items-center text-center">
              <TeamLogo name={awayTeam} size="lg" />
              <span className="mt-2 text-xs font-bold text-white truncate max-w-[100px]">{awayTeam}</span>
              <span className="text-[10px] text-slate-400">Visitante</span>
            </div>

            {/* Scores & Clock */}
            <div className="flex flex-col items-center">
              <div className="flex items-center gap-2 font-mono text-2xl sm:text-3xl font-extrabold text-white">
                <span>{awayScore}</span>
                <span className="text-slate-600">-</span>
                <span>{homeScore}</span>
              </div>
              <span className="mt-1.5 rounded-full border border-emerald-400/30 bg-emerald-400/15 px-2.5 py-0.5 font-mono text-[10px] font-bold text-emerald-300">
                {clockText}
              </span>
            </div>

            {/* Home Team */}
            <div className="flex flex-col items-center text-center">
              <TeamLogo name={homeTeam} size="lg" />
              <span className="mt-2 text-xs font-bold text-white truncate max-w-[100px]">{homeTeam}</span>
              <span className="text-[10px] text-slate-400">Local</span>
            </div>
          </div>

          {/* Win Probability Bar */}
          <div className="mt-4 space-y-1.5 border-t border-white/10 pt-3">
            <div className="flex items-center justify-between text-[11px] font-mono text-slate-300">
              <span>{awayPct}% Win</span>
              <span className="text-[10px] uppercase tracking-wider text-slate-400">Prob. Modelo</span>
              <span>{homePct}% Win</span>
            </div>
            <div className="flex h-2 w-full overflow-hidden rounded-full bg-cyan-900/40">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${awayPct}%` }}
                transition={{ duration: 0.6 }}
                className="h-full bg-cyan-400"
              />
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${homePct}%` }}
                transition={{ duration: 0.6 }}
                className="h-full bg-emerald-400"
              />
            </div>
          </div>
        </div>

        {/* Tab switcher */}
        <div className="mt-4 flex justify-center">
          <PillTabs
            tabs={TABS}
            activeTab={activeTab}
            onChange={setActiveTab}
            size="sm"
          />
        </div>

        {/* Tab Content Panels */}
        <div className="mt-3 min-h-[140px] rounded-xl border border-white/5 bg-black/20 p-3 text-xs">
          <AnimatePresence mode="wait">
            {activeTab === 'timeline' && (
              <motion.div
                key="timeline"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="space-y-2"
              >
                <div className="flex justify-between text-slate-400 border-b border-white/5 pb-1">
                  <span>Mercado Moneyline</span>
                  <span className="text-slate-200 font-mono">
                    {match?.odds.away_ml ? (match.odds.away_ml > 0 ? `+${match.odds.away_ml}` : match.odds.away_ml) : '+115'} / {match?.odds.home_ml ? (match.odds.home_ml > 0 ? `+${match.odds.home_ml}` : match.odds.home_ml) : '-135'}
                  </span>
                </div>
                <div className="flex justify-between text-slate-400 border-b border-white/5 pb-1">
                  <span>Línea Hándicap</span>
                  <span className="text-slate-200 font-mono">{match?.odds.home_spread ?? '-4.5'}</span>
                </div>
                <div className="flex justify-between text-slate-400 border-b border-white/5 pb-1">
                  <span>Línea Total (O/U)</span>
                  <span className="text-slate-200 font-mono">{match?.odds.total_line ?? (isNba ? '226.5' : '47.5')}</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Mejor Bookmaker</span>
                  <span className="text-emerald-300 font-semibold">{match?.odds.best_bookmaker ?? 'Pinnacle'}</span>
                </div>
              </motion.div>
            )}

            {activeTab === 'lineup' && (
              <motion.div
                key="lineup"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="space-y-1.5"
              >
                <p className="text-[10px] font-semibold uppercase text-emerald-400">Props Destacados</p>
                {match?.prediction?.player_props && match.prediction.player_props.length > 0 ? (
                  match.prediction.player_props.slice(0, 3).map((prop, i) => (
                    <div key={i} className="flex justify-between text-slate-300">
                      <span className="truncate">{prop.player_name} ({prop.prop_type})</span>
                      <span className="font-mono text-emerald-300">{prop.market_line}</span>
                    </div>
                  ))
                ) : (
                  <p className="text-slate-400 text-[11px] leading-relaxed">
                    Consulta el reporte de bajas de ambos equipos y análisis de quintetos iniciales en el centro integral.
                  </p>
                )}
              </motion.div>
            )}

            {activeTab === 'stats' && (
              <motion.div
                key="stats"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="space-y-2"
              >
                <div className="flex justify-between text-slate-400">
                  <span>Rating Ofensivo</span>
                  <span className="text-white font-mono">116.8 vs 114.2</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Rating Defensivo</span>
                  <span className="text-white font-mono">110.4 vs 112.9</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Ritmo (Pace)</span>
                  <span className="text-white font-mono">99.8 posesiones</span>
                </div>
              </motion.div>
            )}

            {activeTab === 'insights' && (
              <motion.div
                key="insights"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="space-y-1.5"
              >
                <p className="text-[10px] font-semibold uppercase text-amber-400">Ventaja Cuantitativa</p>
                <p className="text-slate-300 text-[11px] leading-snug">
                  {match?.prediction?.best_bet?.reason ?? 'El ensamble detecta desajuste en el rebote defensivo y valor positivo en la línea de dinero del local.'}
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* Big glowing CTA button "Watch Now" */}
      <div className="mt-4">
        <button
          type="button"
          onClick={onOpenDeepDive}
          className="btn-glow-primary w-full py-3.5 text-center text-sm font-bold uppercase tracking-wider"
        >
          <Sparkles className="h-4 w-4" />
          <span>Watch Now / Ver Análisis</span>
        </button>
      </div>
    </div>
  )
}
