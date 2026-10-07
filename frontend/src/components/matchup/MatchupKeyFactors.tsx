/**
 * MatchupKeyFactors — Componente modular para los factores clave del modelo.
 * Responsabilidad Única (SRP):
 * Desplegar en formato acordeón interactivo los argumentos tácticos y cuantitativos
 * explicados en lenguaje coloquial y comprensible para humanos ("peras y manzanas").
 */
import React from 'react'

interface MatchupKeyFactorsProps {
  factors?: string[]
  isExpanded: boolean
  onToggle: () => void
}

export const MatchupKeyFactors: React.FC<MatchupKeyFactorsProps> = ({
  factors,
  isExpanded,
  onToggle,
}) => {
  if (!factors || factors.length === 0) return null

  return (
    <div className="mt-3">
      <button
        onClick={onToggle}
        className="text-xs text-gray-400 hover:text-white flex items-center justify-between w-full py-1 transition-colors"
      >
        <span className="flex items-center gap-1.5 font-medium">
          <span>🧠</span> Factores Clave del Modelo ({factors.length})
        </span>
        <span className="text-[10px] text-gray-500 font-mono">
          {isExpanded ? '▲ Ocultar' : '▼ Ver Análisis'}
        </span>
      </button>

      {isExpanded && (
        <ul className="mt-2 bg-gray-950/60 border border-gray-800/80 rounded-lg p-3 space-y-1.5 text-[11px] text-gray-300 animate-fadeIn">
          {factors.map((factor, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className="text-brand-400 text-xs mt-0.5">✓</span>
              <span>{factor}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
