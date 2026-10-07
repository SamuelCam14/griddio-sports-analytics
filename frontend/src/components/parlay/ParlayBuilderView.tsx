/**
 * ParlayBuilderView — Constructor y optimizador de micro-parlays con ventaja matemática.
 * Diseñado para armar combinadas de 2 a 3 legs buscando momio ~2.0 (+100) con EV combinado > 0.
 */
import { useState } from 'react'
import { useRecommendedParlays, useEvaluateParlay } from '@/hooks'
import { type RecommendedParlay } from '@/services/api'

interface Props {
  bankroll: number
}

interface CustomLegDraft {
  team_name: string
  prob_model: number
  odds_american: number
}

const PRESET_LEGS: CustomLegDraft[] = [
  { team_name: "Boston Celtics", prob_model: 0.72, odds_american: -135 },
  { team_name: "Denver Nuggets", prob_model: 0.69, odds_american: -120 },
  { team_name: "Oklahoma City Thunder", prob_model: 0.67, odds_american: -115 },
  { team_name: "Minnesota Timberwolves", prob_model: 0.65, odds_american: -110 },
]

export function ParlayBuilderView({ bankroll }: Props) {
  const [selectedLegs, setSelectedLegs] = useState<CustomLegDraft[]>([PRESET_LEGS[0], PRESET_LEGS[1]])
  const [customTeam, setCustomTeam] = useState("")
  const [customProb, setCustomProb] = useState(70)
  const [customOdds, setCustomOdds] = useState(-125)

  // ─── Custom Hooks ───────────────────────────────────────────────────────────
  const recommendedQuery = useRecommendedParlays(bankroll, 4)
  const evaluateMutation = useEvaluateParlay()

  const handleToggleLeg = (leg: CustomLegDraft) => {
    const exists = selectedLegs.some(l => l.team_name === leg.team_name)
    let newLegs: CustomLegDraft[]
    if (exists) {
      if (selectedLegs.length <= 2) return // mínimo 2
      newLegs = selectedLegs.filter(l => l.team_name !== leg.team_name)
    } else {
      if (selectedLegs.length >= 4) return // máximo 4 para micro-parlays
      newLegs = [...selectedLegs, leg]
    }
    setSelectedLegs(newLegs)
    evaluateMutation.mutate({ legs: newLegs, bankroll })
  }

  const handleAddCustomLeg = () => {
    if (!customTeam) return
    const leg: CustomLegDraft = {
      team_name: customTeam,
      prob_model: customProb / 100.0,
      odds_american: customOdds,
    }
    const newLegs = [...selectedLegs, leg]
    setSelectedLegs(newLegs)
    setCustomTeam("")
    evaluateMutation.mutate({ legs: newLegs, bankroll })
  }

  const activeSlip = evaluateMutation.data

  return (
    <div className="space-y-8">
      {/* ─── Header & Reglas de Oro ──────────────────────────────────────── */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <span>🎯</span> Micro-Parlay Builder con Ventaja Matemática
            </h2>
            <p className="text-sm text-gray-400 mt-1 max-w-3xl">
              Estrategia institucional: Combina de 2 a 3 eventos de alta probabilidad (&gt;65%)
              buscando momio combinado cercano a <strong>2.0 (+100)</strong> donde cada pata aporte EV positivo individual.
            </p>
          </div>
          <div className="flex items-center gap-2 bg-brand-950/60 border border-brand-800/60 px-4 py-2 rounded-xl text-xs font-mono text-brand-300">
            <span>🛡️ Cap Máximo Kelly: 5% Bankroll</span>
          </div>
        </div>
      </div>

      {/* ─── Parlays Recomendados Automáticos ─────────────────────────────── */}
      <section className="space-y-4">
        <h3 className="text-base font-semibold text-gray-200 flex items-center gap-2">
          <span>⚡</span> Parlays Sugeridos del Día (Generados por Algoritmo Combinatorio)
        </h3>

        {recommendedQuery.isLoading && (
          <div className="text-center py-10 text-gray-500 animate-pulse">
            Buscando combinaciones óptimas de 2 y 3 legs...
          </div>
        )}

        {recommendedQuery.data && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {recommendedQuery.data.parlays.map((parlay, idx) => (
              <ParlayCard key={idx} parlay={parlay} bankroll={bankroll} />
            ))}
          </div>
        )}
      </section>

      {/* ─── Constructor Interactivo Personalizado ───────────────────────── */}
      <section className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-6">
        <div>
          <h3 className="text-base font-semibold text-white">
            🛠️ Constructor de Parlay a Medida (2 a 4 Patas)
          </h3>
          <p className="text-xs text-gray-400 mt-0.5">
            Selecciona eventos de la lista o ingresa patas personalizadas para auditar el EV y monto Kelly en vivo.
          </p>
        </div>

        {/* Patas Preset */}
        <div className="space-y-2">
          <label className="text-xs text-gray-500 uppercase tracking-wider block">
            Candidatos Disponibles (Haz clic para agregar/quitar):
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {PRESET_LEGS.map(leg => {
              const isSelected = selectedLegs.some(l => l.team_name === leg.team_name)
              return (
                <button
                  key={leg.team_name}
                  onClick={() => handleToggleLeg(leg)}
                  className={`text-left p-3 rounded-lg border transition-all ${
                    isSelected
                      ? 'bg-purple-950/60 border-purple-600 text-white'
                      : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
                  }`}
                >
                  <div className="flex justify-between items-center text-xs font-semibold">
                    <span>{leg.team_name}</span>
                    <span className="font-mono text-purple-300">
                      {leg.odds_american > 0 ? `+${leg.odds_american}` : leg.odds_american}
                    </span>
                  </div>
                  <div className="text-[11px] text-gray-500 mt-1">
                    Prob. Modelo: {(leg.prob_model * 100).toFixed(0)}%
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        {/* Formulario para agregar pata ad-hoc */}
        <div className="border-t border-gray-800 pt-4 flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[160px]">
            <label className="block text-[11px] text-gray-500 mb-1 uppercase">Equipo / Selección</label>
            <input
              type="text"
              placeholder="ej. Golden State Warriors"
              value={customTeam}
              onChange={e => setCustomTeam(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-white outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
          <div className="w-28">
            <label className="block text-[11px] text-gray-500 mb-1 uppercase">Prob. (%)</label>
            <input
              type="number"
              value={customProb}
              onChange={e => setCustomProb(Number(e.target.value))}
              min={10} max={99}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-white font-mono outline-none"
            />
          </div>
          <div className="w-28">
            <label className="block text-[11px] text-gray-500 mb-1 uppercase">Momio</label>
            <input
              type="number"
              value={customOdds}
              onChange={e => setCustomOdds(Number(e.target.value))}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-white font-mono outline-none"
            />
          </div>
          <button
            onClick={handleAddCustomLeg}
            disabled={!customTeam}
            className="px-4 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-white rounded-lg text-xs font-medium disabled:opacity-40 transition-colors"
          >
            + Agregar Pata
          </button>
        </div>

        {/* Resumen del Slip Activo */}
        <div className="bg-gray-950 border border-gray-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-gray-800 pb-3">
            <h4 className="text-sm font-semibold text-white">
              Ticket Combinado ({selectedLegs.length} patas seleccionadas)
            </h4>
            <button
              onClick={() => evaluateMutation.mutate({ legs: selectedLegs, bankroll })}
              disabled={evaluateMutation.isPending}
              className="text-xs text-brand-400 hover:text-brand-300 font-semibold"
            >
              🔄 Recalcular Métricas
            </button>
          </div>

          <div className="space-y-2">
            {selectedLegs.map((l, i) => (
              <div key={i} className="flex items-center justify-between text-xs bg-gray-900/60 p-2.5 rounded-lg border border-gray-800/80">
                <span className="font-medium text-gray-200">
                  {i + 1}. {l.team_name}
                </span>
                <div className="flex items-center gap-4 font-mono">
                  <span className="text-gray-400">P: {(l.prob_model * 100).toFixed(0)}%</span>
                  <span className="text-purple-300 font-bold">
                    {l.odds_american > 0 ? `+${l.odds_american}` : l.odds_american}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Métricas del Parlay */}
          {activeSlip && (
            <div className={`mt-4 border rounded-xl p-4 ${activeSlip.viable ? 'bg-green-950/20 border-green-800/50' : 'bg-red-950/20 border-red-800/50'}`}>
              <p className="text-xs font-semibold mb-3 text-gray-200">
                {activeSlip.razon}
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center text-xs font-mono">
                <div className="bg-black/30 p-2 rounded-lg">
                  <p className="text-gray-500 text-[10px]">PROB. CONJUNTA</p>
                  <p className="font-bold text-white mt-0.5">{(activeSlip.prob_combinada * 100).toFixed(1)}%</p>
                </div>
                <div className="bg-black/30 p-2 rounded-lg">
                  <p className="text-gray-500 text-[10px]">CUOTA COMBINADA</p>
                  <p className="font-bold text-yellow-400 mt-0.5">
                    {activeSlip.momio_americano > 0 ? `+${activeSlip.momio_americano}` : activeSlip.momio_americano} ({activeSlip.momio_decimal.toFixed(2)})
                  </p>
                </div>
                <div className="bg-black/30 p-2 rounded-lg">
                  <p className="text-gray-500 text-[10px]">EV ESPERADO / $100</p>
                  <p className={`font-bold mt-0.5 ${activeSlip.ev_dolares > 0 ? 'text-green-400' : 'text-red-400'}`}>
                    ${activeSlip.ev_dolares.toFixed(2)}
                  </p>
                </div>
                <div className="bg-black/30 p-2 rounded-lg">
                  <p className="text-gray-500 text-[10px]">KELLY SUGERIDO</p>
                  <p className="font-bold text-brand-400 mt-0.5">${activeSlip.kelly_monto.toFixed(2)}</p>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

function ParlayCard({ parlay, bankroll }: { parlay: RecommendedParlay; bankroll: number }) {
  return (
    <div className="bg-gray-900 border border-gray-800 hover:border-brand-700/60 rounded-xl p-5 shadow-lg transition-all space-y-4">
      <div className="flex items-center justify-between border-b border-gray-800 pb-3">
        <span className="text-xs font-bold uppercase tracking-wider bg-brand-950 text-brand-300 border border-brand-800 px-2.5 py-0.5 rounded-full">
          {parlay.type}
        </span>
        <span className="font-mono font-bold text-sm text-yellow-400">
          {parlay.momio_americano > 0 ? `+${parlay.momio_americano}` : parlay.momio_americano} (x{parlay.momio_decimal.toFixed(2)})
        </span>
      </div>

      <div className="space-y-2">
        {parlay.legs_evaluadas.map((leg, i) => (
          <div key={i} className="flex justify-between items-center text-xs">
            <span className="text-gray-300 font-medium">
              ✓ {leg.team_name}
            </span>
            <span className="font-mono text-gray-400">
              {(leg.prob_model * 100).toFixed(0)}% · {leg.odds_american > 0 ? `+${leg.odds_american}` : leg.odds_american}
            </span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-2 text-center text-xs pt-3 border-t border-gray-800 font-mono">
        <div>
          <span className="text-[10px] text-gray-500 block uppercase">Probabilidad</span>
          <span className="text-white font-bold">{(parlay.prob_combinada * 100).toFixed(1)}%</span>
        </div>
        <div>
          <span className="text-[10px] text-gray-500 block uppercase">EV / $100</span>
          <span className="text-green-400 font-bold">+${parlay.ev_dolares.toFixed(2)}</span>
        </div>
        <div>
          <span className="text-[10px] text-gray-500 block uppercase">Kelly ($)</span>
          <span className="text-brand-400 font-bold">${parlay.kelly_monto.toFixed(2)}</span>
        </div>
      </div>
    </div>
  )
}
