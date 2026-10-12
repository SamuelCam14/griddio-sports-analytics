# Fase 02 — Disponibilidad NFL e injuries point-in-time

## Enmienda de alcance — 2026-10-12

Esta enmienda prevalece sobre cualquier paso o criterio abajo que haga obligatoria availability para todo pick o partido.

- El modelo NFL base es team-only y usa el historial local completo disponible. Availability/lesiones/props son un ajuste independiente y opcional; su ausencia no cancela la probabilidad base.
- Ningún variant recibe valores de lesión ausentes como cero. El variant injury-aware solo se ejecuta con sus inputs; el team-only tiene schema propio de 26 features y no incluye columnas de lesiones.
- Las cachés legacy pueden servir para pruebas exploratorias del parser o como contexto actual débil, pero sus datos sin timestamp no se usan como conocimiento point-in-time histórico ni justifican asumir que un equipo sin registros está sano.
- Para lesiones actuales, usar el tiempo real de recuperación (`retrieved_at`) como captura si lo genera el fetcher. No rebautizarlo como timestamp de observación del proveedor. Si no hay props archivadas, no entrenar una señal histórica de mercado; en vivo, no tener props significa signal unavailable, no “no jugará”. Consultar solo cuestionables/dudosos según ventana de actualización y presupuesto.
- No exigir identidad de Odds API para correr el modelo base. Para comparar EV, vincular las líneas disponibles con schedule por deporte/equipos/kickoff y exigir vínculo único; ambigüedades excluyen solo la comparación de mercado.
- El backtest team-only de este addendum reporta 4,398 juegos y holdout de 824 (Brier 0.2267, AUC 0.6682 para logística). No es evidencia de rentabilidad. Toda ruta queda experimental hasta probar calibración y retorno prospectivo contra cuotas actuales sin vig.

## Objetivo

Representar lo que se sabía sobre los jugadores antes de la decisión. Separar el predictor de disponibilidad del resultado real del partido. Esta fase no modifica NBA.

## Contrato conceptual

- **Feature/información de entrada:** ESPN injury status observado antes del cut-off; props y mercados existentes a la hora de consulta; rol del jugador calculado únicamente con partidos previos.
- **Etiqueta/ground truth histórica:** el boxscore/snap del partido objetivo indica si realmente participó, y puede utilizarse para entrenar/medir un modelo de probabilidad de participación.
- **No válido:** utilizar el boxscore del objetivo para establecer qué creía el modelo antes del kick-off.
- **Desconocido:** reporte ausente, timestamp posterior/desconocido, identidad sin resolver, endpoint incompleto, mercado no abierto o cobertura de casa insuficiente.

## Pasos

1. Trazar actualmente la llamada completa: `inference.py` → `FantasyInjuryService` → `matchup_context_service`/ESPN y The Odds API. Verificar si las props se pasan realmente. El estado actual esperado por auditoría es que `inference.py` llama al servicio sin `active_prop_players` ni `has_active_game_props`; confirmar antes de modificar.
2. Crear registro de reporte por team/player/game: canonical player ID, ESPN ID, team, position, raw status, injury description, source, `observed_at`, `fetched_at` y game kickoff UTC.
3. Aceptar como pregame únicamente snapshots con observación verificable anterior al `decision_cutoff`. Un `date_modified` sin semántica temporal clara no basta para garantizar que una lesión ya se conocía a esa hora.
4. Implementar salida de disponibilidad separada: `confirmed_out`, `questionable`, `doubtful`, `active_report`, `unknown`. No usar un único número sin estado/fuente.
5. Para juegos futuros del slate actual, si ESPN marca `OUT`/IR antes del cutoff, tratarlo como no disponible según el contrato de dominio; no consultar odds/props para “desconfirmar” ese status.
6. Solo para juegos futuros aún no iniciados y con estado `Questionable`/`Doubtful`: tomar snapshot de props en el horizonte definido por configuración (inicial: objetivo 2 horas antes del kickoff, o la ventana de actualización que se valide). Registrar bookmaker(s), market(s), jugador mapeado, existencia de outcome/line, timestamp, precio y si el evento tenía mercados activos.
7. Codificar la señal como `listed_at_cutoff`, `not_listed_with_coverage`, o `unknown`; no convertir `listed` directamente en `will_play=true`. Si no hay mercado abierto, no hay jugador comparable, solo una casa no ofrece ese mercado o el mapeo de nombre no es fiable, el resultado es desconocido.
8. Si se estima `P(plays)`, entrenar un modelo auxiliar con información pregame y etiquetas `did_play` del boxscore. Separar por fecha, usar snapshots históricos auténticos del mismo horizonte, medir Brier/calibración por status/posición/mercado. Nunca crear train input copiando `did_play` del objetivo.
9. Estimar impacto deportivo del equipo con métricas anteriores del jugador: snap-share/rol, fantasy/PPR, posición, roster/depth chart y posible replacement. No multiplicadores manuales constantes hasta medir impacto incremental. Considerar QB y trenches/defensa: PPR por sí solo no representa toda su contribución.
10. Calcular pérdida esperada condicional a la disponibilidad, por ejemplo contribución previa × probabilidad de ausencia, con definición/versionado común train/inference. Validar divisiones por potencial pequeño y datos no observados sin rellenar a cero.
11. Persistir resultado, referencias, cut-off, coverage flags y versión del resolutor. Inference falla cerrado para esa feature o usa explícitamente un modelo entrenado sin ella; nunca manda una observación heurística no presente en training.
12. Auditar temporadas/partidos con report status, modified date, snap data, trade/team changes, bye week, jugador sin snaps y jugador no rostered. Documentar tasas de unknown por motivo.

## Modelo para verificar props como “intención del mercado”

La consulta de odds list para resolver `Questionable`/`Doubtful` pertenece exclusivamente a la operación prepartido actual de encuentros todavía no iniciados. No se llama a Odds API para juegos históricos con el fin de reconstruir si el jugador estaba listado. En entrenamiento histórico, el boxscore/snap del juego objetivo sirve como etiqueta de si participó; solo puede usarse una señal de props como feature si existe un snapshot archivado auténtico, identificado y capturado antes del corte de decisión de ese mismo juego.

La hipótesis del usuario es que las casas tienen mejores señales que el reporte público. Se evaluará, no se codificará como verdad:

- Hipótesis A: tener línea de player prop antes del kickoff incrementa la probabilidad real de participación frente a status report solo.
- Hipótesis B: no tener línea, condicionado a mercado de evento abierto y casa/prop cubrir a comparables, incrementa probabilidad de no participación.
- Test con corte de 2h, segmentos por bookmaker/status/posición, etiqueta boxscore y muestra futura aparte.
- Si B no se calibra o su cobertura es demasiado baja, el predictor usa la señal como “market coverage/listed” o la excluye; no la convierte en OUT.

## Aceptación

- No hay inputs de lesión después del corte.
- Boxscore objetivo aparece solamente como target/evaluación de disponibilidad.
- No hay `fillna(0)`/default neutral para lesión en filas de producción.
- La ruta de inference lee snapshots reales y aplica el mismo schema/transformación que entrenamiento.
- Ante fuente caída, cutoff faltante o player ID ambiguo, response muestra `unknown/unavailable` y se abstiene si el campo es obligatorio.
- Métricas del resolutor y cobertura se evalúan separadas del modelo ganador.

