/**
 * PickCard — Tarjeta de pick con EV, Kelly y tier visual.
 */
import { useState } from 'react'
import { Star, AlertCircle } from 'lucide-react'
import { useAppStore } from '@/store/useAppStore'
import type { Pick } from '@/services/api'

interface Props {
  pick: Pick
  bankroll: number
}

const TIER_STYLES = {
  PREMIUM: {
    border: 'border-purple-700',
    badge: 'bg-purple-900 text-purple-200',
    glow: 'shadow-purple-900/30',
  },
  VALUE: {
    border: 'border-blue-700',
    badge: 'bg-blue-900 text-blue-200',
    glow: 'shadow-blue-900/30',
  },
  SKIP: {
    border: 'border-gray-700',
    badge: 'bg-gray-800 text-gray-400',
    glow: '',
  },
  NEUTRAL: {
    border: 'border-gray-700',
    badge: 'bg-gray-800 text-gray-300',
    glow: '',
  },
  EXPERIMENTAL: {
    border: 'border-emerald-700',
    badge: 'bg-emerald-900 text-emerald-200',
    glow: 'shadow-emerald-900/30',
  },
}

export function PickCard({ pick, bankroll }: Props) {
  const { savePick, savedPicks, removePick } = useAppStore()
  const [expanded, setExpanded] = useState(false)

  const style = TIER_STYLES[pick.tier]
  const isSaved = savedPicks.some(p => p.game_id === pick.game_id)

  const kellyAmount = pick.kelly_fraccion
    ? (pick.kelly_fraccion * bankroll).toFixed(2)
    : null

  const evColor = pick.ev_dolares && pick.ev_dolares > 0
    ? 'text-green-400'
    : 'text-red-400'

  return (
    <div
      className={`
        bg-gray-900 border rounded-xl p-5 shadow-lg transition-all duration-200
        ${style.border} ${style.glow}
        hover:scale-[1.01] cursor-pointer
      `}
      onClick={() => setExpanded(!expanded)}
    >
      {/* ─── Header ──────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${style.badge}`}>
            {pick.tier}
          </span>
          <p className="text-xs text-gray-500 mt-1">{pick.sport} · {pick.model_agreement}/3 modelos</p>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation()
            isSaved ? removePick(pick.game_id) : savePick(pick)
          }}
          className={`p-1 transition-colors ${isSaved ? 'text-brand-400' : 'text-gray-600 hover:text-gray-400'}`}
          title={isSaved ? 'Quitar de guardados' : 'Guardar pick'}
        >
          <Star className={`h-4 w-4 ${isSaved ? 'fill-brand-400 text-brand-400' : 'text-gray-500'}`} />
        </button>
      </div>

      {/* ─── Pick principal ───────────────────────────────────────── */}
      {pick.tiene_ventaja && pick.pick_side ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-400">
              {pick.pick_side === 'home' ? 'Local' : 'Visitante'}
            </span>
            {pick.momio && (
              <span className="font-mono font-bold text-white">
                {pick.momio > 0 ? `+${pick.momio}` : pick.momio}
              </span>
            )}
          </div>

          {/* Probabilidades */}
          <ProbBar
            homeProb={pick.home_win_prob}
            awayProb={pick.away_win_prob}
            pickSide={pick.pick_side}
          />

          {/* EV y Kelly */}
          <div className="flex justify-between text-sm mt-2">
            <span className="text-gray-400">EV esperado</span>
            <span className={`font-mono font-bold ${evColor}`}>
              {pick.ev_dolares !== null ? `$${pick.ev_dolares.toFixed(2)}` : '—'}
            </span>
          </div>

          {kellyAmount && (
            <div className="flex justify-between text-sm">
              <span className="text-gray-400">Kelly sugerido</span>
              <span className="font-mono text-brand-400">${kellyAmount}</span>
            </div>
          )}
        </div>
      ) : (
        <div className="flex items-center justify-center gap-1.5 py-4 text-gray-500 text-sm">
          <AlertCircle className="h-4 w-4 text-gray-500" />
          <span>Sin ventaja matemática suficiente</span>
        </div>
      )}

      {/* ─── Detalles expandidos ──────────────────────────────────── */}
      {expanded && pick.tiene_ventaja && (
        <div className="mt-4 pt-4 border-t border-gray-800 space-y-2 text-sm">
          <DetailRow label="Prob. modelo" value={`${((pick.prob_modelo ?? 0) * 100).toFixed(1)}%`} />
          <DetailRow label="Prob. implícita" value={pick.momio ? impliedProb(pick.momio) : '—'} />
          <DetailRow label="Bookmaker" value={pick.bookmaker ?? '—'} />
          <DetailRow label="Kelly fraction" value={pick.kelly_fraccion ? `${(pick.kelly_fraccion * 100).toFixed(2)}%` : '—'} />
        </div>
      )}
    </div>
  )
}

function ProbBar({ homeProb, awayProb: _awayProb, pickSide }: {
  homeProb: number
  awayProb: number
  pickSide: 'home' | 'away'
}) {
  const homeWidth = Math.round(homeProb * 100)
  return (
    <div className="relative h-2 rounded-full overflow-hidden bg-gray-800">
      <div
        className={`absolute left-0 h-full rounded-full transition-all ${
          pickSide === 'home' ? 'bg-green-500' : 'bg-blue-500'
        }`}
        style={{ width: `${homeWidth}%` }}
      />
    </div>
  )
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between text-gray-400">
      <span>{label}</span>
      <span className="text-gray-200">{value}</span>
    </div>
  )
}

function impliedProb(ml: number): string {
  const p = ml > 0 ? 100 / (ml + 100) : Math.abs(ml) / (Math.abs(ml) + 100)
  return `${(p * 100).toFixed(1)}%`
}
