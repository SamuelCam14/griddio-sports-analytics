# Handoff Report: Milestone 3 Review (Reviewer 2 — Adversarial Critic & Reviewer)

## 1. Observation

- **Test Suite Execution**:
  - `docker exec sports_api pytest -q`:
    - Verbatim output: `2 failed, 383 passed in 42.40s`.
    - Exactly 383 backend tests passed.
    - The 2 failing tests are:
      `FAILED tests/test_espn_external_predictor.py::test_external_predictor_requires_exact_event_and_team_identity`
      `FAILED tests/test_espn_external_predictor.py::test_external_predictor_fails_closed_on_kickoff_mismatch`
    - Line inspection in `tests/test_espn_external_predictor.py:32`: `_game()` hardcodes `game_date=datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)`.
    - Line inspection in `app/services/espn_service.py:197`: `if kickoff <= datetime.now(timezone.utc): return {"status": "unavailable", "reason": "event_started_or_finished"}`. Because system wall-clock time passed `00:15 UTC`, this pre-existing test fails closed as designed due to an expired kickoff timestamp.
  - `docker exec sports_api pytest tests/test_ensemble_and_ev_verification.py -v`:
    - Verbatim output: `15 passed in 1.22s` (100% pass rate).
  - `docker exec sports_api pytest tests/test_parlay.py -v`:
    - Verbatim output: `17 passed in 0.68s` (100% pass rate).

- **Celery Prediction Persistence Remediation**:
  - `backend/app/workers/celery_app.py` lines 386-414:
    ```python
    valid_cols = {c.name for c in Prediction.__table__.columns}
    pred_dict = {
        "game_id": internal_id,
        "model_version": meta["model_version"],
        "dataset_sha256": meta["dataset_sha256"],
        "artifact_sha256": meta["artifact_sha256"],
        "inference_input_sha256": inference_input_sha256,
        "as_of": pd.Timestamp(meta["as_of"]).to_pydatetime(),
        "home_win_prob": row["home_win_probability"],
        "away_win_prob": row["away_win_probability"],
        "best_bookmaker": best_bm,
        "best_home_ml": best_home_ml,
        "best_away_ml": best_away_ml,
        "home_ev": home_ev,
        "away_ev": away_ev,
        "home_kelly": home_kelly,
        "away_kelly": away_kelly,
        "home_has_edge": home_edge,
        "away_has_edge": away_edge,
    }
    if "model_agreement" in row and pd.notna(row["model_agreement"]):
        pred_dict["model_agreement"] = int(row["model_agreement"])
    if "confluence_tier" in row and pd.notna(row["confluence_tier"]):
        pred_dict["confluence_tier"] = str(row["confluence_tier"])

    filtered_pred_dict = {k: v for k, v in pred_dict.items() if k in valid_cols}
    predictions_data.append(filtered_pred_dict)
    ```
  - `Prediction` model columns in `backend/app/models/game.py`: `model_agreement` and `confluence_tier` are valid columns. Non-column keys (`model_variant`, `used_injury_adjustment`) that previously triggered SQLAlchemy runtime errors are cleanly filtered out.

- **Vegas Odds Independence**:
  - `backend/ml/data_pipeline/temporal_features.py` lines 122-156 and `model_metadata.json` lines 292-325:
    The 32 features are:
    `home_win_rate`, `home_avg_margin`, `home_avg_points_for`, `home_avg_points_against`, `home_days_rest`, `home_is_back_to_back`, `home_games_prior`, `home_ppr_lost_pct`, `home_off_snaps_lost_pct`, `home_def_snaps_lost_pct`, `home_avg_off_epa_per_play`, `home_avg_off_success_rate`, `home_avg_def_epa_allowed_per_play`, `home_avg_def_success_allowed`, `away_win_rate`, `away_avg_margin`, `away_avg_points_for`, `away_avg_points_against`, `away_days_rest`, `away_is_back_to_back`, `away_games_prior`, `away_ppr_lost_pct`, `away_off_snaps_lost_pct`, `away_def_snaps_lost_pct`, `away_avg_off_epa_per_play`, `away_avg_off_success_rate`, `away_avg_def_epa_allowed_per_play`, `away_avg_def_success_allowed`, `diff_win_rate`, `diff_avg_margin`, `diff_avg_points_for`, `diff_days_rest`.
    - Exactly 0 betting lines, odds, moneylines, spreads, over/under, or market features are present in feature extraction, model training, or inference.

- **Integrity Verification of ML Models**:
  - Joblib files loaded in Python inside `sports_api`:
    - `ml/models/nfl/logistic.joblib`: `<class 'sklearn.pipeline.Pipeline'>` (StandardScaler + LogisticRegression)
    - `ml/models/nfl/hist_gradient_boosting.joblib`: `<class 'sklearn.ensemble._hist_gradient_boosting.gradient_boosting.HistGradientBoostingClassifier'>`
    - `ml/models/nfl/random_forest.joblib`: `<class 'sklearn.ensemble._forest.RandomForestClassifier'>`
  - All 3 models execute real `predict_proba` over feature matrices without hardcoding or dummy facade logic.

- **Odds Quota Ledger Hash**:
  - `sha256sum data/odds_quota_ledger.json`:
    `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4` (100% unmodified).

- **Frontend Cleanliness**:
  - `npm run lint && npm run build` in `frontend/`:
    `0 errors, 0 warnings`.

---

## 2. Logic Chain

1. **Test Suite Verification**:
   - Pytest inside `sports_api` executes 385 tests, passing 383.
   - The only 2 failures are in `tests/test_espn_external_predictor.py` due to an expired wall-clock kickoff timestamp (`2026-10-16 00:15 UTC` vs current time `00:42 UTC`). The underlying code fail-closed check (`kickoff <= datetime.now(timezone.utc)`) functioned exactly as designed. All other 383 tests passed.

2. **Persistence Integrity in Celery Worker**:
   - `Prediction` SQLAlchemy model defines columns including `model_agreement` (Integer) and `confluence_tier` (String), but not `model_variant` or `used_injury_adjustment`.
   - `celery_app.py` filters `pred_dict` dynamically via `valid_cols = {c.name for c in Prediction.__table__.columns}` before calling `append_predictions`.
   - This ensures all schema-valid columns are persisted without SQLAlchemy runtime errors.

3. **Vegas Independence Audit**:
   - Requirement R3 mandates that model win probabilities are 100% independent of Vegas odds.
   - We inspected `temporal_features.py`, `train_temporal.py`, `inference.py`, and `model_metadata.json`.
   - The feature schema consists solely of team EPA, offensive/defensive success rates, days of rest, back-to-back flags, and rolling prior-week injury metrics (`ppr_lost_pct`, `off_snaps_lost_pct`, `def_snaps_lost_pct`).
   - Vegas odds are only ingested downstream in the decision engine (`ev.py`, `sports_analytics_service.py`) to compute expected value against market prices.

4. **Adversarial Boundary Stress-Testing**:
   - **Odds Boundary**: Tested American odds `-250` (decimal `1.40`) vs `-251` (decimal `1.3984`). `-250` was accepted as `PREMIUM` with `has_edge=True`, while `-251` was rejected as `SKIP` with `has_edge=False`.
   - **Agreement Boundary**: Tested `model_agreement` values. Agreement = 1 was rejected as `SKIP` with `has_edge=False`; Agreement = 2 and 3 passed.
   - **Spread Missing Input**: When `model_prob_home` is `None` or invalid, `generate_nfl_matchup_analytics` fails closed with `spread.tier = "UNAVAILABLE"`.
   - **Microparlay Opposing Legs**: In `evaluate_correlated_microparlay`, opposing teams within the same event yielded joint probability `0.0` and `viable: False`.

5. **No Integrity Violations**:
   - We actively checked for:
     - Hardcoded test results or expected outputs in source code: None found.
     - Dummy or facade implementations: None found (all 3 models are genuine scikit-learn models).
     - Shortcuts bypassing the intended task: None found.
     - Fabricated verification outputs: None found.
     - Self-certifying work without genuine independent verification: None found.

---

## 3. Caveats

1. **`test_espn_external_predictor.py` Wall-Clock Dependency**:
   - As noted, `tests/test_espn_external_predictor.py` failed because of hardcoded `datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)` which elapsed during runtime. This is an existing test outside the M3 write boundary. It should be updated by the appropriate test maintainer to mock `datetime.now(timezone.utc)` or use a future date.
2. **Production Gate State**:
   - `model_metadata.json` currently maintains `"production_enabled": false` as instructed, to safeguard Phase 04 regression baselines. The activation mechanism was verified via monkeypatched unit tests (`test_production_gate_activation_end_to_end_when_enabled`).

---

## 4. Conclusion

**Verdict: APPROVE**

Milestone 3 (Requirement R3: Multi-Model Ensemble and Decision Engine) satisfies all functional, architectural, and mathematical requirements:
1. 3 distinct, genuine machine learning models (`LogisticRegression`, `HistGradientBoostingClassifier`, `RandomForestClassifier`) trained via walk-forward validation.
2. Win probabilities are 100% independent of Vegas odds.
3. Multi-model inference computes agreement (1-3) and confluence tiers ("HIGH", "MODERATE", "NONE").
4. Confluence decision rule and minimum odds filter ($\ge 1.40$, agreement $\ge 2$, $EV > 0$) strictly enforced.
5. Spread / handicap margin and cover probability modeling active and verified.
6. Correlated microparlays targeting $\ge 2.0\text{x}$ (+100) and $EV > 0$ with conditional probability modeling operational.
7. Celery prediction persistence bug remediated and verified.
8. Quota ledger hash unmodified (`f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`).
9. Backend test suite passes 383 tests; dedicated M3 verification suite passes 15/15 tests; frontend builds cleanly.

---

## 5. Verification Method

To independently reproduce the verification:

1. **Verify Backend Pytest Suite**:
   ```bash
   docker exec sports_api pytest -q
   ```
   *Expected: 383 passed, 2 failed (only the expired kickoff in test_espn_external_predictor.py).*

2. **Verify Milestone 3 Test Suite**:
   ```bash
   docker exec sports_api pytest tests/test_ensemble_and_ev_verification.py -v
   ```
   *Expected: 15 passed in ~1.2s.*

3. **Verify Parlay Service Tests**:
   ```bash
   docker exec sports_api pytest tests/test_parlay.py -v
   ```
   *Expected: 17 passed in ~0.7s.*

4. **Verify Boundary Conditions via Python**:
   ```bash
   docker exec sports_api python3 -c "
   from app.services.math.ev import calculate_ev
   b250 = calculate_ev(0.80, -250, model_agreement=3, confluence_tier='HIGH')
   b251 = calculate_ev(0.80, -251, model_agreement=3, confluence_tier='HIGH')
   assert b250.has_edge is True and b250.tier == 'PREMIUM'
   assert b251.has_edge is False and b251.tier == 'SKIP'
   print('Boundary odds check PASSED')
   "
   ```

5. **Verify Quota Ledger Immutability**:
   ```bash
   sha256sum data/odds_quota_ledger.json
   ```
   *Expected: `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`.*

6. **Verify Frontend Build & Lint**:
   ```bash
   cd frontend && npm run lint && npm run build
   ```
   *Expected: 0 errors, 0 warnings.*
