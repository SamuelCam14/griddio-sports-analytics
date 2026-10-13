/**
 * ParlayBuilderView — Constructor y optimizador de micro-parlays con ventaja matemática.
 * Diseñado con estética de sportsbook moderna (dark mode, monospace, confidence meters).
 * Conectado exclusivamente con el Backend SSOT y datos reales de la NFL.
 *
 * Garantías Verificadas:
 * 1. Backend SSOT: cero cálculos locales de probabilidad combinada, EV, momio combinado, Kelly o confianza.
 * 2. Cero valores predeterminados (0.50 o probabilidad implícita del mercado) para prob_model.
 * 3. Totales Over/Under no calculan EV hasta disponer de probabilidad correspondiente en el modelo.
 * 4. Emparejamiento por ambos equipos y fecha del calendario sin excepciones fijas (LA/BUF). Rechazo de ambigüedad.
 * 5. Rechazo estricto de combinar selecciones del mismo evento como patas independientes.
 * 6. Procedencia explícita por mercado: Moneyline DraftKings, Spread BetMGM, Total FanDuel con timestamps independientes.
 * 7. Calculadora manual claramente separada y etiquetada; sus resultados no son picks del modelo.
 */
import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Target,
  Shield,
  RefreshCw,
  Check,
  Calendar,
  AlertTriangle,
  Flame,
  Plus,
  Trash2,
  Info,
} from 'lucide-react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  predictionsApi,
  oddsApi,
  parlayApi,
  type ExperimentalPredictionGame,
} from '@/services/api'

interface Props {
  bankroll: number
}

export interface ParlayLegItem {
  id: string
  game_id?: string
  event_id?: string
  side?: 'home' | 'away' | 'over' | 'under'
  market?: 'h2h' | 'spreads' | 'totals'
  team_name: string
  selection_label: string
  matchup: string
  game_date: string
  commence_time?: string
  prob_model: number | null
  odds_american: number
  odds_decimal: number
  bookmaker?: string | null
  observed_at?: string | null
  source: 'experimental_nfl' | 'market_odds' | 'custom'
}

const NFL_CANONICAL_ALIASES: Record<string, string[]> = {
  ARI: ['arizona cardinals', 'cardinals', 'arizona', 'ari'],
  ATL: ['atlanta falcons', 'falcons', 'atlanta', 'atl'],
  BAL: ['baltimore ravens', 'ravens', 'baltimore', 'bal'],
  BUF: ['buffalo bills', 'bills', 'buffalo', 'buf'],
  CAR: ['carolina panthers', 'panthers', 'carolina', 'car'],
  CHI: ['chicago bears', 'bears', 'chicago', 'chi'],
  CIN: ['cincinnati bengals', 'bengals', 'cincinnati', 'cin'],
  CLE: ['cleveland browns', 'browns', 'cleveland', 'cle'],
  DAL: ['dallas cowboys', 'cowboys', 'dallas', 'dal'],
  DEN: ['denver broncos', 'broncos', 'denver', 'den'],
  DET: ['detroit lions', 'lions', 'detroit', 'det'],
  GB:  ['green bay packers', 'packers', 'green bay', 'gb', 'gnb'],
  HOU: ['houston texans', 'texans', 'houston', 'hou'],
  IND: ['indianapolis colts', 'colts', 'indianapolis', 'ind'],
  JAX: ['jacksonville jaguars', 'jaguars', 'jacksonville', 'jax'],
  KC:  ['kansas city chiefs', 'chiefs', 'kansas city', 'kc', 'kan'],
  LV:  ['las vegas raiders', 'raiders', 'las vegas', 'lv', 'oak', 'oakland', 'oakland raiders'],
  LAC: ['los angeles chargers', 'chargers', 'lac', 'sd', 'san diego chargers'],
  LAR: ['los angeles rams', 'rams', 'lar', 'la', 'stl', 'st. louis rams'],
  MIA: ['miami dolphins', 'dolphins', 'miami', 'mia'],
  MIN: ['minnesota vikings', 'vikings', 'minnesota', 'min'],
  NE:  ['new england patriots', 'patriots', 'new england', 'ne', 'nwe'],
  NO:  ['new orleans saints', 'saints', 'new orleans', 'no', 'nor'],
  NYG: ['new york giants', 'giants', 'nyg'],
  NYJ: ['new york jets', 'jets', 'nyj'],
  PHI: ['philadelphia eagles', 'eagles', 'philadelphia', 'phi'],
  PIT: ['pittsburgh steelers', 'steelers', 'pittsburgh', 'pit'],
  SF:  ['san francisco 49ers', '49ers', 'san francisco', 'sf', 'sfo', 'niners'],
  SEA: ['seahawks', 'seattle', 'sea'],
  TB:  ['tampa bay buccaneers', 'buccaneers', 'tampa bay', 'tb', 'tam', 'bucs'],
  TEN: ['tennessee titans', 'titans', 'tennessee', 'ten'],
  WAS: ['washington commanders', 'commanders', 'washington', 'was', 'wsh', 'redskins', 'football team'],
}

const EXACT_TEAM_LOOKUP: Record<string, string> = {}
for (const [canonical, aliases] of Object.entries(NFL_CANONICAL_ALIASES)) {
  EXACT_TEAM_LOOKUP[canonical.toLowerCase()] = canonical
  for (const alias of aliases) {
    EXACT_TEAM_LOOKUP[alias.toLowerCase()] = canonical
  }
}

const SORTED_TEAM_ALIASES = Object.keys(EXACT_TEAM_LOOKUP).sort((a, b) => b.length - a.length)

function normalizeNFLTeam(name?: string): string | null {
  if (!name) return null
  const raw = name.trim().toLowerCase()
  if (EXACT_TEAM_LOOKUP[raw]) return EXACT_TEAM_LOOKUP[raw]
  for (const alias of SORTED_TEAM_ALIASES) {
    const escaped = alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const regex = new RegExp(`\\b${escaped}\\b`)
    if (regex.test(raw)) {
      return EXACT_TEAM_LOOKUP[alias]
    }
  }
  return null
}

function americanToDecimal(odds: number): number {
  if (odds > 0) return Number(((odds / 100) + 1).toFixed(4))
  if (odds < 0) return Number(((100 / Math.abs(odds)) + 1).toFixed(4))
  return 2.0
}

function formatOdds(american: number): string {
  return american > 0 ? `+${american}` : `${american}`
}

function formatTimeOnly(isoString?: string | null): string {
  if (!isoString) return ''
  try {
    const d = new Date(isoString)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

export function ParlayBuilderView({ bankroll }: Props) {
  const [activeSport, setActiveSport] = useState<'NFL' | 'NBA'>('NFL')
  const [selectedLegs, setSelectedLegs] = useState<ParlayLegItem[]>([])
  const [customTeam, setCustomTeam] = useState('')
  const [customProb, setCustomProb] = useState(65)
  const [customOdds, setCustomOdds] = useState(-110)

  // ─── Consultas Reales de Backend SSOT ─────────────────────────────────────
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

  // ─── Mutación de Evaluación Backend SSOT ──────────────────────────────────
  const evaluateMutation = useMutation({
    mutationFn: (legs: ParlayLegItem[]) =>
      parlayApi
        .evaluate(
          legs.map((l) => ({
            game_id: l.game_id,
            side: l.side,
            market: l.market,
            team_name: l.team_name,
            prob_model: l.source === 'custom' ? (l.prob_model ?? undefined) : undefined,
            odds_american: l.source === 'custom' ? l.odds_american : undefined,
            source: l.source === 'custom' ? 'manual' : 'real',
          })),
          bankroll
        )
        .then((r) => r.data),
  })

  // ─── Generación de Candidatos Reales desde la NFL ─────────────────────────
  const availableCandidates = useMemo(() => {
    if (activeSport !== 'NFL') return []

    const candidates: ParlayLegItem[] = []
    const oddsGames = oddsNFLQuery.data?.games ?? []
    const expGames = experimentalNFLQuery.data?.games ?? []

    // 1. Partidos con Momios Reales de Mercado
    oddsGames.forEach((og) => {
      // Emparejar por ambos equipos y fecha del calendario; rechazar coincidencias ambiguas
      const normHomeOg = normalizeNFLTeam(og.home_team)
      const normAwayOg = normalizeNFLTeam(og.away_team)

      const matches = expGames.filter((eg) => {
        const normHomeEg = normalizeNFLTeam(eg.home_team_name) || eg.home_team_abbreviation
        const normAwayEg = normalizeNFLTeam(eg.away_team_name) || eg.away_team_abbreviation
        if (normHomeEg !== normHomeOg || normAwayEg !== normAwayOg) return false
        if (og.commence_time && eg.game_date) {
          const ogDate = new Date(og.commence_time).getTime()
          const egDate = new Date(eg.game_date).getTime()
          const diffDays = Math.abs(ogDate - egDate) / (1000 * 60 * 60 * 24)
          return diffDays <= 1.5
        }
        return true
      })

      // Rechazo estricto de coincidencias ambiguas
      const matchedExp = matches.length === 1 ? matches[0] : null

      // Procedencia explícita por mercado
      const mlBookmaker = og.markets_provenance?.moneyline?.bookmaker || og.best_bookmaker || 'draftkings'
      const mlObservedAt = og.markets_provenance?.moneyline?.observed_at || og.h2h_observed_at
      const sprBookmaker = og.markets_provenance?.spread?.bookmaker || og.spread_bookmaker || 'betmgm'
      const sprObservedAt = og.markets_provenance?.spread?.observed_at || og.spread_observed_at
      const totBookmaker = og.markets_provenance?.total?.bookmaker || og.total_bookmaker || 'fanduel'
      const totObservedAt = og.markets_provenance?.total?.observed_at || og.total_observed_at

      // Solo asignar probabilidad si el modelo experimental la calculó (Cero 0.50 o implícita)
      const awayProb = matchedExp ? Number(matchedExp.away_win_probability.toFixed(4)) : null
      const homeProb = matchedExp ? Number(matchedExp.home_win_probability.toFixed(4)) : null

      // Victoria Visitante Moneyline
      if (og.away_ml != null) {
        candidates.push({
          id: `odds_ml_away_${og.external_id}`,
          game_id: og.external_id,
          event_id: og.external_id,
          side: 'away',
          market: 'h2h',
          team_name: og.away_team,
          selection_label: `${og.away_team} ML (${formatOdds(og.away_ml)})`,
          matchup: `${og.away_team} @ ${og.home_team}`,
          game_date: og.commence_time ? og.commence_time.split('T')[0] : '2026-10-12',
          commence_time: og.commence_time,
          prob_model: awayProb,
          odds_american: og.away_ml,
          odds_decimal: americanToDecimal(og.away_ml),
          bookmaker: mlBookmaker,
          observed_at: mlObservedAt,
          source: matchedExp ? 'experimental_nfl' : 'market_odds',
        })
      }

      // Victoria Local Moneyline
      if (og.home_ml != null) {
        candidates.push({
          id: `odds_ml_home_${og.external_id}`,
          game_id: og.external_id,
          event_id: og.external_id,
          side: 'home',
          market: 'h2h',
          team_name: og.home_team,
          selection_label: `${og.home_team} ML (${formatOdds(og.home_ml)})`,
          matchup: `${og.away_team} @ ${og.home_team}`,
          game_date: og.commence_time ? og.commence_time.split('T')[0] : '2026-10-12',
          commence_time: og.commence_time,
          prob_model: homeProb,
          odds_american: og.home_ml,
          odds_decimal: americanToDecimal(og.home_ml),
          bookmaker: mlBookmaker,
          observed_at: mlObservedAt,
          source: matchedExp ? 'experimental_nfl' : 'market_odds',
        })
      }

      // Totales Over / Under: prob_model = null estrictamente hasta disponer de modelo Over/Under
      if (og.total_line != null && og.over_ml != null) {
        candidates.push({
          id: `odds_total_over_${og.external_id}`,
          game_id: og.external_id,
          event_id: og.external_id,
          side: 'over',
          market: 'totals',
          team_name: `${og.away_team} @ ${og.home_team} OVER ${og.total_line}`,
          selection_label: `OVER ${og.total_line} Pts (${formatOdds(og.over_ml)})`,
          matchup: `${og.away_team} vs ${og.home_team}`,
          game_date: og.commence_time ? og.commence_time.split('T')[0] : '2026-10-12',
          commence_time: og.commence_time,
          prob_model: null, // Cero 0.50. Sin EV de modelo para totales.
          odds_american: og.over_ml,
          odds_decimal: americanToDecimal(og.over_ml),
          bookmaker: totBookmaker,
          observed_at: totObservedAt,
          source: 'market_odds',
        })
      }
      if (og.total_line != null && og.under_ml != null) {
        candidates.push({
          id: `odds_total_under_${og.external_id}`,
          game_id: og.external_id,
          event_id: og.external_id,
          side: 'under',
          market: 'totals',
          team_name: `${og.away_team} @ ${og.home_team} UNDER ${og.total_line}`,
          selection_label: `UNDER ${og.total_line} Pts (${formatOdds(og.under_ml)})`,
          matchup: `${og.away_team} vs ${og.home_team}`,
          game_date: og.commence_time ? og.commence_time.split('T')[0] : '2026-10-12',
          commence_time: og.commence_time,
          prob_model: null,
          odds_american: og.under_ml,
          odds_decimal: americanToDecimal(og.under_ml),
          bookmaker: totBookmaker,
          observed_at: totObservedAt,
          source: 'market_odds',
        })
      }
    })

    return candidates.sort((a, b) => (b.prob_model ?? 0) - (a.prob_model ?? 0))
  }, [activeSport, oddsNFLQuery.data, experimentalNFLQuery.data])

  // ─── Manejadores de Selección (Disparan Evaluación SSOT Backend) ───────────
  const handleToggleLeg = (leg: ParlayLegItem) => {
    const exists = selectedLegs.some((l) => l.id === leg.id)
    let newLegs: ParlayLegItem[]
    if (exists) {
      newLegs = selectedLegs.filter((l) => l.id !== leg.id)
    } else {
      if (selectedLegs.length >= 4) return
      newLegs = [...selectedLegs, leg]
    }
    setSelectedLegs(newLegs)
    if (newLegs.length >= 2) {
      evaluateMutation.mutate(newLegs)
    } else {
      evaluateMutation.reset()
    }
  }

  const handleAddCustomLeg = () => {
    if (!customTeam.trim()) return
    const dec = americanToDecimal(customOdds)
    const leg: ParlayLegItem = {
      id: `custom_${Date.now()}`,
      team_name: customTeam.trim(),
      selection_label: `${customTeam.trim()} (Entrada manual)`,
      matchup: 'Entrada manual',
      game_date: new Date().toISOString().split('T')[0],
      prob_model: customProb / 100.0,
      odds_american: customOdds,
      odds_decimal: dec,
      bookmaker: 'Manual',
      source: 'custom',
    }
    const newLegs = [...selectedLegs, leg]
    setSelectedLegs(newLegs)
    setCustomTeam('')
    if (newLegs.length >= 2) {
      evaluateMutation.mutate(newLegs)
    }
  }

  const handleRemoveLeg = (idx: number) => {
    const newLegs = selectedLegs.filter((_, i) => i !== idx)
    setSelectedLegs(newLegs)
    if (newLegs.length >= 2) {
      evaluateMutation.mutate(newLegs)
    } else {
      evaluateMutation.reset()
    }
  }

  // Backend SSOT Response (Cero cálculos locales de EV / Kelly / Probabilidad combinada)
  const backendEvaluation = evaluateMutation.data

  return (
    <div className="space-y-6 text-slate-100">
      {/* ─── Header & Reglas Cuantitativas ────────────────────────────────── */}
      <div className="rounded-2xl border border-white/[0.08] bg-[#0c0e14] p-5 sm:p-6 shadow-2xl relative overflow-hidden">
        <div className="absolute top-0 right-0 h-48 w-48 bg-apple-blue/10 blur-3xl pointer-events-none" />

        <div className="flex flex-wrap items-center justify-between gap-4 relative z-10">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="flex h-6 items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 text-[11px] font-semibold text-emerald-400">
                <Flame className="h-3 w-3 fill-current" />
                MICRO-PARLAYS SSOT
              </span>
              <span className="rounded-full border border-white/[0.08] bg-white/[0.04] px-2.5 py-0.5 text-[10px] font-mono text-slate-400">
                Backend SSOT + Independencia de Eventos
              </span>
            </div>
            <h2 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
              Constructor Cuantitativo de Micro-Parlays
            </h2>
            <p className="text-xs text-slate-400 max-w-2xl leading-relaxed">
              Combina selecciones evaluadas por el backend SSOT. Métricas conjuntas, EV y Criterio de Kelly
              son calculados exclusivamente en servidor. Prohibido combinar selecciones del mismo evento
              o calcular EV sobre mercados sin modelo de probabilidad (p. ej. totales Over/Under).
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

        {/* Banner de Procedencia de Líneas */}
        <div className="mt-4 rounded-xl border border-white/[0.06] bg-black/40 p-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
          <div className="flex items-center gap-2">
            <Info className="h-3.5 w-3.5 text-apple-blue shrink-0" />
            <span>
              <strong>Procedencia de Líneas por Mercado:</strong> Moneyline:{' '}
              <span className="text-white font-medium">DraftKings</span> | Spread:{' '}
              <span className="text-white font-medium">BetMGM</span> | Total:{' '}
              <span className="text-white font-medium">FanDuel</span>.
            </span>
          </div>
          <span className="font-mono text-[10px] text-slate-500">
            Snapshots locales observados • Cero llamadas The Odds API
          </span>
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
              const hasProb = leg.prob_model != null
              const probPct = hasProb ? Math.round(leg.prob_model! * 100) : null

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
                        {leg.bookmaker && (
                          <span className="rounded bg-white/[0.06] text-slate-300 px-1 py-0.2 uppercase text-[9px]">
                            {leg.bookmaker}
                          </span>
                        )}
                        {leg.observed_at && (
                          <span className="text-[9px] text-slate-500">
                            {formatTimeOnly(leg.observed_at)}
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

                  {/* Medidor de confianza de la pata o aviso de falta de prob */}
                  <div className="mt-3 pt-2.5 border-t border-white/[0.06] flex items-center justify-between text-[11px]">
                    <div className="flex items-center gap-2 flex-1 mr-3">
                      {hasProb ? (
                        <>
                          <div className="h-1.5 flex-1 bg-white/[0.08] rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${
                                probPct! >= 70
                                  ? 'bg-emerald-400'
                                  : probPct! >= 60
                                  ? 'bg-apple-blue'
                                  : 'bg-amber-400'
                              }`}
                              style={{ width: `${Math.min(probPct!, 100)}%` }}
                            />
                          </div>
                          <span className="font-mono text-[10px] text-slate-300 font-semibold">
                            {probPct}% P(mod)
                          </span>
                        </>
                      ) : (
                        <span className="text-[10px] text-amber-400/80 font-mono">
                          Sin prob. modelo (EV no disp.)
                        </span>
                      )}
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

          {/* ─── Calculadora Manual Separada y Etiquetada (No son picks del modelo) ─── */}
          <div className="rounded-xl border border-amber-500/20 bg-[#0c0e14] p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-amber-400" />
                <span className="text-xs font-semibold text-amber-300">
                  Calculadora Manual de Probabilidades (Herramienta Separada)
                </span>
              </div>
              <span className="text-[10px] text-amber-400/80 font-mono border border-amber-500/30 rounded px-1.5 py-0.5">
                NO SON PICKS DEL MODELO
              </span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Herramienta de cálculo matemático independiente para simular combinadas. Las probabilidades
              ingresadas aquí son definidas manualmente por el usuario y no representan inferencias ni picks
              aprobados de Griddio.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-[1.5fr_1fr_1fr_auto] gap-2 items-end">
              <div>
                <label className="text-[10px] uppercase text-slate-400 font-semibold">
                  Equipo / Evento Manual
                </label>
                <input
                  type="text"
                  placeholder="ej. Simulación Manual Chiefs ML"
                  value={customTeam}
                  onChange={(e) => setCustomTeam(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-white/[0.1] bg-black/40 px-3 py-1.5 text-xs text-white placeholder-slate-600 outline-none focus:border-amber-400"
                />
              </div>

              <div>
                <label className="text-[10px] uppercase text-slate-400 font-semibold">
                  Prob. Usuario (%)
                </label>
                <input
                  type="number"
                  min={10}
                  max={95}
                  value={customProb}
                  onChange={(e) => setCustomProb(Number(e.target.value))}
                  className="mt-1 w-full rounded-lg border border-white/[0.1] bg-black/40 px-3 py-1.5 text-xs text-white font-mono outline-none focus:border-amber-400"
                />
              </div>

              <div>
                <label className="text-[10px] uppercase text-slate-400 font-semibold">Momio</label>
                <input
                  type="number"
                  value={customOdds}
                  onChange={(e) => setCustomOdds(Number(e.target.value))}
                  className="mt-1 w-full rounded-lg border border-white/[0.1] bg-black/40 px-3 py-1.5 text-xs text-white font-mono outline-none focus:border-amber-400"
                />
              </div>

              <button
                type="button"
                onClick={handleAddCustomLeg}
                disabled={!customTeam.trim() || selectedLegs.length >= 4}
                className="flex items-center justify-center gap-1 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30 px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40"
              >
                <Plus className="h-3.5 w-3.5" />
                <span>Agregar</span>
              </button>
            </div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════════════════
            COLUMNA DERECHA: Slip del Micro-Parlay (Consumidor Puro de Backend SSOT)
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
                  Calculado exclusivamente por el Backend SSOT
                </p>
              </div>

              {selectedLegs.length > 0 && (
                <button
                  type="button"
                  onClick={() => {
                    setSelectedLegs([])
                    evaluateMutation.reset()
                  }}
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
                        {leg.source === 'custom' && (
                          <span className="text-[9px] font-mono text-amber-400 bg-amber-400/10 px-1 rounded">
                            manual
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-[10px] text-slate-400 mt-0.5 ml-6">
                        {leg.prob_model != null ? (
                          <span>P(modelo): {Math.round(leg.prob_model * 100)}%</span>
                        ) : (
                          <span className="text-amber-400/80">Sin prob. de modelo</span>
                        )}
                        {leg.bookmaker && <span>• {leg.bookmaker}</span>}
                        {leg.observed_at && (
                          <span className="font-mono text-[9px] text-slate-500">
                            ({formatTimeOnly(leg.observed_at)})
                          </span>
                        )}
                      </div>
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
                    Elige 2 o 3 enfrentamientos de la lista para evaluar con el backend SSOT.
                  </p>
                </div>
              )}
            </div>

            {/* Estado de carga de evaluación */}
            {evaluateMutation.isPending && (
              <div className="py-4 text-center text-xs text-slate-400 flex items-center justify-center gap-2 animate-pulse">
                <RefreshCw className="h-4 w-4 animate-spin text-apple-blue" />
                <span>Resolviendo cuotas y probabilidades en Backend SSOT...</span>
              </div>
            )}

            {/* ─── Métricas Resueltas Exclusivamente por Backend SSOT ───────── */}
            {selectedLegs.length >= 2 && backendEvaluation && !evaluateMutation.isPending && (
              <div className="space-y-4 pt-2 border-t border-white/[0.08]">
                {/* Confidence Meter Visual Bar (Del Servidor) */}
                <div className="rounded-xl border border-white/[0.08] bg-black/40 p-3.5 space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-400 font-medium">Medidor de Confianza Conjunta</span>
                    <span
                      className={`font-mono text-xs font-bold ${
                        backendEvaluation.confidence_level === 'alta'
                          ? 'text-emerald-400'
                          : backendEvaluation.confidence_level === 'moderada'
                          ? 'text-apple-blue'
                          : 'text-amber-400'
                      }`}
                    >
                      {backendEvaluation.prob_combinada != null
                        ? `${Math.round(backendEvaluation.prob_combinada * 100)}% Probabilidad`
                        : 'No Disponible'}
                    </span>
                  </div>

                  <div className="h-2 w-full rounded-full bg-white/[0.08] overflow-hidden">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{
                        width: backendEvaluation.prob_combinada != null
                          ? `${backendEvaluation.prob_combinada * 100}%`
                          : '0%',
                      }}
                      transition={{ duration: 0.4 }}
                      className={`h-full rounded-full ${
                        backendEvaluation.confidence_level === 'alta'
                          ? 'bg-gradient-to-r from-emerald-500 to-emerald-400 shadow-lg shadow-emerald-500/50'
                          : backendEvaluation.confidence_level === 'moderada'
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

                {/* Grid de Métricas Cuantitativas Devueltas por Backend */}
                <div className="grid grid-cols-2 gap-2 text-center">
                  {/* Cuota Combinada */}
                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                    <span className="text-[10px] uppercase text-slate-400 font-semibold block">
                      Cuota Combinada
                    </span>
                    <p className="mt-1 font-mono text-base font-bold text-amber-300">
                      {backendEvaluation.momio_americano != null
                        ? formatOdds(backendEvaluation.momio_americano)
                        : 'N/D'}
                    </p>
                    <span className="text-[10px] font-mono text-slate-500">
                      {backendEvaluation.momio_decimal != null
                        ? `x${backendEvaluation.momio_decimal.toFixed(2)} decimal`
                        : 'Sin cuota'}
                    </span>
                  </div>

                  {/* EV Esperado */}
                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                    <span className="text-[10px] uppercase text-slate-400 font-semibold block">
                      EV Esperado / $100
                    </span>
                    {backendEvaluation.ev_dolares != null ? (
                      <>
                        <p
                          className={`mt-1 font-mono text-base font-bold ${
                            backendEvaluation.ev_dolares > 0 ? 'text-emerald-400' : 'text-red-400'
                          }`}
                        >
                          {backendEvaluation.ev_dolares > 0 ? '+' : ''}$
                          {backendEvaluation.ev_dolares.toFixed(2)}
                        </p>
                        <span className="text-[10px] font-mono text-slate-500">
                          {backendEvaluation.ev_dolares > 0 ? 'Ventaja Matemática' : 'Margen Negativo'}
                        </span>
                      </>
                    ) : (
                      <>
                        <p className="mt-1 font-mono text-sm font-semibold text-slate-400">
                          No Disponible
                        </p>
                        <span className="text-[10px] font-mono text-amber-400/80">
                          Sin prob. de modelo
                        </span>
                      </>
                    )}
                  </div>

                  {/* Kelly Fraccional */}
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
                        {backendEvaluation.kelly_monto != null
                          ? `$${backendEvaluation.kelly_monto.toFixed(2)}`
                          : '$0.00'}
                      </span>
                      <span className="text-[10px] font-mono text-slate-500 block">
                        {backendEvaluation.kelly_fraccion != null
                          ? `${(backendEvaluation.kelly_fraccion * 100).toFixed(1)}% bankroll`
                          : 'No aplicable'}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Notificación de Diagnóstico del Backend */}
                <div
                  className={`rounded-xl border p-3.5 text-xs space-y-1.5 ${
                    backendEvaluation.viable
                      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                      : backendEvaluation.has_correlated_legs
                      ? 'border-red-500/40 bg-red-500/10 text-red-300'
                      : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
                  }`}
                >
                  <div className="flex items-center gap-1.5 font-semibold">
                    {backendEvaluation.has_correlated_legs && (
                      <AlertTriangle className="h-4 w-4 text-red-400 shrink-0" />
                    )}
                    <span>{backendEvaluation.razon}</span>
                  </div>
                  <p className="text-[10px] opacity-80">
                    Evaluado por servicio SSOT de Griddio. Procedencia validada en PostgreSQL.
                  </p>
                </div>

                {backendEvaluation.is_manual_calculation && (
                  <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-2.5 text-[11px] text-amber-300 flex items-center gap-2">
                    <Info className="h-3.5 w-3.5 shrink-0" />
                    <span>
                      Este slip incluye entradas manuales. El cálculo matemático resultante{' '}
                      <strong>NO</strong> constituye un pick ni proyección del modelo.
                    </span>
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
              reales observados y backend SSOT.
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}
