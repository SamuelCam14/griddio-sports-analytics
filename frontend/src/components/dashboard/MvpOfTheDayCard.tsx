import { motion } from 'framer-motion'
import { Sparkles, Trophy } from 'lucide-react'
import type { Pick } from '@/services/api'

interface MvpOfTheDayCardProps {
  sport: 'NBA' | 'NFL'
  topPick?: Pick
}

export function MvpOfTheDayCard({ sport, topPick }: MvpOfTheDayCardProps) {
  const isNba = sport === 'NBA'

  const playerName = topPick?.best_bet?.selection?.includes('OVER') || topPick?.best_bet?.selection?.includes('UNDER')
    ? topPick.player_props?.[0]?.player_name ?? (isNba ? 'Nikola Jokić' : 'Patrick Mahomes')
    : isNba
    ? 'Nikola Jokić'
    : 'Patrick Mahomes'

  const teamName = topPick?.home_team?.name ?? (isNba ? 'Denver Nuggets' : 'Kansas City Chiefs')
  const position = isNba ? 'Center · All-Star' : 'Quarterback · MVP'
  const ratingScore = topPick?.prob_modelo ? Math.round(topPick.prob_modelo * 100) : 92
  const advantageText = topPick?.ev_dolares ? `+$${topPick.ev_dolares.toFixed(1)} EV` : '+14.8% EV'

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="macos-card p-4 transition-all duration-200"
    >
      {/* Card Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 text-apple-blue" />
          <span className="text-[10px] font-semibold tracking-wider uppercase text-slate-400">
            AI · MVP OF THE DAY
          </span>
        </div>
        <div className="flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.06] px-2 py-0.5 font-mono text-[10px] font-semibold text-slate-200">
          <Trophy className="h-3 w-3 text-apple-orange" />
          <span>Score {ratingScore}</span>
        </div>
      </div>

      {/* Player content */}
      <div className="mt-3.5 flex items-center gap-3.5">
        {/* Player Image with subtle hairline border */}
        <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-xl border border-white/[0.1] bg-black/30 sm:h-18 sm:w-18">
          <img
            src="/images/mvp_portrait.jpg"
            alt={playerName}
            className="h-full w-full object-cover object-top"
            loading="lazy"
          />
        </div>

        {/* Player Metadata */}
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-base font-semibold text-white tracking-tight">{playerName}</h4>
          <p className="truncate text-xs text-slate-300">{teamName}</p>
          <p className="mt-0.5 text-[11px] text-slate-400">{position}</p>

          <div className="mt-2 flex items-center gap-2">
            <span className="rounded border border-apple-green/20 bg-apple-green/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-[#54e378]">
              {advantageText}
            </span>
            <span className="text-[10px] text-slate-400">
              {sport} · Confiabilidad 98%
            </span>
          </div>
        </div>
      </div>
    </motion.div>
  )
}
