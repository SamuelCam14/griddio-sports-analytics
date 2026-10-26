/**
 * MatchupTotalsTab — Componente modular para el mercado de Totales (Over / Under).
 * Responsabilidad Única (SRP):
 * Comparar la línea de anotación combinada ofrecida por la casa contra los puntos
 * totales proyectados por el modelo analítico.
 */
import React from 'react'
import type { MatchupTotals } from '@/services/api'

interface MatchupTotalsTabProps {
  totals?: MatchupTotals
}

export const MatchupTotalsTab: React.FC<MatchupTotalsTabProps> = ({ totals }) => {
  if (!totals || totals.status === 'unavailable' || totals.market_line == null) {
    return (
      <div className="text-center py-2 text-xs text-gray-500">
        Sin línea de totales activa disponible para este encuentro.
      </div>
    )
  }

  const projectedTotalLabel =
    totals.projected_total != null && Number.isFinite(totals.projected_total)
      ? `${totals.projected_total} Puntos`
      : '—'

  const probDisplay =
    totals.probability != null && Number.isFinite(totals.probability)
      ? `${(totals.probability * 100).toFixed(1)}% Prob.`
      : '—'

  const edgeDisplay =
    totals.edge_pct != null && Number.isFinite(totals.edge_pct)
      ? `${totals.edge_pct > 0 ? '+' : ''}${totals.edge_pct}% Ventaja`
      : '—'

  const pickLabel = totals.pick ? `${totals.pick} ${totals.market_line}` : 'Sin recomendación'

  return (
    <div className="space-y-2.5">
      <div className="flex justify-between items-center text-xs">
        <span className="text-gray-400">Línea Total Casa:</span>
        <span className="font-bold text-white font-mono">
          {totals.market_line} Puntos
        </span>
      </div>

      <div className="flex justify-between items-center text-xs">
        <span className="text-gray-400">Puntos Totales Proyectados:</span>
        <span className="font-mono text-yellow-400 font-bold">
          {projectedTotalLabel}
        </span>
      </div>

      <div className="bg-emerald-950/30 border border-emerald-800/40 p-2 rounded-lg text-xs flex justify-between items-center">
        <div>
          <span className="text-[10px] text-emerald-300 block uppercase font-bold">Recomendación Totales:</span>
          <span className="text-white font-bold">{pickLabel}</span>
        </div>
        <div className="text-right font-mono">
          <span className="text-emerald-400 font-bold">
            {probDisplay}
          </span>
          <span className="text-gray-400 text-[10px] block">
            {edgeDisplay}
          </span>
        </div>
      </div>
    </div>
  )
}
