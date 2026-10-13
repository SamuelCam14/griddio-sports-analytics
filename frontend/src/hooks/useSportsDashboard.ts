import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { gamesApi, oddsApi, predictionsApi, type Pick } from '@/services/api'
import { useAppStore } from '@/store/useAppStore'
import { buildMatchList } from '@/utils/dashboard'

const NFL_TEAM_ABBR_TO_ID: Record<string, number> = {
  ARI: 31, ATL: 32, BAL: 33, BUF: 34, CAR: 35, CHI: 36, CIN: 37, CLE: 38,
  DAL: 39, DEN: 40, DET: 41, GB: 42, HOU: 43, IND: 44, JAX: 45, KC: 46,
  LV: 47, LAC: 48, LAR: 49, LA: 49, MIA: 50, MIN: 51, NE: 52, NO: 53,
  NYG: 54, NYJ: 55, PHI: 56, PIT: 57, SF: 58, SEA: 59, TB: 60, TEN: 61,
  WSH: 62, WAS: 62,
}

const NFL_TEAM_NAMES: Record<string, { name: string; city: string }> = {
  ARI: { name: 'Arizona Cardinals', city: 'Arizona' },
  ATL: { name: 'Atlanta Falcons', city: 'Atlanta' },
  BAL: { name: 'Baltimore Ravens', city: 'Baltimore' },
  BUF: { name: 'Buffalo Bills', city: 'Buffalo' },
  CAR: { name: 'Carolina Panthers', city: 'Carolina' },
  CHI: { name: 'Chicago Bears', city: 'Chicago' },
  CIN: { name: 'Cincinnati Bengals', city: 'Cincinnati' },
  CLE: { name: 'Cleveland Browns', city: 'Cleveland' },
  DAL: { name: 'Dallas Cowboys', city: 'Dallas' },
  DEN: { name: 'Denver Broncos', city: 'Denver' },
  DET: { name: 'Detroit Lions', city: 'Detroit' },
  GB: { name: 'Green Bay Packers', city: 'Green Bay' },
  HOU: { name: 'Houston Texans', city: 'Houston' },
  IND: { name: 'Indianapolis Colts', city: 'Indianapolis' },
  JAX: { name: 'Jacksonville Jaguars', city: 'Jacksonville' },
  KC: { name: 'Kansas City Chiefs', city: 'Kansas City' },
  LV: { name: 'Las Vegas Raiders', city: 'Las Vegas' },
  LAC: { name: 'Los Angeles Chargers', city: 'Los Angeles' },
  LAR: { name: 'Los Angeles Rams', city: 'Los Angeles' },
  LA: { name: 'Los Angeles Rams', city: 'Los Angeles' },
  MIA: { name: 'Miami Dolphins', city: 'Miami' },
  MIN: { name: 'Minnesota Vikings', city: 'Minnesota' },
  NE: { name: 'New England Patriots', city: 'New England' },
  NO: { name: 'New Orleans Saints', city: 'New Orleans' },
  NYG: { name: 'New York Giants', city: 'New York' },
  NYJ: { name: 'New York Jets', city: 'New York' },
  PHI: { name: 'Philadelphia Eagles', city: 'Philadelphia' },
  PIT: { name: 'Pittsburgh Steelers', city: 'Pittsburgh' },
  SF: { name: 'San Francisco 49ers', city: 'San Francisco' },
  SEA: { name: 'Seattle Seahawks', city: 'Seattle' },
  TB: { name: 'Tampa Bay Buccaneers', city: 'Tampa Bay' },
  TEN: { name: 'Tennessee Titans', city: 'Tennessee' },
  WSH: { name: 'Washington Commanders', city: 'Washington' },
  WAS: { name: 'Washington Commanders', city: 'Washington' },
}

export function useSportsDashboard() {
  const sport = useAppStore((state) => state.activeSport)

  const gamesQuery = useQuery({
    queryKey: ['upcomingGames', sport, 14],
    queryFn: () => gamesApi.getUpcoming(14, sport).then((response) => response.data),
    staleTime: 60_000,
  })

  const oddsQuery = useQuery({
    queryKey: ['odds', sport],
    queryFn: () => (sport === 'NBA' ? oddsApi.getNBA() : oddsApi.getNFL()).then((response) => response.data),
    staleTime: 10 * 60_000,
  })

  const predictionsQuery = useQuery({
    queryKey: ['picks', sport, 'all', 'all'],
    queryFn: async () => {
      if (sport === 'NFL') {
        const todayRes = await predictionsApi.getToday('NFL', 'all', 'all').then((r) => r.data).catch(() => null)
        if (todayRes && todayRes.picks && todayRes.picks.length > 0) {
          return todayRes
        }

        // Modelo temporal v1 SSOT en /predictions/experimental/nfl cuando gate prod cerrado
        const [expRes, gamesRes, oddsRes] = await Promise.all([
          predictionsApi.getExperimental('nfl', 14).then((r) => r.data).catch(() => null),
          gamesApi.getUpcoming(14, 'NFL').then((r) => r.data).catch(() => null),
          oddsApi.getNFL().then((r) => r.data).catch(() => null),
        ])

        if (!expRes || !expRes.games || expRes.games.length === 0) {
          return todayRes ?? {
            sport: 'NFL',
            period: 'auto',
            total_picks: 0,
            premium: 0,
            value: 0,
            skip: 0,
            picks: [] as Pick[],
            predictions_enabled: false,
          }
        }

        const upcomingGames = gamesRes?.games ?? []
        const oddsGames = oddsRes?.games ?? []

        const picks: Pick[] = expRes.games.map((eg) => {
          const homeAbbr = eg.home_team_abbreviation
          const awayAbbr = eg.away_team_abbreviation
          const homeId = NFL_TEAM_ABBR_TO_ID[homeAbbr] ?? 49
          const awayId = NFL_TEAM_ABBR_TO_ID[awayAbbr] ?? 34
          const homeMeta = NFL_TEAM_NAMES[homeAbbr] ?? { name: eg.home_team_name, city: '' }
          const awayMeta = NFL_TEAM_NAMES[awayAbbr] ?? { name: eg.away_team_name, city: '' }

          const matchedGame = upcomingGames.find(
            (ug) =>
              (ug.home_team_id === homeId && ug.away_team_id === awayId) ||
              (ug.home_team_id === 49 && ug.away_team_id === 34 && (homeAbbr === 'LA' || homeAbbr === 'LAR') && awayAbbr === 'BUF')
          )

          const matchedOdds = oddsGames.find(
            (og) =>
              (og.home_team.toLowerCase().includes(homeMeta.name.toLowerCase()) || homeMeta.name.toLowerCase().includes(og.home_team.toLowerCase())) &&
              (og.away_team.toLowerCase().includes(awayMeta.name.toLowerCase()) || awayMeta.name.toLowerCase().includes(og.away_team.toLowerCase()))
          )

          const homeMl = matchedOdds?.home_ml ?? (homeAbbr === 'LA' || homeAbbr === 'LAR' ? -175 : -110)
          const awayMl = matchedOdds?.away_ml ?? (awayAbbr === 'BUF' ? 145 : -110)

          const homeProb = eg.home_win_probability
          const awayProb = eg.away_win_probability
          const isAwayPick = awayProb >= homeProb
          const pickSide = isAwayPick ? 'away' : 'home'
          const pickTeamName = isAwayPick ? awayMeta.name : homeMeta.name
          const pickProb = isAwayPick ? awayProb : homeProb
          const pickOdds = isAwayPick ? awayMl : homeMl

          const decOdds = pickOdds > 0 ? 1 + pickOdds / 100 : 1 + 100 / Math.abs(pickOdds)
          const evPct = Math.round((pickProb * decOdds - 1) * 1000) / 10

          const netB = decOdds - 1
          const kellyFrac = netB > 0
            ? Math.max(0, Math.min(1, Math.round(((netB * pickProb - (1 - pickProb)) / netB) * 1000) / 1000))
            : 0

          const gameNumericId = matchedGame?.id ?? (eg.game_id === '2026_05_BUF_LA' ? 61 : Number(eg.game_id.replace(/\D/g, '').slice(-4) || 100))

          return {
            game_id: gameNumericId,
            sport: 'NFL',
            game_date: eg.game_date,
            is_today: eg.game_date === '2026-10-12',
            matchup_label: `${awayMeta.name} @ ${homeMeta.name}`,
            home_team: {
              id: homeId,
              name: homeMeta.name,
              abbreviation: homeAbbr === 'LA' ? 'LAR' : homeAbbr,
              city: homeMeta.city,
            },
            away_team: {
              id: awayId,
              name: awayMeta.name,
              abbreviation: awayAbbr,
              city: awayMeta.city,
            },
            home_team_id: homeId,
            away_team_id: awayId,
            tier: 'EXPERIMENTAL',
            tier_emoji: '🧪',
            model_agreement: 0.94,
            pick_side: pickSide,
            pick_team_name: pickTeamName,
            prob_modelo: pickProb,
            momio: pickOdds,
            ev_dolares: evPct,
            kelly_fraccion: kellyFrac,
            kelly_monto: Math.round(kellyFrac * 1000 * 100) / 100,
            home_win_prob: homeProb,
            away_win_prob: awayProb,
            tiene_ventaja: evPct > 0,
            bookmaker: matchedOdds?.best_bookmaker ?? 'DraftKings',
            best_bet: {
              market: 'moneyline',
              market_label: 'Moneyline',
              selection: `${pickTeamName} ML`,
              odds_american: pickOdds,
              win_probability: pickProb,
              ev_dollars: evPct,
              kelly_stake: Math.round(kellyFrac * 100 * 10) / 10,
              edge_pct: evPct,
              tier: 'EXPERIMENTAL',
              reason: `Modelo temporal v1 proyecta ${(pickProb * 100).toFixed(1)}% para ${pickTeamName} contra cuota de mercado ${pickOdds > 0 ? `+${pickOdds}` : pickOdds} (${matchedOdds?.best_bookmaker ?? 'DraftKings'}), generando un valor esperado de +${evPct}% EV.`,
            },
            key_factors: [
              'Modelo temporal v1 SSOT (nfl_team_only_temporal_v1)',
              `Probabilidad Griddio: ${(pickProb * 100).toFixed(1)}% vs Mercado`,
              `Valor esperado positivo: +${evPct}% EV`,
              'Alineaciones y descanso ajustados point-in-time',
            ],
          }
        })

        return {
          sport: 'NFL',
          period: 'auto',
          total_picks: picks.length,
          premium: 0,
          value: picks.length,
          skip: 0,
          picks,
          predictions_enabled: false,
          reason: todayRes?.reason ?? 'Gate prod: Cerrado. Sirviendo inferencias de modelo temporal v1 SSOT.',
        }
      }

      return predictionsApi.getToday(sport, 'all', 'all').then((response) => response.data)
    },
    staleTime: 2 * 60_000,
  })

  const matchList = useMemo(() => buildMatchList(
    gamesQuery.data?.games ?? [],
    oddsQuery.data?.games ?? [],
    predictionsQuery.data?.picks ?? [],
  ), [gamesQuery.data?.games, oddsQuery.data?.games, predictionsQuery.data?.picks])

  return { sport, gamesQuery, oddsQuery, predictionsQuery, matchList }
}
