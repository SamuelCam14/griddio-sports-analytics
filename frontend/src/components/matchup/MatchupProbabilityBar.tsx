/**
 * MatchupProbabilityBar — Componente modular de probabilidad.
 * Responsabilidad Única (SRP):
 * Renderizar la barra dual de probabilidad de victoria estimada por el modelo cuantitativo
 * para el equipo local y el equipo visitante.
 */
import React from 'react'

interface MatchupProbabilityBarProps {
  awayAbbr: string
  homeAbbr: string
  awayWinProb: number
  homeWinProb: number
}

export const MatchupProbabilityBar: React.FC<MatchupProbabilityBarProps> = ({
  awayAbbr,
  homeAbbr,
  awayWinProb,
  homeWinProb,
}) => {
  const awayPct = Math.round(awayWinProb * 100)
  const homePct = Math.round(homeWinProb * 100)

  return (
    <div className="mt-4 space-y-1.5">
      <div className="flex justify-between items-center text-xs font-mono">
        <span className={awayWinProb >= 0.5 ? 'text-white font-bold' : 'text-gray-400'}>
          {awayAbbr} {(awayWinProb * 100).toFixed(1)}%
        </span>
        <span className="text-[10px] text-gray-500 uppercase tracking-wider">Probabilidad Victoria IA</span>
        <span className={homeWinProb >= 0.5 ? 'text-white font-bold' : 'text-gray-400'}>
          {(homeWinProb * 100).toFixed(1)}% {homeAbbr}
        </span>
      </div>

      <div className="relative h-2.5 rounded-full overflow-hidden bg-gray-800 flex">
        <div
          className={`h-full transition-all duration-300 ${
            awayWinProb >= homeWinProb ? 'bg-purple-500' : 'bg-gray-600'
          }`}
          style={{ width: `${awayPct}%` }}
        />
        <div
          className={`h-full transition-all duration-300 ${
            homeWinProb > awayWinProb ? 'bg-brand-500' : 'bg-gray-700'
          }`}
          style={{ width: `${homePct}%` }}
        />
      </div>
    </div>
  )
}
