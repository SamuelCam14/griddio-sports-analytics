import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { TeamLogo } from '@/components/ui/TeamLogo'

interface StandingsTableCardProps {
  sport: 'NBA' | 'NFL'
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

export function StandingsTableCard({ sport }: StandingsTableCardProps) {
  const [selectedConference, setSelectedConference] = useState<'Estadísticas' | 'Oeste' | 'Este'>('Oeste')
  const isNba = sport === 'NBA'

  const standings: TeamStanding[] = isNba
    ? [
        { pos: 1, name: 'Boston Celtics', w: 58, l: 16, pct: '.784', pts: 8, highlight: true },
        { pos: 2, name: 'Denver Nuggets', w: 53, l: 23, pct: '.697', pts: 8 },
        { pos: 3, name: 'Oklahoma City Thunder', w: 52, l: 24, pct: '.684', pts: 6 },
        { pos: 4, name: 'Minnesota Timberwolves', w: 51, l: 25, pct: '.671', pts: 4 },
        { pos: 5, name: 'Los Angeles Clippers', w: 47, l: 28, pct: '.627', pts: 4 },
        { pos: 6, name: 'Dallas Mavericks', w: 46, l: 30, pct: '.605', pts: 2 },
      ]
    : [
        { pos: 1, name: 'Kansas City Chiefs', w: 14, l: 2, pct: '.875', pts: 8, highlight: true },
        { pos: 2, name: 'San Francisco 49ers', w: 13, l: 3, pct: '.813', pts: 8 },
        { pos: 3, name: 'Baltimore Ravens', w: 12, l: 4, pct: '.750', pts: 6 },
        { pos: 4, name: 'Detroit Lions', w: 11, l: 5, pct: '.688', pts: 4 },
        { pos: 5, name: 'Buffalo Bills', w: 10, l: 6, pct: '.625', pts: 4 },
        { pos: 6, name: 'Philadelphia Eagles', w: 10, l: 6, pct: '.625', pts: 2 },
      ]

  return (
    <div className="macos-card p-4 sm:p-5">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.08] pb-3">
        <h3 className="text-sm font-semibold tracking-tight text-white">Group Standing</h3>

        <button
          type="button"
          onClick={() =>
            setSelectedConference((prev) =>
              prev === 'Oeste' ? 'Este' : prev === 'Este' ? 'Estadísticas' : 'Oeste'
            )
          }
          className="flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.05] px-2 py-0.5 text-[11px] text-slate-300 hover:text-white transition-colors"
        >
          <span>{selectedConference}</span>
          <ChevronDown className="h-3 w-3" />
        </button>
      </div>

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
            {/* Position */}
            <span className={`font-mono text-[11px] ${team.pos <= 4 ? 'text-apple-blue font-semibold' : 'text-slate-500'}`}>
              {team.pos}
            </span>

            {/* Team Crest & Name */}
            <div className="flex items-center gap-2 min-w-0">
              <TeamLogo name={team.name} size="sm" />
              <span className="truncate font-medium text-slate-200">
                {team.name}
              </span>
            </div>

            {/* Wins, Losses, Pct, Pts */}
            <span className="text-center font-mono text-slate-400">{team.w}</span>
            <span className="text-center font-mono text-slate-500">{team.l}</span>
            <span className="text-center font-mono text-slate-400">{team.pct}</span>
            <span className="text-center font-mono font-semibold text-white">{team.pts}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
