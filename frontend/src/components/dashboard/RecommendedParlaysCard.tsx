import type { RecommendedParlay } from '@/services/api'
import { formatPercentage } from '@/utils/dashboard'

interface RecommendedParlaysCardProps {
  status?: string
  reason?: string
  total?: number
  parlays?: RecommendedParlay[]
  loading: boolean
  hasError: boolean
}

export function RecommendedParlaysCard({
  status,
  reason,
  total = 0,
  parlays = [],
  loading,
  hasError,
}: RecommendedParlaysCardProps) {
  return (
    <section className="macos-card p-4" aria-label="Estado de microparlays recomendados">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">Microparlays recomendados</h3>
        <span className="text-[10px] uppercase tracking-wide text-slate-500">
          {status === 'success' && total > 0 ? 'DISPONIBLES' : 'NO PUBLICADOS'}
        </span>
      </div>
      <p className="mt-2 text-xs text-slate-400" role="status">
        {loading
          ? 'Consultando elegibilidad y datos actuales…'
          : hasError
            ? 'No se pudo consultar el servicio. No se infieren combinaciones.'
            : status === 'success' && total > 0
              ? `${total} combinaciones reportadas por el backend.`
              : reason ?? 'No hay combinaciones aprobadas con probabilidades y cuotas verificadas.'}
      </p>
      {status === 'success' && parlays.length > 0 && (
        <div className="mt-3 space-y-2">
          {parlays.map((parlay, index) => (
            <article key={`${parlay.type ?? 'parlay'}-${index}`} className="rounded-lg border border-white/[0.06] bg-white/[0.025] p-3 text-xs">
              <div className="flex justify-between gap-2 text-slate-200">
                <span>{parlay.type ?? `${parlay.n_legs}-patas`}</span>
                <span className="font-mono">{parlay.momio_americano != null ? `${parlay.momio_americano > 0 ? '+' : ''}${parlay.momio_americano}` : 'Cuota sin dato'}</span>
              </div>
              <p className="mt-1 text-slate-400">Probabilidad combinada {formatPercentage(parlay.prob_combinada)} · EV {parlay.ev_dolares == null ? 'no calculado' : `${parlay.ev_dolares > 0 ? '+' : ''}$${parlay.ev_dolares} por cada $100`}</p>
              <ul className="mt-2 space-y-1 text-slate-400">
                {parlay.legs_evaluadas.map((leg, legIndex) => (
                  <li key={`${leg.team_name}-${legIndex}`}>
                    {leg.team_name} · {leg.market ?? 'mercado'} · {leg.prob_model == null ? 'prob. no disponible' : formatPercentage(leg.prob_model)} · {leg.odds_american == null ? 'cuota no disponible' : `${leg.odds_american > 0 ? '+' : ''}${leg.odds_american}`} · {leg.bookmaker ?? 'casa desconocida'} · {leg.observed_at ?? 'sin hora'}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
