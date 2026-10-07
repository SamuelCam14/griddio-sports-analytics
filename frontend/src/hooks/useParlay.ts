/**
 * Hook modular para generación, recomendación y evaluación interactiva de Micro-Parlays con ventaja matemática.
 */
import { useQuery, useMutation } from '@tanstack/react-query'
import { parlayApi, type CustomLegPayload } from '@/services/api'

export function useRecommendedParlays(bankroll: number, limit = 4) {
  return useQuery({
    queryKey: ['recommendedParlays', bankroll, limit],
    queryFn: () => parlayApi.getRecommended(bankroll, limit).then(r => r.data),
    staleTime: 5 * 60 * 1000,
  })
}

export function useEvaluateParlay() {
  return useMutation({
    mutationFn: ({ legs, bankroll }: { legs: CustomLegPayload[]; bankroll: number }) =>
      parlayApi.evaluate(legs, bankroll).then(r => r.data),
  })
}
