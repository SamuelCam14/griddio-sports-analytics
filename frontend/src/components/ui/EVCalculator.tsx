/**
 * EVCalculator — Calculadora interactiva de Expected Value.
 * El usuario ingresa probabilidad del modelo y momio → recibe EV, Kelly y recomendación.
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Calculator, Zap } from 'lucide-react'
import { evApi, type EVResult } from '@/services/api'

interface Props {
  bankroll: number
}

export function EVCalculator({ bankroll }: Props) {
  const [prob, setProb] = useState(72)         // %
  const [odds, setOdds] = useState(-130)        // Momio americano
  const [result, setResult] = useState<EVResult | null>(null)

  const mutation = useMutation({
    mutationFn: () => evApi.calculate(prob / 100, odds, bankroll).then(r => r.data),
    onSuccess: setResult,
  })

  const tierColor = {
    PREMIUM: 'text-purple-400 border-purple-700 bg-purple-950',
    VALUE:   'text-blue-400 border-blue-700 bg-blue-950',
    SKIP:    'text-gray-400 border-gray-700 bg-gray-900',
  }

  return (
    <div>
      <h3 className="flex items-center gap-2 text-base font-semibold text-gray-200 mb-4">
        <Calculator className="h-4 w-4 text-emerald-400" />
        <span>Calculadora de EV</span>
      </h3>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
        {/* Probabilidad */}
        <div>
          <label className="block text-xs text-gray-500 mb-1 uppercase tracking-wider">
            Prob. modelo (%)
          </label>
          <input
            type="number"
            value={prob}
            onChange={e => setProb(Number(e.target.value))}
            min={1} max={99} step={1}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                       text-white font-mono text-center focus:ring-2 focus:ring-brand-500 outline-none"
          />
          <input
            type="range"
            value={prob} min={1} max={99}
            onChange={e => setProb(Number(e.target.value))}
            className="w-full mt-2 accent-brand-500"
          />
        </div>

        {/* Momio */}
        <div>
          <label className="block text-xs text-gray-500 mb-1 uppercase tracking-wider">
            Momio americano
          </label>
          <input
            type="number"
            value={odds}
            onChange={e => setOdds(Number(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                       text-white font-mono text-center focus:ring-2 focus:ring-brand-500 outline-none"
          />
          <p className="text-xs text-gray-600 mt-1 text-center">
            Negativo = favorito · Positivo = underdog
          </p>
        </div>

        {/* Bankroll (informativo) */}
        <div>
          <label className="block text-xs text-gray-500 mb-1 uppercase tracking-wider">
            Bankroll actual ($)
          </label>
          <div className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-center">
            <span className="text-brand-400 font-mono font-bold">${bankroll.toLocaleString()}</span>
          </div>
          <p className="text-xs text-gray-600 mt-1 text-center">
            Configura en ajustes
          </p>
        </div>
      </div>

      <button
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        className="w-full sm:w-auto px-6 py-2.5 bg-brand-600 hover:bg-brand-500 text-white
                   rounded-lg font-medium transition-colors disabled:opacity-50"
      >
        {mutation.isPending ? (
          'Calculando...'
        ) : (
          <span className="flex items-center justify-center gap-1.5">
            <Zap className="h-4 w-4" />
            <span>Calcular EV</span>
          </span>
        )}
      </button>

      {/* ─── Resultado ───────────────────────────────────────────────────── */}
      {result && (
        <div className={`mt-4 border rounded-xl p-4 ${tierColor[result.tier]}`}>
          <div className="flex items-center justify-between mb-3">
            <span className="font-bold text-lg">{result.recomendacion}</span>
            <span className="text-sm font-mono bg-black/20 px-3 py-1 rounded-full">
              {result.tier}
            </span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <Metric label="EV por $100" value={`$${result.ev_dolares.toFixed(2)}`}
              highlight={result.ev_dolares > 0} />
            <Metric label="Ventaja" value={result.ventaja}
              highlight={parseFloat(result.ventaja) > 0} />
            <Metric label="Prob. implícita" value={`${(result.prob_implicita * 100).toFixed(1)}%`} />
            <Metric label="Kelly ($)" value={`$${result.kelly_monto.toFixed(2)}`}
              highlight={true} />
          </div>

          {result.advertencias.length > 0 && (
            <div className="mt-3 space-y-1">
              {result.advertencias.map((w, i) => (
                <p key={i} className="text-xs text-yellow-400">{w}</p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Metric({ label, value, highlight = false }: {
  label: string; value: string; highlight?: boolean
}) {
  return (
    <div className="bg-black/20 rounded-lg p-2 text-center">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`font-mono font-bold ${highlight ? 'text-inherit' : 'text-gray-300'}`}>{value}</p>
    </div>
  )
}
