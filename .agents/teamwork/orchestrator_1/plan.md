# Orchestration Plan: Reevaluación y Entrega MVP NFL Analytics

## Strategy & Pattern
- **Pattern**: Project Pattern (Top-Level Orchestrator with Dual Track: Implementation Track & E2E Testing Track).
- **Scope**: Deliver complete NFL Analytics MVP according to R1-R5 and Acceptance Criteria.
- **Constraints**: 100% real data, zero-synthetic, zero-imputation with 0, no fixed positional injury weights, prior-week fantasy points only, independent multi-model ensemble, +EV threshold (min 1.40 odds), spread value, microparlays >= 2.0x, preserve `data/odds_quota_ledger.json`, docker environment verification.

---

## Phases & Execution Flow

### Phase 0: Comprehensive Survey
- Spawn 3 parallel Explorers:
  - **Explorer 1 (Data & Backend Infrastructure)**: Map existing data ingest pipelines (ESPN, NFLverse, Odds API), API endpoints, data models, database state, caching, and `data/odds_quota_ledger.json` protection.
  - **Explorer 2 (ML Models & Analytics)**: Map current ML models, feature engineering, injury impact logic, ensemble structure, EV calculation, spread/handicap value detection, and microparlay engine.
  - **Explorer 3 (Frontend & Test Infrastructure)**: Map frontend React/TypeScript UI, components, data flow, timestamp handling, Docker setup (`sports_db`, `sports_redis`, `sports_api`, `sports_worker`, `sports_beat`), and backend/frontend test scripts.
- Synthesize all findings into `PROJECT.md` with full Feature Inventory.

### Phase 1: Dual Track Launch
- **Track A (E2E Testing Track)**:
  - Spawn E2E Testing Sub-Orchestrator to build opaque-box test suite covering Tiers 1-4.
  - Produce `TEST_INFRA.md` and publish `TEST_READY.md`.
- **Track B (Implementation Track)**:
  - Decompose into sequential/parallel milestones linked by interface contracts.

### Phase 2: Implementation Track Milestones
- **Milestone 1 (M1: Data & API Integrity)**:
  - Ensure zero synthetic data, zero arbitrary 0-imputation, statistical missing value handling.
  - Guarantee `data/odds_quota_ledger.json` safety in tests.
  - Verification: Backend unit tests + forensic audit.
- **Milestone 2 (M2: Dynamic Injury Impact Engine)**:
  - Correlate ESPN injury reports with prior-week fantasy points (strictly preceding weeks, no leak).
  - Eliminate fixed positional weights; scale impact to player's specific recent production.
  - Schedule pre-kickoff recalculation with official inactive lists.
  - Verification: Automated tests proving dynamic degradation + forensic audit.
- **Milestone 3 (M3: Multi-Model Ensemble, +EV & Microparlays)**:
  - 2-3 independent models trained/evaluated with walk-forward temporal splits.
  - Model confluence requirement for recommendations.
  - +EV calculator with >= 1.40 odds filter, spread/handicap value analysis, correlated microparlays (>= 2.0x, +EV).
  - Verification: Pytest suite + adversarial stress tests + forensic audit.
- **Milestone 4 (M4: Frontend Integration & Non-Blocking Serving)**:
  - Connect predictions, odds, spreads, and microparlays to frontend with timestamps (`observed_at`, `predicted_at`, `as_of`).
  - Verify GET requests do not trigger blocking external sync calls.
  - `npm run lint` and `npm run build` verification.

### Phase 3: Final Acceptance & Delivery
- Execute full E2E test suite in `sports_api` container (100% pass).
- Tier 5 adversarial testing.
- Final forensic audit.
- Victory handoff and report to Sentinel.
