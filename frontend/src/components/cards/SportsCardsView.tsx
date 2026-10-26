/**
 * SportsCardsView — Módulo de Sports Card Analytics & Arbitraje de Mercado.
 * Modela activos coleccionables (NBA / NIL NCAA), valoración hedónica,
 * curvas de depreciación con series de tiempo (Prophet) y detección de anomalías de precio (EV+).
 */
import { useState } from 'react'
import { Calculator, AlertTriangle, TrendingUp, Zap, X, Layers } from 'lucide-react'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import {
  useCardsCatalog,
  useCardTrajectory,
  useEvaluateCard,
} from '@/hooks'
import {
  type SportsCardItem,
  type CardEvaluationResponse,
} from '@/services/api'
import { getApiErrorMessage } from '@/utils/errors'

export function SportsCardsView() {
  const [filterType, setFilterType] = useState<string>('all')
  const [selectedCardForTrajectory, setSelectedCardForTrajectory] = useState<SportsCardItem | null>(null)
  const [showValuationModal, setShowValuationModal] = useState(false)

  // ─── Custom Hooks (Lógica desacoplada) ───────────────────────────────────────
  const catalogQuery = useCardsCatalog(filterType)
  const trajectoryQuery = useCardTrajectory(selectedCardForTrajectory?.id)

  const cards = catalogQuery.data?.cards ?? []
  const arbitrageCards = cards.filter(c => c.ev_opportunity || c.ev_percentage > 15.0)

  return (
    <div className="space-y-8">
      {/* ─── Header & Introducción ────────────────────────────────────────── */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <Layers className="h-5 w-5 text-brand-400" /> Sports Card Analytics & Valuación Hedónica
            </h2>
            <p className="text-sm text-gray-400 mt-1 max-w-3xl">
              Modelado cuantitativo de activos coleccionables (NBA & NCAA NIL).
              Monitoreo del <strong>Efecto Uniforme</strong> (descuento colegial vs NBA oficial),
              análisis de series temporales con Prophet y arbitraje automatizado en mercados secundarios.
            </p>
          </div>
          <button
            onClick={() => setShowValuationModal(true)}
            className="px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-lg text-xs font-semibold transition-colors flex items-center gap-1.5"
          >
            <Calculator className="h-4 w-4" />
            <span>Calculadora de Fair Value</span>
          </button>
        </div>
      </div>

      {/* ─── Banner de Arbitraje / Oportunidades EV+ ────────────────────────── */}
      {arbitrageCards.length > 0 && (
        <section className="bg-gradient-to-r from-emerald-950/40 via-gray-900 to-teal-950/40 border border-emerald-800/40 rounded-xl p-5 space-y-3">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-emerald-400" />
            <h3 className="text-sm font-bold text-emerald-400 uppercase tracking-wider">
              Anomalías de Precio Detectadas (Oportunidades de Arbitraje EV+)
            </h3>
          </div>
          <p className="text-xs text-gray-400">
            Cartas donde el precio listado en mercado es inferior al Fair Market Value neto de comisiones (eBay 13% + envíos):
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 pt-2">
            {arbitrageCards.map(c => (
              <div key={c.id} className="bg-gray-900/80 border border-emerald-800/60 rounded-lg p-3 text-xs flex justify-between items-center">
                <div>
                  <span className="font-semibold text-white block">{c.player_name}</span>
                  <span className="text-gray-400 text-[11px]">{c.brand} {c.parallel_name} ({c.grade})</span>
                </div>
                <div className="text-right font-mono">
                  <span className="text-gray-400 block line-through">
                    {c.fair_market_value != null && Number.isFinite(c.fair_market_value) ? `$${c.fair_market_value.toFixed(0)} FMV` : '—'}
                  </span>
                  <span className="text-emerald-400 font-bold">
                    {c.current_market_price != null && Number.isFinite(c.current_market_price) ? `$${c.current_market_price.toFixed(0)}` : '—'}{' '}
                    {c.ev_percentage != null && Number.isFinite(c.ev_percentage) && c.ev_percentage > 0 ? `(+${c.ev_percentage.toFixed(0)}%)` : ''}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ─── Catálogo de Cartas ────────────────────────────────────────────── */}
      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h3 className="text-base font-semibold text-gray-200">
            Catálogo Monitoreado (Prospectos Clave NBA & NCAA)
          </h3>
          <div className="flex gap-2">
            {[
              { id: 'all', label: 'Todas' },
              { id: 'Rookie Card', label: 'Rookie Cards NBA' },
              { id: 'NIL', label: 'NCAA NIL' },
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setFilterType(tab.id)}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  filterType === tab.id
                    ? 'bg-purple-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {catalogQuery.isLoading && (
          <div className="text-center py-12 text-gray-500 animate-pulse">
            Cargando catálogo cuantitativo de tarjetas...
          </div>
        )}

        {catalogQuery.isError && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-950/20 p-6 text-center text-xs text-rose-300" role="alert">
            <p className="font-semibold text-rose-200">Error al consultar el catálogo de tarjetas</p>
            <p className="mt-1 text-rose-300/80">{getApiErrorMessage(catalogQuery.error)}</p>
          </div>
        )}

        {!catalogQuery.isLoading && !catalogQuery.isError && cards.length === 0 && (
          <div className="rounded-xl border border-gray-800 bg-gray-900/50 p-8 text-center text-xs text-gray-500" role="status">
            No hay tarjetas registradas para el filtro seleccionado.
          </div>
        )}

        {!catalogQuery.isLoading && !catalogQuery.isError && cards.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {cards.map(card => (
              <CardItem
                key={card.id}
                card={card}
                onInspectTrajectory={() => setSelectedCardForTrajectory(card)}
              />
            ))}
          </div>
        )}
      </section>

      {/* ─── Modal de Trayectoria / Curva Prophet ───────────────────────────── */}
      {selectedCardForTrajectory && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-2xl max-w-3xl w-full p-6 space-y-5 shadow-2xl">
            <div className="flex items-start justify-between border-b border-gray-800 pb-4">
              <div>
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                  selectedCardForTrajectory.card_type === 'NIL' ? 'bg-amber-950 text-amber-300 border border-amber-800' : 'bg-purple-950 text-purple-300 border border-purple-800'
                }`}>
                  {selectedCardForTrajectory.card_type} · {selectedCardForTrajectory.grade}
                </span>
                <h3 className="text-lg font-bold text-white mt-1">
                  {selectedCardForTrajectory.player_name} — {selectedCardForTrajectory.brand} {selectedCardForTrajectory.parallel_name}
                </h3>
                <p className="text-xs text-gray-400">
                  {selectedCardForTrajectory.card_type === 'NIL'
                    ? 'Curva de depreciación esperada por rotación hacia cartas oficiales con uniforme NBA'
                    : 'Proyección de precio a 90 días modelada con Prophet (Time-Series)'}
                </p>
              </div>
              <button
                onClick={() => setSelectedCardForTrajectory(null)}
                className="text-gray-400 hover:text-white p-1"
                title="Cerrar"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Gráfico Recharts */}
            <div className="h-64 w-full">
              {trajectoryQuery.isLoading ? (
                <div className="h-full flex items-center justify-center text-xs text-gray-500 animate-pulse">
                  Generando proyección con Prophet...
                </div>
              ) : trajectoryQuery.isError ? (
                <div className="h-full flex items-center justify-center text-xs text-rose-300">
                  {getApiErrorMessage(trajectoryQuery.error)}
                </div>
              ) : trajectoryQuery.data ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={trajectoryQuery.data.trajectory_points}>
                    <defs>
                      <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.4} />
                        <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0.0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.5} />
                    <XAxis
                      dataKey="date"
                      tickFormatter={d => d.slice(5)}
                      stroke="#9ca3af"
                      fontSize={10}
                    />
                    <YAxis
                      stroke="#9ca3af"
                      fontSize={10}
                      tickFormatter={val => `$${val}`}
                    />
                    <Tooltip
                      contentStyle={{ backgroundColor: '#111827', borderColor: '#374151', borderRadius: '8px', fontSize: '11px' }}
                      formatter={(val: any) => [`$${Number(val).toFixed(2)}`, 'Precio Proyectado']}
                    />
                    <Area
                      type="monotone"
                      dataKey="projected_price"
                      stroke="#a855f7"
                      strokeWidth={2}
                      fillOpacity={1}
                      fill="url(#priceGradient)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-xs text-gray-500">
                  No hay datos suficientes de serie temporal.
                </div>
              )}
            </div>

            <div className="bg-gray-950 p-4 rounded-xl border border-gray-800 text-xs flex justify-between items-center font-mono">
              <div>
                <span className="text-gray-500 block text-[10px]">PRECIO ACTUAL</span>
                <span className="text-white font-bold text-sm">
                  {Number.isFinite(selectedCardForTrajectory.current_market_price) ? `$${selectedCardForTrajectory.current_market_price.toFixed(2)}` : '—'}
                </span>
              </div>
              <div>
                <span className="text-gray-500 block text-[10px]">VALOR JUSTO (FMV)</span>
                <span className="text-purple-400 font-bold text-sm">
                  {Number.isFinite(selectedCardForTrajectory.fair_market_value) ? `$${selectedCardForTrajectory.fair_market_value.toFixed(2)}` : '—'}
                </span>
              </div>
              <div>
                <span className="text-gray-500 block text-[10px]">EFECTO UNIFORME</span>
                <span className="text-amber-400 font-bold text-sm">
                  {selectedCardForTrajectory.card_type === 'NIL' ? '-52% Descuento Colegial' : '0% (Oficial NBA)'}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─── Modal de Calculadora Hedónica ─────────────────────────────────── */}
      {showValuationModal && (
        <CardValuationModal onClose={() => setShowValuationModal(false)} />
      )}
    </div>
  )
}

function CardItem({ card, onInspectTrajectory }: { card: SportsCardItem; onInspectTrajectory: () => void }) {
  const isNIL = card.card_type === 'NIL'
  const isArbitrage = card.ev_opportunity || card.ev_percentage > 15.0

  return (
    <div className={`bg-gray-900 border rounded-xl p-4 shadow-lg flex flex-col justify-between transition-all hover:scale-[1.01] ${
      isArbitrage ? 'border-emerald-600/70 shadow-emerald-950/30' : 'border-gray-800'
    }`}>
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
            isNIL ? 'bg-amber-950 text-amber-300 border border-amber-800' : 'bg-purple-950 text-purple-300 border border-purple-800'
          }`}>
            {card.card_type}
          </span>
          <span className="text-[11px] font-mono bg-gray-800 text-gray-300 px-2 py-0.5 rounded">
            {card.grade}
          </span>
        </div>

        <h4 className="font-bold text-white text-sm">{card.player_name}</h4>
        <p className="text-xs text-gray-400 mt-0.5">{card.manufacturer} {card.brand} {card.parallel_name}</p>

        {isArbitrage && (
          <div className="mt-2 inline-flex items-center gap-1 text-[10px] font-bold text-emerald-400 bg-emerald-950/60 border border-emerald-800 px-2 py-0.5 rounded">
            <AlertTriangle className="h-3 w-3" />
            <span>ARBITRAJE EV+ ({Number.isFinite(card.ev_percentage) && card.ev_percentage > 0 ? `+${card.ev_percentage.toFixed(0)}%` : ''})</span>
          </div>
        )}
      </div>

      <div className="mt-4 pt-3 border-t border-gray-800 space-y-2">
        <div className="flex justify-between items-center text-xs font-mono">
          <span className="text-gray-400">Precio Mercado:</span>
          <span className="text-white font-bold">
            {Number.isFinite(card.current_market_price) ? `$${card.current_market_price.toFixed(0)}` : '—'}
          </span>
        </div>
        <div className="flex justify-between items-center text-xs font-mono">
          <span className="text-gray-400">Fair Value:</span>
          <span className="text-purple-400 font-bold">
            {Number.isFinite(card.fair_market_value) ? `$${card.fair_market_value.toFixed(0)}` : '—'}
          </span>
        </div>

        <button
          onClick={onInspectTrajectory}
          className="w-full mt-2 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5"
        >
          <TrendingUp className="h-3.5 w-3.5" />
          <span>Ver Curva de Precios</span>
        </button>
      </div>
    </div>
  )
}

function CardValuationModal({ onClose }: { onClose: () => void }) {
  const [player, setPlayer] = useState("Victor Wembanyama")
  const [cardType, setCardType] = useState("Rookie Card")
  const [grade, setGrade] = useState("PSA 10")
  const [parallel, setParallel] = useState("Silver")
  const [auto, setAuto] = useState("None")
  const [listingPrice, setListingPrice] = useState(1200)
  const [evalResult, setEvalResult] = useState<CardEvaluationResponse | null>(null)

  const mutation = useEvaluateCard()

  const handleEvaluate = () => {
    mutation.mutate(
      {
        player_name: player,
        card_type: cardType,
        grade,
        parallel_name: parallel,
        autograph_type: auto,
        listing_price: listingPrice,
      },
      {
        onSuccess: setEvalResult,
      }
    )
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl max-w-xl w-full p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-800 pb-3">
          <h3 className="text-base font-bold text-white flex items-center gap-2">
            <Calculator className="h-4 w-4 text-emerald-400" />
            <span>Calculadora Hedónica de Fair Value</span>
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white p-1" title="Cerrar">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <label className="text-gray-500 uppercase block mb-1">Jugador</label>
            <select
              value={player}
              onChange={e => setPlayer(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white"
            >
              <option value="Victor Wembanyama">Victor Wembanyama</option>
              <option value="Cooper Flagg">Cooper Flagg (NIL)</option>
              <option value="Chet Holmgren">Chet Holmgren</option>
              <option value="Luka Doncic">Luka Doncic</option>
              <option value="Stephon Castle">Stephon Castle</option>
            </select>
          </div>

          <div>
            <label className="text-gray-500 uppercase block mb-1">Tipo de Carta</label>
            <select
              value={cardType}
              onChange={e => setCardType(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white"
            >
              <option value="Rookie Card">Rookie Card (NBA Oficial)</option>
              <option value="NIL">NIL (Colegial NCAA)</option>
              <option value="Veteran">Veteran NBA</option>
            </select>
          </div>

          <div>
            <label className="text-gray-500 uppercase block mb-1">Graduación</label>
            <select
              value={grade}
              onChange={e => setGrade(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white"
            >
              <option value="Raw">Raw (Sin Graduar)</option>
              <option value="PSA 9">PSA 9 Mint</option>
              <option value="PSA 10">PSA 10 Gem Mint</option>
              <option value="BGS 9.5">BGS 9.5</option>
            </select>
          </div>

          <div>
            <label className="text-gray-500 uppercase block mb-1">Paralelo / Rareza</label>
            <select
              value={parallel}
              onChange={e => setParallel(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white"
            >
              <option value="Base">Base</option>
              <option value="Silver">Silver / Refractor</option>
              <option value="Purple /99">Purple /99</option>
              <option value="Gold /10">Gold /10</option>
              <option value="1/1 Nebula">1/1 One of One</option>
            </select>
          </div>

          <div>
            <label className="text-gray-500 uppercase block mb-1">Autógrafo</label>
            <select
              value={auto}
              onChange={e => setAuto(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white"
            >
              <option value="None">Sin Firma</option>
              <option value="Sticker">Sticker Auto</option>
              <option value="On-Card">On-Card Auto</option>
            </select>
          </div>

          <div>
            <label className="text-gray-500 uppercase block mb-1">Precio Listado ($)</label>
            <input
              type="number"
              value={listingPrice}
              onChange={e => setListingPrice(Number(e.target.value))}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white font-mono"
            />
          </div>
        </div>

        <button
          onClick={handleEvaluate}
          disabled={mutation.isPending}
          className="w-full py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-lg text-xs font-semibold transition-colors flex items-center justify-center gap-1.5"
        >
          {mutation.isPending ? (
            'Calculando Valoración...'
          ) : (
            <>
              <Zap className="h-3.5 w-3.5" />
              <span>Calcular Fair Value</span>
            </>
          )}
        </button>

        {evalResult && (
          <div className="bg-gray-950 p-4 rounded-xl border border-gray-800 space-y-2 text-xs font-mono">
            <div className="flex justify-between">
              <span className="text-gray-400">Valor Justo Estimado (FMV):</span>
              <span className="text-purple-400 font-bold text-sm">
                {evalResult.valuation?.fair_market_value != null && Number.isFinite(evalResult.valuation.fair_market_value)
                  ? `$${evalResult.valuation.fair_market_value.toFixed(2)}`
                  : '—'}
              </span>
            </div>
            {evalResult.arbitrage_analysis && (
              <>
                <div className="flex justify-between">
                  <span className="text-gray-400">Retorno Neto Proyectado:</span>
                  <span className={`font-bold ${(evalResult.arbitrage_analysis.expected_profit_dollars ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {Number.isFinite(evalResult.arbitrage_analysis.expected_profit_dollars)
                      ? `$${evalResult.arbitrage_analysis.expected_profit_dollars.toFixed(2)}`
                      : '—'}{' '}
                    ({Number.isFinite(evalResult.arbitrage_analysis.roi_percentage)
                      ? `${evalResult.arbitrage_analysis.roi_percentage.toFixed(1)}%`
                      : '—'})
                  </span>
                </div>
                <div className="p-2 bg-gray-900 rounded border border-gray-800 text-[11px] font-semibold text-center mt-2 text-brand-300">
                  {evalResult.arbitrage_analysis.recommendation}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
