const unavailableSections = [
  'Plantillas y alineaciones',
  'Lesionados desde la base de datos',
  'Partidos recientes por equipo',
  'Historial cara a cara (H2H)',
]

export function DataAvailabilityPanel() {
  return (
    <section className="surface-card">
      <div>
        <p className="eyebrow">COBERTURA DE DATOS</p>
        <h2 className="section-title">Secciones pendientes</h2>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-400">No se muestran datos de estas categorías porque el backend aún no ofrece una API para consultarlos.</p>
      <ul className="mt-4 space-y-2">
        {unavailableSections.map((section) => (
          <li key={section} className="pending-row"><span aria-hidden="true" className="pending-dot" />{section}</li>
        ))}
      </ul>
    </section>
  )
}
