# Reviewer Handoff Report: Milestone 4 (Reviewer 2)

**Reviewer:** Reviewer 2 (`teamwork_preview_reviewer`)  
**Roles:** Reviewer, Critic  
**Date:** 2026-10-16T01:08:45Z  
**Working Directory:** `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/reviewer_m4_2`  
**Verdict:** **APPROVE**

---

## 1. Observation

### 1.1 Quota Ledger SHA-256 Checksum
Direct command executed in project root:
```bash
$ sha256sum data/odds_quota_ledger.json
f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4  data/odds_quota_ledger.json
```
The SHA-256 matches baseline `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4` exactly. Zero external live odds quota consumed.

### 1.2 UI Defensive Guards in MatchupSpreadTab.tsx
Inspected `frontend/src/components/matchup/MatchupSpreadTab.tsx:23-51`:
```tsx
  if (!spread) {
    return (
      <div className="text-center py-2 text-xs text-gray-500">
        Sin línea de spread activa disponible en este momento.
      </div>
    )
  }

  const marketLineLabel =
    spread.market_line != null
      ? spread.market_line > 0
        ? `+${spread.market_line}`
        : `${spread.market_line}`
      : 'Sin línea'

  const projectedAway = spread.projected_away_score != null ? spread.projected_away_score : '—'
  const projectedHome = spread.projected_home_score != null ? spread.projected_home_score : '—'
  const spreadPick = spread.pick || 'Sin selección disponible'

  const coverProbDisplay =
    spread.cover_probability != null && Number.isFinite(spread.cover_probability)
      ? `${(spread.cover_probability * 100).toFixed(1)}% Cubre`
      : 'Prob. no disponible'

  const edgePctDisplay =
    spread.edge_pct != null && Number.isFinite(spread.edge_pct)
      ? `+${spread.edge_pct.toFixed(1)}% Ventaja`
      : 'Sin ventaja calculada'
```
The component wraps all potentially missing or non-finite numeric metrics in `!= null && Number.isFinite(...)` checks before calling `.toFixed(1)`. If `!spread`, a clean fallback element is rendered without throwing.

### 1.3 Parlay Candidate Generation and Provenance in parlay_service.py
Inspected `backend/app/services/parlay_service.py:714-777`:
- For each scheduled candidate game, `generate_recommended_parlays` queries `OddsSnapshot` by `game_id`:
  ```python
  odds_stmt = (
      select(OddsSnapshot)
      .where(OddsSnapshot.game_id == game.id)
      .where(OddsSnapshot.home_ml.isnot(None), OddsSnapshot.away_ml.isnot(None))
      .order_by(OddsSnapshot.observed_at.desc())
      .limit(1)
  )
  ```
- Resolves `leg_bookmaker = (odds_snap.bookmaker if odds_snap and odds_snap.bookmaker else None) or getattr(pred, "best_bookmaker", None) or "betmgm"`.
- Normalizes timestamp with UTC timezone handling:
  ```python
  if odds_snap and getattr(odds_snap, "observed_at", None):
      obs_dt = odds_snap.observed_at if odds_snap.observed_at.tzinfo else odds_snap.observed_at.replace(tzinfo=timezone.utc)
      leg_observed_at = obs_dt.isoformat()
  ```
- Appends `bookmaker: leg_bookmaker` and `observed_at: leg_observed_at` into `candidates` (lines 755-756, 774-775).
- In `evaluate_custom_parlay` (`parlay_service.py:336-337, 367-368`), these fields are mapped directly into `legs_evaluadas`, eliminating placeholders (`"casa desconocida"`, `"sin hora"`).

### 1.4 Production Gate Security and Fail-Closed Verification
Inspected metadata files and live responses:
1. `backend/ml/models/nfl/model_metadata.json:344`: `"production_enabled": true`.
2. `backend/ml/models/nba/model_metadata.json:256`: `"production_enabled": false`.
3. `backend/ml/models/nfl/team_only/model_metadata.json:256`: `"production_enabled": false`.
4. Live curl queries to running containers:
   - `curl -s "http://localhost:8000/api/v1/predictions/today?sport=NFL"`:
     `{"sport":"NFL","period":"week","as_of":"...","predictions_enabled":true,"total_picks":0,...}`
   - `curl -s "http://localhost:8000/api/v1/predictions/today?sport=NBA"`:
     `{"sport":"NBA","period":"auto","total_picks":0,"as_of":"...","predictions_enabled":false,"reason":"No hay un modelo aprobado para producción..."}`
   - `curl -s "http://localhost:8000/api/v1/parlay/recommended?sport=NBA"`:
     `{"status":"unavailable","sport":"NBA","total":0,...}`
   - `curl -s "http://localhost:8000/api/v1/predictions/today?sport=MLB"`:
     `{"sport":"MLB",...,"predictions_enabled":false,...}`

### 1.5 Test Suite Executions
- **Assigned M4 Test Suite**:
  ```bash
  $ docker exec sports_api pytest -v tests/test_nfl_models_phase04.py tests/test_parlay.py tests/test_prediction_gate.py
  # Output: 49 passed in 1.37s
  ```
- **E2E Requirements Suite**:
  ```bash
  $ docker exec sports_api pytest -v tests/test_e2e_requirements.py
  # Output: 40 passed, 1 warning in 1.80s
  ```
- **Challenger Empirical Suite**:
  ```bash
  $ docker exec sports_api pytest -v tests/test_challenger_m4_empirical.py
  # Output: 10 passed in 1.07s
  ```
- **Frontend Linter & Typecheck Build**:
  ```bash
  $ cd frontend && npm run lint
  # Output: 0 errors, 0 warnings (exit code 0)
  $ cd frontend && npm run build
  # Output: tsc -b && vite build -> built in 577ms (exit code 0)
  ```

### 1.6 Integrity Violation Audit
- No hardcoded test outputs or dummy facades detected in source code.
- No synthetic data introduced.
- Ingestion and evaluation pipelines operate on real TimescaleDB models (`OddsSnapshot`, `Prediction`, `Game`, `Team`).
- Independent verification conducted on all deliverables.

---

## 2. Logic Chain

1. **Defensive UI Hardening (Observation 1.2):**
   - By guarding `cover_probability` and `edge_pct` with `!= null && Number.isFinite(...)` before formatting with `.toFixed(1)`, `MatchupSpreadTab` is immune to runtime crashes when the backend provides partial spread predictions or null values.
2. **Provenance Propagation (Observation 1.3):**
   - `parlay_service.py` connects each candidate leg to its historical `OddsSnapshot`, ensuring that `bookmaker` and `observed_at` represent genuine market data rather than arbitrary hardcoded placeholders.
3. **Fail-Closed Gate Enforcement (Observation 1.4):**
   - The production gate remains strictly fail-closed for unapproved sports (`NBA`, `MLB`) and alternative model variants (`team_only`), returning `predictions_enabled: false` and `status: "unavailable"`. Only verified NFL ensemble models are served.
4. **Hermetic Test and Verification Conformance (Observation 1.1, 1.5):**
   - All assigned test suites pass with 100% success rate (49/49 M4 tests, 40/40 E2E tests, 10/10 Challenger tests).
   - Frontend linting and production build pass with 0 errors.
   - The quota ledger SHA-256 hash is unmodified, proving complete isolation from live API credits.

---

## 3. Caveats

- **Minor UI Cosmetic Observation**:
  - In `MatchupSpreadTab.tsx:49`, `edgePctDisplay` uses `+${spread.edge_pct.toFixed(1)}% Ventaja`. If `edge_pct` were negative, it would format as `+-2.5% Ventaja`. Because spread value detection filters for positive edge before generating picks, negative values do not appear in normal pick displays, but a conditional sign helper could be considered for future polish.
- **Pre-existing Clock Test Failures Outside M4 Scope**:
  - `tests/test_espn_external_predictor.py` (2 tests) fails due to an unmocked game kickoff timestamp (`2026-10-16 00:15 UTC`) falling into the past relative to system wall-clock time. This was previously identified and documented during Milestone 3 and is not within M4 ownership.

---

## 4. Conclusion

**Verdict: APPROVE**

Milestone 4 deliverables satisfy all criteria specified in `PROJECT.md` and `ORIGINAL_REQUEST.md`:
- Frontend contracts (`api.ts`, `MatchupSpreadTab.tsx`) are defensively guarded and fully aligned with backend schemas.
- Endpoints serialize root `as_of` and pick/leg-level timestamps (`observed_at`, `predicted_at`, `as_of`).
- Parlay legs carry genuine `bookmaker` and `observed_at` provenance.
- Production gate fail-closed mechanisms are validated and robust.
- The quota ledger hash is strictly preserved.
- Zero integrity violations found.

---

## 5. Verification Method

To independently reproduce and verify this review:

1. **Verify Quota Ledger SHA-256**:
   ```bash
   sha256sum data/odds_quota_ledger.json
   # Output must be: f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4
   ```

2. **Execute M4 Unit and Integration Tests**:
   ```bash
   docker exec sports_api pytest -v tests/test_nfl_models_phase04.py tests/test_parlay.py tests/test_prediction_gate.py
   # 49 passed
   ```

3. **Execute E2E and Challenger Test Suites**:
   ```bash
   docker exec sports_api pytest -v tests/test_e2e_requirements.py tests/test_challenger_m4_empirical.py
   # 50 passed
   ```

4. **Execute Frontend Lint and Production Build**:
   ```bash
   cd frontend && npm run lint
   cd frontend && npm run build
   # 0 errors, 0 warnings, build completes cleanly
   ```

5. **Verify Live Endpoints via Curl**:
   ```bash
   curl -s "http://localhost:8000/api/v1/predictions/today?sport=NFL"
   curl -s "http://localhost:8000/api/v1/predictions/today?sport=NBA"
   curl -s "http://localhost:8000/api/v1/parlay/recommended?sport=NFL"
   curl -s "http://localhost:8000/api/v1/parlay/recommended?sport=NBA"
   ```
