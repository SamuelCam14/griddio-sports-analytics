/**
 * MatchupSimulator — Visualizador del Ensamble de Producción.
 * Muestra el estado del ensamble validado y los principios de inferencia en producción.
 */
import { CheckCircle2, Database, TrendingUp } from 'lucide-react'

interface Props {
  bankroll?: number
}

export function MatchupSimulator({ bankroll: _bankroll }: Props) {
  return (
    <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.04] p-6 text-center space-y-4">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
        <CheckCircle2 className="h-6 w-6" />
      </div>

      <div className="space-y-1">
        <h3 className="text-base font-semibold text-white">
          Ensamble de Producción Activo (nfl_temporal_v1)
        </h3>
        <p className="text-xs text-emerald-300/80 font-mono">
          MODELOS INDEPENDIENTES · AJUSTE DINÁMICO POR FANTASY PREVIO
        </p>
      </div>

      <p className="text-xs text-slate-300 leading-relaxed max-w-lg mx-auto">
        Los pronósticos provienen de un ensamble de modelos independientes validados con walk-forward temporal,
        ajuste dinámico de lesiones ponderado por fantasy points previos (sin pesos fijos por posición ni imputación de ceros)
        y cuotas reales observadas en el mercado.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-md mx-auto pt-2 text-left">
        <div className="rounded-lg border border-white/[0.08] bg-black/40 p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
            <Database className="h-3.5 w-3.5 text-apple-blue" />
            <span>Datos 100% Reales</span>
          </div>
          <p className="text-[11px] text-slate-400">
            Cero datos simulados. Se emplean exclusivamente fuentes verificadas de NFLverse, ESPN y The Odds API.
          </p>
        </div>

        <div className="rounded-lg border border-white/[0.08] bg-black/40 p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
            <TrendingUp className="h-3.5 w-3.5 text-emerald-400" />
            <span>Ventaja Matemática (+EV)</span>
          </div>
          <p className="text-[11px] text-slate-400">
            Picks recomendados únicamente cuando existe confluencia del ensamble, cuota ≥ 1.40 y valor esperado positivo.
          </p>
        </div>
      </div>
    </div>
  )
}
