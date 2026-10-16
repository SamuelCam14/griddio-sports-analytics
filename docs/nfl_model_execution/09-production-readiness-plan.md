# Plan de cierre para producción NFL

**Versión:** 1.0  
**Fecha:** 2026-10-15  
**Estado:** Plan de ejecución; no constituye autorización para abrir el gate.  
**Alcance:** NFL únicamente. NBA permanece fuera de producción durante el receso y hasta que exista temporada activa, datos de calendario/resultados/cuotas verificables y un plan de validación propio.

## 1. Mandato y criterio rector

El producto debe mostrar solo datos con fuente, identidad, hora de observación y cobertura comprobables. Si no hay datos, si no se pueden enlazar inequívocamente, si están vencidos o si falla una dependencia, la respuesta debe indicar ese estado y abstenerse. Está prohibido sustituir ausencias por ceros, promedios, líneas típicas, probabilidades de mercado, nombres, estados de salud, filas de ejemplo, datos de seed o valores estéticos. Los fixtures solo viven en pruebas y deben ser explícitos, deterministas y aislados del runtime.

**No se abre `production_enabled` como parte de las tareas técnicas.** El gate permanece cerrado hasta cumplir todos los criterios de salida de este documento, una revisión independiente y una decisión explícita del responsable del producto. Generar predicciones experimentales no equivale a autorización de recomendar apuestas.

Este plan complementa, no reemplaza, los documentos 00–08. Cuando haya discrepancia, prevalecen el requisito más conservador de veracidad, el estado vigente de `EXECUTION_STATE.md` y el gate cerrado. Quien ejecute debe releer el checkout y no asumir que informes históricos describen el código actual.

## 2. Línea base y límites conocidos

La revisión estática encontró servicios de calendario NFL/NBA que aún son placeholders con resultado `games_synced: 0`; errores de cuotas transformados en respuestas vacías exitosas; lectura de snapshots sin corte de frescura; consulta de props desde GET de picks con excepción silenciada; cálculos de spread/totals que contienen defaults y precios `-110`; enlace de predicción a partido por equipos y fecha con tolerancia de un día; y una huella llamada SHA-256 que usa el `hash()` no criptográfico de Python.

También se encontraron `debug=True`, una clave secreta de desarrollo por defecto y peticiones a Odds API con la clave en query string en rutas cuyos errores se registran. Los modelos revisados no tienen elegibilidad de producción. La documentación reconoce bloqueos de availability NFL, falta de props point-in-time calibradas y fases de validación/revisión pendientes. El repositorio puede contener cambios locales de otros trabajos: cada agente debe inventariarlos y preservar su autoría; no limpiar, revertir, regenerar ni sobrescribirlos sin necesidad directa y registro explícito.

Estos son hallazgos iniciales para reconfirmar, no una lista exhaustiva ni una afirmación sobre el estado de infraestructura viva. No hacer llamadas externas para “probar” disponibilidad sin autorización, cuota presupuestada y registro previo.

## 3. Reglas no negociables para todos los agentes

1. **Fail closed:** datos obligatorios ausentes, ambiguos, inválidos, vencidos o con hash/origen no verificable bloquean esa salida. Mantener `production_enabled: false`.
2. **No sustitución:** ningún `fallback`, `default`, `coalesce`, `??`, `or`, `fillna`, seed o recuperación puede fabricar un valor deportivo. Solo se permiten defaults de presentación que no impliquen hechos deportivos (por ejemplo, estado visual vacío), debidamente separados del contrato de datos.
3. **Desconocido no es cero:** `unknown`, `not_reported`, `not_applicable`, `unavailable` y un valor observado de cero son estados distintos. No convertir entre ellos sin evidencia específica de fuente/cobertura.
4. **Tiempo explícito:** guardar `observed_at` de la fuente, `captured_at` local, zona horaria, `decision_cutoff`, kickoff y `generated_at`. Ninguna feature, lesión, cuota o resultado posterior al cutoff puede influir en la decisión.
5. **Identidad fuerte:** `source + source_event_id` es una identidad externa tipada. Nunca enlazar solo por nombres o tolerancia de fecha; si no hay exactamente una coincidencia por identificador autorizado, abstenerse y registrar motivo.
6. **Un dueño por superficie:** los agentes pueden trabajar en paralelo solo en carpetas/archivos asignados. Cambios compartidos a modelos, contratos, migraciones o estado global requieren un responsable integrador y revisión antes de merge.
7. **Probar y auditar la propia tarea:** cada agente ejecuta sus pruebas focalizadas, revisa todo su diff y busca leaks/fallbacks/valores hardcodeados en la superficie modificada. Debe registrar comandos exactos, resultado, limitaciones y evidencia. No declarar “listo” por compilar únicamente.
8. **No exposición de secretos:** nunca imprimir valores de `.env`, tokens, URLs autenticadas o payloads sensibles; redacción antes de logs, artefactos, screenshots o reporte. Usar secretos efímeros de test solamente.
9. **Sin escritura de apuestas:** no existe ni se habilita ejecución automática de apuestas. Las cuotas son observaciones, no promesa de precio ejecutable.
10. **Sin reclamos no demostrados:** no llamar a datos “oficiales”, “en vivo”, completos, calibrados, rentables o actuales salvo evidencia identificable y verificable que soporte exactamente ese reclamo.

## 4. Organización multiagente

Un PM/orquestador mantiene `EXECUTION_STATE.md`, asigna una tarea por agente, integra contratos y resuelve conflictos. Cada agente devuelve: objetivo y archivos tocados; estado inicial del árbol; causa raíz; cambios; decisiones y alternativas descartadas; comandos de prueba completos y salidas resumidas; inspección de fugas/leaks realizada; riesgos abiertos; hashes/IDs de artefactos afectados; y estado recomendado (`ready_for_review`, `blocked` o `not_done`). No editar el tablero compartido simultáneamente: enviar el reporte al integrador, que consolida el estado.

| Rol | Propiedad exclusiva | Resultado exigido |
|---|---|---|
| A. Datos, calendario e identidad | adaptadores NFL, schedule service, ESPN/NFLverse, identidad canónica de eventos/equipos | ingestión real e idempotente, ledger de cobertura, reconciliación schedule/score |
| B. Odds, disponibilidad y proveedor | Odds API, injury/availability, quota, freshness, props | snapshots verificables, redacción de secretos, clasificación de errores/cuota y política explícita de abstención |
| C. ML temporal y validación | `backend/ml`, metadata/artefactos y generación de predicciones | auditoría de leakage por feature, splits correctos, hashes reproducibles y informe OOS |
| D. Backend, persistencia y API | worker, modelos DB, servicios, rutas, migraciones y contratos | identidad exacta, estado API honesto, idempotencia, seguridad de configuración y separación de responsabilidades |
| E. Frontend y presentación | `frontend/src` | mapa widget→endpoint, cero datos de deporte locales, estados available/partial/stale/unavailable/experimental y pruebas UI |
| F. QA independiente / auditor | no implementa la misma superficie que valida | revisión de diffs, pruebas de integración adversariales, evidencia de datos y dictamen de aceptación por fase |

Con menos agentes, combinar A+B o D+E solo si se preserva revisión cruzada. C y QA no deben ser la misma persona para la aceptación del modelo. No iniciar trabajos dependientes hasta que el dueño publique el contrato acordado. Mantener una bitácora breve por sesión para permitir reanudación cuando un agente se quede sin contexto/tokens; el reporte y las pruebas deben ser autosuficientes, sin depender de conversación previa.

## 5. Secuencia, trabajo y criterios de aceptación

### Fase 0 — Congelar la línea base y reconciliar contratos

**Responsables:** orquestador + QA. **Paralelizable:** inventario estático por servicio, sin editar.

- Registrar commit, branch, `git status --short`, versiones y hashes de código/datasets/modelos/reportes sin revelar secretos. Marcar los cambios preexistentes y su dueño; tomar diffs de solo lectura.
- Construir mapa de flujo: proveedor → raw response → snapshot canónico → validación → features → inferencia → persistencia → API → componente. Anotar claves de identidad, timestamps, filtros y cada manejo de error.
- Reconciliar todos los estados de fase 00–08 con archivos y pruebas actuales. Un reporte antiguo no prueba el comportamiento del checkout.
- Crear catálogo NFL con proveedor, recurso, licencia/uso, clave primaria, cobertura temporal, cadencia, campos requeridos, hora observada, hash, ventana de retención, responsable y política de fallo.

**Salida:** baseline inmutable y discrepancias rastreables. Gate cerrado comprobado por API y metadata. Si un estado no se puede verificar, etiquetarlo `unknown`, no `done`.

### Fase 1 — Calendario NFL y fuente de verdad de partidos

**Responsable:** A; revisión D/QA.

- Reemplazar placeholder NFL por adaptador soportado y documentado. Ingestar calendario, kickoff UTC, season/week, venue, equipos, fuente y event ID real; score/status únicamente desde fuente con autoridad definida.
- Crear identidad canónica explícita para evento, separado de identificadores sintéticos o de proveedor de cuotas. Agregar constraint de unicidad y política de mapeo versionada. No reutilizar `external_id` con semánticas mixtas.
- Enlazar Odds API↔calendario solo con crosswalk persistido con fuente, exactitud de equipos, kickoff y cardinalidad 1:1. Ambigüedad, cambio de kickoff o doble match se pone en quarantine, no se “elige el más cercano”.
- Ingesta idempotente: upsert por fuente/ID, guardar raw o referencia inmutable, hash, retrieved_at, schema version, resultado de validación y conteos inserted/updated/unchanged/rejected.
- Reconciliar partidos esperados/recibidos por season/week y fuente. No inferir que el calendario está completo porque existan filas para los 32 equipos.

**Pruebas:** fixture sintético marcado exclusivamente test para duplicados, local/visitante invertidos, IDs reutilizados entre fuentes, kickoff cambiado, juegos internacionales, semanas con bye, doble header, atrasos, eventos cancelados y partidos sin cuotas. E2E en DB desechable verifica insert→replay sin duplicados y rechazo de identidad ambigua. La prueba usa fixtures propios, no los expone al producto.

### Fase 2 — Proveedores, disponibilidad, cuotas y protección de secretos

**Responsable:** B; revisión de seguridad por QA.

- Clasificar cada respuesta externa en `available`, `empty_valid`, `partial`, `rate_limited`, `unauthorized`, `quota_exhausted`, `timeout`, `provider_error`, `invalid_payload` o `mapping_ambiguous`. Persistir intento, request ID no secreto, endpoint/mercado, HTTP status, duración, credits reported/reserved/charged/refunded, `retrieved_at` y reason code.
- Evitar API key en URL cuando el proveedor admita header seguro. Si el proveedor obliga query parameter, configurar redacción en cliente HTTP, errores, trazas, APM, logs, métricas y snapshots. Registrar exception class/status/request ID, nunca `str(exception)` con URL. Prueba canaria comprueba que clave de test no aparece en ningún sink.
- Mantener la reserva de cuota atómica, dedupe de jobs y budget/circuit breaker; retries acotados con backoff y clasificación. No reintentar errores permanentes ni gastar cuota desde una petición de usuario. Confirmar saldo solo contra proveedor autorizado; ledger local es estimación auditada, no saldo verificado.
- Separar lectura de API de consulta de dashboard: worker captura; API solo lee snapshots. Player props no se obtienen dentro de GET picks. Resolver jugador con ID canónico y equipo; nombre textual no basta.
- Availability: guardar reportes por jugador/equipo/partido y source timestamp; definir cobertura de fuente (feed completo confirmado, equipo-semana, listado emitido). Ausencia de un jugador del informe no equivale a sano. `covered_healthy` exige evidencia positiva de informe completo y pre-cutoff. Estado no resuelto bloquea uso de esa feature, no se vuelve una probabilidad fija.
- Aplicar ventanas de frescura configuradas y específicas por recurso/mercado, basadas en `observed_at` real. Regla temporal debe bloquear kickoff alcanzado, mercado cerrado, snapshots posteriores al cutoff y retrasos fuera de política. Exponer vencimiento y causa.

**Pruebas:** 401/403/429/5xx/timeout/JSON inválido/respuesta vacía válida; proveedor con cuota agotada; cancelación/retry; carrera entre workers; duplicado; reloj/zona horaria/DST; datos tardíos y post-kickoff; market/property mappings desconocidos. Verificar costo contabilizado exactamente una vez y que los errores no se transforman en `available`.

### Fase 3 — Features, dataset y auditoría de data leakage

**Responsable:** C; auditoría adversarial QA independiente.

- Definir por feature: significado, unidad, fuente, ID de fila, `observed_at`, disponible-a-partir-de, cutoff permitido, transformación, nulos permitidos, version, uso train/inference y checks de rango. Registrar todo en `DATA_DICTIONARY.md`/manifest.
- Para cada juego, fijar un cutoff de decisión explícito y reproducible (documentar si es kickoff−2h u otra regla aprobada). Entrenamiento e inference usan mismo código/semántica; toda fuente se filtra `observed_at <= cutoff`. Inputs no disponibles live no pueden aparecer en train como features observadas.
- Auditar targets, box scores, snap counts, lesiones reportadas después, roster posterior, closing line, settlement y estadísticas del mismo juego. Outcome/settlement son labels posteriores; nunca features prepartido. No usar normalización/imputación ajustada con validación/holdout.
- Excluir de features datos que solo existen en datasets históricos sin fuente prospectiva equivalente. No rellenar missing con mediana, media, cero, prior ni league average, a menos que el significado del valor faltante sea una observación explícita y la regla esté validada; para este producto, ante incertidumbre usar missing con abstención.
- Group split temporal estricto por kickoff; filas del mismo día/partido no cruzan train/test. Separar train, selección/validación y lockbox final cronológico, con manifiestos de fechas e IDs. Lockbox se abre una sola vez para hipótesis congelada.
- Reconciliar reportes JSON, manifests reales y archivos de fuente. Cada corrida debe recrearse desde snapshot con `dataset_sha256`, source manifest hash, feature schema/code version, filas rechazadas y razones. Si los inputs no están disponibles en checkout/almacenamiento aprobado, no certificar sus métricas.
- Revisar fuga indirecta: ordenamiento aleatorio, merge de resultados, aliases, leakage de temporada, mapping por score, cálculo de rolling stats que incluye target, estados finales, odds closing y conocimiento de lesión posterior.

**Pruebas:** tests por feature con cutoff antes/igual/después; permutación de futuro que no puede cambiar feature previa; same-date batch; duplicate keys; target leakage canary; splitter no solapado; train/inference parity con fixtures conocidos; reproducibilidad de hash entre procesos; cambios de zona horaria; dataset truncado y hash incorrecto. QA reporta para cada feature `pass/fail/not_applicable` y evidencia concreta.

### Fase 4 — Modelo, calibración y evidencia prospectiva

**Responsable:** C; aceptación F/QA.

- Mantener NFL moneyline como primer/único mercado candidato hasta que otro mercado tenga especificación, datos y validación independientes. No sacar recomendaciones NBA en el gate NFL.
- Comparar contra baseline apropiado (frecuencia/base rate y probabilidad no-vig de precio verificable) usando mismo conjunto de eventos y cutoff. Reportar Brier, log loss, calibración (reliability/ECE con definición), intervalos/incertidumbre, tamaños por segmento y datos excluidos. Accuracy/AUC no prueba edge ni rentabilidad.
- No escoger modelo por el lockbox ni repetir ajustes sobre él. Congelar modelo, calibrador, features, cutoff, odds-source rule y acceptance thresholds antes de confirmación. Si falla, registrar fallo y diseñar hipótesis/dataset futuro; no “arreglar” el holdout.
- EV se habilita únicamente si existen probabilidad aprobada, odds ejecutables observadas con bookmaker/mercado, no-vig benchmark correctamente calculado y snapshot fresco. Sin precio/mercado no hay EV, Kelly, tier, best bet ni pick. No usar odds `-110` ni defaults de spread/total.
- Modelo que da solo ganador ML no puede alimentar proyecciones de puntos, spreads, totals o props. No derivar distribución de score con sigma fijo para aparentar forecast. Mercados secundarios requieren modelos y validación propios.
- Reportar status de utilidad como `not_evaluable` mientras falten muestra, snapshots históricos, CLV o trial prospectivo. Shadow trial debe almacenar oferta mostrada y closing line por separado; no simular ROI histórico con datos no observados.

**Pruebas/reproducción:** ejecutar el pipeline desde manifiestos identificados en entorno limpio; comparar hash/salida exacta; verificar folds chronológicos; cambiar/inyectar filas futuras para demostrar invariancia; verificar calibrador no ve holdout; probar probabilities finitas, suma=1 y monotonicidad/criterios acordados; recalcular todas las métricas desde prediction-level records. El informe debe incluir comando, entorno, versiones, artefactos y limitaciones.

### Fase 5 — Persistencia exacta, API y fallos observables

**Responsable:** D; contratos se acuerdan con A/B/C/E antes de merge.

- Guardar por snapshot y prediction: `sport`, `source`, `source_event_id`, canonical event ID, equipo IDs, market/selection, source/raw reference, observed/captured/cutoff/kickoff, model/calibrator/feature versions, artifact/dataset/source-manifest hashes, hash criptográfico SHA-256 de inputs canónicos exactos, probabilidad, price/bookmaker, no-vig probability, EV solo cuando válido, estado y `reason_codes`.
- Hash canónico incluye IDs, cutoff, features exactas (orden/esquema fijados), availability refs y mercados usados. Stable serialization (encoding, floats, nulls y orden) testada en procesos distintos. No usar `hash()` de Python ni solo refs de schedule como input digest.
- Persistencia de predicción debe resolver exactamente un partido por canonical identity. Si cero o más de una coincidencia: no insertar y emitir `identity_unresolved`/`identity_ambiguous`. Unique constraint con event ID + modelo + feature version + cutoff/input digest. Upsert/replay idempotente.
- API estandariza `available`, `partial`, `stale`, `unavailable`, `experimental`, `no_upcoming_events`; incluye `reason_codes`, fuente/timestamps/cobertura, `as_of`, versión y counts esperados/recibidos/rechazados. Error de DB/provider responde status distinguible y request/correlation ID; nunca `200 []` para ocultar error.
- Separar endpoints informativos/manuales (cálculos hechos por usuario) de recomendaciones del modelo; etiquetas y contratos distintos. Rechazar requests de deporte no soportado con 4xx/503 explícito, no derivar una salida NFL para NBA.
- `/today` y `/game/{id}` exigen `Game.status` elegible, kickoff futuro y `prediction.as_of < decision_cutoff < kickoff`; confirmar zona horaria. Los picks históricos no pueden servirse como recomendación actual.
- Schedule/injury/odds endpoints deben distinguir “válido sin eventos”, error, temporada sin actividad y provider no consultado. Standings debe reportar cobertura de temporada/juegos esperados; contar equipos presentes no demuestra temporada completa.
- Protección operativa: `debug=False` por default no-dev y secret required/generated outside repo; validación de settings al arranque; no secretos hardcodeados; revisar auth/rate limits de jobs de sync expuestos y CORS/reverse proxy conforme deployment. Nunca entregar payload debug/traceback al cliente.

**Pruebas:** API contract tests para cada estado; DB down, tabla vacía, provider 429/timeout y modelo cerrado; gate NFL/NBA; partida pasada/en juego; dos fuentes con distinta frescura; import duplicado; input hash/replay; validación de config production; error response sin stack/secret. Verificar que GET no hace HTTP externo con cliente mock que falla si se invoca.

### Fase 6 — Frontend completamente fiel al contrato

**Responsable:** E; QA valida por captura y test.

- Inventariar cada componente NFL que dibuja games, odds, records/standings, injuries, model inference, MVP, picks, parlays, props, EV, métricas y timestamps. Mapear a endpoint/field, fuente, estado, loading, empty, stale, error y experimental.
- El frontend solo presenta respuesta backend. Retirar arrays, equipos, nombres, conteos, resultados, simulator values, cuotas, 0/50%, defaults y cálculos deportivos locales. `0` se presenta únicamente si el backend lo identifica como cero observado, no para suplir null/error.
- Mostrar el estado y causa en la tarjeta afectada; no ocultar errors de games/odds detrás de lista vacía. Distinguir “sin partidos NFL en ventana”, “calendario no disponible”, “cuotas vencidas”, “lesiones sin cobertura” y “modelo experimental/no pick aprobado”. El usuario nunca debe inferir que vacío equivale a saludable/completo.
- En offseason NBA: vista fuera de alcance/temporada no activa con fecha/fuente solo si backend lo verifica; no mostrar datos NBA de muestra ni badges engañosos. Su ausencia no debe degradar el flujo NFL.
- Mostrar precio, casa, mercado, `observed_at`, cutoff y status usados en pick. Si se vence durante la sesión, retirar etiqueta actual y marcar stale; no conservar EV/Kelly como vigente.
- El copy “MVP”, “Pick del día”, “value”, “premium”, “edge” y “confianza” necesita fuente calculada/backend y gate válido; en otro caso ocultarlo o reemplazarlo por estado no disponible sin adjetivos de rendimiento.

**Pruebas:** component tests para cada estado (available/partial/stale/unavailable/experimental/error/empty); snapshots de UI con API responses sanitizadas; prueba de texto para ausencia de fallback numérico; endpoint failure visible; actualización stale; NFL activo con NBA offseason; lint y build.

### Fase 7 — Auditoría integral, pruebas E2E y revisión de seguridad

**Responsable:** F como reviewer separado; dueños corrigen, no alteran evidencia.

- Repetir búsqueda exhaustiva de números/strings de deporte, `mock`, `seed`, `fallback`, `default`, `fillna`, valores `??`/`||`, static response, synthetic, sample, hardcoded, `except: pass`, respuestas vacías en catch, hash de procedencia, logs de exception/request URL y cualquier `debug=True`. Clasificar cada hallazgo: producción, fixture de test, migración/dato de ejemplo aislado o falso positivo con prueba.
- Trazar al menos una fila verificable de cada recurso de una fuente a UI, comprobando IDs, timestamps, cutoff, hash, rechazo/aceptación y estado. Trazar también datos faltantes, ambiguos, corruptos, vencidos y errores hasta el mensaje exacto mostrado.
- Buscar fugas temporales por feature, SQL join, cache, parquet, inference, Celery/retry y API response. Revisar logs/métricas/traces, raw snapshot permissions, backups y secretos; probar redacción con canarios.
- Revisión read-only de cambios locales previos, migraciones y archivos generados; no descartar cambios para hacer pasar tests. Confirmar no se consumió cuota durante pruebas de fixtures.

**Pruebas de integración E2E** (DB/Redis de test aislados): ingest → valida → persist snapshot → create input manifest → inference experimental → attempt persistence (gate debe bloquear) → API response experimental/unavailable → frontend muestra estado correcto. Luego escenario de identidad exacta y fuentes completas, manteniendo la salida experimental. Ejecutar fallos de provider/DB y verificar no datos. Nunca usar proveedor real en la suite regular.

## 6. Matriz mínima de pruebas obligatoria

| Capa | Pruebas obligatorias | Evidencia para el informe |
|---|---|---|
| Unidad de fuentes | status/semana/event ID; payload inválido; mapping; timezone; raw hash | IDs fixtures y casos rechazados con reason code |
| Cuota/seguridad | reserva concurrente, 429, timeout, retry, canary de secreto en logs | before/after ledger de test, búsqueda de canary con cero resultados |
| DB/persistencia | upsert, duplicado, identidad 0/2 matches, rollback, hash estable entre procesos | filas esperadas y restricciones verificadas |
| Tiempo/leakage | cutoff, kickoff, DST, same-day, feature futura, joins de label, splitter | fechas/IDs por partición; prueba de invariancia futura |
| API | modelos/gates, 200 válido, unavailable, stale, provider/db error, unknown sport | status/body/reason codes y ausencia de traceback/secreto |
| Frontend | datos reales de contrato, vacío, error, stale, experimental, NBA offseason | test de componente, copy mostrado, lint/build |
| E2E | fuente→raw→canonical→feature→inference→API→UI; mismo flujo con fallo | IDs/hashes y request/correlation ID desde fixture al render |
| Regresión | suites completas backend y lint/build frontend | comandos, versiones, duración, pass/fail/skip/warnings |

Usar el entorno y comandos del repo revisados al iniciar cada fase (pytest en backend; `npm run lint` y `npm run build` en frontend; Docker solo si el entorno es aislado y conocido). Añadir pruebas necesarias; no editar fixtures de producción ni depender de red. Los tests previos registrados en documentos no sustituyen una ejecución en el commit final. No esconder pruebas fallidas, skips ni warnings: explicar cada uno y bloquear aceptación si afecta un requisito.

## 7. Plantilla obligatoria de reporte por agente

```text
Agente / fase / fecha UTC:
Commit base y git status inicial:
Objetivo y archivos de propiedad:
Hallazgos confirmados con path:linea y causa raíz:
Cambios realizados y contratos afectados:
Datos/fuentes/hashes/timestamps revisados (sin secretos):
Riesgo de data leakage investigado y evidencia:
Revisión no-fallback/hardcode realizada y búsqueda usada:
Pruebas: comando literal | resultado | skips/warnings:
Flujo verificado extremo a extremo:
Diff propio revisado: sí/no; resultado:
Limitaciones y bloqueos:
Estado recomendado: ready_for_review | blocked | not_done
```

El QA escribe informe separado que confirma o refuta cada criterio con evidence links/logs sanitizados. “No encontré” debe nombrar alcance y búsqueda; no equivale a revisar todo el repo.

## 8. Go/no-go para NFL

**NO-GO** si cualquiera aplica: calendario real incompleto o sync placeholder; eventos sin identity 1:1; datos de odds/lesiones vencidos sin disclosure; fallo de proveedor convertido en vacío exitoso; secreto observado en logs/URLs/traces; feature temporal posterior; mismatch train/inference; dataset/artifact/hash no reproducible; recomendación con datos requeridos ausentes; spread/totals/props derivados de supuestos o de prob modelo no validada para ese mercado; UI muestra recomendación con gate cerrado; endpoint pasa partido iniciado/final; pruebas obligatorias pendientes/fallidas; rollout sin observabilidad/rollback.

**GO técnico para trial sombra**, no para apostar, solo si el checklist anterior está verde, las fases 00–07 se aceptaron y F reprodujo las pruebas en el commit a liberar. Trial comercial/financiero y eventual gate de picks necesita completar Fase 08 con protocolo, límites y métricas preregistradas y autorización explícita del usuario. Durante todo ello `production_enabled` sigue `false` hasta esa decisión separada.

Rollback inmediato: deshabilitar la ruta de recomendaciones/gate conservando datos y diagnóstico; no borrar snapshots/historial; fijar versión previa conocida; preservar logs redactados y ledger de cuota; registrar causa y hora. Nunca resolver una falla volviendo a hardcodear un valor.

## 9. Actualización de estado e índice

El orquestador agrega este plan al índice principal y registra en `EXECUTION_STATE.md` la fase activa, propietarios, PR/diff, hashes, pruebas actuales y criterio abierto. Solo el revisor marca `done`; el dueño de una implementación propone `review`. No cerrar fase con reportes sin reproducir, datos fuera de alcance o pruebas no ejecutadas. Reabrir una fase si un cambio downstream altera contrato, features, cutoff, hashes o significado de estados.

