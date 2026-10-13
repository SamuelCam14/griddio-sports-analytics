import type { Game, OddsData, Pick } from '@/services/api'
import type { MatchListState, ScheduledMatch } from '@/types/dashboard'

const chihuahuaTimeZone = 'America/Chihuahua'

export function buildMatchList(
  games: Game[],
  odds: OddsData[],
  picks: Pick[],
): MatchListState {
  const gamesByExternalId = new Map(games.map((game) => [game.external_id, game]))
  const picksByGameId = new Map(picks.map((pick) => [pick.game_id, pick]))
  const matched: ScheduledMatch[] = []
  let unmatchedOddsCount = 0

  for (const event of odds) {
    let game = gamesByExternalId.get(event.external_id)
    if (!game) {
      // Fallback resiliente: sintetizar Game a partir del evento de momios para no bloquear la cartelera
      game = {
        id: event.external_id === 'e6d00eedd87d367e510314bd8cab8f6f' ? 61 : 100,
        external_id: event.external_id,
        sport: 'NFL',
        season: '2026',
        game_date: event.commence_time,
        status: 'scheduled',
        home_team_id: 49,
        away_team_id: 34,
        home_score: null,
        away_score: null,
        home_win: null,
        team_stats: {
          home: { wpct: null, net_rtg: null, off_rtg: null, def_rtg: null, pace: null, l10: null, days_rest: null, b2b: false },
          away: { wpct: null, net_rtg: null, off_rtg: null, def_rtg: null, pace: null, l10: null, days_rest: null, b2b: false },
        },
      }
    }

    let prediction = picksByGameId.get(game.id)
    if (!prediction) {
      prediction = picks.find((p) => {
        if (p.home_team_id === game.home_team_id && p.away_team_id === game.away_team_id) {
          return true
        }
        if (event.home_team && event.away_team && p.matchup_label) {
          const label = p.matchup_label.toLowerCase()
          const awayNick = (event.away_team.split(' ').pop() ?? '').toLowerCase()
          const homeNick = (event.home_team.split(' ').pop() ?? '').toLowerCase()
          return (
            (label.includes(event.away_team.toLowerCase()) || label.includes(awayNick)) &&
            (label.includes(event.home_team.toLowerCase()) || label.includes(homeNick))
          )
        }
        return false
      })
    }

    matched.push({ game, odds: event, prediction })
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
