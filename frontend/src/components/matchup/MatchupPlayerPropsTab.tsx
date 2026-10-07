/**
 * MatchupPlayerPropsTab — Componente modular para Player Props (Rendimiento Individual).
 * Responsabilidad Única (SRP):
 * Renderizar las líneas individuales de atletas verificados (yardas por pase, acarreos, recepciones)
 * o mostrar un estado vacío informativo y transparente cuando las casas aún no han publicado props.
 */
import React from 'react'
import type { PlayerProp } from '@/services/api'

interface MatchupPlayerPropsTabProps {
  props?: PlayerProp[]
}

export const MatchupPlayerPropsTab: React.FC<MatchupPlayerPropsTabProps> = ({ props }) => {
  const hasProps = props && props.length > 0

  if (!hasProps) {
    return (
      <div className="py-3 px-2 text-center space-y-1">
        <p className="text-xs text-gray-400 font-medium">
          No se recibieron cuotas de Player Props para este partido.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
      {props.map((prop, idx) => (
        <div
          key={idx}
          className="bg-gray-900/90 border border-gray-800 p-2 rounded-lg text-xs flex justify-between items-center"
        >
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-bold text-white">{prop.player_name}</span>
              {prop.position && (
                <span className="text-[10px] bg-gray-800 text-gray-400 px-1 rounded">
                  {prop.position}
                </span>
              )}
            </div>
            <p className="text-[11px] text-gray-400 mt-0.5">
              {prop.prop_type.replace(/_/g, ' ')}: línea {prop.market_line} · {prop.bookmaker}
            </p>
            <p className="text-[10px] text-gray-500 mt-1">
              Cuotas de mercado; sin pronóstico de jugador validado.
            </p>
          </div>

          <div className="text-right font-mono">
            <span className="text-purple-300 font-bold block">
              O {prop.over_odds_american > 0 ? '+' : ''}{prop.over_odds_american}
            </span>
            <span className="text-gray-400 text-[10px] block">
              U {prop.under_odds_american > 0 ? '+' : ''}{prop.under_odds_american}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}
