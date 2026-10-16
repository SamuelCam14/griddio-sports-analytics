/**
 * MatchupSpreadTab — Componente modular para el mercado de Spread / Hándicap.
 * Responsabilidad Única (SRP):
 * Presentar la comparación cuantitativa entre:
 * - La línea de spread establecida por las casas de apuestas
 * - El marcador proyectado por el modelo cuantitativo
 * - La probabilidad de cubrir la línea y la ventaja porcentual (+Edge)
 */
import React from 'react'
import type { MatchupSpread } from '@/services/api'

interface MatchupSpreadTabProps {
  spread?: MatchupSpread
  homeAbbr: string
  awayAbbr: string
}

export const MatchupSpreadTab: React.FC<MatchupSpreadTabProps> = ({
  spread,
  homeAbbr,
  awayAbbr,
}) => {
  if (!spread) {
    return (
      <div className="text-center py-2 text-xs text-gray-500">
        Sin línea de spread activa disponible en este momento.
      </div>
    )
  }

  const marketLineLabel =
    spread.market_line != null
      ? spread.market_line > 0
        ? `+${spread.market_line}`
        : `${spread.market_line}`
      : 'Sin línea'

  const projectedAway = spread.projected_away_score != null ? spread.projected_away_score : '—'
  const projectedHome = spread.projected_home_score != null ? spread.projected_home_score : '—'
  const spreadPick = spread.pick || 'Sin selección disponible'

  const coverProbDisplay =
    spread.cover_probability != null && Number.isFinite(spread.cover_probability)
      ? `${(spread.cover_probability * 100).toFixed(1)}% Cubre`
      : 'Prob. no disponible'

  const edgePctDisplay =
    spread.edge_pct != null && Number.isFinite(spread.edge_pct)
      ? `+${spread.edge_pct.toFixed(1)}% Ventaja`
      : 'Sin ventaja calculada'

  return (
    <div className="space-y-2.5">
      <div className="flex justify-between items-center text-xs">
        <span className="text-gray-400">Línea de Hándicap Mercado:</span>
        <span className="font-bold text-white font-mono">
          {homeAbbr} {marketLineLabel}
        </span>
      </div>

      <div className="flex justify-between items-center text-xs">
        <span className="text-gray-400">Marcador Proyectado:</span>
        <span className="font-mono text-purple-300 font-semibold">
          {awayAbbr} {projectedAway} - {projectedHome} {homeAbbr}
        </span>
      </div>

      <div className="bg-purple-950/30 border border-purple-800/40 p-2 rounded-lg text-xs flex justify-between items-center">
        <div>
          <span className="text-[10px] text-purple-300 block uppercase font-bold">Pick en el Spread:</span>
          <span className="text-white font-bold">{spreadPick}</span>
        </div>
        <div className="text-right font-mono">
          <span className="text-green-400 font-bold">
            {coverProbDisplay}
          </span>
          <span className="text-gray-400 text-[10px] block">
            {edgePctDisplay}
          </span>
        </div>
      </div>
    </div>
  )
}
