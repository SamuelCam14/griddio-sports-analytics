/**
 * MatchupKeyFactors — Componente modular para los factores clave del modelo.
 * Responsabilidad Única (SRP):
 * Desplegar en formato acordeón interactivo los argumentos tácticos y cuantitativos
 * explicados en lenguaje coloquial y comprensible para humanos ("peras y manzanas").
 */
import React from 'react'
import { Brain, ChevronDown, ChevronUp, Check } from 'lucide-react'

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
        type="button"
        onClick={onToggle}
        className="text-xs text-gray-400 hover:text-white flex items-center justify-between w-full py-1 transition-colors"
      >
        <span className="flex items-center gap-1.5 font-medium">
          <Brain className="h-3.5 w-3.5 text-purple-400" />
          <span>Factores Clave del Modelo ({factors.length})</span>
        </span>
        <span className="flex items-center gap-1 text-[10px] text-gray-500 font-mono">
          <span>{isExpanded ? 'Ocultar' : 'Ver Análisis'}</span>
          {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </span>
      </button>

      {isExpanded && (
        <ul className="mt-2 bg-gray-950/60 border border-gray-800/80 rounded-lg p-3 space-y-1.5 text-[11px] text-gray-300 animate-fadeIn">
          {factors.map((factor, i) => (
            <li key={i} className="flex items-start gap-2">
              <Check className="h-3.5 w-3.5 text-brand-400 shrink-0 mt-0.5" />
              <span>{factor}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
