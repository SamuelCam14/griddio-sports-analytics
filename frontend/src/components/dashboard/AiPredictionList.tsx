import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { Sparkline } from '@/components/ui/Sparkline'
import { TeamLogo } from '@/components/ui/TeamLogo'
import type { ScheduledMatch } from '@/types/dashboard'

interface PredictionItem {
  id: string | number
  teamName: string
  probability: number
  trendData: number[]
  sparkColor: string
}

interface AiPredictionListProps {
  matches: ScheduledMatch[]
  sport: 'NBA' | 'NFL'
  onSelectMatch?: (match: ScheduledMatch) => void
}

export function AiPredictionList({ matches, sport, onSelectMatch }: AiPredictionListProps) {
  const [conferenceFilter, setConferenceFilter] = useState<'All' | 'West' | 'East'>('All')

  // Construir lista con colores macOS Apple (Blue, Cyan, Purple, Green, Orange)
  const predictions: PredictionItem[] = matches.length > 0
    ? matches.slice(0, 5).map((match, idx) => {
        const prob = match.prediction?.home_win_prob ?? match.odds.home_implied_prob ?? 0.55
        const pct = Math.round(prob * 100)
        const base = Math.max(20, pct - 20)
        const trend = [base, base + 5, base - 2, base + 12, base + 8, base + 18, pct]
        const colors = ['#0a84ff', '#30d158', '#64d2ff', '#bf5af2', '#ff9f0a']

        return {
          id: match.game.id,
          teamName: match.odds.home_team,
          probability: pct,
          trendData: trend,
          sparkColor: colors[idx % colors.length],
        }
      })
    : (sport === 'NBA'
        ? [
            { id: 1, teamName: 'Boston Celtics', probability: 82, trendData: [50, 60, 55, 70, 75, 82], sparkColor: '#0a84ff' },
            { id: 2, teamName: 'Denver Nuggets', probability: 68, trendData: [45, 50, 60, 58, 65, 68], sparkColor: '#30d158' },
            { id: 3, teamName: 'Oklahoma City Thunder', probability: 64, trendData: [40, 48, 52, 60, 58, 64], sparkColor: '#64d2ff' },
            { id: 4, teamName: 'Minnesota Timberwolves', probability: 55, trendData: [35, 42, 45, 52, 50, 55], sparkColor: '#bf5af2' },
            { id: 5, teamName: 'Dallas Mavericks', probability: 48, trendData: [40, 38, 45, 44, 46, 48], sparkColor: '#ff9f0a' },
          ]
        : [
            { id: 1, teamName: 'Kansas City Chiefs', probability: 85, trendData: [55, 65, 70, 75, 80, 85], sparkColor: '#0a84ff' },
            { id: 2, teamName: 'San Francisco 49ers', probability: 74, trendData: [50, 58, 62, 68, 70, 74], sparkColor: '#30d158' },
            { id: 3, teamName: 'Baltimore Ravens', probability: 66, trendData: [45, 50, 55, 60, 62, 66], sparkColor: '#64d2ff' },
            { id: 4, teamName: 'Detroit Lions', probability: 58, trendData: [40, 45, 50, 52, 55, 58], sparkColor: '#bf5af2' },
            { id: 5, teamName: 'Buffalo Bills', probability: 51, trendData: [42, 40, 46, 48, 49, 51], sparkColor: '#ff9f0a' },
          ])

  return (
    <div className="space-y-3">
      {/* Header with See All */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold tracking-tight text-white">AI Prediction</h3>
        <button
          type="button"
          className="text-xs font-medium text-apple-blue hover:underline transition-colors"
        >
          Ver todo
        </button>
      </div>

      {/* Filter & Update line */}
      <div className="flex items-center justify-between text-xs text-slate-400">
        <div className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-apple-blue" />
          <span className="text-[11px]">Probabilidad de victoria · 2 min</span>
        </div>

        <button
          type="button"
          onClick={() =>
            setConferenceFilter((prev) =>
              prev === 'All' ? 'West' : prev === 'West' ? 'East' : 'All'
            )
          }
          className="flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.05] px-2 py-0.5 text-[10px] text-slate-300 hover:text-white transition-colors"
        >
          <span>{conferenceFilter === 'All' ? 'Todas' : conferenceFilter === 'West' ? 'Oeste' : 'Este'}</span>
          <ChevronDown className="h-3 w-3" />
        </button>
      </div>

      {/* List of teams with sparklines */}
      <div className="space-y-1.5">
        {predictions.map((item) => {
          const match = matches.find((m) => m.game.id === item.id)

          return (
            <button
              key={`${item.id}-${item.teamName}`}
              type="button"
              onClick={() => match && onSelectMatch && onSelectMatch(match)}
              className="flex w-full items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.03] p-2.5 transition-all duration-150 hover:border-white/[0.12] hover:bg-white/[0.07]"
            >
              {/* Left: Team Crest & Name */}
              <div className="flex items-center gap-2.5 min-w-0">
                <TeamLogo name={item.teamName} size="sm" />
                <span className="truncate text-xs font-medium text-slate-200">
                  {item.teamName}
                </span>
              </div>

              {/* Right: Clean Sparkline and percentage badge */}
              <div className="flex items-center gap-3 shrink-0">
                <Sparkline
                  data={item.trendData}
                  color={item.sparkColor}
                  width={54}
                  height={18}
                />
                <span className="w-9 text-right font-mono text-xs font-semibold text-slate-200">
                  {item.probability}%
                </span>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
