import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { gamesApi, oddsApi, parlayApi, predictionsApi } from '@/services/api'
import { useAppStore } from '@/store/useAppStore'
import { buildMatchList } from '@/utils/dashboard'

export function useSportsDashboard(selectedGameId?: number) {
  const sport = useAppStore((state) => state.activeSport)

  const gamesQuery = useQuery({
    queryKey: ['upcomingGames', sport, 14],
    queryFn: () => gamesApi.getUpcoming(14, sport).then((response) => response.data),
    staleTime: 60_000,
  })

  const oddsQuery = useQuery({
    queryKey: ['odds', sport],
    queryFn: () => (sport === 'NBA' ? oddsApi.getNBA() : oddsApi.getNFL()).then((response) => response.data),
    staleTime: 60_000,
  })

  // This endpoint is the production source of picks. Experimental probability
  // files remain available through their separate read-only endpoint and are
  // never converted into recommendations, EV, or Kelly stakes in the UI.
  const predictionsQuery = useQuery({
    queryKey: ['picks', sport, 'all', 'auto'],
    queryFn: () => predictionsApi.getToday(sport, 'all', 'auto').then((response) => response.data),
    staleTime: 60_000,
  })

  const experimentalQuery = useQuery({
    queryKey: ['experimentalPredictions', sport],
    queryFn: () => predictionsApi.getExperimental(sport.toLowerCase(), 14).then((response) => response.data),
    enabled: sport === 'NFL',
    staleTime: 60_000,
    retry: false,
  })

  const recommendedParlaysQuery = useQuery({
    queryKey: ['recommendedParlays', sport],
    queryFn: () => parlayApi.getRecommended(undefined, 3).then((response) => response.data),
    enabled: sport === 'NFL',
    staleTime: 60_000,
    retry: false,
  })

  const experimentalPredictions = useMemo(() => {
    const data = experimentalQuery.data
    if (!data || data.status !== 'experimental_only' || data.predictions_enabled || data.production_eligible) return []
    return data.games.map((game) => ({
      ...game,
      run_as_of: data.as_of,
      model_version: data.model_version,
      dataset_sha256: data.dataset_sha256,
      artifact_sha256: data.artifact_sha256,
    }))
  }, [experimentalQuery.data])

  const matchList = useMemo(() => buildMatchList(
    gamesQuery.data?.games ?? [],
    oddsQuery.data?.games ?? [],
    predictionsQuery.data?.picks ?? [],
    experimentalPredictions,
  ), [gamesQuery.data?.games, oddsQuery.data?.games, predictionsQuery.data?.picks, experimentalPredictions])

  const externalForecastGameId = selectedGameId ?? matchList.matches[0]?.game.id
  const externalForecastQuery = useQuery({
    queryKey: ['externalForecast', sport, externalForecastGameId],
    queryFn: () => gamesApi.getExternalPrediction(externalForecastGameId!).then((response) => response.data),
    enabled: sport === 'NFL' && externalForecastGameId != null,
    staleTime: 30_000,
    retry: false,
  })

  return { sport, gamesQuery, oddsQuery, predictionsQuery, experimentalQuery, recommendedParlaysQuery, externalForecastQuery, matchList }
}
