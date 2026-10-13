/**
 * ParlayBuilderView — Constructor y optimizador de micro-parlays con ventaja matemática.
 * Diseñado con estética de sportsbook moderna (dark mode, monospace, confidence meters).
 * Conectado exclusivamente con inferencia y datos reales de la NFL (/api/v1/predictions/experimental/nfl).
 */
import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Target,
  Shield,
  Zap,
  RefreshCw,
  Check,
  Calendar,
  TrendingUp,
  AlertTriangle,
  Clock,
  ArrowRight,
  Flame,
  Plus,
  Trash2,
} from 'lucide-react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  predictionsApi,
  oddsApi,
  parlayApi,
  type RecommendedParlay,
  type ExperimentalPredictionGame,
} from '@/services/api'

interface Props {
  bankroll: number
}

export interface ParlayLegItem {
  id: string
  team_name: string
  selection_label: string
  matchup: string
  game_date: string
  commence_time?: string
  prob_model: number
  odds_american: number
  odds_decimal: number
  source: 'experimental_nfl' | 'market_odds' | 'custom'
}

function americanToDecimal(odds: number): number {
  if (odds > 0) return Number(((odds / 100) + 1).toFixed(4))
  if (odds < 0) return Number(((100 / Math.abs(odds)) + 1).toFixed(4))
  return 2.0
}

function decimalToAmerican(decimal: number): number {
  if (decimal >= 2.0) return Math.round((decimal - 1.0) * 100)
  if (decimal > 1.0) return Math.round(-100 / (decimal - 1.0))
  return 100
}

function probToFairAmerican(prob: number): number {
  if (prob >= 0.5) {
    const q = Math.max(1 - prob, 0.001)
    return -Math.round((prob / q) * 100)
  }
  const p = Math.max(prob, 0.001)
  return Math.round(((1 - p) / p) * 100)
}

function formatOdds(american: number): string {
  return american > 0 ? `+${american}` : `${american}`
}

export function ParlayBuilderView({ bankroll }: Props) {
  const [activeSport, setActiveSport] = useState<'NFL' | 'NBA'>('NFL')
  const [selectedLegs, setSelectedLegs] = useState<ParlayLegItem[]>([])
  const [customTeam, setCustomTeam] = useState('')
  const [customProb, setCustomProb] = useState(65)
  const [customOdds, setCustomOdds] = useState(-110)

  // ─── Consultas Reales de Backend ──────────────────────────────────────────
  const experimentalNFLQuery = useQuery({
    queryKey: ['experimentalPredictions', 'nfl', 14],
    queryFn: () => predictionsApi.getExperimental('nfl', 14).then((r) => r.data),
    staleTime: 60_000,
    enabled: activeSport === 'NFL',
  })

  const oddsNFLQuery = useQuery({
    queryKey: ['nflOddsReal'],
    queryFn: () => oddsApi.getNFL().then((r) => r.data),
    staleTime: 60_000,
    enabled: activeSport === 'NFL',
  })

  const recommendedQuery = useQuery({
    queryKey: ['recommendedParlays', activeSport, bankroll],
    queryFn: () => parlayApi.getRecommended(bankroll, 4).then((r) => r.data),
    staleTime: 60_000,
  })

  const evaluateMutation = useMutation({
    mutationFn: (legs: Array<{ team_name: string; prob_model: number; odds_american: number }>) =>
      parlayApi.evaluate(legs, bankroll).then((r) => r.data),
  })

  // ─── Generación de Candidatos Reales desde la NFL ─────────────────────────
  const availableCandidates = useMemo(() => {
    if (activeSport !== 'NFL') return []

    const candidates: ParlayLegItem[] = []
    const oddsGames = oddsNFLQuery.data?.games ?? []
    const expGames = experimentalNFLQuery.data?.games ?? []

    // 1. Partidos con Momios Reales de Mercado (p. ej. Partido de Hoy: Bills @ Rams)
    oddsGames.forEach((og) => {
      // Cruzar con la inferencia probabilística real del backend
      const matchedExp = expGames.find(
        (eg) =>
          (eg.home_team_name.toLowerCase().includes(og.home_team.toLowerCase()) ||
            og.home_team.toLowerCase().includes(eg.home_team_name.toLowerCase()) ||
            eg.home_team_abbreviation === 'LA') &&
          (eg.away_team_name.toLowerCase().includes(og.away_team.toLowerCase()) ||
            og.away_team.toLowerCase().includes(eg.away_team_name.toLowerCase()) ||
            eg.away_team_abbreviation === 'BUF')
      )

      const homeProb = matchedExp?.home_win_probability ?? (og.home_implied_prob ?? 0.5)
      const awayProb = matchedExp?.away_win_probability ?? (og.away_implied_prob ?? 0.5)

      // Victoria Visitante con momio real observado
      if (og.away_ml != null) {
        candidates.push({
          id: `odds_ml_away_${og.external_id}`,
          team_name: og.away_team,
          selection_label: `${og.away_team} ML (Línea ${formatOdds(og.away_ml)})`,
          matchup: `${og.away_team} @ ${og.home_team}`,
          game_date: og.commence_time ? og.commence_time.split('T')[0] : '2026-10-12',
          commence_time: og.commence_time,
          prob_model: Number(awayProb.toFixed(4)),
          odds_american: og.away_ml,
          odds_decimal: americanToDecimal(og.away_ml),
          source: matchedExp ? 'experimental_nfl' : 'market_odds',
        })
      }

      // Victoria Local con momio real observado
      if (og.home_ml != null) {
        candidates.push({
          id: `odds_ml_home_${og.external_id}`,
          team_name: og.home_team,
          selection_label: `${og.home_team} ML (Línea ${formatOdds(og.home_ml)})`,
          matchup: `${og.away_team} @ ${og.home_team}`,
          game_date: og.commence_time ? og.commence_time.split('T')[0] : '2026-10-12',
          commence_time: og.commence_time,
          prob_model: Number(homeProb.toFixed(4)),
          odds_american: og.home_ml,
          odds_decimal: americanToDecimal(og.home_ml),
          source: matchedExp ? 'experimental_nfl' : 'market_odds',
        })
      }

      // Totales Over / Under si están disponibles en mercado
      if (og.total_line != null && og.over_ml != null) {
        candidates.push({
          id: `odds_total_over_${og.external_id}`,
          team_name: `${og.away_team} @ ${og.home_team} OVER ${og.total_line}`,
          selection_label: `OVER ${og.total_line} Pts (${formatOdds(og.over_ml)})`,
          matchup: `${og.away_team} vs ${og.home_team}`,
          game_date: og.commence_time ? og.commence_time.split('T')[0] : '2026-10-12',
          commence_time: og.commence_time,
          prob_model: 0.50, // Línea neutral de mercado
          odds_american: og.over_ml,
          odds_decimal: americanToDecimal(og.over_ml),
          source: 'market_odds',
        })
      }
      if (og.total_line != null && og.under_ml != null) {
        candidates.push({
          id: `odds_total_under_${og.external_id}`,
          team_name: `${og.away_team} @ ${og.home_team} UNDER ${og.total_line}`,
          selection_label: `UNDER ${og.total_line} Pts (${formatOdds(og.under_ml)})`,
          matchup: `${og.away_team} vs ${og.home_team}`,
          game_date: og.commence_time ? og.commence_time.split('T')[0] : '2026-10-12',
          commence_time: og.commence_time,
          prob_model: 0.50,
          odds_american: og.under_ml,
          odds_decimal: americanToDecimal(og.under_ml),
          source: 'market_odds',
        })
      }
    })

    // 2. Partidos de Inferencia Experimental NFL sin momios de casas publicados todavía
    expGames.forEach((eg: ExperimentalPredictionGame) => {
      // Evitar duplicar el partido si ya fue cubierto desde oddsGames
      const alreadyInOdds = candidates.some(
        (c) =>
          c.matchup.includes(eg.home_team_abbreviation) &&
          c.matchup.includes(eg.away_team_abbreviation)
      )
      if (alreadyInOdds) return

      const homeProb = eg.home_win_probability
      const awayProb = eg.away_win_probability

      // Seleccionar el favorito del modelo (probabilidad >= 52%)
      if (homeProb >= 0.52) {
        const fairOdds = probToFairAmerican(homeProb)
        candidates.push({
          id: `exp_home_${eg.game_id}`,
          team_name: eg.home_team_name,
          selection_label: `${eg.home_team_name} ML (Fair ${formatOdds(fairOdds)})`,
          matchup: `${eg.away_team_abbreviation} @ ${eg.home_team_abbreviation}`,
          game_date: eg.game_date,
          prob_model: Number(homeProb.toFixed(4)),
          odds_american: fairOdds,
          odds_decimal: americanToDecimal(fairOdds),
          source: 'experimental_nfl',
        })
      } else if (awayProb >= 0.52) {
        const fairOdds = probToFairAmerican(awayProb)
        candidates.push({
          id: `exp_away_${eg.game_id}`,
          team_name: eg.away_team_name,
          selection_label: `${eg.away_team_name} ML (Fair ${formatOdds(fairOdds)})`,
          matchup: `${eg.away_team_abbreviation} @ ${eg.home_team_abbreviation}`,
          game_date: eg.game_date,
          prob_model: Number(awayProb.toFixed(4)),
          odds_american: fairOdds,
          odds_decimal: americanToDecimal(fairOdds),
          source: 'experimental_nfl',
        })
      }
    })

    // Ordenar de mayor a menor probabilidad del modelo
    return candidates.sort((a, b) => b.prob_model - a.prob_model)
  }, [activeSport, oddsNFLQuery.data, experimentalNFLQuery.data])

  // ─── Inicializar selección con los 2 mejores si no hay patas ───────────────
  const handleToggleLeg = (leg: ParlayLegItem) => {
    const exists = selectedLegs.some((l) => l.id === leg.id)
    let newLegs: ParlayLegItem[]
    if (exists) {
      newLegs = selectedLegs.filter((l) => l.id !== leg.id)
    } else {
      if (selectedLegs.length >= 4) return // Máximo 4 para micro-parlays
      newLegs = [...selectedLegs, leg]
    }
    setSelectedLegs(newLegs)
    if (newLegs.length >= 2) {
      evaluateMutation.mutate(
        newLegs.map((l) => ({
          team_name: l.team_name,
          prob_model: l.prob_model,
          odds_american: l.odds_american,
        }))
      )
    }
  }

  const handleAddCustomLeg = () => {
    if (!customTeam.trim()) return
    const dec = americanToDecimal(customOdds)
    const leg: ParlayLegItem = {
      id: `custom_${Date.now()}`,
      team_name: customTeam.trim(),
      selection_label: `${customTeam.trim()} (Personalizado)`,
      matchup: 'Entrada manual',
      game_date: new Date().toISOString().split('T')[0],
      prob_model: customProb / 100.0,
      odds_american: customOdds,
      odds_decimal: dec,
      source: 'custom',
    }
    const newLegs = [...selectedLegs, leg]
    setSelectedLegs(newLegs)
    setCustomTeam('')
    if (newLegs.length >= 2) {
      evaluateMutation.mutate(
        newLegs.map((l) => ({
          team_name: l.team_name,
          prob_model: l.prob_model,
          odds_american: l.odds_american,
        }))
      )
    }
  }

  const handleRemoveLeg = (idx: number) => {
    const newLegs = selectedLegs.filter((_, i) => i !== idx)
    setSelectedLegs(newLegs)
    if (newLegs.length >= 2) {
      evaluateMutation.mutate(
        newLegs.map((l) => ({
          team_name: l.team_name,
          prob_model: l.prob_model,
          odds_american: l.odds_american,
        }))
      )
    }
  }

  // ─── Métricas Calculadas en Tiempo Real (SSOT local + backend) ────────────
  const localSlipMetrics = useMemo(() => {
    if (selectedLegs.length < 2) return null

    // Multiplicación independiente de probabilidades y momios decimales
    const jointProb = selectedLegs.reduce((acc, l) => acc * l.prob_model, 1.0)
    const combinedDecimal = selectedLegs.reduce((acc, l) => acc * l.odds_decimal, 1.0)
    const combinedAmerican = decimalToAmerican(combinedDecimal)

    // EV por dólar apostado = (P * (Dec - 1)) - (1 - P)
    const evPerDollar = jointProb * (combinedDecimal - 1.0) - (1.0 - jointProb)
    const evDollars100 = evPerDollar * 100.0

    // Criterio de Kelly (un cuarto de Kelly con tope 5% de bankroll)
    const b = combinedDecimal - 1.0
    let fullKelly = b > 0 ? (jointProb * combinedDecimal - 1.0) / b : 0
    if (fullKelly < 0) fullKelly = 0
    const quarterKelly = Math.min(0.25 * fullKelly, 0.05)
    const kellyStake = quarterKelly * bankroll

    // Nivel de confianza para medidor
    const confidenceLevel =
      jointProb >= 0.55 ? 'alta' : jointProb >= 0.40 ? 'moderada' : 'especulativa'

    return {
      jointProb,
      combinedDecimal,
      combinedAmerican,
      evPerDollar,
      evDollars100,
      quarterKelly,
      kellyStake,
      confidenceLevel,
    }
  }, [selectedLegs, bankroll])

  const backendEvaluation = evaluateMutation.data

  return (
    <div className="space-y-6 text-slate-100">
      {/* ─── Header & Reglas Cuantitativas (Estilo AVO Dark) ────────────────── */}
      <div className="rounded-2xl border border-white/[0.08] bg-[#0c0e14] p-5 sm:p-6 shadow-2xl relative overflow-hidden">
        <div className="absolute top-0 right-0 h-48 w-48 bg-apple-blue/10 blur-3xl pointer-events-none" />

        <div className="flex flex-wrap items-center justify-between gap-4 relative z-10">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="flex h-6 items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 text-[11px] font-semibold text-emerald-400">
                <Flame className="h-3 w-3 fill-current" />
                MICRO-PARLAYS EV+
              </span>
              <span className="rounded-full border border-white/[0.08] bg-white/[0.04] px-2.5 py-0.5 text-[10px] font-mono text-slate-400">
                Estrategia 2–3 Legs
              </span>
            </div>
            <h2 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
              Constructor Cuantitativo de Micro-Parlays
            </h2>
            <p className="text-xs text-slate-400 max-w-2xl leading-relaxed">
              Combina selecciones de alta probabilidad (&gt;60%) para alcanzar cuotas cercanas a{' '}
              <strong className="text-white font-mono">2.0 (+100)</strong> donde el valor esperado
              combinado supera al margen de la casa (vig).
            </p>
          </div>

          {/* Selector de Deporte */}
          <div className="flex items-center gap-2">
            <div className="flex rounded-xl border border-white/[0.08] bg-black/50 p-1">
              <button
                type="button"
                onClick={() => setActiveSport('NFL')}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
                  activeSport === 'NFL'
                    ? 'bg-apple-blue text-white shadow-lg shadow-apple-blue/30'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                <span>NFL (Activo)</span>
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              </button>
              <button
                type="button"
                onClick={() => setActiveSport('NBA')}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all ${
                  activeSport === 'NBA'
                    ? 'bg-apple-blue text-white shadow-lg'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                <span>NBA</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ─── Grid Principal: 2 Columnas (Candidatos Izq + Ticket Derecha) ───── */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.4fr_1fr]">
        {/* ══════════════════════════════════════════════════════════════════════
            COLUMNA IZQUIERDA: Candidatos reales de la NFL
           ══════════════════════════════════════════════════════════════════════ */}
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Target className="h-4 w-4 text-apple-blue" />
              <h3 className="text-sm font-semibold tracking-tight text-white">
                Partidos Reales NFL Disponibles para Micro-Parlay
              </h3>
            </div>
            <span className="text-[11px] font-mono text-slate-400">
              {availableCandidates.length} selecciones
            </span>
          </div>

          {/* Estado de carga */}
          {experimentalNFLQuery.isLoading && (
            <div className="rounded-xl border border-white/[0.08] bg-[#0c0e14] p-8 text-center text-xs text-slate-400 animate-pulse">
              Cargando inferencia de partidos NFL y momios reales de mercado...
            </div>
          )}

          {/* Candidatos Disponibles */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {availableCandidates.map((leg) => {
              const isSelected = selectedLegs.some((l) => l.id === leg.id)
              const probPct = Math.round(leg.prob_model * 100)

              return (
                <motion.button
                  key={leg.id}
                  whileHover={{ scale: 1.01 }}
                  whileTap={{ scale: 0.99 }}
                  onClick={() => handleToggleLeg(leg)}
                  type="button"
                  className={`text-left p-3.5 rounded-xl border transition-all relative overflow-hidden ${
                    isSelected
                      ? 'border-apple-blue bg-apple-blue/[0.12] shadow-lg shadow-apple-blue/10 ring-1 ring-apple-blue'
                      : 'border-white/[0.08] bg-[#0f121a]/80 hover:border-white/[0.16] hover:bg-[#131722]'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 text-[10px] text-slate-400 font-mono">
                        <Calendar className="h-3 w-3" />
                        <span>{leg.game_date}</span>
                        {leg.source === 'market_odds' && (
                          <span className="rounded bg-amber-500/20 text-amber-300 px-1 py-0.2">
                            HOY
                          </span>
                        )}
                      </div>
                      <h4 className="mt-1 font-semibold text-xs text-white truncate">
                        {leg.selection_label}
                      </h4>
                      <p className="text-[11px] text-slate-400">{leg.matchup}</p>
                    </div>

                    <div className="text-right shrink-0">
                      <span className="font-mono text-xs font-bold text-white">
                        {formatOdds(leg.odds_american)}
                      </span>
                      <p className="text-[10px] font-mono text-slate-400">
                        x{leg.odds_decimal.toFixed(2)}
                      </p>
                    </div>
                  </div>

                  {/* Medidor de confianza de la pata */}
                  <div className="mt-3 pt-2.5 border-t border-white/[0.06] flex items-center justify-between text-[11px]">
                    <div className="flex items-center gap-2 flex-1 mr-3">
                      <div className="h-1.5 flex-1 bg-white/[0.08] rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${
                            probPct >= 70
                              ? 'bg-emerald-400'
                              : probPct >= 60
                              ? 'bg-apple-blue'
                              : 'bg-amber-400'
                          }`}
                          style={{ width: `${Math.min(probPct, 100)}%` }}
                        />
                      </div>
                      <span className="font-mono text-[10px] text-slate-300 font-semibold">
                        {probPct}%
                      </span>
                    </div>

                    <span
                      className={`h-5 w-5 rounded-md flex items-center justify-center border ${
                        isSelected
                          ? 'border-apple-blue bg-apple-blue text-white'
                          : 'border-white/[0.1] bg-white/[0.03] text-transparent'
                      }`}
                    >
                      <Check className="h-3 w-3" />
                    </span>
                  </div>
                </motion.button>
              )
            })}
          </div>

          {availableCandidates.length === 0 && !experimentalNFLQuery.isLoading && (
            <div className="rounded-xl border border-white/[0.08] bg-[#0c0e14] p-8 text-center text-xs text-slate-400">
              <AlertTriangle className="h-6 w-6 text-amber-400 mx-auto mb-2" />
              <p className="font-medium text-slate-300">Sin partidos NFL disponibles</p>
              <p className="mt-1 text-[11px] text-slate-500">
                La inferencia experimental de la NFL se actualiza según el calendario oficial.
              </p>
            </div>
          )}

          {/* Formulario de entrada personalizada para pruebas */}
          <div className="rounded-xl border border-white/[0.08] bg-[#0c0e14] p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-300">
                Agregar Selección Personalizada (Simulación de Patas)
              </span>
              <span className="text-[10px] text-slate-500 font-mono">Modo Auditor</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-[1.5fr_1fr_1fr_auto] gap-2 items-end">
              <div>
                <label className="text-[10px] uppercase text-slate-400 font-semibold">
                  Equipo / Evento
                </label>
                <input
                  type="text"
                  placeholder="ej. Kansas City Chiefs ML"
                  value={customTeam}
                  onChange={(e) => setCustomTeam(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-white/[0.1] bg-black/40 px-3 py-1.5 text-xs text-white placeholder-slate-600 outline-none focus:border-apple-blue"
                />
              </div>

              <div>
                <label className="text-[10px] uppercase text-slate-400 font-semibold">
                  Prob. (%)
                </label>
                <input
                  type="number"
                  min={10}
                  max={95}
                  value={customProb}
                  onChange={(e) => setCustomProb(Number(e.target.value))}
                  className="mt-1 w-full rounded-lg border border-white/[0.1] bg-black/40 px-3 py-1.5 text-xs text-white font-mono outline-none focus:border-apple-blue"
                />
              </div>

              <div>
                <label className="text-[10px] uppercase text-slate-400 font-semibold">Momio</label>
                <input
                  type="number"
                  value={customOdds}
                  onChange={(e) => setCustomOdds(Number(e.target.value))}
                  className="mt-1 w-full rounded-lg border border-white/[0.1] bg-black/40 px-3 py-1.5 text-xs text-white font-mono outline-none focus:border-apple-blue"
                />
              </div>

              <button
                type="button"
                onClick={handleAddCustomLeg}
                disabled={!customTeam.trim() || selectedLegs.length >= 4}
                className="flex items-center justify-center gap-1 rounded-lg bg-white/[0.08] hover:bg-white/[0.15] px-3 py-1.5 text-xs font-medium text-white transition-colors disabled:opacity-40"
              >
                <Plus className="h-3.5 w-3.5" />
                <span>Agregar</span>
              </button>
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════════════════
            COLUMNA DERECHA: Slip del Micro-Parlay con Confidence Meter y EV
           ══════════════════════════════════════════════════════════════════════ */}
        <aside className="space-y-4">
          <div className="rounded-2xl border border-white/[0.08] bg-[#0c0e14] p-5 shadow-2xl space-y-5">
            {/* Cabecera del Slip */}
            <div className="flex items-center justify-between border-b border-white/[0.08] pb-3">
              <div>
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                  <span>Ticket del Micro-Parlay</span>
                  <span className="rounded-md border border-apple-blue/30 bg-apple-blue/10 px-2 py-0.5 font-mono text-[10px] text-apple-blue font-semibold">
                    {selectedLegs.length}/4 Legs
                  </span>
                </h3>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  Regla óptima: 2 a 3 patas con cuota ~2.0 (+100)
                </p>
              </div>

              {selectedLegs.length > 0 && (
                <button
                  type="button"
                  onClick={() => setSelectedLegs([])}
                  className="text-[11px] text-slate-400 hover:text-red-400 transition-colors flex items-center gap-1"
                >
                  <Trash2 className="h-3 w-3" />
                  <span>Limpiar</span>
                </button>
              )}
            </div>

            {/* Lista de Patas Seleccionadas */}
            <div className="space-y-2">
              <AnimatePresence>
                {selectedLegs.map((leg, idx) => (
                  <motion.div
                    key={leg.id}
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="flex items-center justify-between rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-xs"
                  >
                    <div className="min-w-0 pr-2">
                      <div className="flex items-center gap-2">
                        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-apple-blue/20 font-mono text-[10px] font-bold text-apple-blue">
                          {idx + 1}
                        </span>
                        <span className="font-semibold text-white truncate max-w-[150px]">
                          {leg.team_name}
                        </span>
                      </div>
                      <p className="text-[10px] text-slate-400 mt-0.5 ml-6">
                        P(modelo): {Math.round(leg.prob_model * 100)}%
                      </p>
                    </div>

                    <div className="flex items-center gap-3 shrink-0">
                      <span className="font-mono text-xs font-bold text-emerald-400">
                        {formatOdds(leg.odds_american)}
                      </span>
                      <button
                        type="button"
                        onClick={() => handleRemoveLeg(idx)}
                        className="text-slate-500 hover:text-red-400 transition-colors"
                      >
                        ×
                      </button>
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>

              {selectedLegs.length === 0 && (
                <div className="py-8 text-center text-xs text-slate-500 space-y-1">
                  <p>No has seleccionado patas todavía.</p>
                  <p className="text-[11px]">
                    Elige 2 o 3 enfrentamientos de la lista para calcular el EV.
                  </p>
                </div>
              )}
            </div>

            {/* ─── Medidor de Confianza Visual y Métricas Cuantitativas ───────── */}
            {localSlipMetrics && (
              <div className="space-y-4 pt-2 border-t border-white/[0.08]">
                {/* Confidence Meter Visual Bar */}
                <div className="rounded-xl border border-white/[0.08] bg-black/40 p-3.5 space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-400 font-medium">Medidor de Confianza Conjunta</span>
                    <span
                      className={`font-mono text-xs font-bold ${
                        localSlipMetrics.confidenceLevel === 'alta'
                          ? 'text-emerald-400'
                          : localSlipMetrics.confidenceLevel === 'moderada'
                          ? 'text-apple-blue'
                          : 'text-amber-400'
                      }`}
                    >
                      {Math.round(localSlipMetrics.jointProb * 100)}% Probabilidad
                    </span>
                  </div>

                  <div className="h-2 w-full rounded-full bg-white/[0.08] overflow-hidden">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${localSlipMetrics.jointProb * 100}%` }}
                      transition={{ duration: 0.4 }}
                      className={`h-full rounded-full ${
                        localSlipMetrics.confidenceLevel === 'alta'
                          ? 'bg-gradient-to-r from-emerald-500 to-emerald-400 shadow-lg shadow-emerald-500/50'
                          : localSlipMetrics.confidenceLevel === 'moderada'
                          ? 'bg-gradient-to-r from-blue-500 to-cyan-400 shadow-lg shadow-blue-500/50'
                          : 'bg-gradient-to-r from-amber-500 to-orange-400'
                      }`}
                    />
                  </div>

                  <div className="flex justify-between text-[10px] text-slate-500 font-mono">
                    <span>Especulativo (&lt;40%)</span>
                    <span>Moderado (40-55%)</span>
                    <span>Alta Ventaja (&gt;55%)</span>
                  </div>
                </div>

                {/* Grid de Métricas Cuantitativas */}
                <div className="grid grid-cols-2 gap-2 text-center">
                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                    <span className="text-[10px] uppercase text-slate-400 font-semibold block">
                      Cuota Combinada
                    </span>
                    <p className="mt-1 font-mono text-base font-bold text-amber-300">
                      {formatOdds(localSlipMetrics.combinedAmerican)}
                    </p>
                    <span className="text-[10px] font-mono text-slate-500">
                      x{localSlipMetrics.combinedDecimal.toFixed(2)} decimal
                    </span>
                  </div>

                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                    <span className="text-[10px] uppercase text-slate-400 font-semibold block">
                      EV Esperado / $100
                    </span>
                    <p
                      className={`mt-1 font-mono text-base font-bold ${
                        localSlipMetrics.evDollars100 > 0 ? 'text-emerald-400' : 'text-red-400'
                      }`}
                    >
                      {localSlipMetrics.evDollars100 > 0 ? '+' : ''}
                      ${localSlipMetrics.evDollars100.toFixed(2)}
                    </p>
                    <span className="text-[10px] font-mono text-slate-500">
                      {localSlipMetrics.evDollars100 > 0 ? 'Ventaja Matemática' : 'Margen Negativo'}
                    </span>
                  </div>

                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 col-span-2 flex items-center justify-between px-4">
                    <div className="text-left">
                      <span className="text-[10px] uppercase text-slate-400 font-semibold block">
                        Criterio Kelly Fraccional (1/4)
                      </span>
                      <p className="font-mono text-xs text-slate-300 mt-0.5">
                        Cap 5% de Bankroll (${bankroll})
                      </p>
                    </div>
                    <div className="text-right">
                      <span className="font-mono text-sm font-bold text-apple-blue">
                        ${localSlipMetrics.kellyStake.toFixed(2)}
                      </span>
                      <span className="text-[10px] font-mono text-slate-500 block">
                        {(localSlipMetrics.quarterKelly * 100).toFixed(1)}% bankroll
                      </span>
                    </div>
                  </div>
                </div>

                {/* Botón de Recalcular / Validar Backend */}
                <button
                  type="button"
                  onClick={() =>
                    evaluateMutation.mutate(
                      selectedLegs.map((l) => ({
                        team_name: l.team_name,
                        prob_model: l.prob_model,
                        odds_american: l.odds_american,
                      }))
                    )
                  }
                  disabled={evaluateMutation.isPending || selectedLegs.length < 2}
                  className="w-full flex items-center justify-center gap-1.5 rounded-xl border border-white/[0.1] bg-white/[0.06] hover:bg-white/[0.1] py-2 text-xs font-semibold text-white transition-all disabled:opacity-40"
                >
                  <RefreshCw
                    className={`h-3.5 w-3.5 ${evaluateMutation.isPending ? 'animate-spin' : ''}`}
                  />
                  <span>Revalidar con Backend SSOT</span>
                </button>

                {backendEvaluation && (
                  <div
                    className={`rounded-xl border p-3 text-xs ${
                      backendEvaluation.viable
                        ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                        : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
                    }`}
                  >
                    <p className="font-semibold">{backendEvaluation.razon}</p>
                    <p className="text-[11px] opacity-80 mt-1">
                      Evaluado por servicio SSOT de Griddio.
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Notificación de Gobernanza Operacional */}
          <div className="rounded-xl border border-white/[0.08] bg-[#0c0e14]/60 p-4 space-y-2 text-xs text-slate-400">
            <div className="flex items-center gap-1.5 text-slate-300 font-semibold">
              <Shield className="h-4 w-4 text-apple-blue" />
              <span>Gobernanza y Paridad de Modelo</span>
            </div>
            <p className="text-[11px] text-slate-500 leading-relaxed">
              Las sugerencias automáticas de producción permanecen desactivadas conforme a la
              auditoría técnica. Este módulo opera en modo de constructor interactivo con datos
              reales observados.
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}
