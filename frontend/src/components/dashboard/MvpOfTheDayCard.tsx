import { motion } from 'framer-motion'
import { Sparkles, Trophy } from 'lucide-react'
import type { Pick } from '@/services/api'

interface MvpOfTheDayCardProps {
  sport: 'NBA' | 'NFL'
  topPick?: Pick
}

export function MvpOfTheDayCard({ sport, topPick }: MvpOfTheDayCardProps) {
  // Datos dinámicos según el deporte activo y el top pick
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
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="group relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-[#122b24]/90 via-[#0e211b]/80 to-[#091713]/90 p-4 shadow-glass backdrop-blur-xl transition-all duration-300 hover:border-emerald-400/30"
    >
      {/* Background glow & watermark */}
      <div className="pointer-events-none absolute -right-8 -top-8 h-32 w-32 rounded-full bg-emerald-500/10 blur-2xl group-hover:bg-emerald-500/15" />

      {/* Card Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 text-emerald-400" />
          <span className="eyebrow-accent text-[10px]">AI · MVP OF THE DAY</span>
        </div>
        <div className="flex items-center gap-1 rounded-full border border-emerald-400/30 bg-emerald-400/15 px-2.5 py-0.5 font-mono text-[10px] font-bold text-emerald-300">
          <Trophy className="h-3 w-3 text-amber-400" />
          <span>Score {ratingScore}</span>
        </div>
      </div>

      {/* Player content grid */}
      <div className="mt-3.5 flex items-center gap-3.5">
        {/* Player Image with glowing border */}
        <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-xl border border-white/15 bg-black/40 shadow-md sm:h-20 sm:w-20">
          <img
            src="/images/mvp_portrait.jpg"
            alt={playerName}
            className="h-full w-full object-cover object-top transition-transform duration-500 group-hover:scale-105"
            loading="lazy"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
        </div>

        {/* Player Metadata */}
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-base font-bold text-white sm:text-lg">{playerName}</h4>
          <p className="truncate text-xs text-slate-300">{teamName}</p>
          <p className="mt-0.5 text-[11px] text-slate-400">{position}</p>

          <div className="mt-2 flex items-center gap-2">
            <span className="rounded-md border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[10px] font-semibold text-emerald-300">
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
