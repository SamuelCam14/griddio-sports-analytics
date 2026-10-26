import { useQuery } from '@tanstack/react-query'
import { TeamLogo } from '@/components/ui/TeamLogo'
import { gamesApi } from '@/services/api'
import { formatMatchDate } from '@/utils/dashboard'

interface StandingsTableCardProps {
  sport: 'NBA' | 'NFL'
}

export function StandingsTableCard({ sport }: StandingsTableCardProps) {
  const query = useQuery({
    queryKey: ['standings', sport],
    queryFn: () => gamesApi.getStandings(sport).then((response) => response.data),
    staleTime: 5 * 60_000,
  })
  const standings = query.data?.standings ?? []

  return (
    <div className="macos-card p-4 sm:p-5">
      <div className="flex items-center justify-between border-b border-white/[0.08] pb-3">
        <h3 className="text-sm font-semibold tracking-tight text-white">{sport} · Récords guardados</h3>
        <span className="text-[10px] font-mono text-slate-500 uppercase">{query.data?.status === 'partial' ? 'PARCIAL' : 'BASE DE DATOS'}</span>
      </div>

      {query.isLoading && <p className="mt-4 text-xs text-slate-400">Consultando resultados guardados…</p>}
      {query.isError && <p className="mt-4 text-xs text-rose-300" role="alert">No se pudieron consultar los récords de la base de datos.</p>}
      {!query.isLoading && !query.isError && standings.length === 0 && (
        <div className="mt-4 rounded-lg border border-white/[0.04] bg-white/[0.02] p-5 text-center text-xs text-slate-400" role="status">
          {query.data?.reason ?? 'No hay resultados finales verificados para construir los récords.'}
        </div>
      )}

      {standings.length > 0 && (
        <>
          <p className="mt-2 text-[10px] text-slate-500">
            Temporada {query.data?.season ?? '—'} · corte {formatMatchDate(query.data?.as_of)} · {query.data?.teams_with_results ?? 0}/{query.data?.teams_in_catalog ?? 0} equipos con resultados.
          </p>
          <div className="mt-2.5 grid grid-cols-[18px_1fr_36px_36px_42px_42px] gap-1 px-2 text-[10px] font-semibold uppercase text-slate-500">
            <span>#</span><span>Equipo</span><span className="text-center">G</span><span className="text-center">P</span><span className="text-center">%</span><span className="text-center">PF</span>
          </div>
          <div className="mt-1 max-h-72 space-y-1 overflow-y-auto">
            {standings.map((team) => (
              <div key={team.team_id} className="grid grid-cols-[18px_1fr_36px_36px_42px_42px] items-center gap-1 rounded-lg px-2 py-1.5 text-xs hover:bg-white/[0.05]">
                <span className="font-mono text-[11px] text-slate-500">{team.position}</span>
                <div className="flex min-w-0 items-center gap-2"><TeamLogo name={team.name} size="sm" /><span className="truncate font-medium text-slate-200">{team.name}</span></div>
                <span className="text-center font-mono text-slate-300">{team.w}</span>
                <span className="text-center font-mono text-slate-400">{team.l}</span>
                <span className="text-center font-mono text-slate-300">{team.pct == null || !Number.isFinite(team.pct) ? '—' : (team.pct * 100).toFixed(1)}</span>
                <span className="text-center font-mono text-slate-300">{team.points_for}</span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[10px] text-slate-500">{query.data?.ranking_note}</p>
        </>
      )}
    </div>
  )
}
