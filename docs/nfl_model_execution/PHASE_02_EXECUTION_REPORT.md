# Reporte de Ejecución: Fase 02 — Disponibilidad NFL e Injuries Point-in-Time (Flujo Práctico SSOT)

**Fecha:** 2026-10-12  
**Plan:** `docs/nfl_model_execution/02-injury-point-in-time.md`  
**Ejecutor:** Gemini  
**Revisor / Orquestador:** Codex  
**Autoridad de Apuestas y Presupuesto:** Samuel  
**Estado:** `review`  
**Gobernanza:** `production_enabled: false` (inmutable en metadata y gates). Fase 03 en `pending`. NBA fuera de alcance.

---

## 1. Resumen Ejecutivo del Flujo Práctico

Se implementó y validó en un ciclo atómico el flujo práctico para la gestión de lesiones, rendimiento individual y cruce de cuotas NFL conforme a los requerimientos de Codex:

1. **Cadencia de Datos (2 Capturas ESPN en Día de Partido):**
   - Dos capturas separadas: inicio de jornada y cerca de kickoff.
   - Cada captura persistida como snapshot independiente (`ESPNInjurySnapshot`) con su propio `captured_at_utc` en UTC fijado con el reloj del sistema al momento de recibir la respuesta.
   - La captura 2 solo afecta a predicciones ejecutadas posteriormente a su captura (`as_of >= captured_at_utc`).
2. **Equipos Omitidos (Sin Bloqueo Artificial de Feed):**
   - Eliminados umbrales artificiales (como 28/32). El feed no se bloquea.
   - Equipos no listados se registran fielmente como `CoverageState.NO_INJURIES_LISTED` ("sin lesiones listadas en esta captura").
   - Prohibido inferir salud (`COVERED_HEALTHY`) por la omisión de un equipo. El modelo base NFL continúa sin interrupción.
3. **Cruce ESPN-Odds (Player Props Verificables):**
   - Para jugadores Questionable (Q) y Doubtful (D), se cruzan contra props disponibles en el mercado para el evento exacto (`event_id`) y jugador canónico.
   - Presencia de línea = señal de mercado observada (`LISTED_AT_CUTOFF`), no certeza de juego.
   - Ausencia de línea = conserva estado desconocido (`UNKNOWN`), no prueba de ausencia.
   - Para bajas confirmadas (`OUT` / `IR`): NUNCA se consultan props ni se consume cuota.
   - Costo variable en `OddsQuotaService` estimado exactamente según documentación V4 de The Odds API (mercados $\times$ regiones). Aislamiento hermético en pruebas sin tocar el ledger real.
4. **Rendimiento Reciente e Importancia Individual:**
   - Métricas calculadas en `calculate_player_recent_importance()` a partir de registros reales previos al corte (`game_date < as_of`) ligados a su ID canónico y equipo actual.
   - Prohibido transferir métricas de equipos anteriores y sustituir muestras con temporadas antiguas.
   - `pos_rank` entra solo como contexto descriptivo con fecha/procedencia, sin multiplicadores fijos.
   - Ausencia de historial reciente deja el ajuste como desconocido y el modelo base (`team_only`) continúa.
5. **Purgado de Probabilidades Fijas:**
   - Purgadas todas las constantes y probabilidades fijas sin respaldo empírico.

---

## 2. Evidencia Reproducible de Pruebas

Todas las suites ejecutadas dentro del contenedor oficial Docker `sports_api` (Python 3.12.15):

```bash
# 1. Suite focalizada Fase 02 (incluye TestPhase02AuditResolutions y TestPhase02CodexConsolidatedClosure)
docker exec -e PYTHONPATH=/app sports_api pytest tests/test_nfl_availability_phase02.py -v
# Resultado: 66 passed in 49.63s (0 errores, 0 fallos)

# 2. Suite consolidada de cierre técnico (10 puntos auditados y edge cases por Codex)
docker exec -e PYTHONPATH=/app sports_api pytest tests/test_nfl_availability_phase02.py -k "TestPhase02CodexConsolidatedClosure" -v
# Resultado: 10 passed in 1.63s

# 3. Suite Celery tasks (incluye typing imports, capture_nfl_injuries_task, schedule_nfl_gameday_captures_task, evaluate_predictions real pipeline)
docker exec -e PYTHONPATH=/app sports_api pytest tests/test_celery_tasks.py -v
# Resultado: 7 passed in 1.63s

# 4. Suite de regresión temporal, gate de predicción y frescura
docker exec -e PYTHONPATH=/app sports_api pytest tests/test_prediction_gate.py tests/test_freshness.py tests/test_temporal_pipeline.py -v
# Resultado: 42 passed in 2.92s
```

---

## 3. Integridad del Ledger The Odds API

- **Archivo:** `data/odds_quota_ledger.json`
- **Algoritmo:** SHA-256
- **Hash verificado antes/después:** `13E43E0C675CF6C17946E3B3E03DF639DBE2E96AB0786680C4A6D37C9AB028BE` (100% inalterado)
- **Créditos disponibles (`requests_remaining`):** 275
- **Créditos consumidos (`requests_used`):** 225
- **Peticiones live realizadas:** 0

---

## 4. Resolución Exhaustiva de los 8 Puntos de Cierre Técnico Auditados por Codex

1. **Capturas honestas y hash de proveniencia:**
   - `fetch_live_injuries_response(sport)` en `matchup_context_service.py` expone `raw_bytes`, `raw_payload`, `received_at_utc`, `captured_at_utc`, `is_from_cache` y `payload_sha256`.
   - `save_espn_injury_snapshot` calcula el hash SHA-256 directamente sobre el payload crudo recibido (no sobre la normalización dict) y asigna `known_by_system_at_utc` en UTC.
   - En la lectura de caché, se preserva la edad del archivo (`cache_age_seconds`) y el timestamp original de captura sin re-fechar a la hora de lectura.
2. **Tiempos live y cutoff de 90 minutos:**
   - La visibilidad del snapshot en un instante `as_of` la decide estrictamente `known_by_system_at_utc <= as_of`.
   - `source_observed_at_utc` es opcional (no causa fail-closed si es None o ausente).
   - Inferencia en `ml/inference.py` acepta cutoffs de 90 minutos prepartido (`kickoff - 90min`), permitiendo la ingestión y predicción con reportes oficiales de inactivos sin forzar el límite rígido de 2 horas.
3. **Predicción final gatillada por prepartido y trazabilidad Celery:**
   - Tras completar una captura prepartido (`near_kickoff` o `pre_kickoff`), `capture_nfl_injuries_task` despacha automáticamente `evaluate_predictions_task` para actualizar las predicciones de los juegos afectados.
   - Celery evalúa dinámicamente la variante `injury_aware` cuando existe snapshot válido, o recurre ordenadamente a `team_only` con `allow_baseline_fallback=True`.
   - `evaluate_predictions_task` registra explícitamente en `results` el modelo ejecutado (`model_variant`), si se usó ajuste de lesiones (`used_injury_adjustment`), y el conteo de predicciones aun cuando la inserción a BD es omitida por `production_eligible: false`.
4. **Player props y exclusión de bajas:**
   - Cruce de props exclusivo para jugadores en estado `QUESTIONABLE` o `DOUBTFUL`, exigiendo `event_id` canónico y jugador canónico.
   - Para bajas confirmadas (`OUT` / `IR`): NUNCA se consultan props ni se reserva cuota (`get_verifiable_props_for_qd_players` retorna None sin reservar cuota).
   - Presencia de línea en el corte = señal de mercado (`LISTED_AT_CUTOFF`). Ausencia de línea = señal desconocida (`UNKNOWN`), sin interpretar la falta de cotización como ausencia o juego seguro.
5. **Rendimiento actual y assets nflverse 2026:**
   - Resolución de schedule ET a UTC mediante `resolve_nfl_kickoff_utc` sin formateo naive.
   - Verificación de parquets 2026: al contener únicamente `pbp` y `schedules` (faltan `player_stats` y `snap_counts`), `load_player_recent_logs_from_nflverse()` retorna lista vacía de forma honesta.
   - Ante ausencia de estadísticas recientes para el equipo actual, el ajuste individual queda como `unknown` (métricas None) y la predicción hace fallback a `team_only` sin inventar priors ni imputar ceros sintéticos.
6. **Pruebas herméticas y consumo end-to-end de features:**
   - Test `test_e2e_features_consumed_by_variant_model` verifica que la matriz de inferencia para `injury_aware` incluye y procesa efectivamente las columnas de lesiones (`home_ppr_lost_pct`, etc.), mientras que el fallback a `team_only` las excluye y corre el modelo base.
   - Mocks estrictamente offline para Odds API y ESPN; cero llamadas de red.
7. **Regresión operativa y edge cases:**
   - Verificación de idempotencia, tareas Celery, gates de predicción y contratos temporales.
   - Resistencia ante corrupción de stream ESPN y manejo fail-closed sin crash para jugadores sin timestamps de ESPN.
8. **Gobernanza:**
   - `production_enabled: false` permanece inmutable en metadatos y endpoints. Fase 02 se mantiene en `review` para inspección de Codex, y Fase 03 en `pending`. Saldo de cuota intacto en 275 créditos.

---

## 5. Distinción Rigurosa: Integrado vs Experimental / Desconocido

Para satisfacer el requerimiento de auditoría de Codex de no sobrestimar lo integrado:

### A. Lo Realmente Integrado y Verificado
- **Flujo de Captura y Provenance:** Captura ESPN que expone bytes/payload original, hash SHA-256 pre-normalización, y hora local de llegada UTC (`known_by_system_at_utc`). Preservación de timestamp original en caché sin refresco engañoso.
- **Ventana Prepartido 90 Minutos:** Desbloqueo de snapshots tomados a 90 minutos del kickoff tanto en el builder como en inferencia, permitiendo el consumo de listas de inactivos oficiales.
- **Orquestación Celery End-to-End:** Despacho dinámico de `evaluate_predictions_task` al completar capturas prepartido (`near_kickoff`), filtrando por `affected_game_ids`, y registrando `model_variant` y `used_injury_adjustment` en el dict de resultado aun con `production_eligible: false`.
- **Aislamiento de Cuota en Player Props:** `OddsQuotaService` y `get_verifiable_props_for_qd_players` garantizan que jugadores OUT/IR nunca disparan reservas de cuota ni queries, y que la ausencia de línea permanece en estado `UNKNOWN`.
- **Contrato Temporal e Inferencia Fail-Closed:** Caída ordenada al baseline `team_only` cuando no hay historial reciente o cuando la cobertura de lesiones no es demostrable.

### B. Lo Experimental o Todavía Desconocido
- **Métricas Individuales Temporada 2026:** Dado que nflverse aún no ha publicado `player_stats` ni `snap_counts` para la temporada 2026 (solo existen `pbp` y `schedules`), todo partido de 2026 resuelve honestamente su ajuste individual como `unknown` y cae al modelo base `team_only`. La integración real de features de lesiones para 2026 requiere la publicación de dichos parquets por nflverse.
- **Variaciones de Nombres de Mercados de Props:** En un entorno live, distintos bookmakers pueden nombrar los mercados de formas diversas (`player_pass_yds` vs `passing_yards`). El mapeo actual cubre los nombres estándar de The Odds API; nombres no contemplados resuelven de forma segura como `UNKNOWN`.
- **Comportamiento Live de Celery y Red:** Todas las validaciones se han realizado de manera hermética con mocks y Docker local. La interacción concurrente bajo carga real con workers distribuidos en producción permanece sin probar en vivo hasta las fases correspondientes.


