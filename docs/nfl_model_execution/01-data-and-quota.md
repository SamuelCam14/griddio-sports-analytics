# Fase 01 — Fuentes NFL, snapshots y presupuesto de API

## Objetivos

1. Hacer reproducible cada dataset NFL, incluso injuries/player data actualmente descargados durante el build.
2. Capturar datos de mercado y disponibilidad solo con consumo permitido.
3. Asegurar que el mes no termina sin créditos de API.

## Fuentes a inventariar

| Fuente | Uso previsto | Captura/historial requerido | Estado a verificar |
|---|---|---|---|
| nflverse vía `nflreadpy` | Schedule, resultados, PBP, player stats, snaps, injuries, weekly roster | Persistir cada Parquet crudo + hash, versión `nflreadpy`, seasons consultadas y `retrieved_at` | Ya guarda schedule/PBP; confirmar todos los demás |
| ESPN | Reporte actual de injuries / scoreboard / hora de partido | Guardar response relevante, fetched_at y timestamp que entrega ESPN; no pedirlo para juegos pasados como si fuese snapshot pregame | Integrado en servicios; revisar versión/schema y refresh |
| The Odds API (primaria) | NFL h2h/spreads/totals y props para evaluar señal/disponibilidad | Evento/mercado/casa/participante/line/price y `last_update`, `fetched_at`, cut-off; headers de cuota | Live integrada; persistencia de player props y jobs a verificar |
| API secundaria (no restaurada) | Solo failover después de verificar identidad, schema, cuota y status | Mismo contrato canónico; mantener fuente separada | Presupuesto = 0 hasta verificación funcional |

El README de [`sportsdataverse-py`](https://github.com/sportsdataverse/sportsdataverse-py) enumera NFL ESPN/NFL.com y loaders nflverse con paridad a `nflreadpy`; su presencia en el paquete no significa que el repo lo tenga instalado ni que el release de una semana esté actualizado. Instalarlo solo ante gap demostrado, no como reemplazo preventivo. El punto de partida de consulta es el [repositorio oficial](https://github.com/sportsdataverse/sportsdataverse-py) y su [documentación NFL](https://py.sportsdataverse.org/docs/nfl/reference/loaders/other); Gemini debe verificar funciones, cobertura, actualización y campos actuales antes de concluir que resuelve un gap.

## Pasos de implementación

1. Extender sincronización NFL para snapshots inmutables y versionados de `player_stats`, `snap_counts`, `injuries` y `rosters_weekly` requeridos por Fase 02. No descargarlos de nuevo en silencio desde el builder de entrenamiento.
2. Para cada asset: persistir proveedor, nombre de dataset, temporada, versión de librería, URL sin credenciales, hora UTC de adquisición, filas/columnas, bytes, SHA-256 y errores. Un build usa solo manifest validado.
3. Agregar a quality report conteo por source/season, fechas mínimas/máximas, porcentaje de cobertura, nulos, duplicados, filas ignoradas y motivo. Recalcular report después de build y comprobar `dataset_sha256` igual a metadata.
4. Fijar el identificador de partido y kickoff UTC. El schedule actual deja `game_start_utc` como `NaT`; no usar zona implícita de `gameday/gametime`. Obtener timestamp UTC de fuente inequívoca y fallar cerrado si no se resuelve.
5. Construir mapeo canónico de equipos y jugadores. Registrar aliases, GSIS/PFR/ESPN IDs y conflictos; no empatar jugador solamente por display name cuando exista ID estable.
6. Crear normalizador de Odds API con raw response inmutable (sin api key), bookmaker, market key, event ID, player/description, selection, line, American price, source `last_update`, captura UTC y registro de créditos.
7. Al ingerir props, hacer snapshot query solo bajo el scheduler de partido (fase 06), no en render/request de UI. El consumo se deduplica por event/time window/markets; si ya existe snapshot válido, reutilizar.
8. No consultar la API secundaria hasta probar una petición de bajo costo, documentar proveedor, cuotas, rate-limit, términos y campo de cobertura. El fallo temporal de la key secundaria no convierte primaria en presupuesto de 1000.
9. Si se requiere historial previo y la cuenta tiene permiso: primero crear pequeño pilot explícitamente autorizado y medir coste real. La documentación oficial marca históricos como plan de pago; no asumir acceso con la cuenta de 500 créditos gratuita.

## Cuota Odds API — Reglas verificables

La cuota se calcula por mercado/región, no por respuesta HTTP. Para `/sports/{sport}/odds`, la [documentación oficial V4](https://the-odds-api.com/liveapi/guides/v4/index.html) indica `mercados devueltos únicos × regiones`; h2h+spreads+totals en una región cuesta hasta 3. `/events/{eventId}/odds` también cuesta por mercados únicos devueltos × regiones. `/events/{eventId}/markets` cuesta 1 crédito; evitarlo si la misma consulta de odds puede responder la pregunta. Las respuestas incluyen `x-requests-used`, `x-requests-remaining` y `x-requests-last`. El endpoint histórico cuesta 10× mercados×regiones y requiere plan pago. Verificar siempre contra headers actuales del proveedor, pues plan, cobertura y tarifas pueden cambiar.

### Presupuesto inicial: 500 créditos/periodo

Este presupuesto es un **límite duro**, no una meta de consumo. Asume una región (`us`) y cinco semanas de calendario para resistir meses largos:

| Sobre | Tope | Uso |
|---|---:|---|
| Reserva protegida | 75 | No consumir salvo aprobación del usuario; representa 15% |
| Cuotas principales | 45 | Hasta 3 ventanas de kickoff por semana × 3 mercados × 5 semanas = 45 |
| Props dirigidas | 320 | Hasta 16 partidos × 4 mercados relevantes × 5 semanas; consultar solo cuando haya jugador Q/D y una decisión cerca |
| Operación/validación/reintento | 60 | Integración, respuestas fallidas recuperables y drift de tarifa; requiere registro |
| **Máximo** | **500** | Si saldo o coste deja menos de 75, suspender consultas no esenciales |

La cantidad real de props/mercados debe medirse con `x-requests-last`; si la cobertura de props requiere más mercados por jugador, reducir juegos/ventanas antes de exceder el presupuesto. No ampliar markets/regions “por si acaso”.

### Si se verifican 1000 créditos el próximo periodo

No duplicar automáticamente la frecuencia. Primeros límites: 150 reserva, 90 líneas principales, 640 props dirigidas y 120 operación/reintentos. La key secundaria solo suma a la capacidad después de confirmación de que es proveedor/cuenta operativa, saldo real y compatibilidad de condiciones. Cada key/proveedor conserva ledger propio.

### Rate limiter y cierre automático

- Lee y persiste headers de cada respuesta. Si headers faltan, calcular coste esperado y parar si excedería el sobre; no asumir coste cero.
- El costo estimado se reserva **antes** de la petición. Al completarse, ajustar al coste real del header.
- Tope de gasto disponible = saldo reportado − reserva protegida. Un job no puede cruzarlo.
- Circuit breaker al llegar a 20% restante del saldo total, al consumir el sobre de props o ante respuesta de cuota agotada. Ante HTTP 429, abrir el breaker y terminar el job sin reintento automático; solo introducir backoff/jitter en una futura política de retry explícitamente limitada y probada.
- No realizar peticiones deportivas por cada reload/poll de frontend. Scheduler única, deduplicación por evento+ventana, caché local de respuesta y locks de job.
- Actualizar la fecha exacta de reset desde cuenta/metadata del proveedor; no asumir que es medianoche del día 1.
- No guardar ni loguear key. Ocultar query params de credenciales en exceptions, access logs y reports.
- No llamar endpoints históricos en periodo gratuito. Su coste es muy superior (10 créditos por market/region) y no es necesario para el trial prospectivo inicial.

## Validación

- Fixture de costo por endpoint/markets/regions con coste esperado y asserts.
- Prueba sin red de circuit breaker, reserva, costo de respuesta, deduplicación y 429.
- Al menos una captura live real autorizada y de bajo costo para comprobar que el header refleja la estimación; registrar saldo antes/después.
- Manifiestos con SHA y sin claves en diffs/logs.

