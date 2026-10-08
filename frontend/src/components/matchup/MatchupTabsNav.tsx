/**
 * MatchupTabsNav — Componente modular para el selector de pestañas de mercado.
 * Responsabilidad Única (SRP):
 * Controlar la navegación entre los 4 mercados deportivos soportados:
 * - Moneyline
 * - Spread / Hándicap
 * - Totales
 * - Player Props
 */
import React from 'react'
import { Coins, Shield, Crosshair, Zap } from 'lucide-react'

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
        type="button"
        onClick={() => onSelectMarket('moneyline')}
        className={`flex-1 py-1 rounded transition-colors flex items-center justify-center gap-1.5 ${
          activeMarket === 'moneyline' ? 'bg-gray-800 text-white shadow' : 'text-gray-400 hover:text-white'
        }`}
      >
        <Coins className="h-3.5 w-3.5 text-emerald-400" />
        <span>Moneyline</span>
      </button>
      <button
        type="button"
        onClick={() => onSelectMarket('spread')}
        className={`flex-1 py-1 rounded transition-colors flex items-center justify-center gap-1.5 ${
          activeMarket === 'spread' ? 'bg-gray-800 text-white shadow' : 'text-gray-400 hover:text-white'
        }`}
      >
        <Shield className="h-3.5 w-3.5 text-cyan-400" />
        <span>Spread</span>
      </button>
      <button
        type="button"
        onClick={() => onSelectMarket('totals')}
        className={`flex-1 py-1 rounded transition-colors flex items-center justify-center gap-1.5 ${
          activeMarket === 'totals' ? 'bg-gray-800 text-white shadow' : 'text-gray-400 hover:text-white'
        }`}
      >
        <Crosshair className="h-3.5 w-3.5 text-amber-400" />
        <span>Totales</span>
      </button>
      <button
        type="button"
        onClick={() => onSelectMarket('props')}
        className={`flex-1 py-1 rounded transition-colors flex items-center justify-center gap-1.5 ${
          activeMarket === 'props'
            ? 'bg-purple-900/60 text-purple-200 border border-purple-700/50 shadow'
            : 'text-gray-400 hover:text-white'
        }`}
      >
        <Zap className="h-3.5 w-3.5 text-purple-400" />
        <span>Props {propsCount > 0 && `(${propsCount})`}</span>
      </button>
    </div>
  )
}
