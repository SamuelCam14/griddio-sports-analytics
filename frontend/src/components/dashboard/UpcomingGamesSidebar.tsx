import { motion } from 'framer-motion'
import type { ScheduledMatch } from '@/types/dashboard'
import { formatMatchDate, formatMatchTime } from '@/utils/dashboard'

interface UpcomingGamesSidebarProps {
  matches: ScheduledMatch[]
  selectedGameId: number | null
  isLoading: boolean
  errorMessage?: string
  unmatchedOddsCount: number
  onSelect: (match: ScheduledMatch) => void
}

export function UpcomingGamesSidebar({
  matches,
  selectedGameId,
  isLoading,
  errorMessage,
  unmatchedOddsCount,
  onSelect,
}: UpcomingGamesSidebarProps) {
  return (
    <aside className="surface-card h-fit lg:sticky lg:top-24" aria-label="Partidos próximos">
      <div className="section-heading">
        <div>
          <p className="eyebrow">CARTELERA</p>
          <h2 className="section-title">Próximos partidos</h2>
        </div>
        <span className="count-pill">{matches.length}</span>
      </div>

      {isLoading && <p className="state-copy">Cargando cartelera desde el backend…</p>}
      {errorMessage && <p className="state-error">{errorMessage}</p>}
      {!isLoading && !errorMessage && matches.length === 0 && (
        <p className="state-copy">No hay partidos que coincidan entre el calendario y los momios disponibles.</p>
      )}

      <div className="mt-4 space-y-2">
        {matches.map((match, index) => {
          const active = selectedGameId === match.game.id
          return (
            <motion.button
              key={match.game.id}
              type="button"
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: Math.min(index * 0.035, 0.25) }}
              onClick={() => onSelect(match)}
              aria-pressed={active}
              className={`match-list-item ${active ? 'match-list-item-active' : ''}`}
            >
              <span className="flex min-w-0 flex-1 flex-col gap-1 text-left">
                <span className="truncate text-sm font-semibold text-slate-100">
                  {match.odds.away_team} <span className="text-slate-500">@</span> {match.odds.home_team}
                </span>
                <span className="text-xs text-slate-400">
                  {formatMatchDate(match.odds.commence_time)} · {formatMatchTime(match.odds.commence_time)}
                </span>
              </span>
            </motion.button>
          )
        })}
      </div>
    </aside>
  )
}
