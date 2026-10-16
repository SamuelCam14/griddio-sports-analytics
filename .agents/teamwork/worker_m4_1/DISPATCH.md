## 2026-10-16T00:47:44Z

You are the Frontend Integration and Real-Time Endpoints Worker (teamwork_preview_worker) for Milestone 4 of the NFL Analytics MVP project.

Your working directory is: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/worker_m4_1
Project root: /home/samuelc/Documents/Progreso/griddio-sports-analytics
Authoritative Request: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/ORIGINAL_REQUEST.md
Master Project Specification: /home/samuelc/Documents/Progreso/griddio-sports-analytics/PROJECT.md
Survey Evidence Report: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/explorer_survey_3/survey_frontend_infra_report.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY INSTRUCTIONS:
1. Read ORIGINAL_REQUEST.md, PROJECT.md, and the Survey Evidence Report before starting work.
2. File Write Ownership:
   You exclusively own:
   - backend/app/api/v1/predictions.py
   - backend/app/api/v1/parlay.py
   - backend/app/services/parlay_service.py
   - backend/ml/models/nfl/model_metadata.json
   - backend/tests/test_nfl_models_phase04.py
   - backend/tests/test_parlay.py
   - backend/tests/test_prediction_gate.py
   - frontend/src/services/api.ts
   - frontend/src/components/matchup/MatchupSpreadTab.tsx
   - frontend/src/components/dashboard/RecommendedParlaysCard.tsx
   Do NOT modify database migration files or core ML training scripts.

3. Scope & Implementation Tasks (Requirement R5 & Acceptance Criteria):
   - Task 1: Timestamp Provenance Serialization in API:
     * In backend/app/api/v1/predictions.py, ensure GET /api/v1/predictions/today?sport=NFL exposes root `as_of` (ISO UTC timestamp) and each item in `picks` contains `observed_at`, `predicted_at`, and `as_of` (using genuine timestamps from Prediction and odds records).
     * In backend/app/api/v1/parlay.py and backend/app/services/parlay_service.py, ensure `legs_evaluadas` in GET /api/v1/parlay/recommended?sport=NFL include `bookmaker` (e.g. "betmgm") and `observed_at` (ISO timestamp), eliminating placeholder strings like "casa desconocida" and "sin hora".
   - Task 2: Production Gate Activation:
     * In backend/ml/models/nfl/model_metadata.json, set `"production_enabled": true`.
     * In backend/tests/test_nfl_models_phase04.py, backend/tests/test_parlay.py, and backend/tests/test_prediction_gate.py, update the legacy tests that previously asserted `production_enabled == False` by default to assert that production is enabled for approved NFL models.
   - Task 3: Frontend Contract Alignment:
     * In frontend/src/services/api.ts, update the TypeScript interfaces:
       - `Pick`: add `observed_at?: string; predicted_at?: string; as_of?: string;`
       - `MatchupSpread`: add `cover_probability?: number; edge_pct?: number; projected_margin?: number; projected_home_score?: number; projected_away_score?: number;`
       - Ensure `ParlayLeg` contains `bookmaker?: string; observed_at?: string;`
   - Task 4: Frontend UI Hardening:
     * In frontend/src/components/matchup/MatchupSpreadTab.tsx, add defensive guards against null/undefined values before calling `.toFixed()` on `cover_probability`, `edge_pct`, etc.
     * In frontend/src/components/dashboard/RecommendedParlaysCard.tsx, ensure bookmaker and observed_at timestamps render cleanly if present.
   - Task 5: Quota Ledger Protection:
     * Verify data/odds_quota_ledger.json is strictly preserved: SHA-256 must remain f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4.
   - Task 6: Execution & Verification:
     * Run backend tests: `docker exec sports_api pytest -q` and confirm 100% pass rate.
     * Run frontend lint: `cd frontend && npm run lint` and confirm 0 errors and 0 warnings.
     * Run frontend build: `cd frontend && npm run build` and confirm clean exit code 0.
     * Query API endpoints via curl to verify real JSON output:
       `curl -s "http://localhost:8000/api/v1/predictions/today?sport=NFL"`
       `curl -s "http://localhost:8000/api/v1/parlay/recommended?sport=NFL"`
4. Output: Write handoff.md in your working directory with all validation commands and results, then send a message to the orchestrator.
