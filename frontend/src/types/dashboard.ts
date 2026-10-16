import type { ExperimentalMatchPrediction, Game, OddsData, Pick } from '@/services/api'

export interface ScheduledMatch {
  game: Game
  odds: OddsData
  prediction?: Pick
  experimentalPrediction?: ExperimentalMatchPrediction
}

export interface MatchListState {
  matches: ScheduledMatch[]
  unmatchedOddsCount: number
}
