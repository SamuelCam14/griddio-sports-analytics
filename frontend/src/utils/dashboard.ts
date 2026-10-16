import type { ExperimentalMatchPrediction, Game, OddsData, Pick } from '@/services/api'
import type { MatchListState, ScheduledMatch } from '@/types/dashboard'

const chihuahuaTimeZone = 'America/Chihuahua'

export function buildMatchList(
  games: Game[],
  odds: OddsData[],
  picks: Pick[],
  experimentalPredictions: ExperimentalMatchPrediction[] = [],
): MatchListState {
  const gamesByExternalId = new Map(games.map((game) => [game.external_id, game]))
  const picksByGameId = new Map(picks.map((pick) => [pick.game_id, pick]))
  const experimentalByExternalId = new Map(experimentalPredictions
    .filter((prediction) => prediction.canonical_external_id)
    .map((prediction) => [prediction.canonical_external_id as string, prediction]))
  const matched: ScheduledMatch[] = []
  let unmatchedOddsCount = 0

  for (const event of odds) {
    const game = gamesByExternalId.get(event.external_id)
    if (!game) {
      unmatchedOddsCount += 1
      continue
    }

    matched.push({
      game,
      odds: event,
      prediction: picksByGameId.get(game.id),
      experimentalPrediction: experimentalByExternalId.get(game.external_id),
    })
  }

  matched.sort((first, second) => Date.parse(first.game.game_date) - Date.parse(second.game.game_date))
  return { matches: matched, unmatchedOddsCount }
}

export function formatMatchDate(value: string, options?: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat('es-MX', {
    timeZone: chihuahuaTimeZone,
    day: '2-digit',
    month: 'short',
    ...options,
  }).format(new Date(value))
}

export function formatMatchTime(value: string): string {
  return new Intl.DateTimeFormat('es-MX', {
    timeZone: chihuahuaTimeZone,
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

export function formatPercentage(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value.toFixed(digits)
}
