# Tier 5 Adversarial Coverage Hardening (Part 1) — Empirical Challenge Report

**Date**: 2026-10-16T01:20:00Z  
**Author**: Challenger 1 (`teamwork_preview_challenger`)  
**Role**: EMPIRICAL CHALLENGER (critic, specialist)  
**Target Milestone**: Final Milestone — Tier 5 Adversarial Coverage Hardening  
**Verdict**: **APPROVE** (Hardened / Zero Gaps Found)

---

## Challenge Summary

**Overall risk assessment**: **LOW**

The white-box implementation code across `backend/app/`, `backend/ml/`, and the data pipelines was subjected to 22 adversarial stress tests covering data integrity, zero-imputation policy, dynamic injury boundary conditions, multi-model ensemble confluences and thresholds, and quota ledger immutability. All 22 adversarial tests passed 100% inside `sports_api`. The system demonstrates robust fail-closed behaviors, mathematical bounds clipping without floating-point overflow, and strict quota preservation.

---

## 1. Observation

### 1.1 Quota Ledger Invariance
- Command: `sha256sum data/odds_quota_ledger.json`
- Output:
  ```
  f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4  data/odds_quota_ledger.json
  ```
- Result: 0 bytes modified, 0 credits consumed across all test executions.

### 1.2 Adversarial Test Suite Execution
- Target File: `backend/tests/test_tier5_adversarial_p1.py`
- Command: `docker exec sports_api pytest -v tests/test_tier5_adversarial_p1.py`
- Output:
  ```
  ============================= test session starts ==============================
  platform linux -- Python 3.12.15, pytest-9.1.1, pluggy-1.7.0 -- /usr/local/bin/python3.12
  rootdir: /app
  plugins: anyio-4.15.1, platformdirs-4.13.0, asyncio-1.4.0
  collected 22 items

  tests/test_tier5_adversarial_p1.py::TestTier5DataIntegrityAndZeroImputation::test_adv_empty_roster_fails_closed_without_synthetic_fallback PASSED [  4%]
  tests/test_tier5_adversarial_p1.py::TestTier5DataIntegrityAndZeroImputation::test_adv_missing_prior_week_stats_yields_none_and_unknown_coverage PASSED [  9%]
  tests/test_tier5_adversarial_p1.py::TestTier5DataIntegrityAndZeroImputation::test_adv_unmapped_or_ambiguous_players_fail_closed_to_unknown PASSED [ 13%]
  tests/test_tier5_adversarial_p1.py::TestTier5DataIntegrityAndZeroImputation::test_adv_corrupted_parquet_header_or_tampered_bytes_raises_data_quality_error PASSED [ 18%]
  tests/test_tier5_adversarial_p1.py::TestTier5DataIntegrityAndZeroImputation::test_adv_zero_imputation_ban_in_absence_of_stats PASSED [ 22%]
  tests/test_tier5_adversarial_p1.py::TestTier5DynamicInjuryEngineAdversarial::test_adv_injury_100_percent_production_injured_caps_cleanly_at_one PASSED [ 27%]
  tests/test_tier5_adversarial_p1.py::TestTier5DynamicInjuryEngineAdversarial::test_adv_injury_zero_historical_snaps_or_zero_potential_denominator PASSED [ 31%]
  tests/test_tier5_adversarial_p1.py::TestTier5DynamicInjuryEngineAdversarial::test_adv_injury_nan_or_negative_stats_fails_closed_in_inference PASSED [ 36%]
  tests/test_tier5_adversarial_p1.py::TestTier5DynamicInjuryEngineAdversarial::test_adv_injury_bye_week_backward_search_no_temporal_leakage PASSED [ 40%]
  tests/test_tier5_adversarial_p1.py::TestTier5DynamicInjuryEngineAdversarial::test_adv_confirmed_out_ir_permanently_immutable_under_conflicting_props PASSED [ 45%]
  tests/test_tier5_adversarial_p1.py::TestTier5MultiModelEnsembleAdversarial::test_adv_ensemble_model_disagreement_tie_resolves_to_none_confluence_and_skip PASSED [ 50%]
  tests/test_tier5_adversarial_p1.py::TestTier5MultiModelEnsembleAdversarial::test_adv_ensemble_extreme_probabilities_no_overflow_or_inf PASSED [ 54%]
  tests/test_tier5_adversarial_p1.py::TestTier5MultiModelEnsembleAdversarial::test_adv_ensemble_exact_borderline_odds_boundary_1_40_vs_1_3999 PASSED [ 59%]
  tests/test_tier5_adversarial_p1.py::TestTier5MultiModelEnsembleAdversarial::test_adv_ensemble_negative_ev_strictly_suppresses_pick_to_skip PASSED [ 63%]
  tests/test_tier5_adversarial_p1.py::TestTier5MultiModelEnsembleAdversarial::test_adv_ensemble_probabilities_sum_to_one_and_pure_football_independence PASSED [ 68%]
  tests/test_tier5_adversarial_p1.py::TestTier5QuotaLedgerAdversarialProtection::test_adv_quota_ledger_hash_invariance_before_and_after_suite PASSED [ 72%]
  tests/test_tier5_adversarial_p1.py::TestTier5QuotaLedgerAdversarialProtection::test_adv_quota_service_fail_closed_on_missing_or_corrupt_ledger PASSED [ 77%]
  tests/test_tier5_adversarial_p1.py::TestTier5QuotaLedgerAdversarialProtection::test_adv_quota_service_circuit_breaker_triggers_at_protected_reserve_boundary PASSED [ 81%]
  tests/test_tier5_adversarial_p1.py::TestTier5QuotaLedgerAdversarialProtection::test_adv_quota_request_cost_and_forbidden_endpoints PASSED [ 86%]
  tests/test_tier5_adversarial_p1.py::TestTier5CrossCuttingCorrelatedMicroparlays::test_adv_correlated_same_game_parlay_rejection PASSED [ 90%]
  tests/test_tier5_adversarial_p1.py::TestTier5CrossCuttingCorrelatedMicroparlays::test_adv_microparlay_under_target_odds_or_negative_ev_rejected PASSED [ 95%]
  tests/test_tier5_adversarial_p1.py::TestTier5CrossCuttingCorrelatedMicroparlays::test_adv_joint_probability_opposing_teams_in_same_game_is_strictly_zero PASSED [100%]

  ============================== 22 passed in 1.17s ==============================
  ```

### 1.3 Combined Suite Execution
- Command: `docker exec sports_api pytest -q tests/test_e2e_requirements.py tests/test_tier5_adversarial_p1.py`
- Output:
  ```
  ..............................................................           [100%]
  62 passed, 1 warning in 1.87s
  ```

---

## 2. Logic Chain

1. **Data Integrity & Zero-Imputation**:
   - *Observation*: In `backend/app/services/nfl_availability_service.py` (lines 746-766), if `prior_roster_stats` is empty or missing, `compute_team_production_lost` sets `coverage_state = UNKNOWN` and returns `ppr_lost_pct = None`, `off_snaps_lost_pct = None`, `def_snaps_lost_pct = None`.
   - *Observation*: In `backend/ml/data_pipeline/common.py` (lines 47-54), `verify_file` compares `hashlib.sha256(file_path.read_bytes()).hexdigest()` against manifest hash and raises `DataQualityError("Snapshot alterado")` on mismatch.
   - *Inference*: The pipeline strictly forbids silent 0-imputation and synthetic fallbacks. Corrupted files fail closed before ingestion.

2. **Dynamic Injury Engine**:
   - *Observation*: In `nfl_availability_service.py` (lines 877-879), `ppr_lost_pct` and snap fractions are clamped using `min(1.0, round(...))`.
   - *Observation*: In `backend/ml/data_pipeline/injury_feature_builder.py` (lines 140-146), `pd.merge_asof(direction="backward")` searches `time_idx <= time_idx_search`, joining pre-bye week data without temporal leakage.
   - *Observation*: In `backend/ml/inference.py` (lines 366-368), availability metrics outside `[0.0, 1.0]` or non-finite (NaN, Inf) raise `DataQualityError`.
   - *Inference*: The injury model correctly scales with player importance, caps gracefully at 100% loss under catastrophic scenarios, handles bye weeks without leakage, and rejects malformed metrics.

3. **Multi-Model Ensemble & Decision Engine**:
   - *Observation*: In `backend/ml/inference.py` (lines 163-174), 3-model disagreement (1/3 agreement or mean prob < 0.55) sets `confluence_tier = "NONE"`.
   - *Observation*: In `backend/app/services/sports_analytics_service.py` (lines 91-96, 134-142), `confluence_tier == "NONE"` forces `ml_tier = "SKIP"`, `spread_tier = "SKIP"`, and suppresses candidate best bets (`best_bet = None`).
   - *Observation*: Decimal odds $< 1.40$ (e.g. 1.3999 / American -251) force `home_eligible = False` and demote pick to `SKIP`.
   - *Observation*: Win probabilities $0.0001$ and $0.9999$ are clipped by `min(0.999, max(0.001, ...))` in projected margin calculation, preventing infinite scores.
   - *Inference*: Pick emission rules strictly enforce multi-model confluence, positive mathematical expectation (+EV), and odds $\ge 1.40$.

4. **Quota Ledger Protection**:
   - *Observation*: In `backend/app/services/odds_quota_service.py` (lines 280-296), `check_circuit_breaker` trips with `CircuitBreakerOpenError` when remaining balance $\le 75$ credits (15% protected reserve).
   - *Observation*: The ledger file `data/odds_quota_ledger.json` maintained SHA-256 `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4` before and after all test runs.
   - *Inference*: Zero live credits were consumed, and the circuit breaker prevents quota starvation.

---

## 3. Challenges & Stress-Test Results

| Challenge ID | Target Subsystem | Adversarial Attack Scenario | Expected Defense | Observed Behavior | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **ADV-01** | Data Pipeline | Empty prior roster stats dictionary | Fail closed to `UNKNOWN`, `ppr_lost_pct=None` | Handled via `compute_team_production_lost` | **PASS** |
| **ADV-02** | Data Pipeline | Injured player missing from historical stats table | Fail closed to `UNKNOWN`, flag missing history | Handled; sets `missing_player_history_count=1` | **PASS** |
| **ADV-03** | Roster Resolver | Player with unmapped ID / "Desconocido" | Fail closed to `UNKNOWN`, flag identity unresolved | Handled via `process_player_injury` | **PASS** |
| **ADV-04** | Provenance | Corrupted/tampered bytes in parquet file | Raise `DataQualityError` | `verify_file` caught SHA-256 discrepancy | **PASS** |
| **ADV-05** | Imputation | Unreachable source / omitted team | Differentiated `UNAVAILABLE_NO_REPORT` / `NO_INJURIES_LISTED` | Distinct enum states returned; zero imputation blocked | **PASS** |
| **ADV-06** | Injury Engine | 100% and >100% team production loss | Cleanly cap at 1.0 without NaN/overflow | `ppr_lost_pct=1.0`, `off_snaps_lost_pct=1.0` | **PASS** |
| **ADV-07** | Injury Engine | Zero team potential denominator (0.0 PPR) | Avoid divide-by-zero, fail closed to `UNKNOWN` | Caught; reason `"insufficient_team_potential_denominator"` | **PASS** |
| **ADV-08** | Inference | Availability snapshot with NaN or negative metric | Raise `DataQualityError` | Caught in `predict_matchups` | **PASS** |
| **ADV-09** | Temporal Engine | Bye week with $t-1$ index gap | Backward search to pre-bye week; zero leakage | Matched Week 6 for Week 8 (`time_idx_y < time_idx_x`) | **PASS** |
| **ADV-10** | Market Modulator | Rogue bookmaker props for confirmed OUT/IR player | Permanent OUT ($p=1.0$), ignore props | Invariant `p_absence=1.0`, `checked_props=False` | **PASS** |
| **ADV-11** | Ensemble | 3-model disagreement tie (1/3 agreement) | Confluence tier `"NONE"`, tier demoted to `"SKIP"` | Handled; `confluence_tier="NONE"`, `best_bet=None` | **PASS** |
| **ADV-12** | Analytics | Extreme probabilities (0.0001, 0.9999) | Finite scores, capped Kelly stake ($\le 5\%$) | Handled; no overflow or NaN, Kelly capped | **PASS** |
| **ADV-13** | EV Engine | Exact borderline odds (1.40 vs 1.3999) | 1.40 eligible; 1.3999 rejected to `"SKIP"` | Handled; 1.40 is VALUE/PREMIUM, 1.3999 is SKIP | **PASS** |
| **ADV-14** | Decision Engine | Positive win prob with negative EV | Demote pick to `"SKIP"`, 0 Kelly stake | Handled; `tier="SKIP"`, `has_kelly_edge=False` | **PASS** |
| **ADV-15** | Inference | Model probabilities sum & Vegas independence | Sum equals 1.0 exactly; no bookmaker bias | Handled; `p_home + p_away == 1.0` | **PASS** |
| **ADV-16** | Infrastructure | Quota ledger SHA-256 invariance | Byte-for-byte exact hash match | Matched `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4` | **PASS** |
| **ADV-17** | Infrastructure | Missing or corrupted quota ledger file | Fail closed with `QuotaExceededError` | Handled; raises `QuotaExceededError` | **PASS** |
| **ADV-18** | Circuit Breaker | Remaining quota at protected reserve ($\le 75$) | Raise `CircuitBreakerOpenError` | Handled; raises `CircuitBreakerOpenError` | **PASS** |
| **ADV-19** | Quota Estimator | Forbidden `/historical` endpoint call | Raise `QuotaExceededError` | Handled; blocks 10x cost historical calls | **PASS** |
| **ADV-20** | Parlay Builder | Same-game correlated legs in standard parlay | Reject with `viable=False`, `has_correlated_legs=True` | Handled via `evaluate_custom_parlay` | **PASS** |
| **ADV-21** | Microparlay | Microparlay with odds $< 2.0x$ or negative EV | Reject with `viable=False` | Handled; enforces $\ge 2.0x$ and $EV > 0$ | **PASS** |
| **ADV-22** | Parlay Engine | Conflicting outcomes in same game | Joint probability strictly equals 0.0 | Handled; `_compute_joint_probability == 0.0` | **PASS** |

---

## 4. Caveats

- **External Network Calls**: Live outbound HTTP requests were mocked or intercepted during testing to guarantee quota ledger protection.
- **Pre-Kickoff Timing**: Pre-kickoff evaluations require simulated UTC clocks consistent with the game slate horizon ($t-90$ min).

---

## 5. Conclusion

**Verdict**: **APPROVE**

The Tier 5 white-box adversarial coverage hardening confirms that the NFL analytics MVP codebase is resilient to boundary condition failures, floating point anomalies, temporal data leaks, and unauthorized quota consumption. The system satisfies all requirements R1 through R5 and all Master Specification acceptance criteria.

---

## 6. Verification Method

To independently reproduce the adversarial test execution:

```bash
# 1. Run the Tier 5 Adversarial Test Suite (22 tests)
docker exec sports_api pytest -v tests/test_tier5_adversarial_p1.py

# 2. Run the combined requirements and adversarial suite (62 tests)
docker exec sports_api pytest -v tests/test_e2e_requirements.py tests/test_tier5_adversarial_p1.py

# 3. Verify quota ledger immutability
sha256sum data/odds_quota_ledger.json
```
