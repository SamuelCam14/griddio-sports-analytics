import { useState, useMemo } from 'react'
import { motion, MotionConfig } from 'framer-motion'
import { DashboardHeader, type ActiveView } from '@/components/dashboard/DashboardHeader'
import { MvpOfTheDayCard } from '@/components/dashboard/MvpOfTheDayCard'
import { MetricsGrid } from '@/components/dashboard/MetricsGrid'
import { AiPredictionList } from '@/components/dashboard/AiPredictionList'
import { HeroSpotlight } from '@/components/dashboard/HeroSpotlight'
import { AiMatchupSimulatorCard } from '@/components/dashboard/AiMatchupSimulatorCard'
import { LiveMatchDetailCard } from '@/components/dashboard/LiveMatchDetailCard'
import { StandingsTableCard } from '@/components/dashboard/StandingsTableCard'
import {
  DashboardModalManager,
  type ModalType,
} from '@/components/dashboard/DashboardModalManager'
import { useSportsDashboard } from '@/hooks/useSportsDashboard'
import type { ScheduledMatch } from '@/types/dashboard'
import { getApiErrorMessage } from '@/utils/errors'

export function DashboardPage() {
  const [selectedGameId, setSelectedGameId] = useState<number | null>(null)
  const [carouselIndex, setCarouselIndex] = useState(0)
  const [activeModal, setActiveModal] = useState<ModalType>(null)

  const { sport, gamesQuery, oddsQuery, predictionsQuery, matchList } = useSportsDashboard()

  const matches = matchList.matches
  const totalMatches = matches.length

  // Partido seleccionado actualmente
  const selectedMatch = useMemo(() => {
    if (selectedGameId != null) {
      const found = matches.find((m) => m.game.id === selectedGameId)
      if (found) return found
    }
    return matches[carouselIndex % Math.max(totalMatches, 1)] ?? matches[0]
  }, [matches, selectedGameId, carouselIndex, totalMatches])

  const topPick = useMemo(() => {
    return matches.find((m) => m.prediction?.tier === 'PREMIUM')?.prediction ??
      matches[0]?.prediction
  }, [matches])

  const handleNextHero = () => {
    if (totalMatches > 0) {
      const nextIdx = (carouselIndex + 1) % totalMatches
      setCarouselIndex(nextIdx)
      setSelectedGameId(matches[nextIdx].game.id)
    }
  }

  const handleSelectHeroIndex = (idx: number) => {
    if (matches[idx]) {
      setCarouselIndex(idx)
      setSelectedGameId(matches[idx].game.id)
    }
  }

  const handleSelectMatch = (match: ScheduledMatch) => {
    setSelectedGameId(match.game.id)
    const idx = matches.findIndex((m) => m.game.id === match.game.id)
    if (idx !== -1) setCarouselIndex(idx)
  }

  const handleOpenView = (view: ActiveView) => {
    if (view === 'dashboard') {
      setActiveModal(null)
    } else if (view === 'simulator') {
      setActiveModal('simulator')
    } else if (view === 'parlay') {
      setActiveModal('parlay')
    } else if (view === 'cards') {
      setActiveModal('cards')
    } else if (view === 'ev') {
      setActiveModal('ev')
    }
  }

  const predictionsError = predictionsQuery.error
    ? getApiErrorMessage(predictionsQuery.error)
    : undefined

  return (
    <MotionConfig reducedMotion="user">
      <div className="macos-window-shell">
        {/* Header con diseño macOS toolbar y selección de vista */}
        <DashboardHeader
          onOpenView={handleOpenView}
          onOpenBankroll={() => setActiveModal('bankroll')}
          hasLiveGames={matches.some((m) => m.game.status === 'live')}
        />

        {/* Layout Principal de 3 Columnas inspirado en la referencia con estilo macOS */}
        <main className="dashboard-grid-main pb-10">
          {/* ══════════════════════════════════════════════════════════════════════
              COLUMNA IZQUIERDA: AI Powered Dashboard, Overview, MVP, Métricas, Predictions
             ══════════════════════════════════════════════════════════════════════ */}
          <motion.aside
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.25 }}
            className="space-y-4"
            aria-label="Panel de Información y Pronósticos AI"
          >
            {/* Título de la sección izquierda */}
            <div>
              <h1 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
                Ai Powered Dashboard
              </h1>
              <p className="mt-0.5 text-xs text-slate-400">
                Real time analysis powered by artificial intelligence
              </p>
            </div>

            {/* Overview header */}
            <div className="flex items-center justify-between pt-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-white">Overview</span>
                <span className="rounded-md border border-white/[0.08] bg-white/[0.05] px-2 py-0.5 text-[10px] text-slate-400">
                  Hoy
                </span>
              </div>
              <button
                type="button"
                className="text-xs font-medium text-apple-blue hover:underline transition-colors"
              >
                Ver todo
              </button>
            </div>

            {/* AI - MVP Of The Day */}
            <MvpOfTheDayCard sport={sport} topPick={topPick} />

            {/* Grid 2x2 de 4 métricas compactas */}
            <MetricsGrid
              totalMatches={totalMatches}
              upcomingCount={matches.filter((m) => m.game.status === 'scheduled').length}
              sport={sport}
              upsetsCount={matches.filter((m) => (m.prediction?.ev_dolares ?? 0) > 0).length || 2}
            />

            {/* AI Prediction con sparklines y probabilidades */}
            <div className="macos-card p-4">
              <AiPredictionList
                matches={matches}
                sport={sport}
                onSelectMatch={handleSelectMatch}
              />
            </div>
          </motion.aside>

          {/* ══════════════════════════════════════════════════════════════════════
              COLUMNA CENTRAL: Hero Spotlight + AI Qualification Simulator
             ══════════════════════════════════════════════════════════════════════ */}
          <motion.section
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, delay: 0.05 }}
            className="space-y-4 min-w-0"
            aria-label="Centro de Partidos y Simulador"
          >
            {/* Centerpiece: Banner Hero con jugador y carrusel de partidos */}
            <HeroSpotlight
              match={selectedMatch}
              totalMatches={totalMatches}
              currentIndex={carouselIndex}
              onNext={handleNextHero}
              onSelectIndex={handleSelectHeroIndex}
              sport={sport}
              onAnalyze={() => setActiveModal('matchup-deep-dive')}
            />

            {/* Sub-tarjeta inferior central: AI Qualification Simulator */}
            <AiMatchupSimulatorCard
              match={selectedMatch}
              sport={sport}
              onOpenFullSimulator={() => setActiveModal('simulator')}
            />
          </motion.section>

          {/* ══════════════════════════════════════════════════════════════════════
              COLUMNA DERECHA: Live Matches Detail + Group Standing Table
             ══════════════════════════════════════════════════════════════════════ */}
          <motion.aside
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.3, delay: 0.1 }}
            className="space-y-4"
            aria-label="Marcador en Vivo y Clasificación"
          >
            {/* Live Matches Card */}
            <LiveMatchDetailCard
              match={selectedMatch}
              sport={sport}
              onOpenDeepDive={() => setActiveModal('matchup-deep-dive')}
            />

            {/* Group Standing Table Card */}
            <StandingsTableCard sport={sport} />
          </motion.aside>
        </main>

        {/* Modal Manager para herramientas integradas sin salir de la vista */}
        <DashboardModalManager
          activeModal={activeModal}
          onClose={() => setActiveModal(null)}
          selectedMatch={selectedMatch}
          sport={sport}
          predictionsLoading={predictionsQuery.isLoading}
          predictionsError={predictionsError}
        />
      </div>
    </MotionConfig>
  )
}
