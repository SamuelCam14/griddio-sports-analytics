/**
 * Hook modular para consulta y refresco periódico de cuotas deportivas desde The Odds API.
 */
import { useQuery } from '@tanstack/react-query'
import { oddsApi } from '@/services/api'

export function useSportsOdds(sport: 'NBA' | 'NFL') {
  return useQuery({
    queryKey: ['odds', sport],
    queryFn: () => (sport === 'NBA' ? oddsApi.getNBA() : oddsApi.getNFL()).then(r => r.data),
    refetchInterval: 15 * 60 * 1000, // Actualización periódica cada 15 minutos para optimizar cuota de API
    staleTime: 10 * 60 * 1000,
  })
}
