/**
 * MatchupMoneylineTab — Componente modular para el mercado Moneyline (+EV).
 * Responsabilidad Única (SRP):
 * Presentar la selección recomendada a ganador directo, con:
 * - Nombre del equipo recomendado
 * - Momio americano oficial ofrecido por la casa
 * - Valor Esperado (EV) proyectado por cada $100 apostados
 * - Monto sugerido según el Criterio Fraccional de Kelly
 */
import React from 'react'
import { formatEvDollars } from '@/utils/dashboard'

interface MatchupMoneylineTabProps {
  pickTeamName: string
  oddsAmerican?: number | null
  evDollars?: number | null
  kellyFraction?: number | null
  bankroll: number
}

export const MatchupMoneylineTab: React.FC<MatchupMoneylineTabProps> = ({
  pickTeamName,
  oddsAmerican,
  evDollars = 0,
  kellyFraction = 0,
  bankroll,
}) => {
  const isPositiveEV = evDollars != null && Number.isFinite(evDollars) && evDollars > 0
  const safeKelly = kellyFraction ?? 0
  const kellyStake = Number.isFinite(safeKelly * bankroll) ? (safeKelly * bankroll).toFixed(2) : '0.00'

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center text-xs">
        <span className="text-gray-400">Selección Recomendada:</span>
        <span className="font-bold text-white flex items-center gap-1.5">
          <span>{pickTeamName}</span>
          {oddsAmerican !== undefined && oddsAmerican !== null && (
            <span className="font-mono text-purple-300 bg-purple-950/60 border border-purple-800 px-1.5 py-0.5 rounded text-[11px]">
              {oddsAmerican > 0 ? `+${oddsAmerican}` : oddsAmerican}
            </span>
          )}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 text-center text-xs font-mono pt-1">
        <div className="bg-gray-900/80 p-2 rounded-lg border border-gray-800">
          <span className="text-gray-500 text-[10px] block uppercase">EV Esperado / $100</span>
          <span className={`font-bold mt-0.5 block ${isPositiveEV ? 'text-green-400' : 'text-red-400'}`}>
            {formatEvDollars(evDollars, 2)}
          </span>
        </div>
        <div className="bg-gray-900/80 p-2 rounded-lg border border-gray-800">
          <span className="text-gray-500 text-[10px] block uppercase">Kelly Sugerido</span>
          <span className="font-bold text-brand-400 mt-0.5 block">
            ${kellyStake}
          </span>
        </div>
      </div>
    </div>
  )
}
