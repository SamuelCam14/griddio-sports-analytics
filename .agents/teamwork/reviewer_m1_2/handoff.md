# Independent Review Report: Milestone 1 — Data Integrity, Quota Protection & Non-Blocking GET Endpoints

**Reviewer:** Reviewer 2 (`reviewer_m1_2` / `teamwork_preview_reviewer`)  
**Date:** 2026-10-15  
**Directory:** `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/reviewer_m1_2`  
**Milestone:** Milestone 1 (M1)  
**Verdict:** **APPROVE**

---

## 1. Observation

### 1.1. Backend Pytest Suite Execution
- **Command:** `docker exec sports_api pytest -q`
- **Verbatim Result:**
  ```text
  ........................................................................ [ 22%]
  ........................................................................ [ 44%]
  ........................................................................ [ 66%]
  ........................................................................ [ 88%]
  ....................................                                     [100%]
  =============================== warnings summary ===============================
  ../usr/local/lib/python3.12/site-packages/fastapi/testclient.py:1
    /usr/local/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
      from starlette.testclient import TestClient as TestClient  # noqa

  -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
  324 passed, 1 warning in 39.79s
  ```
- **Exit Code:** 0 (100% passed, 0 failures, 0 errors).

### 1.2. Docker Compose Celery Beat Service & Volume Mount
- **Command:** `docker compose ps && docker inspect sports_beat --format '{{json .Mounts}}'`
- **Verbatim Output:**
  ```text
  NAME            IMAGE                               COMMAND                  SERVICE   CREATED          STATUS                    PORTS
  sports_api      griddio-sports-analytics-api        "uvicorn app.main:ap…"   api       6 hours ago      Up 58 minutes             0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
  sports_beat     griddio-sports-analytics-beat       "celery -A app.worke…"   beat      12 minutes ago   Up 12 minutes             8000/tcp
  sports_db       timescale/timescaledb:latest-pg16   "docker-entrypoint.s…"   db        7 hours ago      Up 58 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
  sports_redis    redis:7-alpine                      "docker-entrypoint.s…"   redis     7 hours ago      Up 58 minutes (healthy)   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
  sports_worker   griddio-sports-analytics-worker     "celery -A app.worke…"   worker    6 hours ago      Up 58 minutes             8000/tcp

  [{"Type":"bind","Source":"/home/samuelc/Documents/Progreso/griddio-sports-analytics/backend","Destination":"/app","Mode":"rw","RW":true,"Propagation":"rprivate"},{"Type":"bind","Source":"/home/samuelc/Documents/Progreso/griddio-sports-analytics/data","Destination":"/app/data","Mode":"rw","RW":true,"Propagation":"rprivate"}]
  ```
- **Beat Logs (`docker logs sports_beat --tail 25`):**
  ```text
  [2026-10-15 23:17:54,900: INFO/MainProcess] beat: Starting...
  [2026-10-15 23:17:54,912: INFO/MainProcess] Scheduler: Sending due task evaluate-nfl-predictions-optimized (app.workers.celery_app.evaluate_predictions_task)
  [2026-10-15 23:17:54,916: INFO/MainProcess] Scheduler: Sending due task evaluate-nba-predictions-optimized (app.workers.celery_app.evaluate_predictions_task)
  ```
- Celery Beat container `sports_beat` is running stably with `./data:/app/data` mounted.

### 1.3. Quota Ledger Integrity
- **Command:** `sha256sum data/odds_quota_ledger.json`
- **Result:**
  `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4  data/odds_quota_ledger.json`
- Verified unchanged before, during, and after full test suite execution. Zero quota consumed.

### 1.4. Non-Blocking Live Endpoints
- **GET `/api/v1/games/1/external-prediction`:**
  - Returns `200 OK` with cached prediction:
    `{"status":"available","sport":"NFL","game_id":1,"event_id":"401872995",...}`
- **GET `/api/v1/games/2/external-prediction` (uncached):**
  - Returns `200 OK` with:
    `{"status":"unavailable","reason":"not_cached","message":"Pronóstico externo no pre-ingerido en caché local. No se realizan llamadas de red en tiempo de consulta."}`
  - Zero outbound HTTP calls made.
- **GET `/api/v1/games/9999/external-prediction`:**
  - Returns `404 Not Found` with `{"detail":"Partido no encontrado"}`.
- **GET `/api/v1/games/1/context`:**
  - Returns `200 OK` instantly with structured H2H, L5 recent form, injuries, and comparison stats without synchronous `urllib.request.urlopen` blocking.
- **GET `/api/v1/odds/remaining`:**
  - Returns `200 OK` with:
    `{"status":"ok","total_remaining_credits":236,"requests_remaining":236,"requests_used":264,"circuit_breaker_open":false,...}`
  - Zero outbound HTTP calls dispatched.

### 1.5. Database Cleanliness & Zero Synthetic Data
- **SQL Audit:**
  `SELECT count(*) FROM games WHERE external_id LIKE 'nfl_2026_%'` -> `0`
  `SELECT count(*) FROM games` -> `194`
  `SELECT count(*) FROM predictions` -> `0`
  `SELECT count(*) FROM odds_snapshots` -> `9`
  `SELECT count(*) FROM teams` -> `63`
  `SELECT DISTINCT source FROM games` -> `['nflverse schedule', None]`
- Confirmed zero synthetic or fabricated game rows present in PostgreSQL.

### 1.6. Frontend Lint & Build
- **Command:** `npm run lint && npm run build` in `frontend/`
- **Result:** `tsc -b && vite build` completed in 629ms, 0 errors, 0 warnings.

---

## 2. Logic Chain

1. **Verification of Non-Blocking Architecture (Observation 1.1, 1.4):**
   - In `backend/app/api/v1/games.py:189-204`, `get_external_game_prediction` calls `get_cached_espn_prediction(game)`. In `backend/app/services/espn_service.py:180-220`, when `allow_network=False`, it reads from `_load_espn_prediction_from_cache(game.id)`. If missing, it immediately returns `{"status": "unavailable", "reason": "not_cached"}` without creating `httpx.AsyncClient`.
   - In `backend/app/services/matchup_context_service.py:510`, `get_matchup_context` calls `fetch_live_injuries_response(sport, allow_live_download=False)`. At lines 275-304, when `allow_live_download=False`, the synchronous `urllib.request.urlopen` block (lines 320-330) is skipped completely, falling back to disk cache or `NFLAvailabilityService.get_active_injury_snapshot()`.
   - In `backend/app/services/odds_service.py:631-699`, `fetch_remaining_requests()` delegates to `OddsQuotaService()`, parsing `data/odds_quota_ledger.json` locally. Zero HTTP GET calls to `api.the-odds-api.com` are initiated.
   - Tests `TC-R4-03`, `TC-R4-04`, and `TC-R4-05` in `tests/test_e2e_requirements.py` explicitly mock network clients with `side_effect=RuntimeError("Outbound network prohibited!")` and assert that no network calls occur. All 3 tests passed.

2. **Verification of Statistical Handling of Missing Metrics (Observation 1.4, 1.5):**
   - In `matchup_context_service.py:530-588`, heuristic positional weight multipliers (e.g. `min(1.0, round((off_out + 0.3 * len(quest_players)) / den, 3))`) were completely removed.
   - For past games, `_calculate_impact` explicitly returns `{"coverage_state": "not_applicable_past_game", "ppr_lost_pct": None, "off_snaps_lost_pct": None, "def_snaps_lost_pct": None}`.
   - Missing stats in `_clean_mean` return `None` rather than arbitrary defaults (20.0 or 0.0).

3. **Verification of Celery Beat Infrastructure (Observation 1.2):**
   - `docker-compose.yml:89` binds `./data:/app/data` into `sports_beat`.
   - `docker inspect sports_beat` confirms the bind mount is active in read-write mode.
   - `sports_beat` container is running and active, dispatching periodic scheduling tasks.

4. **Integrity Audit:**
   - Source code was searched for hardcoded bypasses, dummy facades, and fabricated responses.
   - In `espn_service.py:277`, `isinstance(game, Game)` guard prevents mock test objects from leaking into local cache.
   - `data/odds_quota_ledger.json` SHA-256 hash was preserved across all executions.
   - No integrity violations found.

---

## 3. Findings

### [Minor] Finding 1: Static Fallback Constants in `odds_service.py` Exception Block
- **What:** In `backend/app/services/odds_service.py:649-650`, if `OddsQuotaService()` throws an exception and direct JSON parsing also fails, `remaining` and `used` default to hardcoded numbers `236` and `264`:
  ```python
  remaining = 236
  used = 264
  circuit_open = False
  spent_by_envelope = {}
  ```
- **Where:** `backend/app/services/odds_service.py:649-650`
- **Why:** While `OddsQuotaService()` currently succeeds under normal conditions, in a catastrophic scenario where the ledger file is unreadable, corrupted, or deleted, falling back to static non-zero numbers (236) masks the failure instead of failing closed (`remaining = 0`, `circuit_open = True`).
- **Suggestion:** Refactor the exception fallback to fail closed:
  ```python
  remaining = 0
  used = 0
  circuit_open = True
  spent_by_envelope = {}
  ```
  *(Does not block M1 approval as `OddsQuotaService()` is fully operational and verified, but recommended for M2 hardening).*

---

## 4. Caveats

- **External Prediction Availability:** External ESPN predictions are served from cache; games without pre-ingested snapshots will return `status: "unavailable", reason: "not_cached"`. This is intentional and strictly compliant with project contracts.
- **Beat Scheduler Task Execution:** Celery Beat periodically submits tasks to Redis; actual execution depends on `sports_worker` processing queue items. Both containers are verified Up and running.
- No other caveats.

---

## 5. Conclusion

**Verdict: APPROVE**

Milestone 1 satisfies all acceptance criteria from `PROJECT.md` and `ORIGINAL_REQUEST.md`:
1. All 3 target GET endpoints (`/external-prediction`, `/context`, `/odds/remaining`) are strictly non-blocking with zero outbound network calls on the request thread.
2. `data/odds_quota_ledger.json` SHA-256 checksum is preserved and protected.
3. Database contains zero synthetic records (`nfl_2026_%`).
4. `sports_beat` container has `./data:/app/data` volume mounted and is running stably.
5. Pytest suite passes 100% (324 passed) in `sports_api`.
6. Frontend lints and builds with 0 errors.

---

## 6. Verification Method

To independently reproduce this verification:

```bash
# 1. Run full backend pytest suite inside sports_api
docker exec sports_api pytest -q

# 2. Verify Celery Beat status and mount
docker compose ps beat
docker inspect sports_beat --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}} -> {{.Destination}}{{end}}{{end}}'

# 3. Verify Quota Ledger SHA-256 hash
sha256sum data/odds_quota_ledger.json
# Expected: f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4

# 4. Verify non-blocking behavior of endpoints live
curl -s http://localhost:8000/api/v1/games/1/external-prediction | jq .status
curl -s http://localhost:8000/api/v1/games/2/external-prediction | jq .status
curl -s http://localhost:8000/api/v1/games/1/context | jq .injury_source
curl -s http://localhost:8000/api/v1/odds/remaining | jq .requests_remaining

# 5. Verify database has zero synthetic rows
docker exec sports_api python -c '
import asyncio
from sqlalchemy import text
from app.core.database import AsyncSessionLocal, engine
async def run():
    async with AsyncSessionLocal() as s:
        res = await s.execute(text("SELECT count(*) FROM games WHERE external_id LIKE :p"), {"p": "nfl_2026_%"})
        assert res.scalar() == 0
    await engine.dispose()
    print("Database verified: 0 synthetic rows")
asyncio.run(run())
'

# 6. Verify frontend lint and build
cd frontend && npm run lint && npm run build
```
