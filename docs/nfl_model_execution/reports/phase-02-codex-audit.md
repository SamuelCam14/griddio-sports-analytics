# Auditoría de aceptación Codex — Fase 02

**Fecha:** 2026-10-11  
**Dictamen:** `review`; aceptación rechazada. No iniciar Fase 03.  
**Alcance:** cotejo con `02-injury-point-in-time.md`, revisión estática y reproducción de la suite focalizada. Sin llamadas a proveedores externos.

## Evidencia

- `docker exec -e PYTHONPATH=/app sports_api pytest -q tests/test_nfl_availability_phase02.py` → **22 passed in 26.43s**.
- El verde no valida las aserciones de dominio: varias pruebas codifican números ficticios y no comprueban la conexión con datos reales, el corte exacto ni la paridad training/inference.

## Hallazgos bloqueantes

### P0 — La temporalidad live no falla cerrado

- En `nfl_availability_service.py`, `process_player_injury` fabrica `observed_at` con la hora actual o lo retrocede al `decision_cutoff` cuando ESPN no proporciona timestamp. Un dato sin procedencia temporal queda falsamente fechado como pre-corte.
- `resolve_availability_status` valida timestamp solo cuando se especificó cutoff; acepta igualdad, aunque se exige observación estrictamente anterior, y acepta estado sin timestamp si también falta cutoff.
- `get_live_injuries` no preserva ESPN athlete ID ni `fetched_at`/`observed_at` verificables por registro. El cache ESPN es mutable, no un snapshot auditable. El mapeo por nombre no satisface identidad canónica.
- **Corrección:** quitar timestamps sintéticos; persistir `fetched_at` real y observación upstream real; timestamp faltante, igual o posterior al cutoff ⇒ `unknown`; requerir cutoff/kickoff por juego; conservar ESPN ID y resolver canonical ID o `unknown`.

### P0 — Player props no están integradas y se fabrican registros de sportsbook

- `fantasy_injury_service.py` transforma `active_prop_players` en props con DraftKings, `receiving_yards` y línea 50.5 sin respuesta verificable del proveedor.
- `inference.py` llama el servicio sin props reales ni kickoff por partido. No hay consulta ni snapshot real de player props en esta ruta.
- `evaluate_market_prop_signal` puede marcar `checked_props: true` cuando no recibió mercado alguno.
- Los tests usan listas fake en memoria; no validan event ID, bookmaker/market, timestamp pre-corte, cobertura real ni cuota.
- **Corrección:** borrar síntesis ficticia; consumir props verificables ya persistidas por Fase 01 o responder `unknown` con `checked_props: false`. Exigir cobertura de evento/casa/mercado y snapshot anterior al cutoff. No consultar históricos ni OUT y respetar ledger.

### P0 — Fallas y cobertura vacía se convierten en cero

- `inference.py` inicializa features en `0.0`, usa defaults 0.0 por equipo y captura excepciones conservando esos ceros.
- `FantasyInjuryService` devuelve métricas cero ante listas vacías; una lista vacía puede indicar error o falta de cobertura. El cache fallback no está ligado a game_id y cutoff exacto.
- `matchup_context_service` cae a heurísticas si el servicio falla; lista de lesiones vacía da 0.0. `sanitize_for_json` convierte NaN/Inf a cero.
- **Corrección:** diferenciar `covered_no_injuries`, `unknown` y `unavailable/no_report`; no imputar neutralidad. Propagar cobertura por juego y abstenerse o usar explícitamente un modelo entrenado sin estas features.

### P1 — El builder histórico acepta datos desconocidos y no aplica decision cutoff

- `injury_feature_builder.py` construye kickoff mediante `(season, week, team)` y elimina duplicados, lo que puede asociar un kickoff arbitrario. Su máscara aprueba timestamp o kickoff ausente: `inj_dm.isna() OR game_ko.isna() OR inj_dm < game_ko`.
- El plan exige `observed_at < decision_cutoff`. El builder compara con kickoff y no aplica el horizonte inicial de dos horas; admite información que llegó dentro de la ventana excluida.
- El builder rellena estadísticas e impactos agregados con cero incluso cuando rol/historial no fueron observados.
- **Corrección:** unión inequívoca por partido; aplicar cutoff configurable (dos horas inicial); timestamp/kickoff faltantes, join ambiguo, identidad o rol faltantes deben ser `unknown/unavailable`, nunca cero. Mismo schema y transformación en training e inference.

### P1 — El impacto usa constantes manuales sin estadísticas individuales reales

- `compute_team_production_lost` recibe `prior_roster_stats`, pero sus callers no los entregan. Usa fallback fijo de 15 PPR, 0.80 snap-share por rol y denominador PPR 100.
- Esto contradice los pasos 9–10: no mide rol/replacement ni valor defensivo/QB/trenches y produce cifras con apariencia de observación real.
- **Corrección:** derivar solo de datos previos verificados y denominadores definidos; sin rol/denominador suficiente, `unknown/unavailable`. Separar dimensiones de contribución.

### P1 — El Brier no prueba calibración

- `audit_nfl_availability.py` asigna probabilidades manuales por status y puntúa la misma muestra, sin holdout temporal ni horizonte de dos horas. No entrenó ni calibró un modelo.
- Brier global mapea `Note` a 0.50. `did_play` se forma por `(season, week, player_id)`, no por game/team; ausencia de fila de snap/identidad se vuelve `False` aunque podría ser falta de cobertura. Auditoría temporal compara con kickoff y excluye timestamps faltantes del denominador.
- 0.1429 no es evidencia de calibración o ventaja. Los conteos reportados no validan la feature pregame.
- **Corrección:** asociar labels a partido/equipo inequívocos; distinguir no participación de label faltante; auditar con cutoff real; separar baseline descriptivo de evaluación de modelo; validar por corte cronológico/out-of-fold. Retirar claims de calibración hasta entonces.

### P1 — Las pruebas no cubren el contrato integral de backend SSOT

- El test de `FantasyInjuryService` omite cutoff, kickoff, `observed_at`, ESPN ID y canonical ID, pero espera impacto positivo. Pasa gracias a timestamp y defaults sintéticos.
- No hay prueba de que inference pase cutoff por juego, se abstenga ante excepción/cobertura incompleta o que props live estén presupuestadas y snapshots históricos no disparen requests.
- **Corrección:** integración hermética que falle ante timestamps/IDs/cutoff faltantes; pruebe abstención, snapshot por juego y cuota: cero requests en históricos/OUT y débito solo para consulta live autorizada.

## Criterio de cierre

Corregir todos los P0/P1 o aislar el componente por completo de cualquier consumo del modelo hasta que cumpla el contrato. Actualizar informe y `EXECUTION_STATE.md`; ejecutar suites focalizada, combinada y regresión; comparar hash del ledger antes/después; solicitar revalidación independiente de Gemini. Hasta entonces: Fase 02 `review`, producción cerrada, Fase 03 `pending`.

---

## Reauditoría de la entrega corregida v2 — 2026-10-11

**Resultado:** mejora parcial; Fase 02 continúa sin aceptación. Codex reprodujo `docker exec -e PYTHONPATH=/app sports_api pytest -q tests/test_nfl_availability_phase02.py` → **30 passed in 29.42s**. Hash ledger antes/después: `13E43E0C675CF6C17946E3B3E03DF639DBE2E96AB0786680C4A6D37C9AB028BE`, sin cambio. Sin llamadas externas.

### Resuelto en código inspeccionado

- `resolve_availability_status` exige timestamp y cutoff, y rechaza igualdad/posterior.
- Se eliminó la generación de líneas falsas DraftKings/50.5.
- El cálculo de impacto deja métricas `None` si faltan estadísticas previas, y la inferencia puede abstenerse.
- La auditoría ahora llama al Brier descriptivo y aclara que no representa calibración.

### Bloqueos remanentes

1. **Inference no pasa contexto por partido:** `backend/ml/inference.py` llama una vez para toda la cartelera y envía solo un cutoff común. No manda `game_id`, kickoff, props ni crosswalk. Si `as_of` existe, se usa sin validar el cutoff de cada juego. Si falta fecha del juego, usa `datetime.now()` como cutoff. La función del servicio es singular por juego, pero inference la usa para varios juegos.
2. **ESPN-GSIS no está conectado:** el parser conserva `espn_id`, pero no obtiene GSIS/canonical ID; el caller real no pasa `crosswalk`. Como el motor exige canonical ID, reportes reales quedan `unknown` y las lesiones causan abstención. Es fail-closed, pero no hay ruta funcional integrada.
3. **Cache/fetch provenance no es confiable:** el parser usa `item.date` antes de `dateModified` como `observed_at` sin demostrar que sea hora de última modificación. `process_player_injury` reemplaza `fetched_at` del registro por hora actual; el fallback de red puede usar cache sin límite de antigüedad. No se comprueba que fetch y observación fueran conocidos al cutoff.
4. **Props incompletas o de otro partido pasan:** `evaluate_market_prop_signal` no requiere precio; timestamp ausente pasa porque solo rechaza cuando `p_ts` existe y es posterior. No compara `event_id` con el evento evaluado ni exige canonical player ID; cobertura comparable puede corresponder a otro evento. `inference.py` no proporciona props.
5. **Unknown histórico sigue siendo cero:** `injury_feature_builder.py` asigna `p_absence=0.0` a observaciones inválidas/sin status, las elimina de `at_risk_injuries` y termina con `final_df.fillna(0.0)`. El dataset interpreta información ausente/no verificada como equipo sano.
6. **Ground truth no demuestra integridad de schedule/snap coverage:** `build_nfl_availability_ground_truth` y el audit usan `drop_duplicates((season, week, team))` en vez de rechazar schedule ambiguo. La cobertura de snaps se define por tener al menos una fila de cualquier jugador del equipo; jugador rostered sin fila se etiqueta `did_play=False`. No se prueba que el feed tenga cobertura exhaustiva para todos los jugadores. El reporte temporal no contabiliza kickoff/cutoff nulos: 28,744 total menos 28,709 pre-cutoff, 1 en ventana, 17 post-kickoff y 0 timestamps faltantes deja 17 filas sin categoría.

### Siguiente acción

No iniciar Fase 03. Corregir la integración por partido, procedencia/frescura, identidad ESPN→GSIS, validación de props por evento con precio y timestamp obligatorios, y tratamiento unknown en entrenamiento. Para ground truth, probar completitud del proveedor antes de inferir no participación. Mantener producción cerrada y pedir nueva reauditoría Codex.

---

## Correcciones directas Codex — 2026-10-11

**Estado:** implementación correctiva parcial; la Fase 02 sigue en `review` y no se acepta aún. No se habilitó producción ni se hicieron llamadas live a Odds API.

### Corregido en esta iteración

1. **Contexto de inferencia por juego:** `build_matchup_features` conserva kickoff/temporada/semana; `predict_matchups` calcula cutoff a partir del kickoff de cada partido, exige la ventana de dos horas y pasa `game_id`, kickoff, crosswalk ESPN→GSIS no ambiguo y los dos equipos esperados al servicio. No reutiliza `as_of` como cutoff de lesión.
2. **Identidad y provenance:** el parser ESPN deja de tratar `date` como timestamp de modificación, conserva `espn_id` y `fetched_at`; `process_player_injury` preserva el fetch original y resuelve canonical ID usando ESPN ID. El fallback de caché ESPN rechaza caché de más de dos horas. La ruta NFL dejó de leer/escribir resúmenes de availability antiguos desligados del contexto de la ejecución.
3. **Props verificables:** las props normalizadas por `fetch_real_player_props` ahora incluyen `event_id`, timestamp de captura y precio. El evaluador exige evento esperado, bookmaker, mercado, línea, precio válido y timestamp anterior al cutoff; eventos de otro partido o datos incompletos quedan `unknown`. Una designación Questionable/Doubtful sin señal de mercado verificable produce métricas `None`, no la expectativa base como feature observada.
4. **Desconocido histórico:** los snapshots faltantes de stats/snaps no se rellenan con cero; el builder histórico solo emite availability si los registros del equipo tienen estado/corte/identidad verificables, y el entrenamiento descarta juegos sin availability completa. Se filtran temporadas solo cuando manifest tiene los cuatro assets requeridos.
5. **Ground truth:** schedule duplicado o kickoff ausente ahora detiene el cálculo. Un jugador sin fila individual en snaps se etiqueta `missing_snap_record`, nunca `did_play=False`. El reporte deja Brier/rate en `null` cuando no hay muestra y la prueba ya no impone porcentajes empíricos como si fueran calibración.
6. **Cobertura de fuente live:** la inferencia requiere entrada explícita de ESPN para ambos equipos; un equipo omitido no es considerado sano. Solo procesa los bloques abreviados esperados, evitando duplicar equipos por alias de nombre.

### Evidencia reproducida

- Suite focalizada combinada — `docker exec -e PYTHONPATH=/app sports_api pytest -q tests/test_nfl_availability_phase02.py tests/test_prediction_gate.py tests/test_temporal_pipeline.py tests/test_parlay.py tests/test_nfl_data_phase01.py` → **119 passed**.
- Regresión operativa — `docker exec -e PYTHONPATH=/app sports_api pytest -q -k "not test_exhaustive_idempotency and not test_exhaustive_multiple"` → **167 passed, 2 deselected**.
- Smoke de `build_training_datasets.build("nfl")` aislado en `/tmp` (sin reemplazar dataset productivo): **110 de 4,398** partidos conservaron disponibilidad verificable; **4,288** fueron descartados. Esto evita fabricar ceros, pero es insuficiente para afirmar robustez o entrenar el artefacto de producción.
- Auditoría actualizada de temporadas 2020–2024: **28,744** reportes; Brier descriptivo **0.0639**, no de calibración; la muestra pre-cutoff efectiva fue 1 `Out`, 6 `Doubtful`, 4,068 `Questionable` y 11,367 sin designación. Debe leerse como diagnóstico de cobertura/selección, no como habilidad predictiva.
- Ledger antes/después: SHA-256 `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`; 275 créditos restantes; cero llamadas externas en esta iteración.

### Bloqueos que siguen abiertos

1. **Props no llegan a inferencia:** `fetch_real_player_props` está normalizado y presupuesto por Fase 01, pero `predict_matchups`/Celery aún no resuelven el `Game.external_id` de The Odds API ni consultan/inyectan props por el sobre `props`. Por diseño, Q/D queda `unknown` y la inferencia se abstiene. No se debe declarar que la comparación ESPN–Vegas ya opera de punta a punta.
2. **Priors individuales live no se cargan:** la inferencia no entrega `prior_roster_stats`; cualquier equipo con lesiones queda `unknown`. Falta una función de priors por roster con snapshots actuales disponibles y corte semanal estricto. El manifest cargado solo incluye stats/snaps/injuries/rosters 2020–2024, no la temporada live 2026.
3. **Muestra histórica pequeña:** el filtro estricto conserva 110/4,398 partidos en el smoke. No reentrenar ni aprobar producción hasta resolver cobertura de la fuente y validar el dataset cronológicamente.

Estos bloqueos son reales, no se deben ocultar con defaults ni gastar créditos en una prueba live. Gemini debe realizar ahora una revisión independiente, solo lectura, del diff y evidencia: intentar encontrar falsos ceros, leakage, errores en joins/tiempos, límites del ledger y discrepancias en los tres bloqueos enumerados; no iniciar Fase 03 ni activar producción. Si detecta correcciones, reportar pasos y archivos exactos a Codex para implementarlos.

---

## Correcciones adicionales Codex tras reauditoría Gemini — 2026-10-11

**Dictamen:** Fase 02 sigue en `review`; no aceptar ni iniciar Fase 03. Producción permanece cerrada y no se consumió cuota.

### Cambios contrastados y aplicados

1. **Impacto histórico según posición:** `injury_feature_builder.py` ya no exige PPR para OL/defensa ni estadísticas de defensa para posiciones ofensivas. Solo asigna cero estructural a una dimensión que la posición demuestra no aplicable; una dimensión aplicable sin historia permanece desconocida e invalida ese team-week. Posiciones sin identidad se excluyen de denominadores; posición desconocida no se trata como cero.
2. **Ventana de inferencia:** `predict_matchups` rechaza un kickoff ya iniciado antes de leer lesiones/props, y solo permite availability live con `as_of` de ejecución actual. `build_matchup_features` permite juego el mismo día solo si hay `game_start_utc` verificable posterior al corte temporal; sigue rechazando juego iniciado/completado y targets same-day sin kickoff.
3. **Ground truth sin fuente de inactivos:** los Parquet de schedules presentes tienen 46 columnas y no contienen inactive lists. `snap_counts` tampoco aporta una fila por jugador rostered que garantice cero participación. Por tanto, falta de fila sigue siendo `did_play=None / missing_snap_record`; no se añadieron falsos negativos. El proyecto aún necesita una fuente oficial/validada de inactivos o cobertura exhaustiva para etiquetar estos casos como `False`.
4. **Cobertura de reporte:** las semanas/equipos sin filas en `injuries` continúan `unavailable_no_report`; el builder no inventa un reporte sano a partir de una ausencia. Solo un conjunto de filas completo, con fecha/identidad/estado verificables, habilita el cálculo.

### Validación

- `build_nfl_historical_injury_features([2020, 2021, 2022, 2023, 2024])`: **1,022 team-weeks** con features observadas después del ajuste por rol.
- Cruce sobre el dataset temporal NFL aislado: **184/4,398 juegos** con availability completa para ambos equipos; **4,214** se excluyen. La muestra sigue siendo demasiado pequeña para validar un modelo robusto; no se entrenó ni se reemplazó ningún dataset.
- Suite combinada (`test_nfl_availability_phase02.py`, `test_prediction_gate.py`, `test_temporal_pipeline.py`, `test_parlay.py`, `test_nfl_data_phase01.py`): **121 passed**.
- Regresión operativa (`-k "not test_exhaustive_idempotency and not test_exhaustive_multiple"`): **169 passed, 2 deselected**.
- Ledger antes/después: SHA-256 `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`; saldo local **275**, cero llamadas externas.

### Bloqueos aún abiertos

1. Props reales no fluyen desde `Game.external_id` hacia inference y la consulta presupuestada por el sobre `props`; Questionable/Doubtful permanece desconocido y debe abstenerse.
2. Inference no construye/inyecta `prior_roster_stats`; además, los snapshots disponibles llegan a 2024 y no representan roster/stats de la temporada actual 2026.
3. La fuente local no permite convertir en falsos negativos los casos sin snap row ni certificar team-weeks sin reporte de lesión.
4. Solo 184 de 4,398 juegos del dataset temporal tienen availability completa para ambos equipos. Requiere resolver cobertura/fuentes y revalidar cronológicamente antes de entrenar/aprobar.

Solicitada revalidación independiente de Gemini, solo lectura. Hasta resolver los bloqueos no se avanza a Fase 03 ni se realizan consultas live pagadas.

---

## Revisión del reporte Gemini: SportsDataverse y labels de inactividad — 2026-10-11

**Dictamen:** la conclusión útil se confirma parcialmente, pero la Ruta B es preferible para el histórico local. No se necesita integrar el endpoint ESPN Core para los seasons ya descargados. La muestra de cinco partidos reportada por Gemini no se acepta como prueba de cobertura exhaustiva: no adjunta capturas raw ni script reproducible y no valida temporadas anteriores.

### Verificación local y corrección aplicada

- Los Parquet locales `rosters_weekly` de 2020–2024 sí contienen el campo `status`; el caso Travis Kelce, KC, week 1 de 2023 aparece como `INA`. `nflverse` documenta `INA` como roster status inactivo. [Diccionario nflverse](https://py.sportsdataverse.org/docs/nfl/reference/loaders/other)
- Se corrigió el armado de roster keys para eliminar nulos antes de formar tuplas e incluir `(season, week, team, player_id)`. El cálculo anterior alineaba columnas de longitudes diferentes y no validaba equipo, lo que inflaba los jugadores “unrostered”.
- El ground truth ahora permite `did_play=False` cuando no existe fila en `snap_counts` **y** el mismo jugador/equipo/semana tiene `status == INA`. No generaliza esa inferencia a `ACT`, `RES`, ausencia de roster ni ausencia de fila. Si INA contradice snaps positivos, detiene la auditoría con error de calidad.
- En las 13,390 observaciones con designación de lesión y timestamp pre-cutoff (2020–2024): **5,093** están observadas por snaps, **6,799** tienen negativo explícito INA y **1,498** permanecen unknown por falta de evidencia suficiente. Labels observados: 11,892/13,390 (88.8%).
- La auditoría completa regenerada registra 28,744 filas; 7,288 `observed_inactive_roster`, 2,183 `missing_snap_record`, y Brier descriptivo 0.128. Es descriptivo, no mide calibración ni ventaja, y mezcla estados que deben analizarse por cobertura/tiempo.

### Evidencia reproducida

- `docker exec -e PYTHONPATH=/app sports_api pytest -q tests/test_nfl_availability_phase02.py` → **37 passed**.
- Suites combinadas (`test_prediction_gate.py`, `test_temporal_pipeline.py`, `test_parlay.py`, `test_nfl_data_phase01.py`, `test_nfl_availability_phase02.py`) → **122 passed**.
- Regresión `docker exec -e PYTHONPATH=/app sports_api pytest -q -k "not test_exhaustive_idempotency and not test_exhaustive_multiple"` → **170 passed, 2 deselected**.
- Ledger SHA-256 se mantiene `13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be`, saldo 275; cero llamadas externas.

### Bloqueos vigentes

La corrección mejora labels históricos, pero no cierra Fase 02: continúan sin integración real de props desde event ID, priors actuales por jugador, cobertura integral de reportes pregame y validación temporal suficiente. El smoke documentado sigue en 184/4,398 partidos con disponibilidad completa para ambos equipos. `production_enabled` permanece `false`; Fase 03 no inicia.
