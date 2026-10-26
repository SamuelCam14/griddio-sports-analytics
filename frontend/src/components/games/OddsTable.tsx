/**
 * OddsTable — Tabla de momios con probabilidades implícitas sin vig.
 */
import type { OddsData } from '@/services/api'
import { formatMatchTime } from '@/utils/dashboard'

interface Props {
  games: OddsData[]
}

export function OddsTable({ games }: Props) {
  if (games.length === 0) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 text-center text-gray-500">
        No hay momios disponibles. Verifica que ODDS_API_KEY está configurado.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-gray-800">
      <table className="w-full text-sm">
        <thead className="bg-gray-900 text-gray-500 text-xs uppercase tracking-wider">
          <tr>
            <th className="px-4 py-3 text-left">Partido</th>
            <th className="px-4 py-3 text-center">Hora</th>
            <th className="px-4 py-3 text-center">ML Local</th>
            <th className="px-4 py-3 text-center">ML Visita</th>
            <th className="px-4 py-3 text-center">Spread</th>
            <th className="px-4 py-3 text-center">Total</th>
            <th className="px-4 py-3 text-center">Prob. sin vig</th>
            <th className="px-4 py-3 text-center">Book</th>
          </tr>
        </thead>
        <tbody className="bg-gray-950 divide-y divide-gray-800">
          {games.map(game => (
            <tr key={game.external_id} className="hover:bg-gray-900 transition-colors">
              <td className="px-4 py-3">
                <div className="font-medium text-gray-200">{game.home_team}</div>
                <div className="text-gray-500 text-xs">vs {game.away_team}</div>
              </td>
              <td className="px-4 py-3 text-center text-gray-400">
                {formatMatchTime(game.commence_time)}
              </td>
              <td className="px-4 py-3 text-center">
                <OddsBadge value={game.home_ml} />
              </td>
              <td className="px-4 py-3 text-center">
                <OddsBadge value={game.away_ml} />
              </td>
              <td className="px-4 py-3 text-center text-gray-300 font-mono">
                {game.home_spread !== null
                  ? `${game.home_spread > 0 ? '+' : ''}${game.home_spread}`
                  : '—'}
              </td>
              <td className="px-4 py-3 text-center text-gray-300 font-mono">
                {game.total_line ?? '—'}
              </td>
              <td className="px-4 py-3 text-center">
                {game.home_implied_prob !== null && game.away_implied_prob !== null ? (
                  <div className="text-xs">
                    <span className="text-green-400">{(game.home_implied_prob * 100).toFixed(1)}%</span>
                    <span className="text-gray-600 mx-1">/</span>
                    <span className="text-red-400">{(game.away_implied_prob * 100).toFixed(1)}%</span>
                  </div>
                ) : '—'}
              </td>
              <td className="px-4 py-3 text-center">
                <span className="text-xs text-gray-500 capitalize">
                  {game.best_bookmaker ?? '—'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function OddsBadge({ value }: { value: number | null }) {
  if (value === null) return <span className="text-gray-600">—</span>
  const isFav = value < 0
  return (
    <span className={`font-mono font-bold ${isFav ? 'text-gray-300' : 'text-yellow-400'}`}>
      {value > 0 ? `+${value}` : value}
    </span>
  )
}
