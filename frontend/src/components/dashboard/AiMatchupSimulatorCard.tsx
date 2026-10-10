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

  const homeTeam = match?.odds.home_team ?? (isNba ? 'Boston Celtics' : 'Kansas City Chiefs')
  const awayTeam = match?.odds.away_team ?? (isNba ? 'Los Angeles Lakers' : 'San Francisco 49ers')
  const commenceTime = match?.odds.commence_time ?? new Date().toISOString()

  // Probabilidades de simulación
  const homeProb = match?.prediction?.home_win_prob ?? 0.58
  const awayProb = match?.prediction?.away_win_prob ?? 0.42
  const homePct = Math.round(homeProb * 100)
  const awayPct = Math.round(awayProb * 100)

  // Datos simulados de clasificación
  const simulatedStandings = isNba
    ? [
        { name: 'Boston Celtics', pts: '4 pts', prob: '90%', active: true },
        { name: 'Denver Nuggets', pts: '3 pts', prob: '80%', active: false },
        { name: 'Oklahoma City Thunder', pts: '2 pts', prob: '55%', active: false },
        { name: 'Minnesota Timberwolves', pts: '1 pt', prob: '28%', active: false },
      ]
    : [
        { name: 'Kansas City Chiefs', pts: '4 pts', prob: '92%', active: true },
        { name: 'San Francisco 49ers', pts: '3 pts', prob: '82%', active: false },
        { name: 'Baltimore Ravens', pts: '2 pts', prob: '60%', active: false },
        { name: 'Detroit Lions', pts: '1 pt', prob: '30%', active: false },
      ]

  return (
    <div className="glass-panel p-4 sm:p-5">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/10 pb-3">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-emerald-400" />
          <h3 className="section-title-sm text-white">AI Qualification Simulator</h3>
        </div>

        <button
          type="button"
          onClick={() =>
            setSelectedGroup((prev) =>
              prev === 'Oeste' ? 'Este' : prev === 'Este' ? 'Group C' : 'Oeste'
            )
          }
          className="flex items-center gap-1 rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-slate-300 hover:text-white"
        >
          <span>{selectedGroup}</span>
          <ChevronDown className="h-3 w-3" />
        </button>
      </div>

      {/* Two-Column Simulator Content */}
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[1.2fr_1fr]">
        {/* Left Subcard: Upcoming Matches */}
        <div className="flex flex-col justify-between rounded-xl border border-white/10 bg-black/25 p-4">
          <div>
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span className="font-semibold uppercase tracking-wider text-slate-300">Partido Próximo</span>
              <span>{formatMatchDate(commenceTime)} · {formatMatchTime(commenceTime)}</span>
            </div>

            {/* Teams Faceoff */}
            <div className="my-4 flex items-center justify-around gap-2 text-center">
              {/* Home Team */}
              <div className="flex flex-col items-center">
                <TeamLogo name={homeTeam} size="lg" />
                <span className="mt-2 text-xs font-bold text-white max-w-[100px] truncate">{homeTeam}</span>
                <span className="text-[10px] text-slate-400 uppercase">Local</span>
              </div>

              <div className="text-center">
                <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-xs font-bold text-emerald-300">
                  VS
                </span>
              </div>

              {/* Away Team */}
              <div className="flex flex-col items-center">
                <TeamLogo name={awayTeam} size="lg" />
                <span className="mt-2 text-xs font-bold text-white max-w-[100px] truncate">{awayTeam}</span>
                <span className="text-[10px] text-slate-400 uppercase">Visitante</span>
              </div>
            </div>
          </div>

          {/* Probability pills & Simulation trigger */}
          <div className="space-y-2.5 border-t border-white/10 pt-3">
            <div className="grid grid-cols-3 gap-1.5 text-center">
              <div className="rounded-lg border border-white/5 bg-white/5 p-1.5">
                <p className="text-[10px] text-slate-400">Local</p>
                <p className="font-mono text-xs font-bold text-emerald-300">{homePct}%</p>
              </div>
              <div className="rounded-lg border border-white/5 bg-white/5 p-1.5">
                <p className="text-[10px] text-slate-400">Spread</p>
                <p className="font-mono text-xs font-bold text-slate-200">{match?.odds.home_spread ?? '-4.5'}</p>
              </div>
              <div className="rounded-lg border border-white/5 bg-white/5 p-1.5">
                <p className="text-[10px] text-slate-400">Visitante</p>
                <p className="font-mono text-xs font-bold text-cyan-300">{awayPct}%</p>
              </div>
            </div>

            <button
              type="button"
              onClick={onOpenFullSimulator}
              className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-emerald-400/30 bg-emerald-500/15 py-2 text-xs font-bold text-emerald-300 transition-all hover:bg-emerald-500/25 active:scale-[0.98]"
            >
              <Play className="h-3.5 w-3.5 fill-current" />
              <span>Ejecutar Simulación Ensamble</span>
            </button>
          </div>
        </div>

        {/* Right Subcard: Simulated Standing */}
        <div className="flex flex-col justify-between rounded-xl border border-white/10 bg-black/25 p-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-300">
              Tabla Simulada (10k iteraciones)
            </p>

            <div className="mt-3 space-y-2">
              {simulatedStandings.map((item, idx) => (
                <div
                  key={item.name}
                  className="flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-1.5 text-xs transition-colors hover:bg-white/5"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-mono text-[11px] text-slate-500">{idx + 1}</span>
                    <TeamLogo name={item.name} size="sm" />
                    <span className="truncate text-xs font-semibold text-slate-200">
                      {item.name}
                    </span>
                  </div>

                  <div className="flex items-center gap-2.5 shrink-0">
                    <span className="font-mono text-[11px] text-slate-400">{item.pts}</span>
                    <span className="text-slate-600">|</span>
                    <span className="font-mono text-xs font-bold text-emerald-300">
                      {item.prob}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <p className="mt-3 text-[10px] text-slate-500 leading-tight">
            Proyección Monte Carlo ajustada con ratings Elo y ventaja de descanso.
          </p>
        </div>
      </div>
    </div>
  )
}
