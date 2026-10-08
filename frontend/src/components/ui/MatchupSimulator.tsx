/**
 * MatchupSimulator — Simulador interactivo de enfrentamientos NBA.
 * Ejecuta el ensamble de 3 modelos (CatBoost + XGBoost + Logistic) y calcula EV/Kelly en tiempo real.
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Cpu, Home, Plane, Zap } from 'lucide-react'
import { predictionsApi, type SimulationResult } from '@/services/api'

const NBA_TEAMS = [
  "Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets",
  "Chicago Bulls", "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets",
  "Detroit Pistons", "Golden State Warriors", "Houston Rockets", "Indiana Pacers",
  "LA Clippers", "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat",
  "Milwaukee Bucks", "Minnesota Timberwolves", "New Orleans Pelicans", "New York Knicks",
  "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers", "Phoenix Suns",
  "Portland Trail Blazers", "Sacramento Kings", "San Antonio Spurs", "Toronto Raptors",
  "Utah Jazz", "Washington Wizards"
]

interface Props {
  bankroll: number
}

export function MatchupSimulator({ bankroll }: Props) {
  const [homeTeam, setHomeTeam] = useState("Boston Celtics")
  const [awayTeam, setAwayTeam] = useState("Los Angeles Lakers")
  const [homeOdds, setHomeOdds] = useState(-130)
  const [awayOdds, setAwayOdds] = useState(110)
  const [isB2B, setIsB2B] = useState(false)
  const [result, setResult] = useState<SimulationResult | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      predictionsApi.simulate({
        home_team_name: homeTeam,
        visitor_team_name: awayTeam,
        home_odds: homeOdds,
        away_odds: awayOdds,
        bankroll,
        context: isB2B ? { V_IS_B2B: 1, V_DAYS_REST: 1, H_DAYS_REST: 2 } : { V_IS_B2B: 0, H_IS_B2B: 0 },
      }).then(r => r.data),
    onSuccess: setResult,
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-gray-200">
            <Cpu className="h-4 w-4 text-cyan-400" />
            <span>Simulador de Ensamble (CatBoost + XGBoost + Logistic)</span>
          </h3>
          <p className="text-xs text-gray-400">
            Ejecuta inferencia multi-modelo en tiempo real con ponderación walk-forward calibrada.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Local */}
        <div>
          <label className="flex items-center gap-1.5 text-xs text-gray-500 mb-1 uppercase tracking-wider">
            <Home className="h-3.5 w-3.5 text-emerald-400" />
            <span>Equipo Local</span>
          </label>
          <select
            value={homeTeam}
            onChange={e => setHomeTeam(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white text-sm outline-none focus:ring-2 focus:ring-brand-500"
          >
            {NBA_TEAMS.map(t => (
              <option key={t} value={t} disabled={t === awayTeam}>
                {t}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-2 mt-2">
            <span className="text-xs text-gray-500">Momio Local:</span>
            <input
              type="number"
              value={homeOdds}
              onChange={e => setHomeOdds(Number(e.target.value))}
              className="w-20 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white font-mono"
            />
          </div>
        </div>

        {/* Visitante */}
        <div>
          <label className="flex items-center gap-1.5 text-xs text-gray-500 mb-1 uppercase tracking-wider">
            <Plane className="h-3.5 w-3.5 text-cyan-400" />
            <span>Equipo Visitante</span>
          </label>
          <select
            value={awayTeam}
            onChange={e => setAwayTeam(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white text-sm outline-none focus:ring-2 focus:ring-brand-500"
          >
            {NBA_TEAMS.map(t => (
              <option key={t} value={t} disabled={t === homeTeam}>
                {t}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-2 mt-2">
            <span className="text-xs text-gray-500">Momio Visita:</span>
            <input
              type="number"
              value={awayOdds}
              onChange={e => setAwayOdds(Number(e.target.value))}
              className="w-20 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white font-mono"
            />
          </div>
        </div>

        {/* Parámetros de Fatiga */}
        <div className="flex flex-col justify-center">
          <label className="text-xs text-gray-500 uppercase tracking-wider mb-2">
            Fatiga / Calendario
          </label>
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={isB2B}
              onChange={e => setIsB2B(e.target.checked)}
              className="accent-brand-500 rounded"
            />
            <span>Visitante en Back-to-Back (2da noche)</span>
          </label>
        </div>

        {/* Botón de Inferencia */}
        <div className="flex items-end">
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="w-full px-5 py-2.5 bg-brand-600 hover:bg-brand-500 text-white rounded-lg font-medium text-sm transition-colors disabled:opacity-50"
          >
            {mutation.isPending ? (
              'Calculando Ensamble...'
            ) : (
              <span className="flex items-center justify-center gap-1.5">
                <Zap className="h-4 w-4" />
                <span>Simular Matchup</span>
              </span>
            )}
          </button>
        </div>
      </div>

      {/* ─── Resultado del Ensamble ────────────────────────────────────────── */}
      {result && (
        <div className="mt-4 border border-gray-800 bg-gray-950/80 rounded-xl p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-800 pb-3">
            <div>
              <span className="text-sm font-semibold text-white">{result.matchup}</span>
              <span className="text-xs text-gray-500 ml-2">Ensamble Ponderado</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono bg-purple-950 text-purple-300 border border-purple-800 px-3 py-1 rounded-full font-bold">
                {result.confluencia.tier}
              </span>
              <span className="text-xs text-gray-400 bg-gray-800 px-2.5 py-1 rounded-full">
                Acuerdo: {result.confluencia.acuerdo}
              </span>
            </div>
          </div>

          {/* Barra de Probabilidades Calibradas */}
          <div>
            <div className="flex justify-between text-xs font-semibold mb-1">
              <span className="text-green-400">
                {homeTeam}: {(result.probabilidades.home_win * 100).toFixed(1)}%
              </span>
              <span className="text-blue-400">
                {awayTeam}: {(result.probabilidades.away_win * 100).toFixed(1)}%
              </span>
            </div>
            <div className="h-3 w-full bg-gray-800 rounded-full overflow-hidden flex">
              <div
                className="bg-green-500 transition-all duration-500"
                style={{ width: `${result.probabilidades.home_win * 100}%` }}
              />
              <div
                className="bg-blue-500 transition-all duration-500"
                style={{ width: `${result.probabilidades.away_win * 100}%` }}
              />
            </div>
          </div>

          {/* Desglose de los 3 Modelos */}
          <div className="grid grid-cols-3 gap-3 text-center">
            <div className="bg-gray-900/60 p-2.5 rounded-lg border border-gray-800">
              <p className="text-[10px] text-gray-500 uppercase">CatBoost (45%)</p>
              <p className="font-mono text-sm font-bold text-gray-200 mt-0.5">
                {(result.confluencia.desglose_modelos.catboost * 100).toFixed(1)}%
              </p>
            </div>
            <div className="bg-gray-900/60 p-2.5 rounded-lg border border-gray-800">
              <p className="text-[10px] text-gray-500 uppercase">XGBoost (35%)</p>
              <p className="font-mono text-sm font-bold text-gray-200 mt-0.5">
                {(result.confluencia.desglose_modelos.xgboost * 100).toFixed(1)}%
              </p>
            </div>
            <div className="bg-gray-900/60 p-2.5 rounded-lg border border-gray-800">
              <p className="text-[10px] text-gray-500 uppercase">Logistic (20%)</p>
              <p className="font-mono text-sm font-bold text-gray-200 mt-0.5">
                {(result.confluencia.desglose_modelos.logistic_regression * 100).toFixed(1)}%
              </p>
            </div>
          </div>

          {/* Análisis de EV y Recomendación de Kelly */}
          {result.analisis_ev && (
            <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-xs flex flex-wrap items-center justify-between gap-3">
              <div>
                <span className="text-gray-400">Recomendación: </span>
                <span className="font-semibold text-brand-400">
                  {result.analisis_ev.mejor_apuesta}
                </span>
              </div>
              {result.analisis_ev.home.tiene_ventaja && (
                <div className="font-mono text-green-400">
                  Local: Ventaja {result.analisis_ev.home.ventaja} | Kelly: ${result.analisis_ev.home.kelly_monto.toFixed(2)}
                </div>
              )}
              {result.analisis_ev.away.tiene_ventaja && (
                <div className="font-mono text-green-400">
                  Visita: Ventaja {result.analisis_ev.away.ventaja} | Kelly: ${result.analisis_ev.away.kelly_monto.toFixed(2)}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
