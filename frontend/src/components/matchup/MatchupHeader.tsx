/**
 * MatchupHeader — Componente modular para el encabezado del partido.
 * Responsabilidad Única (SRP):
 * Presentar la identidad visual del enfrentamiento:
 * - Equipos (local y visitante con nombres y abreviaciones)
 * - Fecha y hora oficial del encuentro
 * - Insignias de confianza (Tier PREMIUM / VALUE)
 * - Estado de juego (HOY EN VIVO)
 * - Fuente de cuotas (Bookmaker) y confluencia
 * - Botón para guardar/marcar como favorito
 */
import React from 'react'
import { Star, Radio } from 'lucide-react'
import type { Pick } from '@/services/api'

interface MatchupHeaderProps {
  pick: Pick
  isSaved: boolean
  onToggleSave: () => void
}

export const MatchupHeader: React.FC<MatchupHeaderProps> = ({ pick, isSaved, onToggleSave }) => {
  const homeName = pick.home_team?.name || 'Local'
  const awayName = pick.away_team?.name || 'Visitante'

  const tierStyles = {
    PREMIUM: 'bg-purple-950 text-purple-300 border-purple-800',
    VALUE: 'bg-blue-950 text-blue-300 border-blue-800',
    SKIP: 'bg-gray-800 text-gray-400 border-gray-700',
    NEUTRAL: 'bg-gray-800 text-gray-300 border-gray-600',
  }
  const badgeStyle = tierStyles[pick.tier as keyof typeof tierStyles] || tierStyles.NEUTRAL

  const formatGameDate = (dateStr: string) => {
    try {
      const d = new Date(dateStr)
      return d.toLocaleDateString('es-MX', {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return dateStr
    }
  }

  return (
    <div className="flex items-start justify-between gap-3 border-b border-gray-800 pb-3">
      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase ${badgeStyle}`}>
            {pick.tier}
          </span>
          {pick.is_today && (
            <span className="flex items-center gap-1 text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800 px-2 py-0.5 rounded-full">
              <Radio className="h-2.5 w-2.5 animate-pulse text-amber-400" />
              <span>HOY</span>
            </span>
          )}
          <span className="text-[11px] text-gray-400 font-mono">
            {formatGameDate(pick.game_date)}
          </span>
        </div>

        <h3 className="text-base font-bold text-white mt-1.5 flex items-center gap-2">
          <span>{awayName}</span>
          <span className="text-gray-500 font-normal text-xs">@</span>
          <span>{homeName}</span>
        </h3>
        <p className="text-xs text-gray-400 mt-0.5">
          {pick.sport} · {pick.bookmaker ?? 'DraftKings'} · Confluencia {pick.model_agreement}/3 Modelos
        </p>
      </div>

      <button
        onClick={onToggleSave}
        className={`p-1.5 transition-colors ${
          isSaved ? 'text-brand-400' : 'text-gray-600 hover:text-gray-400'
        }`}
        title={isSaved ? 'Quitar de favoritos' : 'Guardar partido'}
        aria-label="Favorito"
      >
        <Star className={`h-4 w-4 ${isSaved ? 'fill-brand-400 text-brand-400' : 'text-gray-500'}`} />
      </button>
    </div>
  )
}
