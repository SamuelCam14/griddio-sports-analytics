import { ArrowRight, ChevronRight, Sparkles } from 'lucide-react'
import type { ScheduledMatch } from '@/types/dashboard'
import { formatMatchDate, formatMatchTime } from '@/utils/dashboard'
import { Badge } from '@/components/ui/Badge'

interface HeroSpotlightProps {
  match?: ScheduledMatch
  totalMatches: number
  currentIndex: number
  onNext: () => void
  onSelectIndex: (index: number) => void
  sport: 'NBA' | 'NFL'
  onAnalyze: () => void
}

export function HeroSpotlight({
  match,
  totalMatches,
  currentIndex,
  onNext,
  onSelectIndex,
  sport,
  onAnalyze,
}: HeroSpotlightProps) {
  const hasMatch = Boolean(match)
  const awayTeam = match?.odds.away_team
  const homeTeam = match?.odds.home_team
  const commenceTime = match?.odds.commence_time ?? ''
  const isLive = match?.game.status === 'live'

  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/[0.1] bg-gradient-to-b from-[#181c28] via-[#121520] to-[#0d0f15] shadow-macos-card">
      {/* Background Hero Image with dark slate gradient */}
      <div className="absolute inset-0">
        <img
          src="/images/hero_spotlight.jpg"
          alt="Sports Spotlight"
          className="h-full w-full object-cover object-center opacity-30 grayscale contrast-125"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-[#0d0f15] via-[#121520]/80 to-transparent" />
        <div className="absolute inset-0 bg-gradient-to-r from-[#0d0f15]/95 via-[#121520]/60 to-transparent" />
      </div>

      {/* Foreground Content */}
      <div className="relative z-10 flex min-h-[290px] sm:min-h-[320px] flex-col justify-between p-5 sm:p-6">
        {/* Top Badges */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Badge variant={isLive ? 'live' : 'blue'} pulse={isLive}>
              {isLive ? 'EN VIVO' : `${sport} SPOTLIGHT`}
            </Badge>
            {match?.prediction?.tier && (
              <Badge variant="premium">
                ★★★ {match.prediction.tier}
              </Badge>
            )}
          </div>

          {commenceTime && (
            <span className="hidden sm:inline-block text-xs font-mono text-slate-400">
              {formatMatchDate(commenceTime, { weekday: 'short', month: 'short', day: 'numeric' })} · {formatMatchTime(commenceTime)}
            </span>
          )}
        </div>

        {/* Center Headline */}
        <div className="my-auto max-w-xl py-3">
          <div className="flex items-center gap-1.5 text-xs font-medium text-apple-blue">
            <Sparkles className="h-3.5 w-3.5" />
            <span>PARTIDO DEL CALENDARIO</span>
          </div>

          {hasMatch ? (
            <>
              <h2 className="mt-1.5 text-2xl font-bold tracking-tight text-white sm:text-3xl">
                {awayTeam} <span className="text-slate-500 font-normal">@</span> {homeTeam}
              </h2>

              <p className="mt-1.5 text-xs text-slate-300 line-clamp-2">
                {match?.prediction?.best_bet
                  ? `Recomendación de producción: ${match.prediction.best_bet.selection} (${match.prediction.best_bet.odds_american > 0 ? '+' : ''}${match.prediction.best_bet.odds_american}) · EV +$${match.prediction.best_bet.ev_dollars.toFixed(1)}/100 · Prob. ${(match.prediction.best_bet.win_probability * 100).toFixed(1)}%.`
                  : match?.prediction
                    ? `Probabilidad estimada: local ${(match.prediction.home_win_prob * 100).toFixed(1)}% · visita ${(match.prediction.away_win_prob * 100).toFixed(1)}% (sin ventaja de cuota suficiente en el mercado actual).`
                    : 'Esperando cuotas de mercado vigentes o reporte de inactivos para este evento.'}
              </p>
            </>
          ) : (
            <>
              <h2 className="mt-1.5 text-2xl font-bold tracking-tight text-white sm:text-3xl">
                Sin partido seleccionado
              </h2>

              <p className="mt-1.5 text-xs text-slate-400 line-clamp-2">
                Selecciona un partido del calendario para inspeccionar las métricas de inferencia.
              </p>
            </>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-2.5">
            <button
              type="button"
              onClick={onAnalyze}
              disabled={!hasMatch}
              className="macos-btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span>Ver Análisis Integral</span>
              <ArrowRight className="h-3.5 w-3.5" />
            </button>

            {match?.odds.home_ml != null && match.odds.away_ml != null && (
              <div className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-black/40 px-3 py-2 text-xs">
                <span className="text-slate-400">Línea Moneyline:</span>
                <span className="font-mono font-medium text-white">
                  {match.odds.away_ml > 0 ? `+${match.odds.away_ml}` : match.odds.away_ml} / {match.odds.home_ml > 0 ? `+${match.odds.home_ml}` : match.odds.home_ml}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Bottom Carousel Indicator & Navigation Arrow */}
        <div className="flex items-center justify-between border-t border-white/[0.08] pt-3.5">
          {/* macOS Carousel Dots */}
          <div className="flex items-center gap-1.5" role="tablist" aria-label="Partidos destacados">
            {Array.from({ length: Math.min(totalMatches, 6) }).map((_, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => onSelectIndex(idx)}
                aria-label={`Seleccionar partido ${idx + 1}`}
                className={`h-1.5 rounded-full transition-all duration-200 ${
                  currentIndex === idx
                    ? 'w-5 bg-white'
                    : 'w-1.5 bg-white/25 hover:bg-white/50'
                }`}
              />
            ))}
          </div>

          {/* Next Button */}
          <button
            type="button"
            onClick={onNext}
            disabled={totalMatches < 2}
            title="Siguiente partido"
            className="flex items-center gap-1 rounded-lg border border-white/[0.09] bg-white/[0.06] px-2.5 py-1 text-xs font-medium text-slate-200 transition-colors hover:bg-white/[0.12] hover:text-white disabled:opacity-40"
          >
            <span>Siguiente</span>
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  )
}
