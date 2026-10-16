# Informe Exhaustivo de Auditoría: Arquitectura de Datos, APIs, Ingesta y Cuotas (R1 & R4)

**Fecha:** 2026-10-15  
**Explorador:** Data Ingestion Explorer (`teamwork_preview_explorer`)  
**Directorio de Trabajo:** `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/explorer_survey_1`  
**Referencia Principal:** `ORIGINAL_REQUEST.md` (Requisitos R1 y R4)

---

## Resumen Ejecutivo

Se completó una auditoría exhaustiva, empírica y basada en evidencia sobre la arquitectura de ingestión de datos, protección de cuotas de APIs, esquemas de bases de datos, manejo de métricas faltantes, y llamadas de red en el backend de Griddio Sports Analytics.

### Hallazgos Principales:
1. **Protección de Cuota Odds API (R4 - Cumplido al 100% en Tests):**
   - El ledger `data/odds_quota_ledger.json` se encuentra intacto en **236 créditos restantes / 264 usados** (saldo provisional reconciliado: 275).
   - Su hash SHA-256 (`f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`) se mantuvo estrictamente idéntico antes y después de la ejecución completa de la suite de pruebas (284 tests aprobados en `sports_api`).
   - El servicio `OddsQuotaService` implementa exclusión mutua mediante file locking cross-process (`.lock`), control presupuestario de dos fases (reserva previa y conciliación post-despacho con headers del proveedor), división estricta por sobres (`main_lines`: 45, `props`: 320, `operational`: 60) y un circuit breaker rígido que bloquea el consumo al alcanzar la reserva protegida de 75 créditos o el 20% del presupuesto.
2. **Violaciones de R4 en Endpoints GET (Llamadas de Red Bloqueantes/Sincrónicas):**
   - **`GET /api/v1/games/{game_id}/external-prediction`** (`backend/app/api/v1/games.py:189-204`): ejecuta **dos llamadas HTTP en vivo y bloqueantes** hacia `site.api.espn.com` mediante `httpx.AsyncClient`.
   - **`GET /api/v1/games/{game_id}/context`** (`backend/app/api/v1/games.py:176-187` -> `matchup_context_service.py:282-290`): ante fallo o expiración de caché local (>2h), ejecuta **una llamada sincrónica bloqueante de hilo** vía `urllib.request.urlopen("https://site.api.espn.com/.../injuries")`.
   - **`GET /api/v1/odds/remaining`** (`backend/app/api/v1/odds.py:56-60` -> `odds_service.py:631-680`): ejecuta peticiones HTTP en vivo a The Odds API (`/sports/`) por cada clave configurada en cada consulta GET.
3. **Auditoría de Datos Sintéticos y Manejo de Métricas Faltantes (R1):**
   - **Rutas de Producción (`backend/app/`):** Libres de datos inventados. Ante datos incompletos o falta de historial de jugadores, `NFLAvailabilityService` y `inference.py` se abstienen de imputar ceros o constantes fijas, devolviendo `None` y asignando estados explícitos (`UNKNOWN`, `NO_INJURIES_LISTED`, `UNAVAILABLE_NO_REPORT`).
   - **Rutas de Semillas y Scripts Offline (`backend/scripts/db/`):** `seed_nfl.py` contiene registros sintéticos heredados (`home_win_prob: 0.405`, `external_id: nfl_2026_*`). El router de producción (`predictions.py:159`) los excluye mediante el filtro estricto `.where(Prediction.model_version == approved_version)`, y `sync_real_data.py` los purga explícitamente en BD.
   - **Brecha Crítica de Producción:** El gate de producción está cerrado (`production_enabled: false` en `backend/ml/models/nfl/model_metadata.json`), provocando que `/api/v1/predictions/today?sport=NFL` y `/api/v1/parlay/recommended?sport=NFL` respondan `0 picks` con mensaje de indisponibilidad. Además, las líneas de spread y totales están marcadas como `UNAVAILABLE` por ausencia de modelo específico.

---

## 1. Auditoría de Adquisición de Datos por Proveedor

### 1.1. NFLverse / nflreadpy
- **Módulos:**
  - `backend/ml/data_pipeline/nfl_source.py` (Líneas 77–246: función `sync_nfl_source`, 248–306: `load_nfl_snapshot`).
  - `backend/app/services/nfl_schedule_service.py` (Líneas 32–239: `sync_nfl_games_from_source`).
- **Mecanismo de Adquisición:**
  - Utiliza la librería oficial `nflreadpy` (versión instalada en container: `>=0.1.5`).
  - Descarga datasets por temporada: `schedules`, `pbp`, `player_stats`, `snap_counts`, `injuries`, `rosters_weekly` y `players_crosswalk`.
  - **Inmutabilidad y Caching:** Los datasets descargados se persisten localmente en formato Parquet en `data/raw/nflverse/{season}/{dataset}_{season}_{hash}.parquet`.
  - Se genera y valida un manifiesto global con SHA-256 (`data/raw/nflverse/manifest.json`).
  - `sync_nfl_games_from_source` sincroniza el calendario verificado a la tabla `games` de PostgreSQL vinculando el crosswalk en `provider_game_crosswalk` con `provider="nflverse"`.
- **Evaluación R1:** **CUMPLE.** No inventa partidos, usa calendarios oficiales y conserva trazabilidad por hash SHA-256.

### 1.2. ESPN (Scoreboards, Rosters, Lesiones y Predictor)
- **Módulos:**
  - `backend/app/services/espn_service.py` (`sync_espn_scores`, `sync_espn_team_catalog`, `fetch_espn_matchup_predictor`).
  - `backend/app/services/matchup_context_service.py` (`fetch_live_injuries_response`, `_normalize_espn_injuries`).
  - `backend/app/services/nfl_availability_service.py` (`save_espn_injury_snapshot`, `get_active_injury_snapshot`).
- **Mecanismo de Adquisición:**
  - Marcadores y catálogo de equipos vía HTTP REST: `https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard` y `.../teams`.
  - Reporte oficial de lesionados: `https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries`.
  - Persistencia de snapshots point-in-time: Almacena capturas completas en `data/cache/nfl_availability/snapshots/` con `captured_at_utc`, `source_observed_at_utc` y `payload_sha256`.
- **Evaluación R1 & R4:**
  - **R1:** CUMPLE en cuanto a fidelidad de datos (no altera reportes de ESPN ni fabrica jugadores).
  - **R4:** **INCUMPLE en endpoints GET** (ver Sección 5).

### 1.3. The Odds API (Líneas Principales, Props y Cuotas)
- **Módulos:**
  - `backend/app/services/odds_quota_service.py` (`OddsQuotaService`).
  - `backend/app/services/odds_service.py` (`fetch_odds`, `fetch_odds_from_db`, `OddsApiKeyManager`).
  - `backend/app/services/live_ingestion_service.py` (`sync_real_games`, `fetch_real_player_props`).
- **Mecanismo de Adquisición:**
  - Base URL: `https://api.the-odds-api.com/v4`.
  - Mercados primarios: `h2h,spreads,totals`, formato americano, región `us`.
  - Almacena respuestas crudas inmutables (`data/raw/odds_api/{date}/{sport}_{time}_{identity}_{digest}.json`) en modo solo lectura (`chmod 0o444`).
  - Persiste snapshots en la tabla `odds_snapshots` de PostgreSQL (TimescaleDB hypertable) utilizando `ON CONFLICT DO NOTHING` sobre la tupla `(game_id, bookmaker, market, observed_at)`.
- **Evaluación R1 & R4:**
  - **R1:** CUMPLE (cero cuotas inventadas; si no hay cuota fresca observada, se marca `is_stale: true` o no se emite pick).
  - **R4:** CUMPLE en protección de cuota y ledger; INCUMPLE en el endpoint GET `/api/v1/odds/remaining` (ver Sección 5).

---

## 2. Auditoría de Datos Sintéticos, Simulados o Hardcodeados (R1)

| Archivo | Líneas | Objeto / Función | Naturaleza del Dato | ¿Afecta Producción? | Estado / Mitigación Actual |
|---|---|---|---|---|---|
| `backend/scripts/db/seed_nfl.py` | 62–128 | `GAMES_SLATE` | Partidos ficticios con probabilidades y EV fijos (`home_win_prob: 0.405`, `away_ev: 8.4`, `ext_id: nfl_2026_*`). | **NO** (Aislado) | Filtrado por `.where(Prediction.model_version == approved_version)` en API. Purgado por `sync_real_data.py`. |
| `backend/scripts/db/seed_cards.py` | 20–150 | `INITIAL_CARDS` | Listado estático de Sports Cards con precios y grados estimados. | **NO** | Endpoints `/api/v1/cards/*` retornan HTTP 503 ("Catálogo deshabilitado"). |
| `backend/scripts/db/seed_database.py` | 20–44 | `MARKET_SIZES`, `CONFERENCES` | Constantes de mercado y conferencias NBA. | **NO** | Solo utilizado para configuración básica de equipos NBA. |
| `backend/app/api/v1/predictions.py` | 643–650 | `simulate_matchup` | Antiguo endpoint POST para simular partidos con stats arbitrarias. | **NO** | Retorna HTTP 503 deshabilitado ("Fail closed: custom/default team features are not observed game data"). |
| `backend/app/services/sports_analytics_service.py` | 128–153 | Spread & Totals analysis | Variables de hándicap y over/under. | **NO** | No inventa números: retorna estrictamente `UNAVAILABLE` (`spread_pick = None`, `totals_pick = None`). |
| `backend/ml/models/nfl/model_metadata.json` | 260–267 | `production_enabled: false` | Bandera de gate de producción. | **SÍ (Bloquea)** | Bloquea la entrega de predicciones reales hasta aprobación de modelos. |

---

## 3. Manejo de Métricas Faltantes e Imputaciones (R1)

### 3.1. Prohibición de Imputación de Ceros (Zero-Imputation Audit)
- **Lesiones y Disponibilidad (`backend/app/services/nfl_availability_service.py`):**
  - **Observed At ausente:** Línea 584: Si el registro de lesión carece de timestamp verificado (`observed_at` o `date_modified`), el estado se asigna automáticamente a `UNKNOWN`. No se inventa un timestamp actual.
  - **ID de jugador no resuelto:** Línea 610: Si `canonical_player_id` no se resuelve, se clasifica como `UNKNOWN` con flag `unresolved_canonical_id = True`.
  - **Falta de estadísticas de rendimiento previo:** Líneas 746–766: Si el jugador no cuenta con historial de semanas previas en `prior_roster_stats`, **no se imputan 0 ni valores fijos (como los antiguos 15 PPR o 0.80 snaps)**; en su lugar, `ppr_lost_pct`, `off_snaps_lost_pct` y `def_snaps_lost_pct` se retornan como `None` y el estado general pasa a `CoverageState.UNKNOWN`.
  - **Equipos omitidos en el reporte de ESPN:** Líneas 698–720: Si un equipo no tiene lesionados listados, se asigna el estado `CoverageState.NO_INJURIES_LISTED`, registrando textualmente *"sin lesiones listadas en esta captura"* y manteniendo métricas en `None`, en vez de asumir un 0% artificial.
  - **Reporte limpio confirmado:** Líneas 723–743: Solo se asigna `ppr_lost_pct = 0.0` si existe un `clean_report_evidence_ref` explícito que verifique que el equipo entregó reporte formal sin novedades.

- **Imputación en Feature Builder (`backend/ml/data_pipeline/injury_feature_builder.py`):**
  - Línea 302: `final_df[column] = final_df[column].fillna(0.0)`.
  - **Auditoría:** Se ejecuta **exclusivamente después** del filtro en líneas 293–299:
    ```python
    final_df["all_rows_verified"].eq(True) & (final_df["report_rows"].fillna(0) > 0)
    ```
    Esto garantiza que el 0.0 solo se aplica a escuadras con reporte completo verificado donde ningún jugador activo sufrió baja. Las semanas sin reporte verificado son descartadas, nunca imputadas.

- **Inferencia en Tiempo Real (`backend/ml/inference.py`):**
  - Líneas 160–275: En modo `injury_aware`, si un partido carece de snapshot de disponibilidad, o si alguno de los dos equipos está en estado `UNKNOWN`, el motor lanza `DataQualityError` y rechaza la inferencia. **No sustituye datos faltantes por ceros ni medias.**

---

## 4. Estado e Integridad del Ledger de Cuota The Odds API (R4)

### 4.1. Inspección del Archivo `data/odds_quota_ledger.json`
- **Ubicación:** `/home/samuelc/Documents/Progreso/griddio-sports-analytics/data/odds_quota_ledger.json`
- **Hash SHA-256 Verificado:** `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`
- **Estructura y Balances Actuales:**
  - **Presupuesto Total (`total_budget`):** 500 créditos.
  - **Reserva Protegida (`protected_reserve`):** 75 créditos (15%). No consumibles bajo ninguna circunstancia.
  - **Créditos Usados (`requests_used`):** 264 créditos.
  - **Créditos Restantes (`requests_remaining`):** 236 créditos disponibles informados por el proveedor.
  - **Saldo Provisional Hermético (`provisional_requests_remaining`):** 275 créditos.
  - **Gasto por Sobres (`spent_by_envelope`):**
    - `main_lines`: 15 créditos consumidos (Tope: 45).
    - `props`: 0 créditos consumidos (Tope: 320).
    - `operational`: 1 crédito consumido (Tope: 60).
  - **Circuit Breaker:** `circuit_breaker_open: false` (se activará si el saldo restante desciende a <= 75 créditos o al 20% del total).

### 4.2. Mecanismos de Blindaje contra Consumo en Pruebas
1. **Aislamiento en Tests:**
   - La suite de pruebas de Pytest (`backend/tests/test_nfl_data_phase01.py`, `test_nfl_backend_ssot_phase06.py`, etc.) utiliza fixtures de `tmp_path` para crear ledgers temporales aislados en cada test funcional.
   - Las llamadas HTTP externas están mockeadas con `unittest.mock.patch` o fixtures pre-capturadas.
2. **Asertividad Criptográfica de Intactitud:**
   - En `backend/tests/test_nfl_data_phase01.py` (líneas 14–17 y 827–837), el test `test_production_ledger_remains_unchanged_during_pytest` captura el SHA-256 del ledger real al momento de importación y comprueba que se mantenga idéntico al finalizar la suite.
3. **Verificación Directa Realizada:**
   - Se ejecutó `docker exec sports_api pytest` (284 tests pasados en 34.49s).
   - Se recalculó `sha256sum data/odds_quota_ledger.json` inmediatamente después:
     `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4` -> **Completamente inalterado.**

---

## 5. Auditoría de Endpoints GET: Detección de Llamadas de Red Externas (R4)

El requisito R4 prohíbe explícitamente que las lecturas HTTP del frontend disparen peticiones sincrónicas o bloqueantes a servicios de terceros.

### 5.1. Violaciones Identificadas (Gaps contra R4)

#### Violación 1: Endpoint de Pronóstico Externo ESPN
- **Archivo y Línea:** `backend/app/api/v1/games.py:189-204`
- **Endpoint:** `GET /api/v1/games/{game_id}/external-prediction`
- **Código Involucrado:**
  ```python
  @router.get("/{game_id}/external-prediction")
  async def get_external_game_prediction(game_id: int, db: AsyncSession = Depends(get_db)):
      ...
      from app.services.espn_service import fetch_espn_matchup_predictor
      return await fetch_espn_matchup_predictor(game)
  ```
- **Mecanismo Bloqueante:**
  En `backend/app/services/espn_service.py` (líneas 160–198), `fetch_espn_matchup_predictor` realiza **dos peticiones HTTP asíncronas consecutivas pero bloqueantes para el request del cliente**:
  1. `await client.get(scoreboard_url)` hacia `site.api.espn.com` (timeout 10s).
  2. `await client.get(summary_url)` hacia `site.api.espn.com` (timeout 10s).
- **Impacto:** Si la API pública de ESPN se ralentiza o cae, la petición GET del usuario se bloquea hasta 20 segundos antes de responder o fallar.

#### Violación 2: Contexto Deportivo SofaScore con Descarga Sincrónica
- **Archivo y Línea:** `backend/app/api/v1/games.py:176-187` -> `backend/app/services/matchup_context_service.py:282-290`
- **Endpoint:** `GET /api/v1/games/{game_id}/context`
- **Código Involucrado:**
  ```python
  # matchup_context_service.py:282-290
  import urllib.request
  req = urllib.request.Request(endpoint, headers=...)
  with urllib.request.urlopen(req, timeout=6) as resp:
      raw_bytes = resp.read()
  ```
- **Mecanismo Bloqueante:**
  Si el archivo en disco `data/cache/nfl_live_injuries.json` tiene más de 2 horas de antigüedad o no existe, la función `fetch_live_injuries_response` invoca `urllib.request.urlopen` sincrónicamente dentro del hilo de ejecución de la API para descargar el reporte de lesiones de ESPN.
- **Impacto:** Bloqueo del hilo de FastAPI (event loop thread pool) por hasta 6 segundos por cada solicitud de contexto en frío.

#### Violación 3: Consulta de Cuota Restante en Vivo
- **Archivo y Línea:** `backend/app/api/v1/odds.py:56-60` -> `backend/app/services/odds_service.py:643-671`
- **Endpoint:** `GET /api/v1/odds/remaining`
- **Código Involucrado:**
  ```python
  async with httpx.AsyncClient(timeout=10.0) as client:
      for idx, key in enumerate(keys):
          response = await client.get(f"{BASE_URL}/sports/", params={"apiKey": key})
  ```
- **Mecanismo Bloqueante:**
  Dispara una petición HTTP externa a The Odds API por cada clave configurada directamente en el flujo de lectura del frontend.
- **Impacto:** Aunque `/sports/` tiene costo 0 créditos en The Odds API, viola la prohibición estricta de tráfico de red saliente en peticiones GET.

### 5.2. Endpoints GET Conformes (100% Libres de Llamadas Externas)
- `GET /api/v1/predictions/today?sport=NFL`: Consulta exclusivamente PostgreSQL (`predictions`, `games`, `teams`, `odds_snapshots`).
- `GET /api/v1/predictions/experimental/{sport}`: Lee únicamente archivos locales manifest y CSV en `data/predictions/experimental/`.
- `GET /api/v1/predictions/top`: Consulta derivada de `get_today_picks` (solo base de datos).
- `GET /api/v1/predictions/game/{game_id}`: Consulta directa a PostgreSQL.
- `GET /api/v1/predictions/model-info`: Lee metadatos locales de `backend/ml/models/{sport}/model_metadata.json`.
- `GET /api/v1/parlay/recommended`: Consulta únicamente tablas locales en PostgreSQL.
- `GET /api/v1/odds/nfl` y `GET /api/v1/odds/nba`: Invocan `fetch_odds_from_db`, resolviendo desde PostgreSQL (`OddsSnapshot`).
- `GET /api/v1/games/today`, `date/{date}`, `upcoming`: Consultas exclusivas sobre PostgreSQL (`Game`).
- `GET /api/v1/games/standings`: Lee snapshots verificados locales en Parquet (`data/raw/nflverse/`) o tablas de resultados finales en PostgreSQL.

---

## 6. Arquitectura de Base de Datos, Caché y Pipelines Asíncronos

### 6.1. Modelos SQLAlchemy y Esquema de Base de Datos
Todos los modelos heredan de `Base` (`app.core.database.py`) y operan sobre PostgreSQL 16 con TimescaleDB:

1. **`teams` (`app/models/game.py:23-40`):**
   - Catálogo canónico de equipos NBA (30) y NFL (32).
   - Columnas: `id`, `api_id` (Unique, Index), `sport`, `name`, `abbreviation`, `city`, `conference`, `division`, `market_size_score`, `logo_url`.
2. **`games` (`app/models/game.py:42-96`):**
   - Partidos programados, en vivo y finalizados.
   - Columnas principales: `id`, `external_id` (Unique, Index), `sport`, `season`, `season_type`, `game_date` (DateTime UTC con Index), `home_team_id`, `away_team_id`, `home_score`, `away_score`, `home_win`, `status` (`scheduled|live|final`).
   - Trazabilidad (P2): `source`, `source_ref` (SHA-256), `fetched_at`, `observed_at`, `missing_reason`.
3. **`provider_game_crosswalk` (`app/models/game.py:97-117`):**
   - Mapeo canónico multi-proveedor para desacoplar IDs externos.
   - Columnas: `id`, `game_id` (FK a `games.id`), `provider` (`the_odds_api`, `nflverse`, `espn`), `provider_event_id`.
   - Restricción: `UniqueConstraint("provider", "provider_event_id")`.
4. **`odds_snapshots` (`app/models/game.py:119-158`):**
   - Hypertable de series temporales para cuotas de mercado observadas.
   - Columnas: `id`, `game_id` (FK), `bookmaker`, `market` (`h2h`, `spreads`, `totals`), `captured_at`, `observed_at` (Index), `home_ml`, `away_ml`, `home_spread`, `home_spread_ml`, `away_spread`, `away_spread_ml`, `total_line`, `over_ml`, `under_ml`, `home_implied_prob`, `away_implied_prob`.
   - Restricción: `UniqueConstraint("game_id", "bookmaker", "market", "observed_at")`.
5. **`predictions` (`app/models/game.py:160-203`):**
   - Predicciones generadas por los modelos validados.
   - Columnas: `id`, `game_id` (FK), `model_version`, `predicted_at`, `dataset_sha256`, `artifact_sha256`, `inference_input_sha256`, `as_of`, `home_win_prob`, `away_win_prob`, `confluence_tier`, `model_agreement`, `best_bookmaker`, `best_home_ml`, `best_away_ml`, `home_ev`, `away_ev`, `home_kelly`, `away_kelly`, `home_has_edge`, `away_has_edge`.
   - Restricción: `UniqueConstraint("game_id", "model_version", "artifact_sha256", "inference_input_sha256")`.
6. **`bankroll_entries` (`app/models/game.py:205-227`):**
   - Registro de transacciones y cálculo de ROI/Kelly.
7. **`sports_cards` (`app/models/card.py:23-62`):**
   - Catálogo coleccionable (deshabilitado en capa API).

### 6.2. Infraestructura Docker y Estado de Servicios
Estado actual verificado en el host:
- **`sports_db` (`timescale/timescaledb:latest-pg16`):** Activo (`Up 33 min`, Healthy), puerto `5432:5432`.
- **`sports_redis` (`redis:7-alpine`):** Activo (`Up 33 min`, Healthy), puerto `6379:6379`.
- **`sports_api` (`uvicorn app.main:app`):** Activo (`Up 33 min`), puerto `8000:8000`.
- **`sports_worker` (`celery worker`):** Activo (`Up 33 min`), colas `ingestion,ml,alerts`.
- **`sports_beat` (`celery beat`):** **DETENIDO / EXITED (0)** hace 3 horas.

### 6.3. Uso de Redis (Caché vs Broker)
- **Rol actual de Redis:**
  - Broker de Celery: `CELERY_BROKER_URL = "redis://sports_redis:6379/1"`.
  - Backend de resultados de Celery: `CELERY_RESULT_BACKEND = "redis://sports_redis:6379/2"`.
- **Subutilización de Redis como Caché de Aplicación:**
  - Aunque `REDIS_URL = "redis://localhost:6379/0"` está configurado en `settings.py`, **ningún servicio de la aplicación utiliza Redis directamente para caché de lectura**.
  - La aplicación depende de diccionarios en memoria de proceso (`_ODDS_CACHE` con 900s TTL en `odds_service.py` y `_HISTORICAL_DF_CACHE` en `matchup_context_service.py`) y archivos JSON en disco (`data/cache/`).
  - Esto provoca que workers distintos o reinicios de Uvicorn no compartan estado de caché.

### 6.4. Pipelines de Tareas en Segundo Plano (Celery & Celery Beat)
- **Colas configuradas:**
  - `ingestion`: Ingesta de cuotas y capturas de lesiones.
  - `ml`: Inferencia y evaluación de modelos.
  - `alerts`: Alertas de arbitraje de tarjetas.
- **Tareas Programadas (Beat Schedule en `backend/app/workers/celery_app.py:41-82`):**
  1. `ingest-nfl-odds-optimized` (`ingest_odds_task`):
     - Días: Jueves, Sábado, Domingo, Lunes (`0,1,4,6`) a las 14:00 y 20:00 UTC.
     - Ingesta cuotas NFL reales llamando a `sync_real_games` con reintentos exponenciales ante `ProviderError`.
  2. `ingest-nfl-schedule-weekly` (`ingest_nfl_schedule_task`):
     - Martes a las 04:00 UTC. Sincroniza calendarios NFLverse con `sync_nfl_games_from_source`.
  3. `schedule-nfl-gameday-captures` (`schedule_nfl_gameday_captures_task`):
     - Días de juego a las 12:00 UTC.
     - Dispara inmediatamente la captura matutina de lesiones (`start_of_day`).
     - Agrupa los partidos del día por kickoff y programa dinámicamente tareas prepartido (`near_kickoff`) con `apply_async(eta=...)` **exactamente 90 minutos antes del kickoff**, disparando evaluación de modelos posterior.
  4. `evaluate-nfl-predictions-optimized` (`evaluate_predictions_task`):
     - Días de juego a las 14:15 y 20:15 UTC. Ejecuta `predict_matchups` y persiste en `predictions`.

---

## 7. Matriz de Características Existentes vs Brechas (Gaps) contra R1 y R4

| Requisito | Funcionalidad Existente | Brecha / Hallazgo Detectado | Acción Requerida |
|---|---|---|---|
| **R1 (Zero-Synthetic)** | Ingesta real de NFLverse, ESPN y The Odds API operativa. Persistencia Parquet con SHA-256. | `seed_nfl.py` contiene registros sintéticos con cuotas y probabilidades inventadas. | Asegurar que `seed_nfl.py` no se ejecute en ambientes de staging/producción y purgar cualquier residuo `nfl_2026_%`. |
| **R1 (Zero-Imputation)** | `NFLAvailabilityService` no imputa 0 ni inventa priors; retorna `None` y estados explícitos (`UNKNOWN`, `NO_INJURIES_LISTED`). | `sports_analytics_service.py` no tiene modelos para spread y totales (están en `UNAVAILABLE`). | Entrenar y calibrar modelos matemáticos/ML para spread/hándicap y totales sin inventar ratings fijos. |
| **R1 (Model Gate)** | Mecanismo de seguridad `get_approved_model_version()` rechaza modelos no aprobados. | Los dos modelos NFL (`nfl_temporal_v1` y `nfl_team_only_temporal_v1`) tienen `production_enabled: false`. Endpoints responden 0 picks. | Validar, evaluar temporalmente y activar el ensamble de 2-3 modelos con `production_enabled: true`. |
| **R4 (Odds Quota)** | Ledger reconciliado con 236 créditos restantes, locks atómicos y división por sobres (líneas: 45, props: 320). 284 tests pasan sin tocarlo. | Ninguna brecha funcional en la protección de cuota. El ledger está blindado. | Mantener las aserciones de invariabilidad del SHA-256 en futuras suites. |
| **R4 (No Blocking GET)** | Endpoints de picks (`/predictions/today`, `/parlay/recommended`) no realizan I/O de red. | Tres endpoints GET realizan peticiones externas sincrónicas/bloqueantes: `/games/{id}/external-prediction`, `/games/{id}/context` (en caché vencida) y `/odds/remaining`. | Rediseñar estos 3 endpoints: mover la captura externa exclusivamente a Celery y hacer que los GET lean únicamente de PostgreSQL o caché local Redis/archivo. |
| **R4 (Infraestructura)** | Contenedores `sports_db`, `sports_redis`, `sports_api` y `sports_worker` saludables y activos. | `sports_beat` se detuvo (`Exited 0`) y no tiene montado `./data:/app/data` en `docker-compose.yml`. | Corregir el volumen de `sports_beat` en `docker-compose.yml` y reiniciar el contenedor para garantizar la ejecución de tareas programadas. |

---

## 8. Conclusiones y Próximos Pasos Recomendados

1. **Blindaje de Endpoints GET (R4):**
   - Refactorizar `GET /api/v1/games/{game_id}/context` para que nunca invoque `urllib.request.urlopen` durante el request; si la caché local no existe, debe responder con el último snapshot point-in-time guardado en PostgreSQL o retornar `status: "unavailable"`.
   - Desacoplar `GET /api/v1/games/{game_id}/external-prediction`: las predicciones de ESPN deben ser ingeridas asíncronamente por Celery worker y leídas desde BD.
   - Refactorizar `GET /api/v1/odds/remaining` para que lea los datos de cuota restante directamente desde el ledger local (`data/odds_quota_ledger.json`) sin consultar la API externa.
2. **Activación de Modelos y Ensamble (R1, R2, R3 & R5):**
   - Disponer de un ensamble de 2 a 3 modelos independientes (ej. Regresión Logística, HistGradientBoosting, y Random Forest o Red Neuronal liviana) validados mediante walk-forward temporal sobre las temporadas 2020-2026.
   - Incorporar el cálculo dinámico de impacto de lesiones basado en fantasy points previos (conforme a `NFLAvailabilityService`).
   - Habilitar `production_enabled: true` en el manifest correspondiente para que los endpoints `/predictions/today?sport=NFL` y `/parlay/recommended?sport=NFL` entreguen picks reales con confluencia y +EV al frontend.
3. **Estabilización de Celery Beat (R4):**
   - Levantar y monitorear `sports_beat` en Docker para que la ingesta de cuotas y las capturas prepartido se ejecuten automáticamente en las ventanas de kickoff.
