/**
 * Hook modular para consumo y gestión reactiva de predicciones de ML y métricas del ensamble.
 */
import { useQuery } from '@tanstack/react-query'
import { predictionsApi, type PredictionTier } from '@/services/api'

export function useModelInfo() {
  return useQuery({
    queryKey: ['modelInfo'],
    queryFn: () => predictionsApi.getModelInfo().then(r => r.data),
    staleTime: 60 * 60 * 1000, // 1 hora de persistencia en caché
  })
}

export function useTodayPicks(
  sport: string,
  tierFilter: 'all' | PredictionTier = 'all',
  period = 'auto'
) {
  return useQuery({
    queryKey: ['picks', sport, tierFilter, period],
    queryFn: () => predictionsApi.getToday(sport, tierFilter, period).then(r => r.data),
    refetchInterval: 5 * 60 * 1000, // Polling de actualización cada 5 minutos
    staleTime: 2 * 60 * 1000,
  })
}
