# Milestone 2 Review & Adversarial Challenge Report

**Reviewer:** Reviewer 1 (`teamwork_preview_reviewer` / Adversarial Critic)  
**Date:** 2026-10-15T23:56:00Z  
**Target:** Milestone 2 (M2) — Dynamic Injury Impact Engine & Inactives Tracking (Requirement R2)  
**Working Directory:** `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/reviewer_m2_1`  
**Verdict:** **APPROVE**

---

## Review Summary

**Verdict**: **APPROVE**  
**Integrity Audit**: **PASS (CLEAN)** — No hardcoded test results, no dummy or facade implementations, no task shortcuts, no fabricated outputs, and no self-certifying work.

---

## 1. Observation

### 1.1. Code Modifications in Scope
Direct source inspection across the affected files confirmed the following implementations:

1. `backend/ml/data_pipeline/injury_feature_builder.py`:
   - Line 109: Deduplicated roster rows on `["season", "week", "team", "player_id"]` to avoid denominator inflation.
   - Lines 137-146: Time index search joined via `merge_asof` with `direction="backward"`:
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
     ```
   - Lines 149-152: Anti-leak runtime assertion:
     ```python
     if "time_idx_y" in roster_strength.columns and "time_idx_x" in roster_strength.columns:
         matched = roster_strength["time_idx_y"].notna()
         if (roster_strength.loc[matched, "time_idx_y"] >= roster_strength.loc[matched, "time_idx_x"]).any():
             raise DataQualityError("Fuga temporal detectada: estadísticas de semana en juego o futuras unidas al roster")
     ```
   - Lines 282-310: Expected loss calculation:
     `lost_prod["expected_ppr_lost"] = lost_prod["rolling_ppr"] * lost_prod["p_absence"]`
     `final_df["ppr_lost_pct"] = final_df["team_lost_ppr"] / final_df["team_potential_ppr"].clip(lower=1.0)`
     No positional weight multiplier is present; the penalty is strictly proportional to recent player production.

2. `backend/app/services/nfl_availability_service.py`:
   - Lines 846, 877:
     `total_ppr_lost += p_ppr * p_abs`
     `ppr_lost_pct = min(1.0, round(total_ppr_lost / team_potential_ppr, 4))`
   - Lines 931, 1037-1044: `load_recent_player_game_logs` accepts `evaluated_week: Optional[int] = None`. Any row where `int(row["week"]) >= int(evaluated_week)` is strictly skipped (`continue`).
   - Lines 1071, 1169-1176: `calculate_player_recent_importance` accepts `evaluated_week` and filters `int(g["week"]) >= int(evaluated_week)`.
   - Lines 1164-1180: Date filter enforces `g_date < as_of_dt`, and season filter enforces `int(g_season) == int(active_season)`.
   - Lines 1220-1234: `pos_rank_context` is explicitly recorded with `"role_context": "descriptive_only_no_fixed_multiplier"`.
   - Lines 1236-1244: `importance_score` is computed without any positional rank multiplier.
   - Lines 1301-1330: In `build_game_availability_snapshot`, `eval_week` is parsed from `game_id` (e.g. `2024_10_DET_GB` -> `10`) and passed to `calculate_player_recent_importance`.

3. `backend/app/workers/celery_app.py`:
   - Lines 169-170: `evaluate_predictions_task` filters upcoming games when `game_ids` is provided:
     `upcoming = upcoming[upcoming["game_id"].astype(str).isin([str(g) for g in game_ids])]`
   - Lines 190-200: Builds fresh availability snapshots per game at current inference cutoff.
   - Lines 202-209: Selects `model_variant="injury_aware"` for NFL when availability snapshots exist, passing `availability_by_game` to `predict_matchups`.
   - Lines 496-505: In `capture_nfl_injuries_task`, cadence `near_kickoff` triggers `evaluate_predictions_task.apply_async(args=("NFL",), kwargs={"game_ids": affected_game_ids}, queue="ml")`.
   - Lines 567-585: In `schedule_nfl_gameday_captures_task`, kickoff times are grouped, `pregame_capture_dt = ko_dt - timedelta(minutes=90)` is computed, and `capture_nfl_injuries_task.apply_async(args=("near_kickoff",), kwargs={"affected_game_ids": window_games}, eta=pregame_capture_py)` is scheduled.

4. `backend/tests/test_dynamic_injury_verification.py`:
   - 22 comprehensive, automated verification tests covering dynamic production impact, positional hierarchy elimination, strict temporal leakage prevention, pre-kickoff inactives workflow, and quota ledger immutability.

### 1.2. Verification Test Execution Results

1. **Dedicated Verification Suite**:
   - Command: `docker exec sports_api pytest tests/test_dynamic_injury_verification.py -v`
   - Output: `22 passed in 1.07s` (exit code 0).
2. **Phase 02 Regression Suite**:
   - Command: `docker exec sports_api pytest tests/test_nfl_availability_phase02.py -v`
   - Output: `67 passed in 28.50s` (exit code 0).
3. **Full Backend Monorepo Suite**:
   - Command: `docker exec sports_api pytest -q`
   - Output: `346 passed in 39.96s` (exit code 0, 0 failures, 0 errors).
4. **Quota Ledger Immutability**:
   - Command: `sha256sum data/odds_quota_ledger.json`
   - Output: `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4  data/odds_quota_ledger.json`
   - Result: 100% identical to baseline. Zero quota credits consumed.

---

## 2. Logic Chain

1. **Anti-Leakage Isolation (Observation 1.1.1 & 1.1.2)**:
   In `injury_feature_builder.py`, `time_idx_search = time_idx - 1` with `direction="backward"` restricts joins to time indices strictly less than the evaluated week. If any row in the merged output ever violates this rule (`time_idx_y >= time_idx_x`), a `DataQualityError` runtime assertion triggers. In `nfl_availability_service.py`, `evaluated_week` explicitly skips any logs from the game week or future weeks (`row["week"] >= evaluated_week`), and `g_date >= as_of_dt` prevents same-day/in-progress game contamination. This provides a two-layer barrier against temporal leakage.

2. **Positional Weight Elimination & Production Proportionality (Observation 1.1.1 & 1.1.2)**:
   `total_ppr_lost` is formulated as $\sum (\text{rolling\_ppr} \times P(\text{absence}))$. Dividing by `team_potential_ppr` makes degradation purely proportional to individual contribution. As demonstrated in `TestPositionalHierarchyElimination`, holding production constant across positions (QB, WR, RB, TE) produces identical degradation, while an elite WR (25.0 PPR) produces $12.5\times$ more degradation than a backup QB (2.0 PPR). This confirms the complete elimination of fixed positional multipliers.

3. **Pre-Kickoff Celery Inactives Workflow (Observation 1.1.3)**:
   `schedule_nfl_gameday_captures_task` dynamically computes the official 90-minute deadline before kickoff for each gameday window. When official inactives are captured, `capture_nfl_injuries_task` triggers `evaluate_predictions_task` for affected games. `build_game_availability_snapshot` ingests the near-kickoff snapshot, updates inactive player absence probabilities from baseline to 1.0 (CONFIRMED_OUT), causing team `ppr_lost_pct` to jump (e.g. from 0.0242 to 0.1611 in verified test scenario). Predictions are recalculated with advanced `as_of` timestamp ($t_2 > t_1$) and updated `inference_input_sha256`.

4. **Integrity & Zero-Imputation Guarantees (Observation 1.1.2 & 1.2)**:
   When a player or team has no prior history in the active season (e.g., Week 1 or newly signed player), `NFLAvailabilityService` returns `status="unknown"`, leading `compute_team_production_lost` to transition into `CoverageState.UNKNOWN` with `ppr_lost_pct=None`. The system refuses to inject arbitrary zeros or default constants. All 346 tests pass without consuming any quota from `data/odds_quota_ledger.json`.

---

## 3. Adversarial Challenges & Stress-Testing

### Challenge 1: Early-Season / Week 1 Cold Start
- **Assumption Challenged**: What happens when calculating dynamic PPR loss in Week 1, where no prior games exist in the active season?
- **Stress-Test**: Inspected `nfl_availability_service.py` lines 1146-1180. The service filters exclusively on the active season (`int(g_season) == int(active_season)`). If no prior logs are available, `eligible_logs` is empty, returning `status: "unknown"`.
- **Blast Radius**: If handled improperly, code could crash or impute default values.
- **Result & Mitigation**: Passed. `compute_team_production_lost` detects missing player history, flags `CoverageState.UNKNOWN`, and returns `ppr_lost_pct=None`, cleanly falling back without synthetic data.

### Challenge 2: Non-Standard Game ID Format in Live Inference
- **Assumption Challenged**: `eval_week` extraction uses `game_id.split("_")[1]`. If an external game ID does not adhere to `YYYY_WW_AWAY_HOME`, does temporal protection break?
- **Stress-Test**: Traced line 1301 of `nfl_availability_service.py`. If splitting fails or part 1 is non-numeric, `eval_week` remains `None`. In that scenario, line 1165 `g_date >= as_of_dt` acts as the safety net. Since pre-kickoff captures occur at $t - 90\text{ min}$, the game has not been played, so game logs for that game do not exist.
- **Result**: Resilient. Two independent layers protect against data leakage.

### Challenge 3: Celery Broker / Task Dispatch Failure
- **Assumption Challenged**: If Redis or Celery experiences transient issues during near-kickoff capture, could it crash the data capture pipeline?
- **Stress-Test**: Inspected `capture_nfl_injuries_task` lines 497-504. The call to `evaluate_predictions_task.apply_async` is guarded by a `try...except Exception` block that logs a warning and returns the valid snapshot data rather than raising an unhandled exception.
- **Result**: Resilient.

---

## 4. Caveats

1. **Production Gate Activation (Milestone 3 Dependency)**:
   `evaluate_predictions_task` skips database insertion of predictions (`skipped_db_insert: True`) because `backend/ml/models/nfl/model_metadata.json` currently has `"production_enabled": false`. This gate is intentionally scheduled to be activated in Milestone 3 following ensemble training and walk-forward validation.
2. **Player Props Market Coverage**:
   Questionable and Doubtful players require verified market prop signals (`LISTED_AT_CUTOFF` or `NOT_LISTED_WITH_COVERAGE`) or confirmed out status to avoid failing closed to `UNKNOWN`. This follows the zero-synthetic data policy.

---

## 5. Conclusion

Milestone 2 (Dynamic Injury Model — Requirement R2) satisfies all requirements and acceptance criteria:
- Rolling PPR fantasy points are strictly derived from prior weeks ($t-1$) with explicit runtime assertions preventing temporal leakage.
- Zero fixed positional weight multipliers exist; degradation is purely proportional to observed recent production relative to team potential.
- Celery near-kickoff capture at $t-90\text{ min}$ automatically schedules and triggers prediction recalculation for affected games with timestamp progression.
- Automated tests pass 100% across the repository (346/346 passed).
- Quota ledger `data/odds_quota_ledger.json` is completely preserved.
- No integrity violations detected.

**Final Verdict: APPROVE**

---

## 6. Verification Method

To independently verify this evaluation:

1. **Check Quota Ledger Checksum**:
   ```bash
   sha256sum data/odds_quota_ledger.json
   # Output must match: f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4
   ```

2. **Execute Dedicated Milestone 2 Verification Test Suite**:
   ```bash
   docker exec sports_api pytest tests/test_dynamic_injury_verification.py -v
   # Output: 22 passed in ~1.0s
   ```

3. **Execute Full Backend Test Suite**:
   ```bash
   docker exec sports_api pytest -q
   # Output: 346 passed in ~40s
   ```

4. **Verify Key Files**:
   - `backend/ml/data_pipeline/injury_feature_builder.py` (lines 137-152, 282-310)
   - `backend/app/services/nfl_availability_service.py` (lines 846-880, 1037-1044, 1169-1176, 1220-1234, 1301-1330)
   - `backend/app/workers/celery_app.py` (lines 169-209, 496-505, 567-585)
   - `backend/tests/test_dynamic_injury_verification.py`
