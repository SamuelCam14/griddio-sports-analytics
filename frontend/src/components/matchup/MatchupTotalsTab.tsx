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
  if (!totals) {
    return (
      <div className="text-center py-2 text-xs text-gray-500">
        Sin línea de totales activa disponible para este encuentro.
      </div>
    )
  }

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
          {totals.projected_total} Puntos
        </span>
      </div>

      <div className="bg-emerald-950/30 border border-emerald-800/40 p-2 rounded-lg text-xs flex justify-between items-center">
        <div>
          <span className="text-[10px] text-emerald-300 block uppercase font-bold">Recomendación Totales:</span>
          <span className="text-white font-bold">{totals.pick} {totals.market_line}</span>
        </div>
        <div className="text-right font-mono">
          <span className="text-emerald-400 font-bold">
            {(totals.probability * 100).toFixed(1)}% Prob.
          </span>
          <span className="text-gray-400 text-[10px] block">
            +{totals.edge_pct}% Ventaja
          </span>
        </div>
      </div>
    </div>
  )
}
