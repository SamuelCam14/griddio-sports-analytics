import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { TeamLogo } from '@/components/ui/TeamLogo'

interface StandingsTableCardProps {
  sport: 'NBA' | 'NFL'
  standings?: TeamStanding[]
}

interface TeamStanding {
  pos: number
  name: string
  w: number
  l: number
  pct: string
  pts: number
  highlight?: boolean
}

export function StandingsTableCard({ sport, standings = [] }: StandingsTableCardProps) {
  const hasStandings = standings.length > 0

  return (
    <div className="macos-card p-4 sm:p-5">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.08] pb-3">
        <h3 className="text-sm font-semibold tracking-tight text-white">{sport} Standings</h3>
        <span className="text-[10px] font-mono text-slate-500 uppercase">OFICIAL</span>
      </div>

      {hasStandings ? (
        <>
          {/* Table header */}
          <div className="mt-2.5 grid grid-cols-[18px_1fr_26px_26px_34px_26px] items-center gap-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            <span>P</span>
            <span>Equipo</span>
            <span className="text-center">G</span>
            <span className="text-center">P</span>
            <span className="text-center">%</span>
            <span className="text-center font-bold text-slate-400">Pts</span>
          </div>

          {/* Table rows */}
          <div className="mt-1 space-y-1">
            {standings.map((team) => (
              <div
                key={team.name}
                className={`grid grid-cols-[18px_1fr_26px_26px_34px_26px] items-center gap-2 rounded-lg px-2 py-1.5 text-xs transition-colors hover:bg-white/[0.05] ${
                  team.highlight ? 'bg-white/[0.06] border border-white/[0.08]' : ''
                }`}
              >
                <span className={`font-mono text-[11px] ${team.pos <= 4 ? 'text-apple-blue font-semibold' : 'text-slate-500'}`}>
                  {team.pos}
                </span>

                <div className="flex items-center gap-2 min-w-0">
                  <TeamLogo name={team.name} size="sm" />
                  <span className="truncate font-medium text-slate-200">
                    {team.name}
                  </span>
                </div>

                <span className="text-center font-mono text-slate-400">{team.w}</span>
                <span className="text-center font-mono text-slate-500">{team.l}</span>
                <span className="text-center font-mono text-slate-400">{team.pct}</span>
                <span className="text-center font-mono font-semibold text-white">{team.pts}</span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="mt-4 flex flex-col items-center justify-center rounded-lg border border-white/[0.04] bg-white/[0.02] p-6 text-center text-xs text-slate-400">
          <p className="font-medium text-slate-300">Clasificación oficial no disponible</p>
          <p className="mt-1 text-[11px] text-slate-500 leading-relaxed max-w-[240px]">
            La tabla de posiciones requiere cobertura de temporada completa en la base de datos. No se inventan récords ficticios.
          </p>
        </div>
      )}
    </div>
  )
}
