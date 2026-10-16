# Milestone 2: Dynamic Injury Model (R2) — Handoff Report

**Agent**: `worker_m2_1` (Dynamic Injury Model Worker)  
**Parent**: `9cd6cbfe-1463-4387-a3bc-691ae49aa9f2`  
**Date**: 2026-10-15T23:50:00Z  
**Working Directory**: `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/worker_m2_1`  
**Target Milestone**: Milestone 2 — Dynamic Injury Impact Engine & Inactives Tracking (Requirement R2)

---

## 1. Observation

1. **Quota Ledger Preservation**:
   - Tool command: `sha256sum data/odds_quota_ledger.json`
   - Output: `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4  data/odds_quota_ledger.json`
   - Quota ledger is completely preserved and untouched throughout all test executions.

2. **Feature Builder Anti-Leakage Join**:
   - In `backend/ml/data_pipeline/injury_feature_builder.py` (lines 137-152):
     ```python
     rosters["time_idx_search"] = rosters["time_idx"] - 1

     roster_strength = pd.merge_asof(
         rosters[["season", "week", "team", "player_id", "time_idx", "time_idx_search"]],
         game_logs[["player_id", "time_idx", "rolling_ppr", "rolling_off_pct", "rolling_def_pct"]],
         left_on="time_idx_search",
         right_on="time_idx",
         by="player_id",
         direction="backward",
     )
     # Verificación explícita de cero fuga temporal:
     if "time_idx_y" in roster_strength.columns and "time_idx_x" in roster_strength.columns:
         matched = roster_strength["time_idx_y"].notna()
         if (roster_strength.loc[matched, "time_idx_y"] >= roster_strength.loc[matched, "time_idx_x"]).any():
             raise DataQualityError("Fuga temporal detectada: estadísticas de semana en juego o futuras unidas al roster")
     ```
   - Matches only observations with `time_idx <= time_idx_search = time_idx - 1 < time_idx`, strictly enforcing prior-week access ($t-1$).

3. **Dynamic Degradation Proportional to Prior Production**:
   - In `backend/app/services/nfl_availability_service.py` (lines 846-880):
     ```python
     total_ppr_lost += p_ppr * p_abs
     ...
     ppr_lost_pct = min(1.0, round(total_ppr_lost / team_potential_ppr, 4))
     ```
   - A player's loss is calculated directly as $\text{rolling\_ppr} \times P(\text{absence})$. No positional multipliers exist (e.g. $QB=4.0, WR=1.5$).
   - A 25.0 PPR star producer produces $\frac{25.0}{72.0} = 0.3472$ degradation, while a 2.0 PPR low producer produces $\frac{2.0}{72.0} = 0.0278$ degradation ($12.5\times$ greater dynamic degradation).

4. **Elimination of Positional Multipliers**:
   - In `backend/app/services/nfl_availability_service.py` (line 1222):
     `"role_context": "descriptive_only_no_fixed_multiplier"`
   - Positional labels (QB, WR, RB, TE) do not modify `ppr_lost_pct`. A high-performing WR (25.0 PPR) penalizes the team significantly more ($12.5\times$) than a low-performing backup QB (2.0 PPR).

5. **Pre-Kickoff Inactives Workflow & Recalculation**:
   - In `backend/app/workers/celery_app.py` (lines 570-584):
     `schedule_nfl_gameday_captures_task` calculates `pregame_capture_dt = kickoff_dt - timedelta(minutes=90)` and schedules `capture_nfl_injuries_task.apply_async(args=("near_kickoff",), kwargs={"affected_game_ids": window_games}, eta=pregame_capture_py)`.
   - In `capture_nfl_injuries_task` (lines 496-505):
     Cadence `near_kickoff` triggers `evaluate_predictions_task.apply_async(args=("NFL",), kwargs={"game_ids": affected_game_ids}, queue="ml")`.
   - In `evaluate_predictions_task`:
     Loads upcoming games filtered by `game_ids`, evaluates `availability_by_game` via `build_game_availability_snapshot`, and executes `predict_matchups` with `model_variant="injury_aware"`.

6. **Test Verification Results**:
   - Command: `docker exec sports_api pytest tests/test_dynamic_injury_verification.py -v`
     - Output: `22 passed in 0.74s` (100% pass rate).
   - Command: `docker exec sports_api pytest -q`
     - Output: `346 passed in 35.51s` (100% pass rate, 0 failures, 0 errors).

---

## 2. Logic Chain

1. **From Observation 2 to Temporal Isolation**:
   Because `time_idx_search = time_idx - 1` and `direction="backward"`, the highest possible index matched from `game_logs` is `time_idx - 1`. Additionally, in `nfl_availability_service.py`, `evaluated_week` parameter explicitly filters out any observation where `week >= evaluated_week`, and timestamp filter rejects `g_dt >= as_of_dt`. Together with the runtime `DataQualityError` assertion, it is mathematically and programmatically impossible for same-week ($t$) or future-week ($t+1$) statistics to enter the availability pipeline or model features.

2. **From Observation 3 & 4 to Positional Multiplier Elimination**:
   `total_ppr_lost` is the unweighted sum of $\text{rolling\_ppr} \times P(\text{absence})$, normalized by `team_potential_ppr`. Because no position-specific multiplier is factored into this formula, player impact is purely a function of individual recent production relative to team potential. When tested with identical production across QB, RB, WR, and TE, the degradation percentage is invariant. When tested with a high-performing WR (25.0 PPR) vs a backup QB (2.0 PPR), the WR creates $12.5\times$ greater degradation, proving the total elimination of positional hierarchy.

3. **From Observation 5 to Pre-Kickoff Workflow Integrity**:
   `schedule_nfl_gameday_captures_task` dynamically computes the official 90-minute deadline before kickoff for each gameday window. When official inactives are captured, `capture_nfl_injuries_task` triggers `evaluate_predictions_task` for affected games. `build_game_availability_snapshot` ingests the near-kickoff snapshot, updates inactive player absence probabilities from baseline to 1.0 (CONFIRMED_OUT), causing team `ppr_lost_pct` to jump (e.g. from 0.0242 to 0.1611 in verified test scenario). Predictions are recalculated with advanced `as_of` timestamp ($t_2 > t_1$) and updated `inference_input_sha256`.

4. **From Observation 1 & 6 to Production Readiness**:
   All 22 dedicated verification tests and all 324 existing tests pass cleanly inside the `sports_api` Docker container (total: 346 tests passed). The quota ledger SHA-256 hash remained completely unaltered, proving zero live API requests were made during automated testing.

---

## 3. Caveats

1. **Production Gate Activation (Milestone 3 Dependency)**:
   In `backend/app/workers/celery_app.py`, `evaluate_predictions_task` skips database insertion of predictions (`skipped_db_insert: True`) because `model_metadata.json` currently has `"production_enabled": false`. This gate is deliberately designed to be activated in Milestone 3 after training the multi-model ensemble and validating chronological walk-forward holdout calibration.
2. **Player Props Market Signals**:
   Questionable and Doubtful players require verified market prop signals (`LISTED_AT_CUTOFF` or `NOT_LISTED_WITH_COVERAGE`) or confirmed out status to avoid failing closed to `UNKNOWN`. This is intentional and follows strict Phase 02 audit guidelines to prevent synthetic guessing.

---

## 4. Conclusion

Milestone 2 (Dynamic Injury Model — Requirement R2) is **100% complete, verified, and compliant**:
- Rolling PPR fantasy points from strictly prior weeks ($t-1$) dynamically govern injury degradation.
- Zero fixed positional multipliers exist. Actual individual production relative to team potential governs team impact.
- Zero temporal leakage is strictly enforced and verified via runtime assertions.
- Pre-kickoff inactives workflow at $t - 90\text{ min}$ is fully implemented and tested.
- Quota ledger is 100% preserved.
- Full test suite passes 100% (346/346).

---

## 5. Verification Method

To independently reproduce and verify this milestone:

1. **Verify Quota Ledger Integrity**:
   ```bash
   sha256sum data/odds_quota_ledger.json
   # Output must match: f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4
   ```

2. **Run Dedicated Milestone 2 Verification Suite**:
   ```bash
   docker exec sports_api pytest tests/test_dynamic_injury_verification.py -v
   # Expect: 22 passed in < 1.0s
   ```

3. **Run Entire Monorepo Backend Test Suite**:
   ```bash
   docker exec sports_api pytest -q
   # Expect: 346 passed in ~35s (0 failures, 0 errors)
   ```

4. **Files to Inspect**:
   - `backend/ml/data_pipeline/injury_feature_builder.py` (lines 140-153)
   - `backend/app/services/nfl_availability_service.py` (lines 842-880, 931-1050, 1075-1175, 1300-1330)
   - `backend/app/workers/celery_app.py` (lines 496-517, 570-605)
   - `backend/tests/test_dynamic_injury_verification.py`
