/**
 * MatchupTabsNav — Componente modular para el selector de pestañas de mercado.
 * Responsabilidad Única (SRP):
 * Controlar la navegación entre los 4 mercados deportivos soportados:
 * - 💰 Moneyline
 * - 🛡️ Spread / Hándicap
 * - 🎯 Totales
 * - ⚡ Player Props
 */
import React from 'react'

export type MarketTab = 'moneyline' | 'spread' | 'totals' | 'props'

interface MatchupTabsNavProps {
  activeMarket: MarketTab
  onSelectMarket: (market: MarketTab) => void
  propsCount?: number
}

export const MatchupTabsNav: React.FC<MatchupTabsNavProps> = ({
  activeMarket,
  onSelectMarket,
  propsCount = 0,
}) => {
  return (
    <div className="mt-4 flex rounded-lg bg-gray-950 p-1 border border-gray-800 text-[11px] font-medium">
      <button
        onClick={() => onSelectMarket('moneyline')}
        className={`flex-1 py-1 rounded transition-colors ${
          activeMarket === 'moneyline' ? 'bg-gray-800 text-white shadow' : 'text-gray-400 hover:text-white'
        }`}
      >
        💰 Moneyline
      </button>
      <button
        onClick={() => onSelectMarket('spread')}
        className={`flex-1 py-1 rounded transition-colors ${
          activeMarket === 'spread' ? 'bg-gray-800 text-white shadow' : 'text-gray-400 hover:text-white'
        }`}
      >
        🛡️ Spread
      </button>
      <button
        onClick={() => onSelectMarket('totals')}
        className={`flex-1 py-1 rounded transition-colors ${
          activeMarket === 'totals' ? 'bg-gray-800 text-white shadow' : 'text-gray-400 hover:text-white'
        }`}
      >
        🎯 Totales
      </button>
      <button
        onClick={() => onSelectMarket('props')}
        className={`flex-1 py-1 rounded transition-colors ${
          activeMarket === 'props'
            ? 'bg-purple-900/60 text-purple-200 border border-purple-700/50 shadow'
            : 'text-gray-400 hover:text-white'
        }`}
      >
        ⚡ Props {propsCount > 0 && `(${propsCount})`}
      </button>
    </div>
  )
}
