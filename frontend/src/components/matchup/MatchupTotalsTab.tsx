/**
 * MatchupTotalsTab — Componente modular para el mercado de Totales (Over / Under).
 * Responsabilidad Única (SRP):
 * Comparar la línea de anotación combinada ofrecida por la casa contra los puntos
 * totales proyectados por el modelo analítico, dejando explícito su estado experimental.
 */
import React from 'react'
import type { MatchupTotals } from '@/services/api'

interface MatchupTotalsTabProps {
  totals?: MatchupTotals
}

export const MatchupTotalsTab: React.FC<MatchupTotalsTabProps> = ({ totals }) => {
  if (
    !totals ||
    totals.status === 'unavailable' ||
    totals.market_line == null ||
    !Number.isFinite(totals.market_line) ||
    totals.market_line <= 0 ||
    totals.projected_total == null ||
    !Number.isFinite(totals.projected_total)
  ) {
    return (
      <div className="text-center py-2 text-xs text-gray-500">
        Sin línea o proyección de totales disponible para este encuentro.
      </div>
    )
  }

  const projectedTotalLabel =
    totals.projected_total != null && Number.isFinite(totals.projected_total)
      ? `${totals.projected_total.toFixed(1)} Puntos`
      : '—'

  const probDisplay =
    totals.probability != null && Number.isFinite(totals.probability)
      ? `${(totals.probability * 100).toFixed(1)}% Prob.`
      : '—'

  const edgeDisplay =
    totals.edge_pct != null && Number.isFinite(totals.edge_pct)
      ? `${totals.edge_pct > 0 ? '+' : ''}${totals.edge_pct}% Ventaja`
      : '—'

  const isPush = totals.pick === 'PUSH'
  const isNeutral = isPush || !totals.pick || totals.odds_american == null
  const pickLabel = isPush
    ? `PUSH (Línea Neutra ${totals.market_line})`
    : totals.pick
    ? `${totals.pick} ${totals.market_line}`
    : 'Sin recomendación'

  return (
    <div className="space-y-2.5">
      {/* Banner de Estado Experimental */}
      <div className="flex items-center justify-between text-xs px-2.5 py-1.5 bg-amber-950/40 border border-amber-500/30 rounded-lg">
        <span className="text-[10px] font-semibold text-amber-300">
          Modelo Experimental (No Producción)
        </span>
        <span className="text-[10px] text-gray-400">Sin EV validado</span>
      </div>

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

      <div
        className={`p-2 rounded-lg text-xs flex justify-between items-center ${
          isNeutral
            ? 'bg-gray-800/60 border border-gray-700/60'
            : 'bg-emerald-950/30 border border-emerald-800/40'
        }`}
      >
        <div>
          <span
            className={`text-[10px] block uppercase font-bold ${
              isNeutral ? 'text-gray-400' : 'text-emerald-300'
            }`}
          >
            Recomendación Totales:
          </span>
          <span className="text-white font-bold">{pickLabel}</span>
          {totals.recommendation && (
            <span className="text-[10px] text-gray-400 block mt-0.5">
              {totals.recommendation}
            </span>
          )}
        </div>
        <div className="text-right font-mono">
          <span
            className={
              isNeutral ? 'text-gray-300 font-bold' : 'text-emerald-400 font-bold'
            }
          >
            {probDisplay}
          </span>
          {!isNeutral && totals.edge_pct != null && (
            <span className="text-gray-400 text-[10px] block">
              {edgeDisplay}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
