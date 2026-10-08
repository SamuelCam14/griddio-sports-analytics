import type { Game, OddsData, Pick } from '@/services/api'

export interface ScheduledMatch {
  game: Game
  odds: OddsData
  prediction?: Pick
}

export interface MatchListState {
  matches: ScheduledMatch[]
  unmatchedOddsCount: number
}
