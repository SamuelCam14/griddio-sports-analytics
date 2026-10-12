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

            <button
              type="button"
              onClick={onOpenFullSimulator}
              disabled={!hasMatch}
              className="macos-btn-secondary w-full disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Play className="h-3 w-3 fill-current text-apple-blue" />
              <span>Ejecutar Simulación Ensamble</span>
            </button>
          </div>
        </div>

        {/* Right Subcard: Simulated Standing */}
        <div className="flex flex-col justify-between rounded-xl border border-white/[0.07] bg-[#12151e]/60 p-4">
          <div>
            <p className="text-xs font-medium text-slate-300">
              Tabla de Clasificación Simulada
            </p>

            <div className="mt-4 flex flex-col items-center justify-center rounded-lg border border-white/[0.04] bg-white/[0.02] p-6 text-center text-xs text-slate-400">
              <span className="font-medium text-slate-300">Simulación no disponible</span>
              <p className="mt-1 text-[11px] text-slate-500 leading-relaxed max-w-[220px]">
                La tabla de 10k iteraciones requiere resultados persistidos del backend. No se inventan posiciones locales.
              </p>
            </div>
          </div>

          <p className="mt-3 text-[10px] text-slate-500 leading-tight">
            Requiere integración backend de simulación probabilística.
          </p>
        </div>
      </div>
    </div>
  )
}
