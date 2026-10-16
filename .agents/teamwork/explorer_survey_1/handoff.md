# Handoff Report — Data Ingestion Explorer (Fase 01 & 04 Architecture Survey)

**Agent:** Data Ingestion Explorer (`teamwork_preview_explorer`)  
**Working Directory:** `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/explorer_survey_1`  
**Date:** 2026-10-15  
**Type:** Hard Handoff (Investigation Complete)

---

## 1. Observation

1. **Protección de Cuota Odds API e Integridad del Ledger:**
   - Archivo inspeccionado: `data/odds_quota_ledger.json`.
   - Saldo actual: `requests_used: 264`, `requests_remaining: 236` (líneas 13-14), con 6 transacciones registradas (la última para NFL main lines el 2026-10-15T22:02:15 UTC).
   - Hash SHA-256 verificado en disco antes y después de pruebas: `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`.
   - Ejecución del comando de prueba en el contenedor:
     ```bash
     docker exec sports_api pytest
     ```
     Resultado verificado: `284 passed in 34.49s`. El test `test_production_ledger_remains_unchanged_during_pytest` (`backend/tests/test_nfl_data_phase01.py:827-837`) verificó que el SHA-256 no cambió en absoluto durante los tests.
   - En `backend/app/services/odds_quota_service.py:118-156`, se utiliza un lock atómico cross-process (`data/odds_quota_ledger.lock`) mediante `fcntl.flock` y reservas presupuestarias en dos fases (`reserve_quota` y `record_transaction`).

2. **Llamadas Externas Bloqueantes en Endpoints GET (Violaciones de R4):**
   - En `backend/app/api/v1/games.py:189-204`, el endpoint `GET /{game_id}/external-prediction` invoca `fetch_espn_matchup_predictor(game)` en `backend/app/services/espn_service.py:159-198`. Esta función ejecuta directamente dos peticiones HTTP sincrónicas al API de ESPN (`scoreboard_url` y `summary_url`) dentro del ciclo de lectura del endpoint.
   - En `backend/app/api/v1/games.py:176-187`, el endpoint `GET /{game_id}/context` invoca `get_matchup_context` en `backend/app/services/matchup_context_service.py:479-482`, que a su vez llama a `fetch_live_injuries_response(sport)`. En las líneas 282-290 de `matchup_context_service.py`, ante expiración (>2h) o ausencia del archivo de caché local, se ejecuta:
     ```python
     with urllib.request.urlopen(req, timeout=6) as resp:
         raw_bytes = resp.read()
     ```
     Bloqueando sincrónicamente el hilo de FastAPI.
   - En `backend/app/api/v1/odds.py:56-60`, el endpoint `GET /remaining` invoca `fetch_remaining_requests()` en `backend/app/services/odds_service.py:643-671`, la cual ejecuta `client.get(f"{BASE_URL}/sports/")` en vivo por cada clave de API configurada.

3. **Manejo de Métricas Faltantes y Prohibición de Imputación de Ceros (R1):**
   - En `backend/app/services/nfl_availability_service.py:746-766`, si un jugador o equipo no tiene estadísticas de semanas previas en `prior_roster_stats`, `compute_team_production_lost` asigna `None` a `ppr_lost_pct`, `off_snaps_lost_pct` y `def_snaps_lost_pct`, devolviendo `CoverageState.UNKNOWN`. No inventa promedios ni penalizaciones arbitrarias.
   - En `backend/app/services/nfl_availability_service.py:698-720`, los equipos omitidos en el reporte de ESPN reciben el estado explícito `CoverageState.NO_INJURIES_LISTED` con nota textual *"sin lesiones listadas en esta captura"* en lugar de imputar un 0% artificial.
   - En `backend/ml/data_pipeline/injury_feature_builder.py:293-303`, `fillna(0.0)` solo se aplica tras validar que el reporte del equipo existe y fue completamente verificado (`all_rows_verified == True & report_rows > 0`); las semanas sin reporte válido son descartadas de la partición temporal.

4. **Estado del Gate de Modelos y Datos Sintéticos:**
   - En `backend/ml/models/nfl/model_metadata.json:262` y `backend/ml/models/nfl/team_only/model_metadata.json:256`, la propiedad `production_enabled` está en `false`.
   - En `backend/app/api/v1/predictions.py:34-79`, `get_approved_model_version("NFL")` valida estrictamente `production_enabled is True`. Al ser `false`, los endpoints `/api/v1/predictions/today?sport=NFL` y `/api/v1/parlay/recommended?sport=NFL` retornan 0 picks con mensaje de indisponibilidad.
   - En `backend/scripts/db/seed_nfl.py:62-128`, existen datos sintéticos heredados (`GAMES_SLATE`), los cuales son filtrados por el API mediante la versión del modelo y purgados por `sync_real_data.py`.

5. **Infraestructura Docker y Caching:**
   - `docker ps` muestra: `sports_api` (Up), `sports_worker` (Up), `sports_db` (Up), `sports_redis` (Up).
   - `docker ps -a` muestra: `sports_beat` se detuvo con estado `Exited (0)` hace 3 horas.
   - Redis solo es utilizado como broker y result backend de Celery (`redis://sports_redis:6379/1` y `/2`). No existe un cliente de aplicación que utilice Redis para cachear lecturas de la API; la aplicación depende de memoria de proceso (`_ODDS_CACHE`, `_HISTORICAL_DF_CACHE`) y archivos en `data/cache/`.

---

## 2. Logic Chain

1. **Premisa A (Protección de Cuota - R4):** El requerimiento R4 exige preservar la integridad de `data/odds_quota_ledger.json` y garantizar cero consumo en pruebas.
   - **Paso 1:** Al examinar `data/odds_quota_ledger.json`, su hash antes de pruebas era `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`.
   - **Paso 2:** La ejecución completa de `docker exec sports_api pytest` corrió 284 tests unitarios e integrados.
   - **Paso 3:** La verificación posterior de `sha256sum data/odds_quota_ledger.json` arrojó exactamente el mismo valor.
   - **Deducción:** Los mecanismos de mocking en tests y el control de reservas en `OddsQuotaService` protegen el saldo real al 100%.

2. **Premisa B (Llamadas de Red Bloqueantes en GET - R4):** El requerimiento R4 prohíbe llamadas de red externas bloqueantes o sincrónicas en endpoints GET.
   - **Paso 1:** Se inspeccionaron todos los routers de `backend/app/api/v1/`.
   - **Paso 2:** Se identificó que `GET /games/{game_id}/external-prediction` invoca `fetch_espn_matchup_predictor`, que realiza 2 llamadas HTTP directas a ESPN.
   - **Paso 3:** Se identificó que `GET /games/{game_id}/context` invoca `urllib.request.urlopen` sincrónicamente ante falta de caché local.
   - **Paso 4:** Se identificó que `GET /odds/remaining` invoca a The Odds API vía HTTP en cada request GET.
   - **Deducción:** Existen 3 endpoints GET que violan R4 y deben ser rediseñados para desacoplar el I/O externo a workers asíncronos.

3. **Premisa C (Cero Datos Sintéticos y Cero Imputación - R1):** El requerimiento R1 prohíbe datos inventados y exige que la falta de métricas se modele estadísticamente sin imputar ceros ni penalizaciones fijas.
   - **Paso 1:** En `backend/app/services/nfl_availability_service.py`, la ausencia de estadísticas de jugadores o reportes produce `None` y estados `UNKNOWN` o `NO_INJURIES_LISTED`.
   - **Paso 2:** En `backend/app/services/sports_analytics_service.py`, las métricas de spread y totales retornan `UNAVAILABLE` en lugar de inventar coberturas.
   - **Paso 3:** Sin embargo, debido a que `production_enabled: false` en los modelos NFL, el frontend no recibe ningún pick real en `/predictions/today` ni en `/parlay/recommended`.
   - **Deducción:** La arquitectura analítica respeta la prohibición de imputación (fail-closed), pero el pipeline de entrega está bloqueado por el gate de modelos y requiere la validación e integración del ensamble R3.

---

## 3. Caveats

1. **Estado de `sports_beat`:** El contenedor `sports_beat` finalizó su ejecución; no se evaluó si su caída se debió a un reinicio de Docker o a una excepción no capturada de Celery beat. Requiere revisión de logs antes de reactivarlo.
2. **Volumen de `sports_beat`:** En `docker-compose.yml`, `sports_beat` no tiene montada la carpeta `./data:/app/data`. Si alguna tarea programada requiriese leer manifiestos de datos directamente al agendarse, fallará por archivo no encontrado.
3. **Consumo Live en Producción:** Aunque las pruebas no consumen créditos, la llamada periódica programada de cuotas NFL (`ingest_odds_task` a las 14:00 y 20:00 UTC) consumirá 3 créditos por ejecución si `ODDS_API_KEY` está configurada y la cuota está disponible.

---

## 4. Conclusion

1. **Integridad de Cuota (R4):** **Aprobado.** El ledger permanece hermético e inalterado en 236 créditos restantes / 264 usados, protegido por `OddsQuotaService` y respaldado por 284 pruebas automatizadas.
2. **Llamadas Bloqueantes en GET (R4):** **Incumple en 3 endpoints.** Se deben desacoplar `GET /external-prediction`, `GET /context` y `GET /odds/remaining` para que lean exclusivamente de PostgreSQL o caché local, delegando las llamadas a Celery.
3. **Integridad de Datos e Imputaciones (R1):** **Aprobado a nivel de diseño conceptual.** `NFLAvailabilityService` no imputa ceros artificiales y maneja la ausencia de datos coherentemente.
4. **Disponibilidad para Frontend (R5):** **Pendiente de activación de ensamble.** La respuesta de `/predictions/today` y `/parlay/recommended` entrega 0 picks porque `production_enabled` es `false`. La fase de modelos debe validar el ensamble y abrir el gate de producción.

---

## 5. Verification Method

Para verificar independientemente los hallazgos de este reporte:

1. **Verificar Hash y Saldo del Ledger de Cuota:**
   ```bash
   sha256sum data/odds_quota_ledger.json
   # Valor esperado: f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4
   cat data/odds_quota_ledger.json | grep -E '"requests_remaining"|"requests_used"'
   # Valor esperado: "requests_used": 264, "requests_remaining": 236
   ```

2. **Ejecutar Suite Completa de Tests Backend en Docker:**
   ```bash
   docker exec sports_api pytest
   # Valor esperado: 284 passed in ~35s
   sha256sum data/odds_quota_ledger.json
   # El hash debe permanecer exactamente idéntico tras la ejecución
   ```

3. **Verificar Violaciones de Llamadas Externas en Endpoints GET:**
   - Inspeccionar `backend/app/api/v1/games.py:204` y `backend/app/services/espn_service.py:161,195`.
   - Inspeccionar `backend/app/api/v1/games.py:183` y `backend/app/services/matchup_context_service.py:289`.
   - Inspeccionar `backend/app/api/v1/odds.py:59` y `backend/app/services/odds_service.py:646`.

4. **Verificar Gate de Producción y Respuesta Actual de Endpoints:**
   ```bash
   curl -s "http://localhost:8000/api/v1/predictions/today?sport=NFL"
   # Responde: {"sport":"NFL","period":"auto","total_picks":0,...,"predictions_enabled":false}
   curl -s "http://localhost:8000/api/v1/parlay/recommended?sport=NFL"
   # Responde: {"status":"unavailable","sport":"NFL","total":0,...,"parlays":[]}
   ```

5. **Verificar Construcción y Lint de Frontend:**
   ```bash
   npm --prefix frontend run lint
   # 0 errores, 0 warnings
   npm --prefix frontend run build
   # Compilación limpia con Vite
   ```
