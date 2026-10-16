# Milestone 2 Reviewer & Critic Handoff Report

**Reviewer**: `reviewer_m2_2` (Reviewer 2 / Adversarial Critic)  
**Parent**: `9cd6cbfe-1463-4387-a3bc-691ae49aa9f2`  
**Date**: 2026-10-15T23:54:00Z  
**Verdict**: **APPROVE**  
**Working Directory**: `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/reviewer_m2_2`  
**Target Milestone**: Milestone 2 — Dynamic Injury Impact Engine & Inactives Tracking (Requirement R2)

---

## 1. Observation

1. **Independent Test Execution (Backend Test Suite)**:
   - Tool command: `docker exec sports_api pytest -q`
   - Output: `346 passed in 39.54s` (100% pass rate, 0 failures, 0 errors, 0 warnings).
   - Tool command: `docker exec sports_api pytest tests/test_dynamic_injury_verification.py -v`
   - Output: `22 passed in 0.75s` (100% pass rate).

2. **Odds Quota Ledger Protection**:
   - Tool command: `sha256sum data/odds_quota_ledger.json`
   - Output: `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4  data/odds_quota_ledger.json`
   - Quota ledger SHA-256 hash remained strictly unchanged throughout all test runs.

3. **Positional Multiplier Elimination and Absence Impact**:
   - In `backend/ml/data_pipeline/injury_feature_builder.py` (lines 45-60, 282-284, 310-312):
     ```python
     lost_prod["expected_ppr_lost"] = lost_prod["rolling_ppr"] * lost_prod["p_absence"]
     lost_prod["expected_off_lost"] = lost_prod["rolling_off_pct"] * lost_prod["p_absence"]
     lost_prod["expected_def_lost"] = lost_prod["rolling_def_pct"] * lost_prod["p_absence"]
     ...
     final_df["ppr_lost_pct"] = final_df["team_lost_ppr"] / final_df["team_potential_ppr"].clip(lower=1.0)
     ```
   - In `backend/app/services/nfl_availability_service.py` (lines 846, 877, 1220-1234):
     ```python
     total_ppr_lost += p_ppr * p_abs
     ...
     ppr_lost_pct = min(1.0, round(total_ppr_lost / team_potential_ppr, 4))
     ...
     validated_rank_ctx = {
         "rank": int(rank_val),
         "as_of": str(rank_as_of),
         "provenance": str(rank_prov),
         "role_context": "descriptive_only_no_fixed_multiplier",
     }
     ```
   - No positional multiplier (such as $QB=4.0$ or $WR=1.5$) exists in feature computation or availability calculation.
   - Positional invariance was verified: given identical 15.0 PPR production, QB, WR, RB, and TE all produce identical degradation ($\frac{15.0}{75.0} = 0.2000$).
   - A star WR (25.0 PPR) produces $\frac{25.0}{72.0} = 0.3472$ loss, while a backup QB (2.0 PPR) produces $\frac{2.0}{72.0} = 0.0278$ loss ($12.5\times$ greater dynamic degradation).

4. **Strict Temporal Leakage Prevention ($t-1$ Strictly)**:
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
     if "time_idx_y" in roster_strength.columns and "time_idx_x" in roster_strength.columns:
         matched = roster_strength["time_idx_y"].notna()
         if (roster_strength.loc[matched, "time_idx_y"] >= roster_strength.loc[matched, "time_idx_x"]).any():
             raise DataQualityError("Fuga temporal detectada: estadísticas de semana en juego o futuras unidas al roster")
     ```
   - In `backend/app/services/nfl_availability_service.py` (lines 1037-1045, 1169-1178, 1304-1331):
     `evaluated_week` parameter strictly filters out any log where `int(g["week"]) >= int(evaluated_week)`.
     Additionally, timestamps are verified with `g_dt < as_of_dt`.

5. **Pre-Kickoff Inactives Workflow & Recalculation**:
   - In `backend/app/workers/celery_app.py` (lines 496-505, 520-612):
     `schedule_nfl_gameday_captures_task` queries today's kickoffs, schedules `capture_nfl_injuries_task.apply_async(args=("near_kickoff",), kwargs={"affected_game_ids": window_games}, eta=pregame_capture_py)` at kickoff $- 90$ minutes.
     When `capture_nfl_injuries_task` runs with `cadence in ("near_kickoff", "pre_kickoff")`, it automatically triggers `evaluate_predictions_task.apply_async(args=("NFL",), kwargs={"game_ids": affected_game_ids}, queue="ml")`.
     Downstream predictions recalculate with upgraded `as_of` timestamp ($t_2 > t_1$) and updated `inference_input_sha256`.

6. **Integrity & Prohibited Pattern Checks**:
   - Grep search for hardcoded fixture tokens (`star_producer`, `backup_qb`, `elite_wr`, `2024_08_CHI_WAS`, `2024_10_DET_GB`) returned zero matches across `backend/app/` and `backend/ml/`.
   - Real mathematical logic is implemented in all services. No facade or dummy implementations.
   - Frontend `npm run lint` and `npm run build` execute with 0 errors and 0 warnings.

---

## 2. Logic Chain

1. **Absence of Positional Bias (Observation 3)**:
   In `nfl_availability_service.py` and `injury_feature_builder.py`, `expected_ppr_lost` is purely defined as `rolling_ppr * p_absence`. Normalization by `team_potential_ppr` computes the exact proportion of prior team offensive production lost. Because position names are never mapped to scaling factors, a high-producing WR or RB naturally exerts higher impact than a low-producing backup QB ($12.5\times$ in verified test). This strictly complies with R2 and the requirement to eliminate arbitrary positional multipliers.

2. **Proof of Leak-Free Temporal Boundaries (Observation 4)**:
   Because `time_idx_search = rosters["time_idx"] - 1` and `direction="backward"`, the highest possible index matched from `game_logs` is `time_idx - 1`. In `nfl_availability_service.py`, `evaluated_week` explicitly prevents records from week $t$ or later from contributing to rolling stats. Furthermore, `resolve_availability_status` enforces `observed_at < decision_cutoff` and fails closed to `UNKNOWN` if late information arrives. Consequently, temporal leakage is mathematically impossible.

3. **Pre-Kickoff Recalculation & System Reactivity (Observation 5)**:
   Official NFL inactives are released 90 minutes before kickoff. The dynamic scheduling logic computes `kickoff_utc - 90 min` for each gameday window. The resulting `near_kickoff` capture triggers `evaluate_predictions_task` with targeted `game_ids`. The verification test demonstrated that Amon-Ra St. Brown transitioning from Questionable ($P(\text{absence})=0.15$) to Confirmed Out ($P(\text{absence})=1.0$) increases Lions' PPR loss from $0.0242$ to $0.1611$ ($>6.6\times$ increase), advancing the inference snapshot timestamp ($t_2 > t_1$).

4. **Preservation of System Baseline & External Quotas (Observations 1, 2, 6)**:
   All 346 tests pass in the container without modifying the quota ledger SHA-256 (`f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`). The implementation exhibits genuine domain logic with zero integrity violations.

---

## 3. Adversarial Challenges & Stress Testing

| Scenario | Tested Stress Condition | Expected Behavior | Observed Result | Status |
|---|---|---|---|---|
| **Zero Team Potential** | Empty roster or Week 1 before games | Fail closed, no ZeroDivisionError | Returns `UNKNOWN` coverage state with `insufficient_team_potential_denominator` | **PASS** |
| **Missing Player Logs** | Injured player has no prior history on team | Do not impute zeros | Excludes guess, flags `missing_player_history_cnt`, fails closed to `UNKNOWN` | **PASS** |
| **Non-PPR Roles (OL / Defense)** | Lineman or defender injured | PPR loss is 0; snap shares captured | `_position_metrics` sets `ppr_applicable=False`; captures `off_snaps_lost_pct` and `def_snaps_lost_pct` | **PASS** |
| **Player Trade / Transfer** | Player traded from another team | Do not transfer other team's stats | Filters strictly on `team == current_team`; fails closed to `UNKNOWN` if no current team games | **PASS** |
| **Past Window Execution** | Gameday capture runs after kickoff $- 90$m | Do not crash or dispatch stale task | Catches condition, records `pregame_window_already_passed`, skips safely | **PASS** |
| **Positional Invariance** | QB vs RB vs WR vs TE with identical 15 PPR | Equal loss percentage | All 4 positions produce exactly $0.2000$ loss ($15.0 / 75.0$) | **PASS** |

---

## 4. Caveats

1. **Production Gate Activation (Milestone 3)**:
   In `celery_app.py`, `evaluate_predictions_task` currently sets `skipped_db_insert: True` because `model_metadata.json` has `"production_enabled": false`. This is the intended design for Milestone 2: the model training and walk-forward validation required to activate the production gate will occur in Milestone 3.
2. **Prop Signal Cutoff Requirement**:
   Questionable and Doubtful players require verified market prop signals (`LISTED_AT_CUTOFF` or `NOT_LISTED_WITH_COVERAGE`) to compute non-default probabilities. If market props are unverified, the system fails closed to `UNKNOWN`.

---

## 5. Conclusion

Milestone 2 (Dynamic Injury Model — Requirement R2) is **APPROVED**.
- All mathematical and dynamic degradation formulas are based strictly on rolling PPR fantasy points from prior weeks ($t-1$).
- Positional multipliers have been completely eliminated.
- Celery pre-kickoff inactives workflow at $t-90$ min is operational and triggers model recalculations.
- Quota ledger is preserved with 0 live API requests.
- Full backend test suite passes 100% (346/346).

---

## 6. Verification Method

To independently reproduce the verification:

1. **Verify Quota Ledger Immutability**:
   ```bash
   sha256sum data/odds_quota_ledger.json
   # Output must be: f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4
   ```

2. **Run Dedicated Verification Suite**:
   ```bash
   docker exec sports_api pytest tests/test_dynamic_injury_verification.py -v
   # Output: 22 passed in < 1.0s
   ```

3. **Run Full Backend Test Suite**:
   ```bash
   docker exec sports_api pytest -q
   # Output: 346 passed in ~39s (0 failures, 0 errors)
   ```

4. **Verify Frontend Cleanliness**:
   ```bash
   cd frontend && npm run lint && npm run build
   # Output: 0 errors, 0 warnings
   ```
