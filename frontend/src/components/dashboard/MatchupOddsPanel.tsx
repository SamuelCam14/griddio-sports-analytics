import type { OddsData } from '@/services/api'
import { formatPercentage } from '@/utils/dashboard'

export function MatchupOddsPanel({ odds }: { odds: OddsData }) {
  return (
    <section className="surface-card">
      <div>
        <p className="eyebrow">MERCADO DISPONIBLE</p>
        <h2 className="section-title">Probabilidades implícitas</h2>
      </div>
      {odds.best_bookmaker && <p className="mt-2 text-xs text-slate-400">Mejor línea: {odds.best_bookmaker}</p>}
      <div className="mt-4 space-y-3">
        <OddsLine label={odds.away_team} value={odds.away_implied_prob} />
        <OddsLine label={odds.home_team} value={odds.home_implied_prob} />
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-slate-500">Probabilidades implícitas reportadas por el proveedor de momios; no son pronósticos del modelo.</p>
    </section>
  )
}

function OddsLine({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl bg-white/[0.03] px-3 py-2.5">
      <span className="truncate text-sm text-slate-300">{label}</span>
      <span className="font-mono text-sm font-semibold text-emerald-200">{formatPercentage(value)}</span>
    </div>
  )
}
