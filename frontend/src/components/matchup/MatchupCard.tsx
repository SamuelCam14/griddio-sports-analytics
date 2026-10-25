/**
 * MatchupCard — Tarjeta Orquestadora de Análisis Deportivo (Multi-Mercado).
 *
 * Arquitectura y Flujo de Datos (Data Flow):
 * 1. ORIGEN: The Odds API + ESPN API (cuotas oficiales en tiempo real, líneas de spreads, totales y player props).
 * 2. PROCESAMIENTO: `backend/app/services/live_ingestion_service.py` almacena snapshots y calcula probabilidades sin vig.
 * 3. MODELADO: `backend/app/services/sports_analytics_service.py` deriva márgenes proyectados y EV.
 * 4. ENDPOINT: `GET /api/v1/predictions/today?sport=NFL&period=week` entrega la entidad `Pick`.
 * 5. CONSUMO: Hook modular `useTodayPicks` en `src/hooks/usePredictions.ts` cachea la respuesta.
 * 6. PRESENTACIÓN: Este componente coordina sub-componentes especializados con Responsabilidad Única (SRP):
 *    - `MatchupHeader`: Nombres, fecha formateada, badges y acción de favoritos.
 *    - `MatchupProbabilityBar`: Visualización dual de probabilidad de victoria IA.
 *    - `MatchupTabsNav`: Selector de mercados (Moneyline, Spread, Totales, Props).
 *    - `MatchupMoneylineTab`: Apuesta directa, momio, EV esperado y Kelly.
 *    - `MatchupSpreadTab`: Hándicap de mercado vs margen proyectado y % de cubrir.
 *    - `MatchupTotalsTab`: Línea de totales vs puntos combinados del modelo.
 *    - `MatchupPlayerPropsTab`: Rendimiento individual de atletas reales verificados.
 *    - `MatchupKeyFactors`: Argumentos explicados para humanos ("peras y manzanas").
 */
import React, { useState } from 'react'
import { useAppStore } from '@/store/useAppStore'
import type { Pick } from '@/services/api'

import { MatchupHeader } from './MatchupHeader'
import { MatchupProbabilityBar } from './MatchupProbabilityBar'
import { MatchupTabsNav, type MarketTab } from './MatchupTabsNav'
import { MatchupMoneylineTab } from './MatchupMoneylineTab'
import { MatchupSpreadTab } from './MatchupSpreadTab'
import { MatchupTotalsTab } from './MatchupTotalsTab'
import { MatchupPlayerPropsTab } from './MatchupPlayerPropsTab'
import { MatchupKeyFactors } from './MatchupKeyFactors'

interface MatchupCardProps {
  pick: Pick
  bankroll: number
}

export const MatchupCard: React.FC<MatchupCardProps> = ({ pick, bankroll }) => {
  const { savePick, savedPicks, removePick } = useAppStore()
  const [activeMarket, setActiveMarket] = useState<MarketTab>('moneyline')
  const [showFactors, setShowFactors] = useState(false)

  const isSaved = savedPicks.some((p) => p.game_id === pick.game_id)

  const homeName = pick.home_team?.name || 'Local'
  const awayName = pick.away_team?.name || 'Visitante'
  const homeAbbr = pick.home_team?.abbreviation || 'LOC'
  const awayAbbr = pick.away_team?.abbreviation || 'VIS'

  const tierBorders: Record<string, string> = {
    CONFIANZA: 'border-emerald-500/80',
    'NO PICK': 'border-gray-800',
    PREMIUM: 'border-purple-600/70',
    VALUE: 'border-blue-600/70',
    SKIP: 'border-gray-800',
    NEUTRAL: 'border-gray-700',
  }
  const borderClass = tierBorders[pick.tier] || tierBorders.NEUTRAL

  const handleToggleSave = () => {
    if (isSaved) {
      removePick(pick.game_id)
    } else {
      savePick(pick)
    }
  }

  const propsCount = pick.player_props ? pick.player_props.length : 0

  return (
    <article
      className={`bg-gray-900 border ${borderClass} rounded-xl p-5 shadow-xl transition-all duration-200 flex flex-col justify-between`}
    >
      <div>
        {/* 1. Encabezado e Identidad del Partido */}
        <MatchupHeader pick={pick} isSaved={isSaved} onToggleSave={handleToggleSave} />

        {/* 2. Barra de Probabilidad de Victoria del Modelo */}
        <MatchupProbabilityBar
          awayAbbr={awayAbbr}
          homeAbbr={homeAbbr}
          awayWinProb={pick.away_win_prob}
          homeWinProb={pick.home_win_prob}
        />

        {/* 3. Navegación de Mercados (Tabs) */}
        <MatchupTabsNav
          activeMarket={activeMarket}
          onSelectMarket={setActiveMarket}
          propsCount={propsCount}
        />

        {/* 4. Contenedor Modular del Mercado Activo */}
        <div className="mt-3.5 bg-gray-950/80 border border-gray-800/80 rounded-xl p-3.5">
          {activeMarket === 'moneyline' && (
            <MatchupMoneylineTab
              pickTeamName={pick.pick_team_name ?? (pick.pick_side === 'home' ? homeName : awayName)}
              oddsAmerican={pick.momio}
              evDollars={pick.ev_dolares}
              kellyFraction={pick.kelly_fraccion}
              bankroll={bankroll}
            />
          )}

          {activeMarket === 'spread' && (
            <MatchupSpreadTab
              spread={pick.spread}
              homeAbbr={homeAbbr}
              awayAbbr={awayAbbr}
            />
          )}

          {activeMarket === 'totals' && (
            <MatchupTotalsTab totals={pick.totals} />
          )}

          {activeMarket === 'props' && (
            <MatchupPlayerPropsTab props={pick.player_props} />
          )}
        </div>

        {/* 5. Factores Clave Explicados en Lenguaje Humano */}
        <MatchupKeyFactors
          factors={pick.key_factors}
          isExpanded={showFactors}
          onToggle={() => setShowFactors(!showFactors)}
        />
      </div>
    </article>
  )
}
