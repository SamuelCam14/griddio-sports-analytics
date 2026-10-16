# Comprehensive Specification & System Survey Report: Frontend, API Contracts, Docker Infrastructure & Test Suite

**Document ID:** `survey_frontend_infra_report.md`  
**Date:** 2026-10-15  
**Surveyor:** `teamwork_preview_spec_miner` (Frontend and System Specialist)  
**Authoritative Reference:** `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/ORIGINAL_REQUEST.md`  
**Target Focus:** R4 (Infrastructure & Resource Protection), R5 (Integrated MVP Delivery with Frontend), Acceptance Criteria

---

## 1. Executive Summary

This report delivers a full architectural and technical audit of the frontend dashboard, backend API contracts, Docker infrastructure, and testing pipelines of Griddio Sports Analytics.

### Key Audit Findings
1. **Frontend Dashboard (`frontend/src/`):** A sophisticated macOS-inspired dashboard is already implemented in React 18, TypeScript 5.6, Tailwind CSS 3.4, Framer Motion 14, Zustand 5, and TanStack React Query 5. It includes a 3-column overview layout, matchup cards with multi-market tabs (Moneyline, Spread, Totales, Player Props, Factores Clave), a dedicated microparlay recommendation card, an interactive Parlay Builder, an EV Calculator, and a Bankroll Manager.
2. **Frontend Quality Check:** Both `npm run lint` and `npm run build` execute with **0 errors and 0 warnings**.
3. **Backend Test Suite (`backend/tests/`):** Contains 24 test files and **284 automated tests**. All 284 tests run and pass cleanly inside the `sports_api` Docker container in **35.71 seconds** (`docker exec sports_api pytest -q`).
4. **Hermeticity & Quota Protection (R4):** Test execution does **not** consume external API credits or alter `data/odds_quota_ledger.json`. All tests run hermetically against local database models and mocks.
5. **Docker Infrastructure (R4):** All 5 services (`sports_db`, `sports_redis`, `sports_api`, `sports_worker`, `sports_beat`) are defined in `docker-compose.yml`. Containers `sports_db`, `sports_redis`, `sports_api`, and `sports_worker` are healthy and up.
6. **Critical Gaps Against R5:**
   - **Timestamp Gap:** R5 requires endpoints with timestamps (`observed_at`, `predicted_at`, `as_of`). In `GET /api/v1/predictions/today?sport=NFL`, the database stores these in `Prediction.predicted_at`, `Prediction.as_of`, and `OddsSnapshot.observed_at`, but the API serialization omits them from individual pick dictionaries and from the root response. Furthermore, `frontend/src/services/api.ts` does not yet declare these fields in the `Pick` interface.
   - **Spread / Handicap Projections:** In `generate_nfl_matchup_analytics()`, the spread object returns `tier = "UNAVAILABLE"` and `None` for projected margins, projected scores, cover probabilities, and picks because no spread model was validated. If null spread data is passed to `MatchupSpreadTab.tsx`, the UI can display formatting glitches (e.g. `NaN%` or `0.0%`).
   - **Production Model Gate:** In `backend/ml/models/nfl/model_metadata.json`, `production_enabled` is currently `false`. Consequently, `/api/v1/predictions/today?sport=NFL` and `/api/v1/parlay/recommended?sport=NFL` fail closed with `predictions_enabled: false` and `parlays: []` until R1-R3 models are validated and approved.
   - **Parlay Leg Provenance:** In `/api/v1/parlay/recommended`, `generate_recommended_parlays` does not attach `observed_at` and `bookmaker` to candidate legs, causing `legs_evaluadas` in `RecommendedParlaysCard.tsx` to render with "sin hora" and "casa desconocida".

---

## 2. Frontend Architecture & Component Survey (`frontend/src/`)

### 2.1 Technology Stack & Build Configuration
- **Runtime & Framework:** React 18.3.1, ReactDOM 18.3.1, Vite 8.3.1
- **Language:** TypeScript 5.6.3 (`strict: true`, target ES2020)
- **Styling:** Tailwind CSS 3.4.19 with custom macOS glassmorphic design tokens (`macos-window-shell`, `macos-card`, `surface-card`)
- **Animation:** Framer Motion 14.0.0
- **Icons:** Lucide React 1.52.0
- **State Management:** Zustand 5.0.1 (global UI/app state in `src/store/useAppStore.ts`)
- **Data Fetching & Cache:** TanStack React Query 5.59.0 (reactive caching, 60s stale time, background refetching)
- **HTTP Client:** Axios 1.7.7 (`src/services/api.ts`)

### 2.2 Component Hierarchy & Visual Layout

```
DashboardPage (src/pages/DashboardPage.tsx)
│
├── DashboardHeader (src/components/dashboard/DashboardHeader.tsx)
│   ├── Window controls (macOS red/yellow/green dots)
│   ├── Sport selector (NFL / NBA toggle)
│   ├── Navigation tabs (Dashboard, Parlay Builder, Simulator, Cards, EV Calculator)
│   └── Bankroll badge button
│
├── Main Grid (3 Columns)
│   │
│   ├── Column 1 (Left - Overview & Predictions)
│   │   ├── Title Header ("Ai Powered Dashboard")
│   │   ├── MvpOfTheDayCard (Top PREMIUM/VALUE pick highlight)
│   │   ├── MetricsGrid (Total matches, scheduled count, value bets count)
│   │   ├── AiPredictionList (Pronósticos y señales, market references, experimental badges)
│   │   └── RecommendedParlaysCard (NFL microparlay combinations)
│   │
│   ├── Column 2 (Center - Spotlight & Simulation)
│   │   ├── HeroSpotlight (Featured game carousel, team logos, kickoff, deep-dive button)
│   │   └── AiMatchupSimulatorCard (Matchup simulation overview, win probabilities)
│   │
│   └── Column 3 (Right - Live Match & Standings)
│       ├── LiveMatchDetailCard (Current match scores, head-to-head preview)
│       └── StandingsTableCard (NFL standings from verified NFLverse results)
│
└── DashboardModalManager (src/components/dashboard/DashboardModalManager.tsx)
    ├── MatchupDeepDiveModal -> MatchupCard (Multi-Market Deep Dive)
    │   ├── MatchupHeader (Teams, kickoff, tier badge, save toggle)
    │   ├── MatchupProbabilityBar (Dual-color probability bar)
    │   ├── MatchupTabsNav (Moneyline, Spread, Totales, Player Props)
    │   ├── MatchupMoneylineTab (Pick team, odds, EV, Kelly stake)
    │   ├── MatchupSpreadTab (Market line vs projected margin & cover prob)
    │   ├── MatchupTotalsTab (Market total vs projected score)
    │   ├── MatchupPlayerPropsTab (Real player props with EV & Kelly)
    │   └── MatchupKeyFactors (Plain-language model explanations)
    ├── ParlayBuilderModal -> ParlayBuilderView (Interactive parlay creator)
    ├── EVCalculatorModal -> EVCalculator (Single-bet EV & Kelly simulator)
    ├── MatchupSimulatorModal -> MatchupSimulator (Offline matchup tester)
    └── BankrollModal -> BankrollWidget (Bankroll & bet sizing tracking)
```

### 2.3 State Management & Data Flow
- **Zustand (`src/store/useAppStore.ts`):**
  - Manages `activeSport`: `'NFL' | 'NBA'` (defaults to `'NFL'`).
  - Manages `bankroll`: number (defaults to `$1,000`).
  - Manages `savedPicks`: array of saved `Pick` items with local persistence.
- **Hook Orchestrator (`src/hooks/useSportsDashboard.ts`):**
  - Executes parallel React Query calls:
    1. `gamesQuery`: `gamesApi.getUpcoming(14, sport)`
    2. `oddsQuery`: `oddsApi.getNFL()` or `getNBA()`
    3. `predictionsQuery`: `predictionsApi.getToday(sport, 'all', 'auto')`
    4. `experimentalQuery`: `predictionsApi.getExperimental(sport.toLowerCase(), 14)`
    5. `recommendedParlaysQuery`: `parlayApi.getRecommended(undefined, 3)` (enabled only for NFL)
    6. `externalForecastQuery`: `gamesApi.getExternalPrediction(gameId)` (ESPN external forecast)
  - Invokes `buildMatchList()` (`src/utils/dashboard.ts`) to join upcoming `Game` records with `OddsData` by `external_id`, linking associated `Pick` by `game.id`.

---

## 3. API Endpoints & Contract Specifications

### 3.1 Endpoint 1: `/api/v1/predictions/today`

#### Method & Route
`GET /api/v1/predictions/today`

#### Query Parameters
| Parameter | Type | Default | Allowed Values | Description |
|-----------|------|---------|----------------|-------------|
| `sport` | string | `"NBA"` | `"NBA"`, `"NFL"` | Sport identifier |
| `tier` | string | `"all"` | `"all"`, `"PREMIUM"`, `"VALUE"`, `"SKIP"` | Confidence tier filter |
| `period` | string | `"auto"` | `"auto"`, `"today"`, `"week"`, `"all"` | Time window (auto resolves to "week" for NFL) |

#### Gate Condition
`get_approved_model_version(sport)` inspects `backend/ml/models/{sport.lower()}/model_metadata.json`.
- If `production_enabled != True`, it returns immediately:
  ```json
  {
    "sport": "NFL",
    "period": "week",
    "total_picks": 0,
    "premium": 0,
    "value": 0,
    "skip": 0,
    "picks": [],
    "predictions_enabled": false,
    "reason": "No hay un modelo aprobado para producción con features en paridad temporal."
  }
  ```

#### Live Contract When Open
When `production_enabled == True`, it executes an asynchronous query against `Prediction`, `Game`, and `Team`, validates `OddsSnapshot` freshness against `settings.FRESHNESS_HOURS_NFL_ODDS` (default 48h), computes EV via `calculate_ev()`, and generates multi-market analytics via `generate_nfl_matchup_analytics()`.

#### Expected vs Current Schema Comparison
| Field | Type | Expected by R5 & Frontend | Currently Returned | Gap / Note |
|-------|------|---------------------------|--------------------|------------|
| `sport` | string | `"NFL"` | `"NFL"` | Matches |
| `period` | string | `"week"` | `"week"` | Matches |
| `predictions_enabled` | boolean | `true` | `false` (gate closed) | Blocked until model approval |
| `total_picks` | integer | Count of picks | `0` (gate closed) | Blocked until model approval |
| `as_of` | ISO datetime string | Global snapshot timestamp | **Missing at root** | **GAP R5** |
| `picks[].game_id` | integer | Game ID | Supported | Matches |
| `picks[].sport` | string | `"NFL"` | Supported | Matches |
| `picks[].game_date` | ISO datetime string | Kickoff time | Supported | Matches |
| `picks[].is_today` | boolean | Today indicator | Supported | Matches |
| `picks[].matchup_label` | string | `"SEA @ DEN"` | Supported | Matches |
| `picks[].home_team` | object | Team detail object | Supported | Matches |
| `picks[].away_team` | object | Team detail object | Supported | Matches |
| `picks[].tier` | string | `"PREMIUM"`, `"VALUE"`, `"SKIP"` | Supported | Matches |
| `picks[].model_agreement`| integer | Number of agreeing models (1-3) | Supported | Matches |
| `picks[].pick_side` | string | `"home"` or `"away"` | Supported | Matches |
| `picks[].prob_modelo` | float | Win probability (0.0 - 1.0) | Supported | Matches |
| `picks[].momio` | integer | American moneyline odds | Supported | Matches |
| `picks[].ev_dolares` | float | Expected return per $100 | Supported | Matches |
| `picks[].kelly_fraccion`| float | Recommended fraction | Supported | Matches |
| `picks[].home_win_prob` | float | Home probability | Supported | Matches |
| `picks[].away_win_prob` | float | Away probability | Supported | Matches |
| `picks[].tiene_ventaja` | boolean | True if edge > 0 | Supported | Matches |
| `picks[].bookmaker` | string | Odds provider (e.g. "betmgm") | Supported | Matches |
| `picks[].best_bet` | object | Top EV recommendation | Supported | Matches |
| `picks[].spread` | object | Spread analysis object | Returned with null values | **GAP R5** (no spread model) |
| `picks[].totals` | object | Totals analysis object | Returned with null values | No totals model |
| `picks[].player_props` | array | Enriched props | Empty array | No props model |
| `picks[].key_factors` | array | Explanatory strings | Supported | Matches |
| `picks[].observed_at` | ISO datetime string | When odds were observed | **Missing in pick** | **GAP R5** |
| `picks[].predicted_at`| ISO datetime string | When inference was run | **Missing in pick** | **GAP R5** |
| `picks[].as_of` | ISO datetime string | Cutoff timestamp | **Missing in pick** | **GAP R5** |

---

### 3.2 Endpoint 2: `/api/v1/parlay/recommended`

#### Method & Route
`GET /api/v1/parlay/recommended`

#### Query Parameters
| Parameter | Type | Default | Allowed Values | Description |
|-----------|------|---------|----------------|-------------|
| `sport` | string | `"NFL"` | Exclusively `"NFL"` | Sport filter |
| `bankroll` | float | `1000.0` | `> 0` | User bankroll for Kelly stake |
| `limit` | integer | `4` | `1 <= limit <= 10` | Maximum parlays to return |

#### Gate Condition
`_production_model_enabled("NFL")` must be `True`. If `False`, returns:
```json
{
  "status": "unavailable",
  "sport": "NFL",
  "total": 0,
  "target_odds": "Cuota ~2.0 (+100) con probabilidad >45% y EV+",
  "parlays": [],
  "reason": "No hay un modelo aprobado para producción para NFL."
}
```

#### Live Contract When Open
When open, `generate_recommended_parlays` evaluates candidate single bets with `prob_model >= 0.62` and `edge_pct >= 0.01`, testing 2-leg and 3-leg combinations:
- Combined odds close to +100 (~2.0 decimal)
- High combined probability (>42%)
- Positive combined EV (>$2.00 per $100)
- Strict rejection of correlated legs (legs from the same game or matchup pair)

#### Expected Output Schema for Each Parlay
```json
{
  "type": "2-Leg Micro Parlay",
  "viable": true,
  "has_correlated_legs": false,
  "has_sufficient_model_data": true,
  "razon": "Parlay viable de 2 patas: Probabilidad combinada 49.5%, EV +$4.20 con cuota +110.",
  "n_legs": 2,
  "prob_combinada": 0.4950,
  "momio_decimal": 2.100,
  "momio_americano": 110,
  "ev_dolares": 4.20,
  "kelly_fraccion": 0.0210,
  "kelly_monto": 21.00,
  "confidence_level": "moderada",
  "is_manual_calculation": false,
  "legs_evaluadas": [
    {
      "team_name": "Denver Broncos",
      "market": "h2h",
      "prob_model": 0.6800,
      "odds_american": -140,
      "odds_decimal": 1.714,
      "edge_pct": 0.0450,
      "has_individual_edge": true,
      "tier": "PREMIUM",
      "bookmaker": "betmgm",
      "observed_at": "2026-10-15T22:01:58+00:00",
      "source": "real"
    }
  ]
}
```

#### Identified Parlay Gaps
1. **Provenance Fields Omitted in Candidates:** In `parlay_service.py` (`generate_recommended_parlays`), candidate dicts populate `game_id`, `team_name`, `prob_model`, `odds_american`, `edge_pct`, `tier`, but do **not** set `bookmaker` or `observed_at`. Thus, `legs_evaluadas` renders with `bookmaker: null` and `observed_at: null`.
2. **Missing Root `as_of`:** The `/parlay/recommended` response root does not include an `as_of` timestamp.

---

## 4. UI Rendering Analysis

### 4.1 Picks Rendering
- **`AiPredictionList.tsx`:** Renders the top 5 matches with production picks. When `predictions.length === 0`, it displays a fallback note ("No hay predicciones de producción disponibles para NFL"). When experimental predictions exist, it displays an amber informational box ("Probabilidades experimentales · sin pick ni EV").
- **`PickCard.tsx`:** Displays tier badge (`PREMIUM` = purple, `VALUE` = blue, `SKIP` = gray), model agreement (`{model_agreement}/3 modelos`), pick side (`Local` / `Visitante`), American odds (`+125`), EV color-coded green/red, and suggested fractional Kelly stake (`${kellyAmount}`).
- **`MvpOfTheDayCard.tsx`:** Highlights the highest-confidence pick of the day with team details and probability.

### 4.2 Model Probabilities
- **`MatchupProbabilityBar.tsx` / `PredictionPanel.tsx`:** Renders a horizontal split bar comparing `away_win_prob` and `home_win_prob` with smooth Framer Motion width transitions.
- **Percentages:** Formatted cleanly using `formatPercentage(value)` in `src/utils/dashboard.ts` (`(value * 100).toFixed(1) + "%"`).

### 4.3 Handicap / Spread Analysis Rendering
- **`MatchupSpreadTab.tsx`:**
  - Expects `spread: MatchupSpread`:
    - `spread.market_line`: e.g. `-2.5`
    - `spread.projected_away_score`: e.g. `20`
    - `spread.projected_home_score`: e.g. `24`
    - `spread.pick`: e.g. `"Denver Broncos -2.5"`
    - `spread.cover_probability`: e.g. `0.582`
    - `spread.edge_pct`: e.g. `5.2`
  - **Identified Fragility:** Line 56 executes `(spread.cover_probability * 100).toFixed(1)`. When `cover_probability` is `null` or `undefined`, this produces `0.0%` or `NaN%`. If `spread.pick` is `null`, it renders blank text.

### 4.4 Microparlays Rendering
- **`RecommendedParlaysCard.tsx`:**
  - When `status === 'success'` and `parlays.length > 0`, it renders each parlay with title (e.g. `"2-Leg Micro Parlay"`), American odds (`+110`), combined probability (`49.5%`), and EV (`+$4.20 por cada $100`).
  - Lists each evaluated leg: `{leg.team_name} · {leg.market} · {prob_model} · {odds_american} · {bookmaker} · {observed_at}`.

---

## 5. Docker Infrastructure Survey

### 5.1 Docker Compose Services Inventory

| Service | Container Name | Image / Build | Ports | Healthcheck | Purpose |
|---------|----------------|---------------|-------|-------------|---------|
| `db` | `sports_db` | `timescale/timescaledb:latest-pg16` | `5432:5432` | `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}` (10s interval, 5s timeout, 5 retries) | PostgreSQL + TimescaleDB for games, odds hypertable, and predictions |
| `redis` | `sports_redis` | `redis:7-alpine` | `6379:6379` | `redis-cli ping` (10s interval, 3s timeout, 5 retries) | Message broker and caching layer for Celery workers |
| `api` | `sports_api` | Build: `./backend/Dockerfile` (Python 3.12-slim) | `8000:8000` | N/A (depends on `db:healthy`, `redis:healthy`) | FastAPI REST API backend running Uvicorn with `--reload` |
| `worker`| `sports_worker`| Build: `./backend/Dockerfile` (Python 3.12-slim) | None | N/A (depends on `db:healthy`, `redis:healthy`) | Celery worker processing queues `ingestion`, `ml`, `alerts` |
| `beat` | `sports_beat` | Build: `./backend/Dockerfile` (Python 3.12-slim) | None | N/A (depends on `redis`) | Celery Beat periodic scheduler (ingestion every 15m, weekly retrain) |

### 5.2 Volumes and Bind Mounts
- `postgres_data:/var/lib/postgresql/data` (persistent database data)
- `./backend/scripts/db/init_db.sql:/docker-entrypoint-initdb.d/init_db.sql:ro` (database schema initialization)
- `redis_data:/data` (Redis persistence)
- `./backend:/app` (live code hot-reloading for `sports_api`, `sports_worker`, and `sports_beat`)
- `./data:/app/data` (shared dataset manifests, experimental prediction outputs, quota ledger)

### 5.3 Live Container Status
Observed via `docker ps -a`:
- `sports_db`: **Up, Healthy**
- `sports_redis`: **Up, Healthy**
- `sports_api`: **Up, Port 8000 accessible**
- `sports_worker`: **Up**
- `sports_beat`: **Exited (0)** (ran background task and terminated or needs restart via `docker compose up -d beat`)

### 5.4 Docker Operational Commands Cheat-Sheet
```bash
# 1. Start all supporting services
docker compose up -d

# 2. Check container status
docker ps -a

# 3. View API logs
docker logs -f sports_api

# 4. View Worker logs
docker logs -f sports_worker

# 5. Restart Celery Beat scheduler
docker compose up -d beat

# 6. Execute backend test suite inside container
docker exec sports_api pytest -q

# 7. Open PostgreSQL shell inside sports_db
docker exec -it sports_db psql -U postgres -d sports_analytics

# 8. Rebuild container images after dependency changes
docker compose build --no-cache api worker beat
```

---

## 6. Backend Test Suite & Test Inventory (`backend/tests/`)

### 6.1 Test Inventory (24 Test Modules, 284 Tests)

| # | Test File | Test Count | Subject Covered |
|---|-----------|------------|-----------------|
| 1 | `test_canary_secrets.py` | 3 | Checks that no secrets or API keys are leaked into logs or codebase |
| 2 | `test_cards.py` | 14 | Sports card hedonic pricing, arbitrage, multiplier math |
| 3 | `test_celery_tasks.py` | 8 | Celery background task scheduling and execution |
| 4 | `test_espn_external_predictor.py` | 6 | External ESPN prediction ingestion and error isolation |
| 5 | `test_exhaustive_scenarios.py` | 18 | Boundary conditions, extreme odds, zero bankroll |
| 6 | `test_freshness.py` | 12 | Odds snapshot staleness checks and timestamp bounds |
| 7 | `test_game_crosswalk_and_freshness_integration.py` | 9 | Crosswalk resolution across The Odds API, nflverse, ESPN |
| 8 | `test_live_ingestion_integration.py` | 14 | Real game schedule and odds snapshot ingestion |
| 9 | `test_math.py` | 26 | EV calculation, Kelly criterion, odds conversion, Monte Carlo |
| 10 | `test_nfl_analytics.py` | 15 | Multi-market matchup analytics, vig removal, best bet selection |
| 11 | `test_nfl_availability_phase02.py` | 16 | Injury reports, practice status tracking, snap loss |
| 12 | `test_nfl_backend_ssot_phase06.py` | 14 | Backend single source of truth for parlay and game context |
| 13 | `test_nfl_data_phase01.py` | 12 | Verified NFLverse schedule loading and schema checks |
| 14 | `test_nfl_features_phase03.py` | 16 | Temporal feature pipeline, no same-day leak, chronological order |
| 15 | `test_nfl_market_ev_phase05.py` | 10 | Market EV detection, edge thresholding, stale odds rejection |
| 16 | `test_nfl_models_phase04.py` | 5 | Model artifact loading, probability bounds, walk-forward splits |
| 17 | `test_nfl_schedule_phase01.py` | 6 | Schedule sync idempotency and team resolution |
| 18 | `test_nfl_shadow_trial_phase08.py` | 13 | Shadow tracking, CLV calculation, kill criteria, drawdown limits |
| 19 | `test_parlay.py` | 17 | Parlay candidate filtering, Golden Rule EV, correlated leg rejection, team alias normalization |
| 20 | `test_persistence.py` | 5 | Game and prediction database persistence idempotency and rollbacks |
| 21 | `test_prediction_gate.py` | 27 | Production gate failure modes, offline manifest verification, sport mismatches, corrupted hashes |
| 22 | `test_production_readiness_audit.py` | 8 | Fail-closed DB errors, no synthetic -110 defaults, exact parlay date match, settings security |
| 23 | `test_provider.py` | 4 | The Odds API HTTP error handling (timeout, 429, empty responses) |
| 24 | `test_temporal_pipeline.py` | 7 | Leak-free temporal training and inference feature contracts |

### 6.2 Test Suite Execution Metrics
- **Command:** `docker exec sports_api pytest -q`
- **Total Tests Executed:** 284
- **Pass Rate:** **100% (284 passed, 0 failed, 0 errors)**
- **Duration:** 35.71 seconds
- **Quota Ledger Status:** `data/odds_quota_ledger.json` was **not modified** during test execution. Requests used remained at 264, remaining at 236.

---

## 7. Frontend Tooling & Verification

### 7.1 Script Verification
1. **ESLint (`npm run lint`):**
   - Config file: `frontend/eslint.config.js` (Flat config format, ESLint 9.12)
   - Command: `npm run lint` in `frontend/`
   - Result: **0 errors, 0 warnings** (exited code 0).
2. **TypeScript & Vite Build (`npm run build`):**
   - Command: `tsc -b && vite build`
   - Result: **0 errors** (exited code 0). Built client environment in 731ms (`dist/assets/index-pPDEZDVW.js`).

---

## 8. Gaps Against Requirements R4 and R5

| ID | Requirement Area | Current Code State | Expected Behavior / Acceptance Criteria | Severity | Remediation Needed |
|----|------------------|--------------------|-----------------------------------------|----------|-------------------|
| **GAP-01** | R5: Timestamps (`observed_at`, `predicted_at`, `as_of`) | In `/api/v1/predictions/today`, `picks` items do not include `observed_at`, `predicted_at`, or `as_of`. The root response also lacks `as_of`. `frontend/src/services/api.ts` does not define these in `Pick`. | R5 explicitly requires: "endpoints FastAPI con marcas de tiempo (`observed_at`, `predicted_at`, `as_of`)". | **High** | 1. Add `observed_at`, `predicted_at`, `as_of` to pick serialization in `predictions.py`.<br>2. Add `as_of` to root dictionary of `/predictions/today`.<br>3. Update `Pick` interface in `frontend/src/services/api.ts`. |
| **GAP-02** | R5: Spread / Handicap Modeling | `generate_nfl_matchup_analytics()` in `sports_analytics_service.py` sets `spread` fields to `None` and `tier: "UNAVAILABLE"` because no spread model was validated. `MatchupSpreadTab.tsx` expects projected scores and cover prob. | R3 & R5 require: "Analizar líneas de handicap / spread... El frontend debe renderizar los picks, probabilidades... análisis de handicap". | **High** | 1. Model spread cover probabilities and projected margins using the independent ensemble.<br>2. Add defensive checks in `MatchupSpreadTab.tsx` to handle `null`/`UNAVAILABLE` without visual glitches. |
| **GAP-03** | R5: Production Model Gate & Real NFL Picks | `production_enabled` is `false` in `backend/ml/models/nfl/model_metadata.json`. Both `/predictions/today?sport=NFL` and `/parlay/recommended?sport=NFL` fail closed with 0 picks. | Acceptance criteria: "Los endpoints `/api/v1/predictions/today?sport=NFL` y `/api/v1/parlay/recommended?sport=NFL` responden datos reales estructurados y consumibles por el dashboard". | **Critical** | Once R1-R3 ensemble and injury models are trained and evaluated: set `production_enabled: true` in metadata, generate and persist real predictions with temporal validation. |
| **GAP-04** | R5: Recommended Parlay Provenance | In `generate_recommended_parlays` (`parlay_service.py`), candidate legs do not carry `bookmaker` or `observed_at`, so `legs_evaluadas` renders with nulls in `RecommendedParlaysCard.tsx`. | Acceptance criteria & UI: "leg.bookmaker ?? 'casa desconocida' · leg.observed_at ?? 'sin hora'". | **Medium** | In `generate_recommended_parlays`, retrieve `bookmaker` and `observed_at` from `pred` / `OddsSnapshot` and include them in each candidate leg. |
| **GAP-05** | R4: Celery Beat Container State | `sports_beat` container is currently `Exited (0)` in `docker ps -a`. | R4 requires: "Utilizar el entorno Docker existente (`sports_db`, `sports_redis`, `sports_api`, `sports_worker`, `sports_beat`)". | **Low** | Start/restart `sports_beat` using `docker compose up -d beat` to ensure periodic tasks run as expected. |

---

## 9. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | API / Predictions | `GET /api/v1/predictions/today` | Delivers game picks classified by tier with EV, Kelly fraction, and matchup analytics. | `sport` (default "NBA"), `tier` ("all"), `period` ("auto") | JSON with picks array, counts by tier, period | Returns 0 picks and `predictions_enabled: false` if model gate closed | `backend/app/api/v1/predictions.py:86` |
| 2 | API / Predictions | `GET /api/v1/predictions/experimental/{sport}` | Serves hash-verified offline probabilities from manifest files. | `sport`, `lookahead_days` | JSON with games list, manifest metadata, dataset SHA256 | HTTP 404 if no manifest, HTTP 503 if hash mismatch | `backend/app/api/v1/predictions.py:401` |
| 3 | API / Predictions | `GET /api/v1/predictions/top` | Returns top N picks sorted by EV, restricted to PREMIUM and VALUE tiers. | `sport`, `limit` (default 5) | JSON with top picks list | Returns empty top picks if gate closed | `backend/app/api/v1/predictions.py:508` |
| 4 | API / Predictions | `GET /api/v1/predictions/game/{game_id}` | Detailed single-game prediction with confluence tier and EV analysis. | `game_id` | JSON with win probabilities, confluence tier, EV per side | HTTP 404 if not found, HTTP 503 if gate closed | `backend/app/api/v1/predictions.py:530` |
| 5 | API / Predictions | `GET /api/v1/predictions/model-info` | Exposes reproducible temporal training metrics without legacy ROI claims. | None | JSON with model validation statuses and fold metrics | Status "unavailable" if metadata file missing | `backend/app/api/v1/predictions.py:601` |
| 6 | API / Predictions | `POST /api/v1/predictions/simulate` | Custom matchup simulator endpoint (intentionally disabled). | `SimulationRequest` | None | Always fails closed with HTTP 503 (no synthetic data) | `backend/app/api/v1/predictions.py:643` |
| 7 | API / Parlay | `GET /api/v1/parlay/recommended` | Computes optimal 2-3 leg microparlays meeting the Golden Rule (EV+, ~2.0 odds). | `sport` ("NFL"), `bankroll`, `limit` | JSON with viable parlays, combined odds, EV, Kelly | Returns empty parlays if gate closed or < 2 candidates | `backend/app/api/v1/parlay.py:40` |
| 8 | API / Parlay | `POST /api/v1/parlay/evaluate` | Backend SSOT evaluator for user-assembled custom parlays. | List of `CustomLeg`, `bankroll`, `kelly_fraction` | JSON with viability, EV, odds, correlation status | Rejects correlated legs from same game | `backend/app/api/v1/parlay.py:89` |
| 9 | API / Odds | `GET /api/v1/odds/nfl` | Current observed NFL odds with vig-free fair probabilities. | None | JSON with games list, markets provenance, freshness | HTTP 500 if DB fails, status "no_upcoming_events" if empty | `backend/app/api/v1/odds.py:37` |
| 10 | API / Games | `GET /api/v1/games/upcoming` | Upcoming scheduled games within N days. | `days` (default 7), `sport` | JSON with scheduled games list | Empty list if no scheduled games | `backend/app/api/v1/games.py:207` |
| 11 | API / Games | `GET /api/v1/games/standings` | Team standings calculated from completed NFLverse schedule results. | `sport` ("NFL") | JSON with standings table, W/L/T, points for/against | HTTP 503 if source unavailable | `backend/app/api/v1/games.py:22` |
| 12 | UI / Dashboard | `AiPredictionList` | Dashboard widget showing market references, model picks, and experimental signals. | `matches`, `sport`, `externalForecast` | Rendered interactive list | Shows fallback message when no picks available | `frontend/src/components/dashboard/AiPredictionList.tsx:15` |
| 13 | UI / Dashboard | `RecommendedParlaysCard` | Displays NFL microparlay recommendations directly in the dashboard sidebar. | `status`, `reason`, `total`, `parlays` | Rendered parlay cards with legs, odds, EV | Shows "NO PUBLICADOS" and reason when unavailable | `frontend/src/components/dashboard/RecommendedParlaysCard.tsx:13` |
| 14 | UI / Matchup | `MatchupCard` & Tabs | Deep dive modal with Moneyline, Spread, Totales, Player Props, and Key Factors tabs. | `pick: Pick`, `bankroll` | Tabbed interactive card | Shows "Sin línea disponible" when market is empty | `frontend/src/components/matchup/MatchupCard.tsx:38` |
| 15 | UI / Parlay | `ParlayBuilderView` | Comprehensive sportsbook-style interactive parlay creator and validator. | `bankroll` | Candidate selection table, bet slip, live evaluation | Alerts on correlated legs or insufficient model data | `frontend/src/components/parlay/ParlayBuilderView.tsx:78` |

---

## 10. Edge Cases & Boundary Conditions

| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | Production Gate | `sport="NFL"` with `production_enabled=false` | Returns HTTP 200 with `total_picks: 0`, `picks: []`, `predictions_enabled: false`. Fails closed safely. |
| 2 | Production Gate | `sport="NBA"` (unapproved) | Same behavior: returns `predictions_enabled: false`, zero picks. |
| 3 | Production Gate | Path traversal attempt `sport="../../etc"` | Normalized and strictly rejected against `{"NFL", "NBA"}` whitelist, returns `predictions_enabled: false`. |
| 4 | Odds Staleness | OddsSnapshot older than `FRESHNESS_HOURS_NFL_ODDS` (48h) | Excluded from picks generation (`excluded_missing_market_data` incremented). |
| 5 | Post-Kickoff Odds | OddsSnapshot with `observed_at >= game.game_date` | Strictly rejected to prevent temporal leakage; game excluded from picks. |
| 6 | Temporal Invariance | `as_of` / `predicted_at` > `game.game_date` | Excluded by SQL filter `func.coalesce(Prediction.as_of, Prediction.predicted_at) <= Game.game_date`. |
| 7 | Same-Game Correlated Parlay | 2 legs from the same `game_id` | `evaluate_custom_parlay` detects duplicate `game_id`, returns `viable: false`, `has_correlated_legs: true`. |
| 8 | Totals Leg in Parlay | Leg with `market: "totals"` | `evaluate_custom_parlay` flags `has_sufficient_model_data: false`, omits EV calculation (no synthetic 0.50 probability). |
| 9 | Team Name Normalization | `team_str="New Orleans Saints"` | Correctly resolves to `NO` without colliding with `NE` (Patriots), using word boundary regex. |
| 10 | Standings Source Error | Missing or corrupted NFLverse schedule file | `GET /games/standings` catches `DataQualityError` and raises HTTP 503 cleanly without crashing. |
| 11 | Simulation Request | Default payload to `POST /predictions/simulate` | No synthetic odds or defaults; schema validates `home_odds: None`, `visitor_team_name: None`, returns HTTP 503. |
| 12 | Database Disconnection | PostgreSQL down during `GET /api/v1/odds/nfl` | Propagates HTTP 500 (`RuntimeError`), never masks failure with HTTP 200 `[]`. |

---

## 11. Recommendations for Phase Delivery

1. **Add Timestamps to `/predictions/today`:**
   - In `backend/app/api/v1/predictions.py`, serialize `observed_at` (from `h2h_snap.observed_at.isoformat()`), `predicted_at` (from `pred.predicted_at.isoformat()`), and `as_of` (from `(pred.as_of or pred.predicted_at).isoformat()`) into each pick dictionary.
   - Include root-level `as_of: now_utc.isoformat()` in the response dictionary.
   - In `frontend/src/services/api.ts`, add `observed_at?: string | null`, `predicted_at?: string | null`, and `as_of?: string | null` to the `Pick` interface.
2. **Propagate Parlay Provenance in `parlay_service.py`:**
   - In `generate_recommended_parlays()`, attach `bookmaker` and `observed_at` to candidate objects so `legs_evaluadas` in `RecommendedParlaysCard` renders the real bookmaker and timestamp.
3. **Guard `MatchupSpreadTab.tsx` Against Null Values:**
   - In `MatchupSpreadTab.tsx`, add null guards before `.toFixed()` on `cover_probability` and `edge_pct` to prevent `NaN%` or runtime errors when spread models are unavailable or transitioning.
4. **Model Spread / Handicap Probabilities (R3/R5):**
   - Provide genuine spread cover probabilities and projected margins via the independent ensemble without synthetic -110 defaults.
5. **Open Production Gate Only After R1-R3 Validation:**
   - Maintain `production_enabled: false` until R1 (real data audit), R2 (dynamic fantasy injury model), and R3 (2-3 model ensemble with EV confluences) are trained, tested, and reviewed. Then update `model_metadata.json` with `production_enabled: true`.
