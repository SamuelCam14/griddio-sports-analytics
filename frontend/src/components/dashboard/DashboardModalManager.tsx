import { Modal } from '@/components/ui/Modal'
import { MatchupOddsPanel } from '@/components/dashboard/MatchupOddsPanel'
import { PredictionPanel } from '@/components/dashboard/PredictionPanel'
import { TeamMetricsPanel } from '@/components/dashboard/TeamMetricsPanel'
import { EVCalculator } from '@/components/ui/EVCalculator'
import { MatchupSimulator } from '@/components/ui/MatchupSimulator'
import { ParlayBuilderView } from '@/components/parlay/ParlayBuilderView'
import { SportsCardsView } from '@/components/cards/SportsCardsView'
import type { ScheduledMatch } from '@/types/dashboard'
import { useAppStore } from '@/store/useAppStore'
import { useState } from 'react'

export type ModalType =
  | 'matchup-deep-dive'
  | 'simulator'
  | 'parlay'
  | 'cards'
  | 'ev'
  | 'bankroll'
  | null

interface DashboardModalManagerProps {
  activeModal: ModalType
  onClose: () => void
  selectedMatch?: ScheduledMatch
  sport: 'NBA' | 'NFL'
  predictionsLoading?: boolean
  predictionsError?: string
}

export function DashboardModalManager({
  activeModal,
  onClose,
  selectedMatch,
  sport,
  predictionsLoading = false,
  predictionsError,
}: DashboardModalManagerProps) {
  const { bankroll, setBankroll, kellyFraction, setKellyFraction } = useAppStore()
  const [bankrollInput, setBankrollInput] = useState(bankroll.toString())

  const handleSaveBankroll = () => {
    const val = parseFloat(bankrollInput)
    if (!isNaN(val) && val > 0) setBankroll(val)
    onClose()
  }

  return (
    <>
      {/* ─── Matchup Deep Dive (SofaScore Intel) ─────────────────────────── */}
      <Modal
        isOpen={activeModal === 'matchup-deep-dive'}
        onClose={onClose}
        eyebrow={`${sport} · ANÁLISIS INTEGRAL AVANZADO`}
        title={
          selectedMatch
            ? `${selectedMatch.odds.away_team} vs ${selectedMatch.odds.home_team}`
            : 'Centro de Análisis Deportivo'
        }
        maxWidth="6xl"
      >
        {selectedMatch ? (
          <div className="space-y-5">
            <div className="grid gap-4 md:grid-cols-2">
              <PredictionPanel
                prediction={selectedMatch.prediction}
                isLoading={predictionsLoading}
                errorMessage={predictionsError}
              />
              <MatchupOddsPanel odds={selectedMatch.odds} />
            </div>

            <TeamMetricsPanel
              game={selectedMatch.game}
              homeName={selectedMatch.odds.home_team}
              awayName={selectedMatch.odds.away_team}
            />
          </div>
        ) : (
          <p className="text-center text-slate-400 py-8">
            Selecciona un partido en el dashboard para ver su análisis profundo.
          </p>
        )}
      </Modal>

      {/* ─── Multi-Model Simulator (Deactivated) ──────────────────────────── */}
      <Modal
        isOpen={activeModal === 'simulator'}
        onClose={onClose}
        eyebrow="GOBERNANZA TÉCNICA · ESTADO EXPERIMENTAL"
        title="Simulador de Ensamble (Desactivado)"
        maxWidth="2xl"
      >
        <MatchupSimulator bankroll={bankroll} />
      </Modal>

      {/* ─── Parlay Builder ──────────────────────────────────────────────── */}
      <Modal
        isOpen={activeModal === 'parlay'}
        onClose={onClose}
        eyebrow="QUANTITATIVE PARLAY OPTIMIZER"
        title="Constructor de Micro-Parlays con Ventaja Matemática (EV+)"
        maxWidth="6xl"
      >
        <ParlayBuilderView bankroll={bankroll} />
      </Modal>

      {/* ─── Sports Cards Analytics ──────────────────────────────────────── */}
      <Modal
        isOpen={activeModal === 'cards'}
        onClose={onClose}
        eyebrow="COLLECTIBLES ASSET VALUATION"
        title="Sports Card Analytics & Arbitraje de Mercado"
        maxWidth="6xl"
      >
        <SportsCardsView />
      </Modal>

      {/* ─── EV & Kelly Calculator ───────────────────────────────────────── */}
      <Modal
        isOpen={activeModal === 'ev'}
        onClose={onClose}
        eyebrow="CALCULADORA DE RIESGO"
        title="Calculadora Cuantitativa de Expected Value (+EV) y Kelly"
        maxWidth="2xl"
      >
        <EVCalculator bankroll={bankroll} />
      </Modal>

      {/* ─── Bankroll Quick Settings ─────────────────────────────────────── */}
      <Modal
        isOpen={activeModal === 'bankroll'}
        onClose={onClose}
        eyebrow="GESTIÓN DE CAPITAL"
        title="Configuración de Bankroll y Criterio Kelly"
        maxWidth="md"
      >
        <div className="space-y-4">
          <div>
            <label className="text-xs font-semibold uppercase text-slate-400">
              Capital disponible (USD)
            </label>
            <div className="mt-1.5 flex items-center gap-2">
              <span className="font-mono text-lg font-bold text-apple-blue">$</span>
              <input
                type="number"
                value={bankrollInput}
                onChange={(e) => setBankrollInput(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-black/40 px-3 py-2 font-mono text-base font-bold text-white outline-none focus:border-apple-blue"
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold uppercase text-slate-400">
              Fracción Kelly Recomendada: {(kellyFraction * 100).toFixed(0)}%
            </label>
            <div className="mt-2 flex gap-2">
              {[0.125, 0.25, 0.5, 1.0].map((frac) => (
                <button
                  key={frac}
                  type="button"
                  onClick={() => setKellyFraction(frac)}
                  className={`flex-1 rounded-lg border py-1.5 text-xs font-mono font-semibold transition-all ${
                    kellyFraction === frac
                      ? 'border-white/20 bg-white/[0.16] text-white shadow-macos-btn'
                      : 'border-white/10 bg-white/5 text-slate-400 hover:bg-white/10 hover:text-white'
                  }`}
                >
                  {frac === 0.125 ? '1/8' : frac === 0.25 ? '1/4' : frac === 0.5 ? '1/2' : 'Full'}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] text-slate-500">
              1/4 Kelly es el estándar matemático óptimo para proteger contra rachas de varianza.
            </p>
          </div>

          <div className="pt-2">
            <button
              type="button"
              onClick={handleSaveBankroll}
              className="macos-btn-primary w-full py-2 text-center text-xs font-semibold"
            >
              Guardar y Actualizar
            </button>
          </div>
        </div>
      </Modal>
    </>
  )
}
