import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { gamesApi, oddsApi, predictionsApi } from '@/services/api'
import { useAppStore } from '@/store/useAppStore'
import { buildMatchList } from '@/utils/dashboard'

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
    queryFn: () => predictionsApi.getToday(sport, 'all', 'all').then((response) => response.data),
    staleTime: 2 * 60_000,
  })

  const matchList = useMemo(() => buildMatchList(
    gamesQuery.data?.games ?? [],
    oddsQuery.data?.games ?? [],
    predictionsQuery.data?.picks ?? [],
  ), [gamesQuery.data?.games, oddsQuery.data?.games, predictionsQuery.data?.picks])

  return { sport, gamesQuery, oddsQuery, predictionsQuery, matchList }
}
