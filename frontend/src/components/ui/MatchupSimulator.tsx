/**
 * MatchupSimulator — Antiguo simulador multi-modelo de ensamble.
 * Desactivado formalmente conforme a las directrices de auditoría técnica.
 * No muestra equipos, momios, pesos de modelos, probabilidades ni métricas ficticias.
 */
import { ShieldAlert, Database, Lock } from 'lucide-react'

interface Props {
  bankroll?: number
}

export function MatchupSimulator({ bankroll: _bankroll }: Props) {
  return (
    <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-6 text-center space-y-4">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-amber-500/10 text-amber-400 border border-amber-500/20">
        <ShieldAlert className="h-6 w-6" />
      </div>

      <div className="space-y-1">
        <h3 className="text-base font-semibold text-white">
          Simulador de Ensamble Desactivado
        </h3>
        <p className="text-xs text-amber-300/80 font-mono">
          GOBERNANZA TÉCNICA · AUDITORÍA DE INFERENCIA SSOT
        </p>
      </div>

      <p className="text-xs text-slate-400 leading-relaxed max-w-lg mx-auto">
        El antiguo simulador con ponderaciones sintéticas (CatBoost, XGBoost, Logistic) y parámetros
        manuales ha sido descontinuado. La plataforma Griddio no genera pronósticos ficticios ni
        pesos simulados sin respaldo empírico out-of-sample.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-md mx-auto pt-2 text-left">
        <div className="rounded-lg border border-white/[0.08] bg-black/40 p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
            <Database className="h-3.5 w-3.5 text-apple-blue" />
            <span>Datos Observados</span>
          </div>
          <p className="text-[11px] text-slate-500">
            Las predicciones válidas proceden de la inferencia experimental real del backend.
          </p>
        </div>

        <div className="rounded-lg border border-white/[0.08] bg-black/40 p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
            <Lock className="h-3.5 w-3.5 text-amber-400" />
            <span>Gate de Producción</span>
          </div>
          <p className="text-[11px] text-slate-500">
            Producción permanece cerrada (production_enabled: false) hasta validación prospective.
          </p>
        </div>
      </div>
    </div>
  )
}
