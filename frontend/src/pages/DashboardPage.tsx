/**
 * Dashboard principal — Página de inicio.
 * Muestra: Picks del día por tier, momios actuales y calculadora rápida de EV.
 */
import { useState, useEffect } from 'react'
import { useAppStore } from '@/store/useAppStore'
import { useModelInfo, useTodayPicks, useSportsOdds } from '@/hooks'
import { MatchupAnalyticsCard } from '@/components/games/MatchupAnalyticsCard'
import { EVCalculator } from '@/components/ui/EVCalculator'
import { MatchupSimulator } from '@/components/ui/MatchupSimulator'
import { ParlayBuilderView } from '@/components/parlay/ParlayBuilderView'
import { SportsCardsView } from '@/components/cards/SportsCardsView'
import { BankrollWidget } from '@/components/ui/BankrollWidget'
import { OddsTable } from '@/components/games/OddsTable'

type TierFilter = 'all' | 'PREMIUM' | 'VALUE'
type PeriodFilter = 'today' | 'week' | 'all'
type MainTab = 'betting' | 'parlay' | 'cards'

export function DashboardPage() {
  const { activeSport, bankroll } = useAppStore()
  const [activeTab, setActiveTab] = useState<MainTab>('betting')
  const [tierFilter, setTierFilter] = useState<TierFilter>('all')
  const [periodFilter, setPeriodFilter] = useState<PeriodFilter>('week')
  const [showEVCalc, setShowEVCalc] = useState(false)
  const [showSimulator, setShowSimulator] = useState(false)

  // Ajustar período por defecto según deporte
  useEffect(() => {
    setPeriodFilter(activeSport === 'NFL' ? 'week' : 'today')
  }, [activeSport])

  // ─── Custom Hooks (Lógica desacoplada de red) ──────────────────────────────
  const modelInfoQuery = useModelInfo()
  const picksQuery = useTodayPicks(activeSport, tierFilter, periodFilter)
  const oddsQuery = useSportsOdds(activeSport)

  const picks = picksQuery.data?.picks ?? []
  const premiumCount = picksQuery.data?.premium ?? 0
  const valueCount   = picksQuery.data?.value ?? 0
  const activeModel = modelInfoQuery.data?.models?.[activeSport]

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* ─── Header ─────────────────────────────────────────────────────── */}
      <header className="border-b border-gray-800 bg-gray-900 px-6 py-4 sticky top-0 z-40">
        <div className="mx-auto max-w-7xl flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="text-2xl">⚡</span>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold text-white">Sports Analytics</h1>
                <span className="text-[10px] bg-brand-600 text-white px-2 py-0.5 rounded-full font-mono">
                  QUANT v2.0
                </span>
              </div>
            </div>
          </div>

          {/* Navegación Principal de Módulos */}
          <nav className="flex items-center bg-gray-950 p-1 rounded-xl border border-gray-800 text-xs">
            <button
              onClick={() => setActiveTab('betting')}
              className={`px-3.5 py-1.5 rounded-lg font-medium transition-all ${
                activeTab === 'betting'
                  ? 'bg-brand-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              🏀 Apuestas EV+
            </button>
            <button
              onClick={() => setActiveTab('parlay')}
              className={`px-3.5 py-1.5 rounded-lg font-medium transition-all ${
                activeTab === 'parlay'
                  ? 'bg-brand-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              🎯 Micro-Parlay Builder
            </button>
            <button
              onClick={() => setActiveTab('cards')}
              className={`px-3.5 py-1.5 rounded-lg font-medium transition-all ${
                activeTab === 'cards'
                  ? 'bg-brand-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              🃏 Sports Cards & NIL
            </button>
          </nav>

          <div className="flex items-center gap-4">
            {activeTab === 'betting' && (
              <div className="flex rounded-lg overflow-hidden border border-gray-700">
                {(['NBA', 'NFL'] as const).map(sport => (
                  <SportButton key={sport} sport={sport} />
                ))}
              </div>
            )}
            <BankrollWidget bankroll={bankroll} />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8 space-y-8">
        {/* Renderizado condicional según pestaña activa */}
        {activeTab === 'parlay' && <ParlayBuilderView bankroll={bankroll} />}
        {activeTab === 'cards' && <SportsCardsView />}
        {activeTab === 'betting' && (
          <>

        {/* ─── Stats rápidas ───────────────────────────────────────────── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard label="Partidos en Cartelera" value={picks.length.toString()} color="gray" />
          <StatCard label="★★★ PREMIUM" value={premiumCount.toString()} color="purple" />
          <StatCard
            label="Hándicaps con Ventaja"
            value={picks.filter(p => p.spread && p.spread.tier !== 'NEUTRAL').length.toString()}
            color="blue"
          />
          <StatCard
            label="Player Props Analizadas"
            value={picks.reduce((acc, p) => acc + (p.player_props?.length || 0), 0).toString()}
            color="green"
          />
        </div>

        {/* ─── Estado reproducible del modelo ─────────────────────────── */}
        {activeModel && (
          <div className="bg-gray-900 border border-amber-800/50 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4">
            <div>
              <h3 className="text-sm font-semibold text-white">
                {activeSport} · {activeModel.model_version ?? 'Modelo no disponible'} · experimental
              </h3>
              <p className="text-xs text-gray-400 mt-1">
                Predicciones de producción desactivadas. La evaluación es walk-forward y no mide ROI.
              </p>
              {activeModel.date_from && activeModel.date_to && (
                <p className="text-xs text-gray-500 mt-1">
                  {activeModel.sample_count?.toLocaleString()} partidos · {activeModel.date_from} a {activeModel.date_to}
                </p>
              )}
            </div>
            {activeModel.metrics && (
              <div className="text-xs font-mono text-gray-300">
                <span>AUC {activeModel.metrics.mean_auc.toFixed(3)}</span>
                <span className="mx-3">Brier {activeModel.metrics.mean_brier.toFixed(3)}</span>
                <span>Baseline superado en todos los folds: {activeModel.metrics.beats_majority_brier_in_all_folds ? 'sí' : 'no'}</span>
              </div>
            )}
          </div>
        )}

        {/* ─── Filtros de Período, Tier y Acciones ─────────────────────── */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* Selector de Período (Hoy vs Semana) */}
          <div className="flex items-center bg-gray-900 p-1 rounded-xl border border-gray-800 text-xs">
            <button
              onClick={() => setPeriodFilter('today')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
                periodFilter === 'today'
                  ? 'bg-brand-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              🔥 Partidos de Hoy
            </button>
            <button
              onClick={() => setPeriodFilter('week')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
                periodFilter === 'week'
                  ? 'bg-brand-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              📅 Cartelera de la Semana
            </button>
            <button
              onClick={() => setPeriodFilter('all')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
                periodFilter === 'all'
                  ? 'bg-brand-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              🗓️ Todos los Juegos
            </button>
          </div>

          {/* Filtros de Tier */}
          <div className="flex items-center gap-2">
            {(['all', 'PREMIUM', 'VALUE'] as TierFilter[]).map(t => (
              <button
                key={t}
                onClick={() => setTierFilter(t)}
                className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  tierFilter === t
                    ? 'bg-purple-600 text-white'
                    : 'bg-gray-900 border border-gray-800 text-gray-400 hover:bg-gray-800'
                }`}
              >
                {t === 'all' ? 'Todos los Tiers' : t}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowSimulator(!showSimulator)}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                showSimulator
                  ? 'bg-purple-600 text-white'
                  : 'bg-gray-900 border border-purple-800/60 text-purple-300 hover:bg-purple-950/40'
              }`}
            >
              {showSimulator ? '✕ Cerrar Simulador' : '🤖 Simular Matchup (ML)'}
            </button>
            <button
              onClick={() => setShowEVCalc(!showEVCalc)}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                showEVCalc
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-900 border border-gray-700 text-gray-300 hover:bg-gray-800'
              }`}
            >
              {showEVCalc ? '✕ Cerrar EV' : '🧮 Calculadora EV'}
            </button>
          </div>
        </div>

        {/* ─── Matchup Simulator inline ─────────────────────────────────── */}
        {showSimulator && (
          <div className="bg-gray-900 border border-purple-800/60 rounded-xl p-6 shadow-xl shadow-purple-950/20">
            <MatchupSimulator bankroll={bankroll} />
          </div>
        )}

        {/* ─── EV Calculator inline ────────────────────────────────────── */}
        {showEVCalc && (
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6">
            <EVCalculator bankroll={bankroll} />
          </div>
        )}

        {/* ─── Grid de Matchups con Análisis Integral ──────────────────── */}
        {picksQuery.isLoading && (
          <div className="flex items-center justify-center py-20 text-gray-500">
            <LoadingDots /> Cargando cartelera y modelos de análisis deportivo...
          </div>
        )}

        {picksQuery.isError && (
          <div className="bg-red-950 border border-red-800 rounded-xl p-6 text-center text-red-300">
            ⚠️ Error al cargar los partidos. Verifica que el backend esté corriendo en :8000
          </div>
        )}

        {!picksQuery.isLoading && !picksQuery.isError && (
          <>
            {picks.length === 0 ? (
              <div className="text-center py-20 text-gray-400 bg-gray-900/60 border border-gray-800 rounded-xl p-8 space-y-3">
                <span className="text-3xl block">🏈</span>
                <p className="text-base font-semibold text-white">
                  No hay partidos registrados para este filtro específico ({periodFilter}).
                </p>
                <p className="text-xs text-gray-500 max-w-md mx-auto">
                  Prueba cambiando a "Cartelera de la Semana" para ver todos los encuentros programados de la NFL con análisis de Spreads y Player Props.
                </p>
                <button
                  onClick={() => setPeriodFilter('week')}
                  className="px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-lg text-xs font-semibold mt-2"
                >
                  📅 Ver Cartelera Completa de la Semana
                </button>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
                {picks.map(pick => (
                  <MatchupAnalyticsCard key={pick.game_id} pick={pick} bankroll={bankroll} />
                ))}
              </div>
            )}
          </>
        )}

        {/* ─── Tabla de momios ─────────────────────────────────────────── */}
        {oddsQuery.data && (
          <section>
            <h2 className="text-lg font-semibold text-gray-200 mb-4">
              📊 Momios actuales — {activeSport}
            </h2>
            <OddsTable games={oddsQuery.data.games} />
          </section>
        )}
          </>
        )}
      </main>
    </div>
  )
}

// ─── Sub-componentes ─────────────────────────────────────────────────────────

function SportButton({ sport }: { sport: 'NBA' | 'NFL' }) {
  const { activeSport, setActiveSport } = useAppStore()
  return (
    <button
      onClick={() => setActiveSport(sport)}
      className={`px-3 py-1.5 text-sm font-medium transition-colors ${
        activeSport === sport
          ? 'bg-brand-600 text-white'
          : 'bg-transparent text-gray-400 hover:text-gray-200'
      }`}
    >
      {sport}
    </button>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color: 'gray' | 'purple' | 'blue' | 'green' }) {
  const colors = {
    gray:   'border-gray-700 text-gray-300',
    purple: 'border-purple-700 text-purple-300',
    blue:   'border-blue-700 text-blue-300',
    green:  'border-green-700 text-green-300',
  }
  return (
    <div className={`bg-gray-900 border rounded-xl p-4 ${colors[color]}`}>
      <p className="text-xs text-gray-500 uppercase tracking-wider">{label}</p>
      <p className="text-3xl font-bold mt-1">{value}</p>
    </div>
  )
}

function LoadingDots() {
  return <span className="animate-pulse mr-2">⋯</span>
}
