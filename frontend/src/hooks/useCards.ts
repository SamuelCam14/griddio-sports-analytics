/**
 * Hook modular para catálogo de Sports Cards.
 */
import { useQuery, useMutation } from "@tanstack/react-query";
import { cardsApi, type CardEvaluationRequest } from "@/services/api";

export function useCardsCatalog(filterType = "all") {
  return useQuery({
    queryKey: ["cardsCatalog", filterType],
    queryFn: () => cardsApi.getCatalog(filterType).then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });
}

export function useCardTrajectory(cardId: number | null | undefined) {
  return useQuery({
    queryKey: ["cardTrajectory", cardId],
    queryFn: () =>
      cardId ? cardsApi.getTrajectory(cardId).then((r) => r.data) : null,
    enabled: typeof cardId === "number" && cardId > 0,
    staleTime: 10 * 60 * 1000,
  });
}

export function useEvaluateCard() {
  return useMutation({
    mutationFn: (payload: CardEvaluationRequest) =>
      cardsApi.evaluate(payload).then((r) => r.data),
  });
}
