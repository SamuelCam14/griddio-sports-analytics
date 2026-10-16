import { motion } from 'framer-motion'
import type { ExperimentalMatchPrediction, Pick } from '@/services/api'
import { formatPercentage } from '@/utils/dashboard'

interface PredictionPanelProps {
  prediction?: Pick
  experimentalPrediction?: ExperimentalMatchPrediction
  isLoading: boolean
  errorMessage?: string
  unavailableReason?: string
}

export function PredictionPanel({ prediction, experimentalPrediction, isLoading, errorMessage, unavailableReason }: PredictionPanelProps) {
  return (
    <section className="surface-card h-full">
      <div className="section-heading">
        <div>
          <p className="eyebrow">MODELO</p>
          <h2 className="section-title">Pronóstico</h2>
        </div>
        {prediction && <span className="status-badge status-badge-green">{prediction.tier}</span>}
      </div>

      {isLoading && <p className="state-copy mt-5">Consultando pronósticos…</p>}
      {errorMessage && <p className="state-error mt-5">No se pudo consultar el pronóstico: {errorMessage}</p>}
      {!isLoading && !errorMessage && !prediction && (
        <div className="empty-model mt-5">
          {experimentalPrediction ? (
            <div className="space-y-3 text-left">
              <span className="status-badge border-amber-400/20 text-amber-300">EXPERIMENTAL · NO ES PICK</span>
              <p className="font-medium text-slate-200">Probabilidad estimada por el modelo experimental</p>
              <div className="grid grid-cols-2 gap-3">
                <Probability label="Local" value={experimentalPrediction.home_win_probability} />
                <Probability label="Visitante" value={experimentalPrediction.away_win_probability} />
              </div>
              <p className="text-xs text-slate-400">{experimentalPrediction.model_version} · corte {experimentalPrediction.run_as_of}</p>
              <p className="text-xs leading-relaxed text-slate-400">No tiene aprobación de producción. No se calcula EV ni recomendación a partir de este resultado.</p>
            </div>
          ) : (
            <>
              <span className="empty-model-icon" aria-hidden="true">—</span>
              <p className="font-medium text-slate-200">Pronóstico no disponible</p>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">
                {unavailableReason ?? 'El backend no publicó una predicción vinculada a este partido.'}
              </p>
            </>
          )}
        </div>
      )}

      {prediction && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-5 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Probability label="Local" value={prediction.home_win_prob} />
            <Probability label="Visitante" value={prediction.away_win_prob} />
          </div>
          <div className="probability-track" aria-label="Probabilidades del modelo">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${Math.max(0, Math.min(100, prediction.home_win_prob * 100))}%` }}
              transition={{ duration: 0.5 }}
              className="probability-home"
            />
          </div>
          {prediction.best_bet && (
            <div className="recommendation-box">
              <p className="eyebrow">MEJOR OPORTUNIDAD REPORTADA</p>
              <p className="mt-2 font-semibold text-white">{prediction.best_bet.selection}</p>
              <p className="mt-1 text-xs text-slate-400">{prediction.best_bet.market_label} · {prediction.best_bet.odds_american > 0 ? '+' : ''}{prediction.best_bet.odds_american}</p>
            </div>
          )}
          {prediction.key_factors && prediction.key_factors.length > 0 && (
            <div>
              <p className="eyebrow mb-2">FACTORES CLAVE REPORTADOS</p>
              <ul className="space-y-2">
                {prediction.key_factors.map((factor, index) => <li className="factor-row" key={`${factor}-${index}`}>{factor}</li>)}
              </ul>
            </div>
          )}
        </motion.div>
      )}
    </section>
  )
}

function Probability({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-white/8 bg-white/[0.025] p-3">
      <p className="text-xs text-slate-400">Probabilidad {label.toLowerCase()}</p>
      <p className="mt-1 font-mono text-xl font-semibold text-white">{formatPercentage(value)}</p>
    </div>
  )
}
