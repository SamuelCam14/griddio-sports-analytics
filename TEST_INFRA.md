# Test Infrastructure & E2E Testing Framework

## 1. Overview & Methodology

This document details the test infrastructure, methodology, runner commands, and coverage thresholds for the **Reevaluación y Entrega MVP NFL Analytics** platform.

The testing architecture follows an **opaque-box, requirement-driven 4-tier methodology** designed strictly around user requirements (R1 through R5) and acceptance criteria outlined in `ORIGINAL_REQUEST.md` and `PROJECT.md`.

### 4-Tier Test Classification

| Tier | Name | Focus | Minimum Cases |
| :--- | :--- | :--- | :--- |
| **Tier 1** | **Feature Coverage** | Primary happy-path contracts across core features (R1-R5) | $\ge 5$ per feature (25 total) |
| **Tier 2** | **Boundary & Corner Cases** | Edge inputs, zero divisions, extreme odds, cutoff limits, stale odds | $\ge 6$ cases |
| **Tier 3** | **Cross-Feature Combinations** | Multi-system confluence, injury vs odds, parlay correlation rejections | $\ge 5$ cases |
| **Tier 4** | **Real-World Scenarios** | Full end-to-end user workflows (dashboard, evaluation, parlay generation) | $\ge 4$ cases |

---

## 2. Requirements Traceability Matrix

| Req | Requirement Description | Primary Test Class / Coverage |
| :--- | :--- | :--- |
| **R1** | **Real Data Audit & Zero-Imputation**<br>- Prohibit synthetic/invented data.<br>- Zero-imputation ban (no artificial 0.0 or fixed priors).<br>- Explicit statistical missingness states (`COVERED_HEALTHY`, `UNKNOWN`, etc.). | `TestTier1R1RealDataAndZeroImputation`<br>- `test_tier1_r1_zero_imputation_prohibits_arbitrary_zeros`<br>- `test_tier1_r1_explicit_coverage_states_in_availability`<br>- `test_tier1_r1_real_data_provenance_and_manifest_hash_integrity`<br>- `test_tier1_r1_standings_use_only_verified_nflverse_snapshots`<br>- `test_tier1_r1_inference_fails_closed_on_unverified_or_missing_coverage` |
| **R2** | **Dynamic Injury Impact Model**<br>- Prior fantasy points ($t-1$) dynamic degradation.<br>- Zero fixed positional weight multipliers.<br>- Pre-kickoff inactives verification and cutoff enforcement. | `TestTier1R2DynamicInjuryImpact`<br>- `test_tier1_r2_dynamic_injury_higher_prior_ppr_causes_proportionally_greater_impact`<br>- `test_tier1_r2_zero_positional_multipliers_player_production_governs`<br>- `test_tier1_r2_strict_temporal_cutoff_prevents_leakage`<br>- `test_tier1_r2_confirmed_out_ir_immutable_without_market_props`<br>- `test_tier1_r2_pre_kickoff_inactives_window_evaluation` |
| **R3** | **Ensemble, +EV, Spread & Microparlays**<br>- Multi-model independent ensemble with confluence.<br>- Vegas independence of win probabilities.<br>- +EV with minimum odds $\ge 1.40$.<br>- Spread margin modeling and microparlays $\ge 2.0x$ return. | `TestTier1R3EnsemblePlusEVSpreadParlay`<br>- `test_tier1_r3_vegas_independence_probabilities_from_football_metrics`<br>- `test_tier1_r3_confluence_rule_requires_model_agreement`<br>- `test_tier1_r3_positive_ev_enforces_minimum_decimal_odds_1_40`<br>- `test_tier1_r3_spread_handicap_projected_margin_and_cover_probability`<br>- `test_tier1_r3_microparlays_target_combined_return_ge_2x_and_positive_ev` |
| **R4** | **Infrastructure & Quota Protection**<br>- Strict preservation of `data/odds_quota_ledger.json`.<br>- Circuit breaker protection for credit reserves.<br>- Non-blocking frontend GET endpoints. | `TestTier1R4InfrastructureAndQuotaProtection`<br>- `test_tier1_r4_odds_quota_ledger_immutability_during_test_execution`<br>- `test_tier1_r4_circuit_breaker_activates_at_protected_reserve_threshold`<br>- `test_tier1_r4_get_external_prediction_is_strictly_non_blocking`<br>- `test_tier1_r4_get_odds_remaining_reads_ledger_without_outbound_http`<br>- `test_tier1_r4_get_game_context_non_blocking_and_serves_db_snapshot` |
| **R5** | **Frontend MVP Integration & Timestamps**<br>- Endpoints aligned with React dashboard.<br>- Full timestamp provenance (`observed_at`, `predicted_at`, `as_of`).<br>- Real structured payload rendering. | `TestTier1R5FrontendMVPIntegration`<br>- `test_tier1_r5_predictions_today_endpoint_schema_and_filters`<br>- `test_tier1_r5_picks_contain_mandatory_timestamps`<br>- `test_tier1_r5_parlay_recommended_endpoint_returns_structured_payload`<br>- `test_tier1_r5_parlay_evaluated_legs_include_bookmaker_and_timestamps`<br>- `test_tier1_r5_upcoming_games_endpoint_serves_scheduled_slate` |

---

## 3. Hermeticity & Quota Protection Guarantee

Automated tests must **never** consume live The Odds API credits or mutate the quota ledger file:
- **Protected File**: `data/odds_quota_ledger.json`
- **Verification Routine**:
  1. Capture SHA-256 before any test session: `sha256sum data/odds_quota_ledger.json`
  2. Execute test suite hermetically (mocking network calls, using database fixtures or read-only services).
  3. Recompute SHA-256 after test session.
  4. Assert `before_hash == after_hash`.

---

## 4. Test Execution Commands

### In Docker Container (`sports_api`):
```bash
docker exec sports_api pytest backend/tests/test_e2e_requirements.py -v
```

### Full Backend Suite:
```bash
docker exec sports_api pytest -v
```

### Host Local Execution (with active backend virtualenv):
```bash
cd backend
pytest tests/test_e2e_requirements.py -v
```

---

## 5. Coverage & Quality Thresholds

- **Total E2E Test Cases**: 40 tests across Tiers 1–4.
- **Pass Rate Threshold**: 100% (0 failures, 0 errors).
- **Quota Leakage Threshold**: Exactly 0 credits consumed.
- **Ledger Hash Invariance**: Byte-for-byte SHA-256 match.
- **Warnings Policy**: Clean execution without unhandled async coroutine leaks.
