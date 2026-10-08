import { useState } from 'react'
import { motion, MotionConfig } from 'framer-motion'
import { DashboardHeader } from '@/components/dashboard/DashboardHeader'
import { UpcomingGamesSidebar } from '@/components/dashboard/UpcomingGamesSidebar'
import { MatchupHero } from '@/components/dashboard/MatchupHero'
import { PredictionPanel } from '@/components/dashboard/PredictionPanel'
import { TeamMetricsPanel } from '@/components/dashboard/TeamMetricsPanel'
import { MatchupOddsPanel } from '@/components/dashboard/MatchupOddsPanel'
import { useSportsDashboard } from '@/hooks/useSportsDashboard'
import type { ScheduledMatch } from '@/types/dashboard'
import { getApiErrorMessage } from '@/utils/errors'

export function DashboardPage() {
  const [selectedGameId, setSelectedGameId] = useState<number | null>(null)
  const { sport, gamesQuery, oddsQuery, predictionsQuery, matchList } = useSportsDashboard()
  const selectedMatch = matchList.matches.find((match) => match.game.id === selectedGameId) ?? matchList.matches[0]
  const scheduleError = gamesQuery.error
    ? getApiErrorMessage(gamesQuery.error)
    : oddsQuery.error
      ? getApiErrorMessage(oddsQuery.error)
      : undefined

  function selectMatch(match: ScheduledMatch) {
    setSelectedGameId(match.game.id)
  }

  return (
    <MotionConfig reducedMotion="user">
    <div className="dashboard-shell">
      <DashboardHeader />
      <main className="dashboard-layout">
        <UpcomingGamesSidebar
          matches={matchList.matches}
          selectedGameId={selectedMatch?.game.id ?? null}
          isLoading={gamesQuery.isLoading || oddsQuery.isLoading}
          errorMessage={scheduleError}
          unmatchedOddsCount={matchList.unmatchedOddsCount}
          onSelect={selectMatch}
        />

        <div className="min-w-0 space-y-4">
          {selectedMatch ? (
            <>
              <MatchupHero match={selectedMatch} />
              <div className="grid gap-4 xl:grid-cols-2">
                <PredictionPanel
                  prediction={selectedMatch.prediction}
                  isLoading={predictionsQuery.isLoading}
                  errorMessage={predictionsQuery.error ? getApiErrorMessage(predictionsQuery.error) : undefined}
                  unavailableReason={predictionsQuery.data?.reason}
                />
                <MatchupOddsPanel odds={selectedMatch.odds} />
              </div>
              <TeamMetricsPanel
                game={selectedMatch.game}
                homeName={selectedMatch.odds.home_team}
                awayName={selectedMatch.odds.away_team}
              />
            </>
          ) : (
            <section className="surface-card flex min-h-64 flex-col items-center justify-center text-center">
              <p className="eyebrow">{sport} · CARTELERA</p>
              <h1 className="mt-3 text-xl font-semibold text-white">No hay un partido disponible para analizar</h1>
              <p className="mt-2 max-w-md text-sm leading-relaxed text-slate-400">
                {scheduleError ?? 'La vista combina partidos del calendario con eventos y momios verificables. Cuando ambos estén disponibles, aparecerán aquí.'}
              </p>
            </section>
          )}
        </div>

        <motion.aside
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
          className="space-y-4"
          aria-label="Resumen de cobertura"
        >
          <section className="surface-card">
            <p className="eyebrow">DEPORTE ACTIVO</p>
            <p className="mt-2 text-3xl font-semibold tracking-tight text-white">{sport}</p>
            <p className="mt-1 text-sm text-slate-400">
              {selectedMatch ? `${selectedMatch.game.season} · ${selectedMatch.game.status}` : 'Sin partido seleccionado'}
            </p>
            <div className="mt-5 border-t border-white/8 pt-4">
              <p className="text-xs text-slate-400">Partidos con cruce de calendario y momios</p>
              <p className="mt-1 font-mono text-2xl font-semibold text-emerald-200">{matchList.matches.length}</p>
            </div>
          </section>
          <section className="surface-card">
            <p className="eyebrow">FUENTES CONSULTADAS</p>
            <ul className="mt-3 space-y-2 text-sm text-slate-300">
              <li className="source-row"><span className={`source-indicator ${gamesQuery.isError ? 'source-indicator-error' : ''}`} />Calendario de partidos</li>
              <li className="source-row"><span className={`source-indicator ${oddsQuery.isError ? 'source-indicator-error' : ''}`} />Momios NBA/NFL</li>
              <li className={`source-row ${predictionsQuery.isError ? 'text-amber-200' : ''}`}><span className={`source-indicator ${predictionsQuery.data?.predictions_enabled === false || predictionsQuery.isError ? 'source-indicator-muted' : ''}`} />Pronósticos del modelo</li>
            </ul>
          </section>
        </motion.aside>
      </main>
      <footer className="dashboard-footer">Los valores se muestran desde las API disponibles. Un guion indica que el campo no fue entregado.</footer>
    </div>
    </MotionConfig>
  )
}
