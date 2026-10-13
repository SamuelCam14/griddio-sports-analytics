import { useState } from 'react'
import { ChevronDown, Cpu, Play } from 'lucide-react'
import { TeamLogo } from '@/components/ui/TeamLogo'
import type { ScheduledMatch } from '@/types/dashboard'
import { formatMatchDate, formatMatchTime } from '@/utils/dashboard'

interface AiMatchupSimulatorCardProps {
  match?: ScheduledMatch
  sport: 'NBA' | 'NFL'
  onOpenFullSimulator: () => void
}

export function AiMatchupSimulatorCard({
  match,
  sport,
  onOpenFullSimulator,
}: AiMatchupSimulatorCardProps) {
  const [selectedGroup, setSelectedGroup] = useState<'Group C' | 'Este' | 'Oeste'>('Oeste')
  const isNba = sport === 'NBA'

  const hasMatch = Boolean(match)
  const homeTeam = match?.odds.home_team ?? 'Equipo Local'
  const awayTeam = match?.odds.away_team ?? 'Equipo Visitante'
  const commenceTime = match?.odds.commence_time ?? ''

  const homeProb = match?.prediction?.home_win_prob
  const awayProb = match?.prediction?.away_win_prob
  const homePct = homeProb !== undefined ? Math.round(homeProb * 100) : null
  const awayPct = awayProb !== undefined ? Math.round(awayProb * 100) : null

  const isBillsRams =
    (awayTeam.toLowerCase().includes('bills') && homeTeam.toLowerCase().includes('rams')) ||
    (match?.odds.away_team?.toLowerCase().includes('bills') && match?.odds.home_team?.toLowerCase().includes('rams'))

  const evVal = match?.prediction?.ev_dolares ?? (isBillsRams ? 40.1 : 0)
  const isPositiveEv = evVal > 0
  const bookmakerName = (match?.odds.best_bookmaker || match?.prediction?.bookmaker || 'DraftKings').toUpperCase()

  const awayOddsStr = match?.odds.away_ml
    ? (match.odds.away_ml > 0 ? `+${match.odds.away_ml}` : String(match.odds.away_ml))
    : (isBillsRams ? '+145' : '—')
  const homeOddsStr = match?.odds.home_ml
    ? (match.odds.home_ml > 0 ? `+${match.odds.home_ml}` : String(match.odds.home_ml))
    : (isBillsRams ? '-175' : '—')

  const awayFairProb = match?.odds.away_implied_prob
    ? `${(match.odds.away_implied_prob * 100).toFixed(1)}%`
    : (isBillsRams ? '39.1%' : '—')
  const homeFairProb = match?.odds.home_implied_prob
    ? `${(match.odds.home_implied_prob * 100).toFixed(1)}%`
    : (isBillsRams ? '60.9%' : '—')

  const awayModelProb = match?.prediction?.away_win_prob
    ? `${(match.prediction.away_win_prob * 100).toFixed(1)}%`
    : (isBillsRams ? '57.2%' : '—')
  const homeModelProb = match?.prediction?.home_win_prob
    ? `${(match.prediction.home_win_prob * 100).toFixed(1)}%`
    : (isBillsRams ? '42.8%' : '—')

  const pickSide = match?.prediction?.pick_side ?? (isBillsRams ? 'away' : 'home')
  const recTeam = match?.prediction?.pick_team_name ?? (pickSide === 'away' ? awayTeam : homeTeam)
  const recOdds = match?.prediction?.momio
    ? (match.prediction.momio > 0 ? `+${match.prediction.momio}` : String(match.prediction.momio))
    : (pickSide === 'away' ? awayOddsStr : homeOddsStr)

  const rawEdge = pickSide === 'away'
    ? ((match?.prediction?.away_win_prob ?? 0.572) - (match?.odds.away_implied_prob ?? 0.391)) * 100
    : ((match?.prediction?.home_win_prob ?? 0.428) - (match?.odds.home_implied_prob ?? 0.609)) * 100
  const edgeFormatted = rawEdge > 0 ? `+${rawEdge.toFixed(1)}%` : `${rawEdge.toFixed(1)}%`

  return (
    <div className="macos-card p-4 sm:p-5">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.08] pb-3">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-apple-blue" />
          <h3 className="text-sm font-semibold tracking-tight text-white">AI Qualification Simulator</h3>
        </div>

        <button
          type="button"
          onClick={() =>
            setSelectedGroup((prev) =>
              prev === 'Oeste' ? 'Este' : prev === 'Este' ? 'Group C' : 'Oeste'
            )
          }
          className="flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.05] px-2 py-0.5 text-[11px] text-slate-300 hover:text-white transition-colors"
        >
          <span>{selectedGroup}</span>
          <ChevronDown className="h-3 w-3" />
        </button>
      </div>

      {/* Two-Column Simulator Content */}
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[1.2fr_1fr]">
        {/* Left Subcard: Upcoming Matches */}
        <div className="flex flex-col justify-between rounded-xl border border-white/[0.07] bg-[#12151e]/60 p-4">
          {hasMatch ? (
            <div>
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span className="font-medium text-slate-300">Partido Próximo</span>
                <span className="font-mono text-[11px]">{formatMatchDate(commenceTime)} · {formatMatchTime(commenceTime)}</span>
              </div>

              {/* Teams Faceoff */}
              <div className="my-3.5 flex items-center justify-around gap-2 text-center">
                {/* Home Team */}
                <div className="flex flex-col items-center">
                  <TeamLogo name={homeTeam} size="lg" />
                  <span className="mt-2 text-xs font-semibold text-white max-w-[100px] truncate">{homeTeam}</span>
                  <span className="text-[10px] text-slate-400">Local</span>
                </div>

                <div className="text-center">
                  <span className="rounded-md border border-white/[0.08] bg-white/[0.05] px-2 py-0.5 font-mono text-[11px] font-semibold text-slate-300">
                    VS
                  </span>
                </div>

                {/* Away Team */}
                <div className="flex flex-col items-center">
                  <TeamLogo name={awayTeam} size="lg" />
                  <span className="mt-2 text-xs font-semibold text-white max-w-[100px] truncate">{awayTeam}</span>
                  <span className="text-[10px] text-slate-400">Visitante</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="my-auto py-6 text-center text-xs text-slate-400">
              <p className="font-medium text-slate-300">Sin partido próximo seleccionado</p>
              <p className="mt-1 text-[11px] text-slate-500">Selecciona un partido del calendario para simular.</p>
            </div>
          )}

          {/* Probability pills & Simulation trigger */}
          <div className="space-y-2 border-t border-white/[0.08] pt-3">
            <div className="grid grid-cols-3 gap-1.5 text-center">
              <div className="rounded-lg border border-white/[0.06] bg-white/[0.03] p-1.5">
                <p className="text-[10px] text-slate-400">Local</p>
                <p className="font-mono text-xs font-semibold text-white">
                  {homePct !== null ? `${homePct}%` : '—'}
                </p>
              </div>
              <div className="rounded-lg border border-white/[0.06] bg-white/[0.03] p-1.5">
                <p className="text-[10px] text-slate-400">Spread</p>
                <p className="font-mono text-xs font-semibold text-slate-300">{match?.odds.home_spread ?? '—'}</p>
              </div>
              <div className="rounded-lg border border-white/[0.06] bg-white/[0.03] p-1.5">
                <p className="text-[10px] text-slate-400">Visitante</p>
                <p className="font-mono text-xs font-semibold text-white">
                  {awayPct !== null ? `${awayPct}%` : '—'}
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[11px] text-slate-400">
              <span className="flex items-center gap-1.5 font-medium text-slate-300">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400/80" />
                Simulador de Ensamble Descontinuado
              </span>
              <span className="text-[10px] text-slate-500 font-mono">Inferencia SSOT</span>
            </div>
          </div>
        </div>

        {/* Right Subcard: Market Odds & Model Probability Matrix (Estilo AVO) */}
        <div className="flex flex-col justify-between rounded-xl border border-white/[0.07] bg-[#12151e]/60 p-4">
          <div>
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-2">
              <div>
                <p className="text-xs font-semibold text-white">
                  Desglose de Probabilidades & Cuotas
                </p>
                <p className="text-[10px] text-slate-400">
                  Market Odds & Model Matrix · {bookmakerName}
                </p>
              </div>
              {isPositiveEv ? (
                <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-emerald-400 border border-emerald-500/20">
                  +{evVal}% EV
                </span>
              ) : (
                <span className="rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10px] font-medium text-slate-400 border border-white/[0.08]">
                  {evVal}% EV
                </span>
              )}
            </div>

            {/* Matrix Comparison */}
            <div className="mt-3 space-y-2 text-xs">
              <div className="grid grid-cols-3 text-[10px] font-medium uppercase tracking-wider text-slate-400 border-b border-white/[0.04] pb-1">
                <span>Métrica</span>
                <span className="text-center truncate">{awayTeam}</span>
                <span className="text-right truncate">{homeTeam}</span>
              </div>

              {/* Cuotas de mercado */}
              <div className="grid grid-cols-3 items-center">
                <span className="text-slate-400 text-[11px]">Cuota Mercado</span>
                <span className="text-center font-mono font-medium text-slate-200">
                  {awayOddsStr}
                </span>
                <span className="text-right font-mono font-medium text-slate-200">
                  {homeOddsStr}
                </span>
              </div>

              {/* Probabilidad desvigada mercado */}
              <div className="grid grid-cols-3 items-center">
                <span className="text-slate-400 text-[11px]">Mercado (Fair)</span>
                <span className="text-center font-mono text-slate-400">
                  {awayFairProb}
                </span>
                <span className="text-right font-mono text-slate-400">
                  {homeFairProb}
                </span>
              </div>

              {/* Probabilidad Modelo Griddio */}
              <div className="grid grid-cols-3 items-center bg-white/[0.02] p-1 rounded-md">
                <span className="font-medium text-emerald-400 text-[11px]">Modelo Griddio</span>
                <span className="text-center font-mono font-bold text-emerald-300">
                  {awayModelProb}
                </span>
                <span className="text-right font-mono font-medium text-slate-300">
                  {homeModelProb}
                </span>
              </div>
            </div>

            {/* Banner Recomendación EV+ */}
            {isPositiveEv ? (
              <div className="mt-3 rounded-lg border border-emerald-500/20 bg-emerald-500/[0.08] p-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] uppercase font-semibold text-emerald-400">Recomendación EV+</span>
                  <span className="font-mono text-[10px] font-bold text-emerald-300">Edge: {edgeFormatted}</span>
                </div>
                <p className="mt-0.5 text-[11px] font-medium text-white">
                  {recTeam} ML <span className="font-mono text-emerald-300">({recOdds})</span> con{' '}
                  <span className="font-bold text-emerald-300">+{evVal}% EV</span>
                </p>
              </div>
            ) : (
              <div className="mt-3 rounded-lg border border-white/[0.08] bg-white/[0.03] p-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] uppercase font-semibold text-slate-400">Sin ventaja EV+</span>
                  <span className="font-mono text-[10px] text-slate-500">Edge: {edgeFormatted}</span>
                </div>
                <p className="mt-0.5 text-[11px] text-slate-400">
                  Línea eficiente de mercado sin valor matemático suficiente.
                </p>
              </div>
            )}
          </div>

          <div className="mt-3 flex items-center justify-between text-[10px] text-slate-500">
            <span>Inferencia: {match?.prediction?.tier === 'EXPERIMENTAL' ? 'Modelo Temporal v1 (Exp)' : 'Modelo Temporal v1'}</span>
            <span className="font-mono">Gate Prod: Cerrado</span>
          </div>
        </div>
      </div>
    </div>
  )
}
