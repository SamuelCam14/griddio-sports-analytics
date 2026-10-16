# Project: Reevaluación y Entrega MVP NFL Analytics

## Architecture
- **Monorepo Structure**:
  - `backend/app/`: FastAPI application, routers (`/api/v1/predictions`, `/api/v1/parlay`, `/api/v1/games`, `/api/v1/odds`), domain services, and database models.
  - `backend/ml/`: Machine learning data pipelines (`nfl_source.py`, `injury_feature_builder.py`, `temporal_features.py`), walk-forward training pipelines (`train_temporal.py`), model artifacts (`backend/ml/models/nfl/`), and inference engine (`inference.py`).
  - `backend/workers/`: Celery application and periodic tasks for live odds ingestion, injury monitoring, pre-kickoff captures, and automated inference.
  - `backend/tests/`: Pytest suite (284+ tests) running inside the `sports_api` container.
  - `frontend/src/`: React 18 / TypeScript 5.6 dashboard using Tailwind CSS, Framer Motion, TanStack Query, and Zustand.
  - `data/`: Dataset storage, raw parquet files, manifests, and the protected quota ledger (`data/odds_quota_ledger.json`).
  - Docker Compose: `sports_db` (TimescaleDB PG16), `sports_redis` (Redis 7), `sports_api` (FastAPI), `sports_worker` (Celery worker), `sports_beat` (Celery beat scheduler).

---

## Feature Inventory

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Real Data Ingestion & Immutability | Ingestion from ESPN, nflverse/nflreadpy, and The Odds API with SHA-256 parquet provenance. Zero synthetic data. | M1 | R1, Survey |
| 2 | Statistical Handling of Missing Metrics | Zero-imputation ban; explicit states (`UNKNOWN`, `NO_INJURIES_LISTED`, `UNAVAILABLE_NO_REPORT`) without arbitrary zeros. | M1 | R1, Survey |
| 3 | Odds API Quota Ledger Protection | Strict preservation of `data/odds_quota_ledger.json`; zero live quota consumption during automated runs or tests. | M1 | R4, Survey |
| 4 | Non-Blocking Frontend GET Endpoints | Eliminate external synchronous/blocking network calls from `GET /games/{id}/external-prediction`, `/games/{id}/context`, and `/odds/remaining`. | M1 | R4, Survey |
| 5 | Docker Infrastructure & Beat Scheduler | Ensure all 5 Docker containers (`sports_db`, `sports_redis`, `sports_api`, `sports_worker`, `sports_beat`) are active and configured with necessary mounts. | M1 | R4, Survey |
| 6 | Dynamic Injury Impact Model | Model player absence impact based on rolling prior-week PPR fantasy points without temporal leakage ($t-1$ strictly). | M2 | R2, Survey |
| 7 | Elimination of Positional Weight Multipliers | Replace arbitrary positional factors with player-specific recent production normalized against team total potential. | M2 | R2, Survey |
| 8 | Pre-Kickoff Inactives Recalculation | Celery pre-kickoff capture at $t-90$ min triggering automated model recalculation and database persistence. | M2 | R2, Survey |
| 9 | Multi-Model Independent Ensemble | 2 to 3 independent models (e.g. Logistic Regression, HistGradientBoosting, Random Forest) with distinct architectures. | M3 | R3, Survey |
| 10 | Vegas Independence of Win Probabilities | Generate pure model win probabilities independent of bookmaker odds using football metrics (EPA, success rate, availability). | M3 | R3, Survey |
| 11 | Model Confluence Decision Rule | Require agreement across independent models (e.g. >= 2/3 agreement) to emit high-confidence picks. | M3 | R3, Survey |
| 12 | Positive Expected Value (+EV) with Minimum Odds | Compute EV using fair no-vig odds; enforce EV > 0 and decimal odds >= 1.40 (American >= -250). | M3 | R3, Survey |
| 13 | Spread / Handicap Value Detection | Model projected margins, calculate cover probabilities, and identify positive expected value against casino spread lines. | M3 | R3, Survey |
| 14 | Correlated Microparlays | Microparlay engine modeling within-game correlations, targeting combined return >= 2.0x (+100) and positive EV. | M3 | R3, Survey |
| 15 | Walk-Forward Validation & Production Gate | Leak-free chronological walk-forward validation (4 folds + holdout lockbox); unlock `production_enabled: true` in metadata. | M3 | R3, Survey |
| 16 | Timestamp Provenance Serialization | Expose `observed_at`, `predicted_at`, and `as_of` in `/api/v1/predictions/today` and `/api/v1/parlay/recommended`. | M4 | R5, Survey |
| 17 | Frontend Contract Alignment & Types | Update TypeScript `Pick` and `MatchupSpread` interfaces in `frontend/src/services/api.ts` to consume timestamps and spread data. | M4 | R5, Survey |
| 18 | Microparlays Dashboard Integration | Ensure `RecommendedParlaysCard` displays real bookmaker, timestamps, combined odds, and evaluated legs without placeholders. | M4 | R5, Survey |
| 19 | Matchup Spread UI Hardening | Add defensive guards to `MatchupSpreadTab.tsx` and render genuine model spread cover probabilities and projected scores. | M4 | R5, Survey |
| 20 | Frontend Build & Lint Cleanliness | Ensure `npm run lint` and `npm run build` execute with 0 errors and 0 warnings. | M4 | R5, Survey |
| 21 | Full Hermetic Backend Pytest Suite | Execute 100% of backend tests inside `sports_api` container with 0 failures and 0 quota consumption. | Final | All, Survey |
| 22 | Requirement-Driven Opaque-Box Test Suite | Implement comprehensive 4-tier test suite (Tiers 1-4) by E2E Testing Track with `TEST_READY.md`. | Final | Acceptance, Survey |
| 23 | Tier 5 Adversarial Coverage Hardening | White-box stress testing and adversarial edge-case verification by Challengers. | Final | Acceptance, Survey |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Data Integrity, Quota Protection & Non-Blocking GETs | Eliminate blocking calls in GET endpoints; purge legacy synthetic records; verify quota ledger immutability; fix `sports_beat` docker mount. | None | DONE (Gate PASS: Auditor CLEAN, Reviewers APPROVE, Challengers APPROVE) |
| M2 | Dynamic Injury Impact Engine & Inactives Tracking | Validate prior-week fantasy points ($t-1$) injury pipeline; ensure zero positional multipliers; test dynamic degradation; wire pre-kickoff recalculation. | M1 Interface Contracts | DONE (Gate PASS: Auditor CLEAN, Reviewers APPROVE, Challengers APPROVE) |
| M3 | Multi-Model Ensemble, +EV, Spread & Microparlays | Implement 2-3 independent models with walk-forward validation; enforce confluence + EV>0 + odds>=1.40; develop spread margin model; generate correlated microparlays (>=2.0x); activate production gate. | M2 | DONE (Gate PASS: Auditor CLEAN, Reviewers APPROVE, Challengers APPROVE) |
| M4 | Frontend Integration, Timestamps & UI Hardening | Expose `observed_at`, `predicted_at`, `as_of` in APIs; update frontend TypeScript contracts; harden spread tab; render real parlay legs; verify lint & build. | M3 | DONE (Gate PASS: Auditor CLEAN, Reviewers APPROVE, Challengers APPROVE) |
| Final | 100% E2E Test Suite Pass & Adversarial Hardening | Pass 100% E2E test suite (Tiers 1-4); perform Tier 5 adversarial stress testing; complete forensic integrity audit. | M4, TEST_READY.md | DONE (Gate PASS: Auditor CLEAN, Reviewers APPROVE, Challengers APPROVE) |

---

## Interface Contracts

### 1. Non-Blocking GET Endpoints ↔ Frontend / Client (M1)
- `GET /api/v1/games/{game_id}/external-prediction`:
  - Must read exclusively from pre-ingested database snapshots in `games` or local cache.
  - If no external prediction exists, return HTTP 200 with `status: "unavailable"` or cached record. Never invoke `httpx` or `urllib` in the request thread.
- `GET /api/v1/games/{game_id}/context`:
  - Must read from database or local snapshot. If cache is stale/missing, return stored historical context; do not block thread with `urllib.request.urlopen`.
- `GET /api/v1/odds/remaining`:
  - Must read directly from `data/odds_quota_ledger.json` without dispatching HTTP calls to The Odds API.

### 2. Availability & Feature Pipeline ↔ Model Inference (M2 ↔ M3)
- Availability Feature Vector:
  - `home_ppr_lost_pct`: Float $[0.0, 1.0]$ representing rolling PPR lost relative to team potential.
  - `away_ppr_lost_pct`: Float $[0.0, 1.0]$.
  - `home_off_snaps_lost_pct`, `away_off_snaps_lost_pct`: Float $[0.0, 1.0]$.
  - `home_def_snaps_lost_pct`, `away_def_snaps_lost_pct`: Float $[0.0, 1.0]$.
  - Temporal Rule: Derived strictly from `season * 100 + week - 1` backward join. If no verified report, value is `None` (triggers `UNKNOWN` coverage state).
- Pre-Kickoff Celery Signal:
  - `capture_nfl_injuries_task("near_kickoff", affected_game_ids=...)` triggers `evaluate_predictions_task("NFL", game_ids=...)` which inserts updated rows into `predictions` table with updated `as_of` and `predicted_at`.

### 3. Multi-Model Inference & Decision Engine ↔ API Endpoints (M3 ↔ M4)
- Model Prediction Output Schema:
  - `home_win_prob`: Float $[0.0, 1.0]$ from ensemble confluence.
  - `away_win_prob`: Float $[0.0, 1.0]$.
  - `individual_models`: Dict with keys `{"logistic": float, "hist_gradient_boosting": float, "random_forest": float}`.
  - `model_agreement`: Integer $[1, 3]$ count of models agreeing on the favorite.
  - `confluence_tier`: `"HIGH"` (3/3), `"MODERATE"` (2/3), or `"NONE"` (<2/3).
- Decision Rules for Pick Emission:
  - `tier`: `"PREMIUM"` if confluence $\ge 2/3$ and $EV \ge 5.0$ and odds $\ge 1.40$.
  - `tier`: `"VALUE"` if confluence $\ge 2/3$ and $EV \ge 3.0$ and odds $\ge 1.40$.
  - If odds $< 1.40$ or $EV \le 0$ or confluence $< 2/3$, emit `tier = "SKIP"` or no pick.
- Spread Analysis Schema:
  - `market_line`: Float (e.g. `-2.5`).
  - `projected_home_score`: Float (e.g. `24.5`).
  - `projected_away_score`: Float (e.g. `21.0`).
  - `projected_margin`: Float (`projected_home_score - projected_away_score`).
  - `cover_probability`: Float $[0.0, 1.0]$.
  - `spread_pick`: String (e.g. `"Denver Broncos -2.5"`).
  - `edge_pct`: Float (e.g. `4.5`).
  - `ev_spread`: Float.
- Microparlay Schema:
  - `momio_decimal`: Float $\ge 2.0$ (American $\ge +100$).
  - `ev_dolares`: Float $> 0.0$.
  - `viable`: Boolean (`True` if $EV > 0$ and odds $\ge 2.0$ and correlated logic validated).

### 4. API Endpoints ↔ Frontend Dashboard (M4)
- `/api/v1/predictions/today?sport=NFL`:
  - Root Response:
    ```json
    {
      "sport": "NFL",
      "period": "week",
      "total_picks": 3,
      "premium": 1,
      "value": 2,
      "skip": 0,
      "predictions_enabled": true,
      "as_of": "2026-10-15T23:00:00Z",
      "picks": [ ... ]
    }
    ```
  - Pick Object Fields:
    - Standard fields (`game_id`, `matchup_label`, `tier`, `prob_modelo`, `momio`, `ev_dolares`, `kelly_fraccion`, `home_win_prob`, `away_win_prob`, `spread`, `key_factors`, etc.).
    - Mandatory Timestamps:
      - `observed_at`: ISO UTC timestamp of observed market odds.
      - `predicted_at`: ISO UTC timestamp when inference was computed.
      - `as_of`: ISO UTC snapshot timestamp.
- `/api/v1/parlay/recommended?sport=NFL`:
  - `legs_evaluadas[].bookmaker`: String (e.g. `"betmgm"`).
  - `legs_evaluadas[].observed_at`: ISO UTC timestamp.

---

## Code Layout
- `backend/app/api/v1/`: Endpoints (`predictions.py`, `parlay.py`, `games.py`, `odds.py`).
- `backend/app/services/`:
  - `odds_quota_service.py`: Protected ledger management.
  - `espn_service.py`: ESPN scoreboards and injury snapshots.
  - `nfl_availability_service.py`: Dynamic injury modeling and roster tracking.
  - `sports_analytics_service.py`: Matchup analytics, EV, spread evaluation.
  - `parlay_service.py`: Parlay candidate filtering, microparlay generation.
- `backend/ml/data_pipeline/`:
  - `nfl_source.py`: NFLverse sync.
  - `injury_feature_builder.py`: Fantasy points rolling aggregation ($t-1$).
  - `temporal_features.py`: Feature matrix builder (32 features).
- `backend/ml/training/`:
  - `train_temporal.py`: Chronological walk-forward training for ensemble models.
- `backend/ml/inference.py`: Multi-model inference engine and confluence evaluator.
- `backend/ml/models/nfl/`: Saved model artifacts (`.joblib`) and `model_metadata.json`.
- `backend/app/workers/celery_app.py`: Celery tasks and scheduling.
- `frontend/src/`:
  - `services/api.ts`: API clients and TypeScript interfaces.
  - `components/dashboard/`: Dashboard widgets (`AiPredictionList.tsx`, `RecommendedParlaysCard.tsx`).
  - `components/matchup/`: Matchup tabs (`MatchupSpreadTab.tsx`, `MatchupMoneylineTab.tsx`).
