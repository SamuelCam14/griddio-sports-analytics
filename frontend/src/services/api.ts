/**
 * API Service — Comunicación con el backend FastAPI.
 * Usa axios con interceptores para manejo de errores global.
 */
import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL ?? '/api/v1'

export const api = axios.create({
  baseURL: BASE_URL,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

// ─── Tipos principales ───────────────────────────────────────────────────────

export interface TeamStats {
  wpct: number | null
  net_rtg: number | null
  off_rtg: number | null
  def_rtg: number | null
  pace: number | null
  l10: number | null
  days_rest: number | null
  b2b: boolean
}

export interface Game {
  id: number
  external_id: string
  sport: string
  season: string
  game_date: string
  status: 'scheduled' | 'live' | 'final'
  home_team_id: number
  away_team_id: number
  home_score: number | null
  away_score: number | null
  home_win: boolean | null
  team_stats: { home: TeamStats; away: TeamStats }
}

export interface MarketProvenanceInfo {
  bookmaker: string | null
  observed_at: string | null
  captured_at: string | null
}

export interface OddsData {
  external_id: string
  home_team: string
  away_team: string
  commence_time: string
  best_bookmaker: string | null
  spread_bookmaker?: string | null
  total_bookmaker?: string | null
  h2h_bookmaker?: string | null
  h2h_observed_at?: string | null
  spread_observed_at?: string | null
  total_observed_at?: string | null
  home_ml: number | null
  away_ml: number | null
  home_spread: number | null
  home_spread_ml?: number | null
  away_spread?: number | null
  away_spread_ml?: number | null
  total_line: number | null
  over_ml?: number | null
  under_ml?: number | null
  home_implied_prob: number | null
  away_implied_prob: number | null
  captured_at?: string | null
  markets_provenance?: {
    moneyline?: MarketProvenanceInfo
    spread?: MarketProvenanceInfo
    total?: MarketProvenanceInfo
  }
}

export interface EVResult {
  ev_dolares: number
  ev_porcentaje: string
  prob_modelo: number
  prob_implicita: number
  ventaja: string
  kelly_fraccion: number
  kelly_monto: number
  tiene_ventaja: boolean
  tier: 'PREMIUM' | 'VALUE' | 'SKIP'
  advertencias: string[]
  recomendacion: string
}

export interface ParlayResult {
  viable: boolean
  razon: string
  n_legs: number
  eventos: Array<{ equipo: string; prob: number; momio: number }>
  prob_combinada: number
  momio_decimal: number
  momio_americano: number
  ev_dolares: number
  kelly_fraccion: number
  recomendacion: string
}

export interface PlayerProp {
  player_name: string
  team?: string
  position?: string
  prop_type: string
  market_line: number
  over_odds_american: number
  under_odds_american: number
  over_implied_probability: number
  under_implied_probability: number
  bookmaker: string
  data_source: string
  reasoning: string
  // Campos EV y Kelly por prop (añadidos en sports_analytics_service.py)
  ev_dollars?: number
  kelly_fraction?: number
  kelly_stake?: number
  has_kelly_edge?: boolean
  win_probability?: number
  odds_american?: number
  pick?: string
  projected_value?: number
  edge_pct?: number
  tier?: string
}

export interface MatchupSpread {
  market_line: number
  odds_american: number
  projected_margin: number
  projected_home_score: number
  projected_away_score: number
  pick: string
  cover_probability: number
  edge_pct: number
  tier: string
  // Campos EV y Kelly
  ev_dollars: number
  kelly_stake: number
  has_kelly_edge: boolean
}

export interface MatchupTotals {
  market_line: number
  odds_american: number
  projected_total: number
  pick: 'OVER' | 'UNDER'
  recommendation: string
  probability: number
  edge_pct: number
  tier: string
  // Campos EV y Kelly
  ev_dollars: number
  kelly_stake: number
  has_kelly_edge: boolean
}

/** La mejor apuesta del enfrentamiento según EV+Kelly — una sola recomendación. */
export interface BestBet {
  market: 'moneyline' | 'spread' | 'totals' | 'props'
  market_label: string
  selection: string
  odds_american: number
  win_probability: number
  ev_dollars: number
  kelly_stake: number
  edge_pct: number
  tier: string
  reason: string
}

export interface TeamDetail {
  id: number
  name: string
  abbreviation: string
  city: string
  conference?: string
  division?: string
}

export interface Pick {
  game_id: number
  sport: string
  game_date: string
  is_today?: boolean
  matchup_label?: string
  home_team?: TeamDetail
  away_team?: TeamDetail
  home_team_id: number
  away_team_id: number
  tier: 'PREMIUM' | 'VALUE' | 'SKIP' | 'NEUTRAL' | 'EXPERIMENTAL'
  tier_emoji: string
  model_agreement: number
  pick_side: 'home' | 'away' | null
  pick_team_name?: string
  prob_modelo: number | null
  momio: number | null
  ev_dolares: number | null
  kelly_fraccion: number | null
  kelly_monto?: number
  home_win_prob: number
  away_win_prob: number
  tiene_ventaja: boolean
  bookmaker: string | null
  best_bet?: BestBet | null
  spread?: MatchupSpread
  totals?: MatchupTotals
  player_props?: PlayerProp[]
  key_factors?: string[]
}

export interface MonteCarloResult {
  bankroll_inicial: number
  n_simulaciones: number
  mediana_final: number
  percentil_10: number
  percentil_90: number
  prob_ruina: number
  roi_esperado: number
  interpretacion: {
    prob_ruina: string
    roi_esperado: string
    rango_probable: string
    evaluacion: string
  }
}

export interface ModelInfo {
  status: string
  artifact_loaded: boolean
  predictions_enabled: boolean
  reason: string
  models: Record<string, {
    status: string
    model_version?: string
    sample_count?: number
    date_from?: string
    date_to?: string
    production_enabled: boolean
    metrics?: {
      mean_accuracy: number
      mean_auc: number
      mean_brier: number
      mean_log_loss: number
      beats_majority_brier_in_all_folds: boolean
    }
  }>
}

export interface SimulationResult {
  matchup: string
  probabilidades: {
    home_win: number
    away_win: number
  }
  confluencia: {
    tier: string
    acuerdo: string
    desglose_modelos: Record<string, number>
  }
  factores_clave: Record<string, any>
  analisis_ev: {
    home: {
      momio: number
      ev_dolares: number
      ventaja: string
      kelly_monto: number
      tiene_ventaja: boolean
    }
    away: {
      momio: number
      ev_dolares: number
      ventaja: string
      kelly_monto: number
      tiene_ventaja: boolean
    }
    mejor_apuesta: string
  } | null
}

// ─── SofaScore Context Types ────────────────────────────────────────────────

export interface MatchupH2HClash {
  date: string
  home_team: string
  away_team: string
  home_score: number
  away_score: number
  winner: string
}

export interface MatchupH2H {
  home_wins: number
  away_wins: number
  draws: number
  total_games: number
  recent_clashes: MatchupH2HClash[]
}

export interface RecentGameForm {
  date: string
  opponent: string
  is_home: boolean
  team_score: number
  opponent_score: number
  result: 'W' | 'L' | 'T'
}

export interface TeamRecentForm {
  streak: Array<'W' | 'L' | 'T'>
  games: RecentGameForm[]
}

export interface OfficialInjury {
  player_name: string
  position: string
  status: 'OUT' | 'QUESTIONABLE' | 'DOUBTFUL' | string
  injury: string
  practice_status?: string
}

export interface TeamProductionImpact {
  ppr_lost_pct: number
  off_snaps_lost_pct: number
  def_snaps_lost_pct: number
}

export interface MatchupTeamInjuries {
  home: OfficialInjury[]
  away: OfficialInjury[]
  home_impact: TeamProductionImpact
  away_impact: TeamProductionImpact
}

export interface ComparisonStatTeam {
  name: string
  abbr: string
  ppg: number
  opp_ppg: number
  avg_margin: number
  days_rest: number
}

export interface MatchupComparisonStats {
  home: ComparisonStatTeam
  away: ComparisonStatTeam
}

export interface MatchupContext {
  game_id: number
  sport: string
  matchup_label: string
  home_team: { id: number; name: string; abbr: string }
  away_team: { id: number; name: string; abbr: string }
  h2h: MatchupH2H
  recent_form: {
    home: TeamRecentForm
    away: TeamRecentForm
  }
  injuries: MatchupTeamInjuries
  comparison_stats: MatchupComparisonStats
}

// ─── Games ───────────────────────────────────────────────────────────────────

export const gamesApi = {
  getToday: () => api.get<{ date: string; total: number; games: Game[] }>('/games/today'),
  getByDate: (date: string) => api.get<{ date: string; total: number; games: Game[] }>(`/games/date/${date}`),
  getUpcoming: (days = 7, sport = 'NBA') => api.get<{
    sport: string
    range_days: number
    total: number
    games: Game[]
  }>('/games/upcoming', { params: { days, sport } }),
  getContext: (gameId: number) => api.get<MatchupContext>(`/games/${gameId}/context`),
}

// ─── Odds ────────────────────────────────────────────────────────────────────

export const oddsApi = {
  getNBA: () => api.get<{ sport: string; total_games: number; games: OddsData[] }>('/odds/nba'),
  getNFL: () => api.get<{ sport: string; total_games: number; games: OddsData[] }>('/odds/nfl'),
  getRemaining: () => api.get<{ remaining: string; used: string }>('/odds/remaining'),
}

// ─── Predictions ─────────────────────────────────────────────────────────────

export type PredictionTier = 'PREMIUM' | 'VALUE' | 'EXPERIMENTAL' | 'all'

export interface ExperimentalPredictionGame {
  game_id: string
  game_date: string
  home_team_id: string
  away_team_id: string
  home_team_name: string
  away_team_name: string
  home_team_abbreviation: string
  away_team_abbreviation: string
  home_win_probability: number
  away_win_probability: number
  feature_cutoff_exclusive?: string
  feature_max_observed_game_date?: string
  schedule_source_ref?: string
}

export interface ExperimentalPredictionsResponse {
  status: string
  sport: string
  predictions_enabled: boolean
  production_eligible: boolean
  lookahead_days: number
  as_of: string
  model_version: string
  dataset_sha256: string
  artifact_sha256: string
  schedule_source_refs: string[]
  game_count: number
  games: ExperimentalPredictionGame[]
}

export const predictionsApi = {
  getToday: (sport = 'NBA', tier: PredictionTier | string = 'all', period = 'auto') =>
    api.get<{
      sport: string
      period?: string
      total_picks: number
      premium: number
      value: number
      skip: number
      picks: Pick[]
      predictions_enabled?: boolean
      reason?: string
    }>('/predictions/today', { params: { sport, tier, period } }),
  getExperimental: (sport = 'nfl', lookahead_days = 14) =>
    api.get<ExperimentalPredictionsResponse>(`/predictions/experimental/${sport.toLowerCase()}`, {
      params: { lookahead_days },
    }),
  getTop: (sport = 'NBA', limit = 5) =>
    api.get<{ sport: string; top_picks: Pick[] }>('/predictions/top', { params: { sport, limit } }),
  getForGame: (gameId: number) => api.get(`/predictions/game/${gameId}`),
  getModelInfo: () => api.get<ModelInfo>('/predictions/model-info'),
  simulate: (payload: {
    home_team_name?: string
    visitor_team_name?: string
    home_odds?: number
    away_odds?: number
    bankroll?: number
    home_stats?: Record<string, any>
    visitor_stats?: Record<string, any>
    context?: Record<string, any>
  }) => api.post<SimulationResult>('/predictions/simulate', payload),
}

// ─── EV Calculator ───────────────────────────────────────────────────────────

export const evApi = {
  calculate: (prob: number, odds: number, bankroll = 1000) =>
    api.post<EVResult>('/ev/calculate', { prob_model: prob, odds_american: odds, bankroll }),

  quick: (prob: number, odds: number, bankroll = 1000) =>
    api.get(`/ev/quick/${prob}/${odds}`, { params: { bankroll } }),

  buildParlay: (legs: Array<{ team_name: string; prob_model: number; odds_american: number }>, maxLegs = 3) =>
    api.post<ParlayResult>('/ev/parlay', { legs, max_legs: maxLegs }),

  monteCarlo: (prob: number, oddsDecimal: number, options?: {
    bankroll_initial?: number
    n_bets?: number
    kelly_fraction?: number
    n_simulations?: number
  }) =>
    api.post<MonteCarloResult>('/ev/montecarlo', { prob, odds_decimal: oddsDecimal, ...options }),
}

export interface RecommendedParlay {
  type?: string
  viable: boolean
  razon: string
  n_legs: number
  prob_combinada?: number | null
  momio_decimal?: number | null
  momio_americano?: number | null
  ev_dolares?: number | null
  kelly_fraccion?: number | null
  kelly_monto?: number | null
  confidence_level?: string | null
  has_sufficient_model_data?: boolean
  has_correlated_legs?: boolean
  is_manual_calculation?: boolean
  legs_evaluadas: Array<{
    team_name: string
    market?: string
    prob_model?: number | null
    odds_american?: number | null
    odds_decimal?: number | null
    edge_pct?: number | null
    has_individual_edge?: boolean
    tier?: string
    bookmaker?: string | null
    observed_at?: string | null
    missing_reason?: string | null
    source?: string
  }>
}

export interface CustomLegPayload {
  game_id?: string
  side?: string
  market?: string
  team_name?: string
  prob_model?: number | null
  odds_american?: number | null
  source?: string
}

export const parlayApi = {
  getRecommended: (bankroll = 1000, limit = 4) =>
    api.get<{ status: string; total: number; target_odds: string; parlays: RecommendedParlay[] }>(
      '/parlay/recommended',
      { params: { bankroll, limit } }
    ),
  evaluate: (
    legs: CustomLegPayload[],
    bankroll = 1000
  ) => api.post<RecommendedParlay>('/parlay/evaluate', { legs, bankroll }),
}

// ─── Sports Card Analytics ──────────────────────────────────────────────────

export interface SportsCardItem {
  id: number
  player_name: string
  sport: string
  season: string
  manufacturer: string
  brand: string
  card_type: string
  parallel_name: string
  print_run: number | null
  has_autograph: boolean
  grade: string
  current_market_price: number
  fair_market_value: number
  ev_opportunity: boolean
  ev_percentage: number
  liquidity_score: number
  image_url: string | null
}

export interface CardTrajectoryPoint {
  date: string
  projected_price: number
  lower_bound: number
  upper_bound: number
  is_forecast: boolean
}

export interface CardEvaluationResponse {
  player_name: string
  card_type: string
  grade: string
  parallel_name: string
  valuation: {
    fair_market_value: number
    base_pedigree_price: number
    multipliers: {
      uniform_effect: number
      grade: number
      parallel_rarity: number
      autograph: number
      franchise_market: number
    }
    nil_discount_applied: boolean
  }
  arbitrage_analysis: {
    listing_price: number
    fair_market_value: number
    net_proceeds_after_fees: number
    expected_profit_dollars: number
    roi_percentage: number
    is_arbitrage_opportunity: boolean
    recommendation: string
  } | null
}

export interface CardEvaluationRequest {
  player_name: string
  card_type?: string
  grade?: string
  parallel_name?: string
  autograph_type?: string
  listing_price?: number
  market_size_score?: number
  print_run?: number
}

export const cardsApi = {
  getCatalog: (card_type = 'all') =>
    api.get<{ status: string; total: number; cards: SportsCardItem[] }>('/cards/catalog', {
      params: { card_type },
    }),
  getArbitrage: () =>
    api.get<{ status: string; total_opportunities: number; arbitrage_cards: any[] }>('/cards/arbitrage'),
  getTrajectory: (cardId: number) =>
    api.get<{
      card_id: number
      player_name: string
      card_type: string
      current_price: number
      trajectory_points: CardTrajectoryPoint[]
    }>(`/cards/${cardId}/trajectory`),
  evaluate: (payload: CardEvaluationRequest) => api.post<CardEvaluationResponse>('/cards/evaluate', payload),
}
