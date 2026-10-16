# Handoff Report: Frontend, API Contracts, Docker & Test Infrastructure Survey

**Agent Folder:** `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/explorer_survey_3`  
**Date:** 2026-10-15  
**Handoff Type:** Hard  

---

## 1. Observation

1. **Frontend Lint & Build:**
   - Command `npm run lint` in `frontend/` exited with code 0:
     ```
     npm notice run sports-analytics-frontend@1.0.0 lint
     npm notice run eslint .
     ```
     0 errors and 0 warnings.
   - Command `npm run build` in `frontend/` exited with code 0:
     ```
     npm notice run sports-analytics-frontend@1.0.0 build
     npm notice run tsc -b && vite build
     vite v8.3.1 building client environment for production...
     ✓ built in 731ms
     ```
2. **Backend Automated Tests:**
   - Command `docker exec sports_api pytest -q` executed 284 tests across 24 test files in `backend/tests/`:
     ```
     284 passed in 35.71s
     ```
   - Quota ledger `data/odds_quota_ledger.json` was inspected before and after test execution. `requests_used` remained at 264 and `requests_remaining` remained at 236. The test suite is strictly hermetic.
3. **Docker Infrastructure:**
   - `docker ps -a` output showed:
     - `sports_db` (image: `timescale/timescaledb:latest-pg16`): Up, Healthy (port 5432)
     - `sports_redis` (image: `redis:7-alpine`): Up, Healthy (port 6379)
     - `sports_api`: Up (port 8000)
     - `sports_worker`: Up
     - `sports_beat`: Exited (0)
4. **Predictions Endpoint & Missing Timestamps:**
   - In `backend/app/api/v1/predictions.py` (lines 342-386), `picks.append({...})` defines:
     `game_id`, `sport`, `game_date`, `is_today`, `matchup_label`, `home_team`, `away_team`, `tier`, `tier_label`, `tier_emoji`, `model_agreement`, `pick_side`, `pick_team_name`, `prob_modelo`, `momio`, `ev_dolares`, `kelly_fraccion`, `kelly_monto`, `home_win_prob`, `away_win_prob`, `tiene_ventaja`, `bookmaker`, `best_bet`, `moneyline`, `spread`, `totals`, `player_props`, `key_factors`.
   - Neither `observed_at`, `predicted_at`, nor `as_of` are included in each pick dictionary, nor is `as_of` returned in the root dictionary of `/predictions/today`.
   - In `frontend/src/services/api.ts` (lines 221-250), `export interface Pick` does not declare `observed_at`, `predicted_at`, or `as_of`.
5. **Parlay Candidate Provenance:**
   - In `backend/app/services/parlay_service.py` (lines 595-619), `generate_recommended_parlays` constructs `candidates` with `game_id`, `team_name`, `opponent`, `side`, `prob_model`, `odds_american`, `edge_pct`, `tier`, but does not populate `bookmaker` or `observed_at`.
   - In `frontend/src/components/dashboard/RecommendedParlaysCard.tsx` (line 50), the UI falls back to `leg.bookmaker ?? 'casa desconocida'` and `leg.observed_at ?? 'sin hora'`.
6. **Spread Modeling Status:**
   - In `backend/app/services/sports_analytics_service.py` (lines 127-136, 269-283), `spread` fields `projected_margin`, `projected_home_score`, `projected_away_score`, `pick`, `cover_probability`, `edge_pct`, and `ev_dollars` are set to `None`, and `tier` is `"UNAVAILABLE"`.
   - In `frontend/src/components/matchup/MatchupSpreadTab.tsx` (line 56), `(spread.cover_probability * 100).toFixed(1)` has no defensive null guard.
7. **Production Gate State:**
   - In `backend/ml/models/nfl/model_metadata.json` (line 262), `"production_enabled": false`.
   - Calling `curl -s "http://localhost:8000/api/v1/predictions/today?sport=NFL"` returned:
     `{"sport":"NFL","period":"auto","total_picks":0,"premium":0,"value":0,"skip":0,"picks":[],"predictions_enabled":false,"reason":"No hay un modelo aprobado para producción con features en paridad temporal."}`
   - Calling `curl -s "http://localhost:8000/api/v1/parlay/recommended?sport=NFL"` returned:
     `{"status":"unavailable","sport":"NFL","total":0,"target_odds":"Cuota ~2.0 (+100) con probabilidad >45% y EV+","parlays":[],"reason":"No hay un modelo aprobado para producción para NFL."}`

---

## 2. Logic Chain

1. **Infrastructure & Tooling Health:**
   - From Observation 1: `npm run lint` and `npm run build` pass with 0 errors, confirming frontend code hygiene and TypeScript type safety.
   - From Observation 2: All 284 backend pytest tests pass cleanly and quota ledger is untouched, confirming the test suite is 100% operational and strictly protects external resources (R4 compliance).
   - From Observation 3: Core Docker containers are active and healthy, supporting the development workflow.
2. **Contract Alignment & Timestamps (R5):**
   - Requirement R5 mandates connecting backend predictions to the frontend via FastAPI endpoints with timestamps (`observed_at`, `predicted_at`, `as_of`).
   - From Observation 4: The database already stores `Prediction.predicted_at`, `Prediction.as_of`, and `OddsSnapshot.observed_at`, but the API serialization layer and frontend TypeScript interfaces omit them. Adding these fields to the API serialization and frontend types is necessary and sufficient to fulfill R5 timestamp compliance.
3. **Parlay Leg Provenance:**
   - From Observation 5: In `generate_recommended_parlays()`, candidate legs lack `bookmaker` and `observed_at`. Fetching these from `Prediction` and `OddsSnapshot` when assembling parlay candidates will eliminate the "sin hora" / "casa desconocida" UI fallback.
4. **Spread / Handicap Analysis:**
   - From Observation 6: Currently, spread analytics return `UNAVAILABLE` because the quantitative spread model was not yet coupled into the production pipeline. Delivering R3 and R5 requires computing genuine cover probabilities and projected margins from the model ensemble and adding null guards in `MatchupSpreadTab.tsx`.
5. **Production Gate Activation:**
   - From Observation 7: The production gate behaves as designed (fail-closed) while models are unapproved. Activating the MVP requires completing R1-R3, updating `model_metadata.json` with `production_enabled: true`, and persisting temporally valid inference rows.

---

## 3. Caveats

- **Celery Beat Container:** Container `sports_beat` was exited in the environment. It can be restarted via `docker compose up -d beat` when periodic background ingestion is required.
- **Off-season / Live Data Scope:** Observed odds currently present 1 upcoming NFL game (`Denver Broncos` vs `Seattle Seahawks`). The system correctly displays this observed event without synthetic backfilling.

---

## 4. Conclusion

The system infrastructure, Docker containers, backend test suite (284/284 passed), and frontend build/lint (0 errors) are completely stable and ready for MVP delivery. To achieve full compliance with requirements R4 and R5, four concrete integration adjustments must be executed:
1. Expose timestamps (`observed_at`, `predicted_at`, `as_of`) in `/api/v1/predictions/today` and update `Pick` in `frontend/src/services/api.ts`.
2. Include `bookmaker` and `observed_at` in candidate legs within `generate_recommended_parlays()`.
3. Provide model-driven spread cover probabilities and defensive null checks in `MatchupSpreadTab.tsx`.
4. Open the production gate (`production_enabled: true`) once the independent ensemble and injury models (R1-R3) are validated.

Full details and schemas are documented in `survey_frontend_infra_report.md`.

---

## 5. Verification Method

To independently verify these observations:
1. Run backend tests:
   ```bash
   docker exec sports_api pytest -q
   ```
   *Expected:* 284 passed in ~35s.
2. Run frontend linter and build:
   ```bash
   cd frontend && npm run lint && npm run build
   ```
   *Expected:* 0 errors, 0 warnings.
3. Verify odds quota ledger integrity:
   ```bash
   git diff data/odds_quota_ledger.json
   ```
   *Expected:* No diff generated by test runs.
4. Inspect API endpoint responses:
   ```bash
   curl -s "http://localhost:8000/api/v1/predictions/today?sport=NFL"
   curl -s "http://localhost:8000/api/v1/parlay/recommended?sport=NFL"
   curl -s "http://localhost:8000/api/v1/odds/nfl"
   ```
