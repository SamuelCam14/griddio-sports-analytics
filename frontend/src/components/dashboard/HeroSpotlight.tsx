import { motion } from 'framer-motion'
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
  const isNba = sport === 'NBA'

  const awayTeam = match?.odds.away_team ?? (isNba ? 'Los Angeles Lakers' : 'San Francisco 49ers')
  const homeTeam = match?.odds.home_team ?? (isNba ? 'Boston Celtics' : 'Kansas City Chiefs')
  const commenceTime = match?.odds.commence_time ?? new Date().toISOString()
  const isLive = match?.game.status === 'live'

  return (
    <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-b from-[#0e2721] via-[#081b16] to-[#040e0b] shadow-2xl">
      {/* Background Hero Banner Image */}
      <div className="absolute inset-0">
        <img
          src="/images/hero_spotlight.jpg"
          alt="Sports Spotlight"
          className="h-full w-full object-cover object-center opacity-45 mix-blend-luminosity brightness-90 contrast-125"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-[#040e0b] via-[#071813]/60 to-transparent" />
        <div className="absolute inset-0 bg-gradient-to-r from-[#040e0b]/90 via-[#071813]/40 to-transparent" />
      </div>

      {/* Foreground Content */}
      <div className="relative z-10 flex min-h-[300px] sm:min-h-[340px] flex-col justify-between p-5 sm:p-7">
        {/* Top Badges */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Badge variant={isLive ? 'live' : 'emerald'} pulse={isLive}>
              {isLive ? 'EN VIVO AHORA' : `${sport} SPOTLIGHT`}
            </Badge>
            {match?.prediction?.tier && (
              <Badge variant="premium">
                ★★★ {match.prediction.tier}
              </Badge>
            )}
          </div>

          <span className="hidden sm:inline-block text-xs font-mono text-slate-300">
            {formatMatchDate(commenceTime, { weekday: 'short', month: 'short', day: 'numeric' })} · {formatMatchTime(commenceTime)}
          </span>
        </div>

        {/* Center Headline */}
        <div className="my-auto max-w-xl py-4">
          <div className="flex items-center gap-2 text-xs font-semibold tracking-wider text-emerald-300">
            <Sparkles className="h-4 w-4" />
            <span>PARTIDO ESTRELLA DE LA JORNADA</span>
          </div>

          <h2 className="mt-2 text-2xl font-black tracking-tight text-white sm:text-4xl">
            {awayTeam} <span className="text-emerald-400">@</span> {homeTeam}
          </h2>

          <p className="mt-2 text-xs sm:text-sm text-slate-300 line-clamp-2">
            {match?.prediction?.best_bet
              ? `Recomendación AI: ${match.prediction.best_bet.selection} (${match.prediction.best_bet.odds_american > 0 ? '+' : ''}${match.prediction.best_bet.odds_american}) · Ventaja: ${match.prediction.best_bet.edge_pct.toFixed(1)}%`
              : `Análisis cuantitativo de momios sin vig, probabilidades implícitas y proyección basada en ensamble CatBoost + XGBoost.`}
          </p>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={onAnalyze}
              className="btn-glow-primary text-xs sm:text-sm"
            >
              <span>Ver Análisis Integral</span>
              <ArrowRight className="h-4 w-4" />
            </button>

            {match?.odds.home_ml && (
              <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-black/40 px-3 py-2 text-xs">
                <span className="text-slate-400">Línea ML:</span>
                <span className="font-mono font-bold text-emerald-300">
                  {match.odds.away_ml && match.odds.away_ml > 0 ? `+${match.odds.away_ml}` : match.odds.away_ml} / {match.odds.home_ml > 0 ? `+${match.odds.home_ml}` : match.odds.home_ml}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Bottom Carousel Indicator & Navigation Arrow */}
        <div className="flex items-center justify-between border-t border-white/10 pt-4">
          {/* Dots */}
          <div className="flex items-center gap-1.5" role="tablist" aria-label="Partidos destacados">
            {Array.from({ length: Math.min(totalMatches || 4, 6) }).map((_, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => onSelectIndex(idx)}
                aria-label={`Seleccionar partido ${idx + 1}`}
                className={`h-2 rounded-full transition-all duration-300 ${
                  currentIndex === idx
                    ? 'w-6 bg-white shadow-[0_0_8px_rgba(255,255,255,0.7)]'
                    : 'w-2 bg-white/30 hover:bg-white/50'
                }`}
              />
            ))}
          </div>

          {/* Next Button */}
          <button
            type="button"
            onClick={onNext}
            title="Siguiente partido"
            className="flex items-center gap-1.5 rounded-full border border-white/15 bg-white/10 px-3 py-1.5 text-xs font-semibold text-white transition-all hover:bg-white/20 active:scale-95"
          >
            <span>Siguiente</span>
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
