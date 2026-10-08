import { motion } from 'framer-motion'
import type { ScheduledMatch } from '@/types/dashboard'
import { formatMatchDate, formatMatchTime } from '@/utils/dashboard'

interface MatchupHeroProps {
  match: ScheduledMatch
}

export function MatchupHero({ match }: MatchupHeroProps) {
  const { game, odds } = match
  return (
    <motion.section
      key={game.id}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="surface-card"
      aria-labelledby="selected-match-title"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="eyebrow">{game.sport} · PARTIDO SELECCIONADO</p>
          <p className="mt-1 text-sm text-slate-300">{formatMatchDate(odds.commence_time, { weekday: 'long', year: 'numeric' })} · {formatMatchTime(odds.commence_time)} · Hora de Chihuahua</p>
        </div>
        <span className="status-badge">{game.status}</span>
      </div>

      <div className="mt-8 grid grid-cols-[1fr_auto_1fr] items-center gap-3 sm:gap-6">
        <div className="min-w-0 text-center">
          <p className="team-initials">{odds.away_team.split(' ').map((part) => part[0]).join('').slice(0, 3)}</p>
          <h1 id="selected-match-title" className="mt-3 truncate text-lg font-semibold text-white sm:text-2xl">{odds.away_team}</h1>
          <p className="mt-1 text-xs uppercase tracking-[0.18em] text-slate-400">Visitante</p>
        </div>
        <div className="text-center">
          <span className="text-xs font-semibold tracking-[0.24em] text-emerald-300">VS</span>
          {game.status === 'final' && game.away_score != null && game.home_score != null && (
            <p className="mt-2 font-mono text-2xl font-semibold text-white">{game.away_score} — {game.home_score}</p>
          )}
        </div>
        <div className="min-w-0 text-center">
          <p className="team-initials team-initials-home">{odds.home_team.split(' ').map((part) => part[0]).join('').slice(0, 3)}</p>
          <p className="mt-3 truncate text-lg font-semibold text-white sm:text-2xl">{odds.home_team}</p>
          <p className="mt-1 text-xs uppercase tracking-[0.18em] text-slate-400">Local</p>
        </div>
      </div>

      <div className="mt-8 grid grid-cols-2 gap-3 border-t border-white/10 pt-4 text-center sm:grid-cols-4">
        <MarketValue label="Línea local" value={odds.home_ml == null ? null : formatAmericanOdds(odds.home_ml)} />
        <MarketValue label="Línea visitante" value={odds.away_ml == null ? null : formatAmericanOdds(odds.away_ml)} />
        <MarketValue label="Hándicap local" value={odds.home_spread} />
        <MarketValue label="Total" value={odds.total_line} />
      </div>
    </motion.section>
  )
}

function MarketValue({ label, value }: { label: string; value: number | string | null }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 font-mono text-sm font-semibold text-slate-200">{value ?? '—'}</p>
    </div>
  )
}

function formatAmericanOdds(value: number): string {
  return value > 0 ? `+${value}` : `${value}`
}
