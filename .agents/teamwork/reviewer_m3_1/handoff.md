# Handoff Report: Reviewer 1 (Milestone 3 — NFL Analytics MVP)

## Review Summary

**Verdict**: **APPROVE**
**Integrity Audit**: **CLEAN (Zero Integrity Violations)**
**Adversarial Risk Assessment**: **LOW**

---

## 1. Observation

### 1.1 Integrity Audit and Code Verification
- **Zero Integrity Violations Detected**:
  - `backend/ml/training/train_temporal.py`, `backend/ml/inference.py`, `backend/app/services/math/ev.py`, `backend/app/services/sports_analytics_service.py`, `backend/app/services/parlay_service.py`, and `backend/app/workers/celery_app.py` were inspected and searched for hardcoded game IDs, mock returns, synthetic probabilities, or bypass shortcuts. Zero instances found.
  - Model artifacts on disk in `backend/ml/models/nfl/`:
    - `logistic.joblib`: `Pipeline(StandardScaler, LogisticRegression(max_iter=2000, random_state=42))`, 32 features, binary classes `[0, 1]`.
    - `hist_gradient_boosting.joblib`: `HistGradientBoostingClassifier(max_iter=200, learning_rate=0.06, l2_regularization=1.0, random_state=42)`, 32 features, binary classes `[0, 1]`.
    - `random_forest.joblib`: `RandomForestClassifier(n_estimators=100, max_depth=6, min_samples_leaf=5, random_state=42)`, 32 features, 100 fitted decision trees, binary classes `[0, 1]`.
  - Feature independence verified in `backend/ml/data_pipeline/temporal_features.py` and `model_metadata.json`: features comprise solely team EPA, success rates, rest days, and dynamic injury impact percentages (`ppr_lost_pct`, `off_snaps_lost_pct`, `def_snaps_lost_pct`). Zero sportsbook/betting lines exist in the feature set.

### 1.2 Multi-Model Evaluation and Walk-Forward Validation
- In `backend/ml/models/nfl/model_metadata.json`:
  - `model_selection.decision`: `"ensemble_selected"`
  - `model_selection.models`: `["logistic", "hist_gradient_boosting", "random_forest"]`
  - Walk-forward chronological folds: 4 folds.
  - Holdout evaluation on 20% holdout lockbox (95 test games from 2022-11-20 through 2025-01-26):
    - `accuracy`: `0.600` (60.0%)
    - `auc`: `0.6476`
    - `brier`: `0.2425` vs `majority_baseline_brier`: `0.2478` (strictly outperforms baseline)
    - `expected_calibration_error`: `0.0893` (well below 0.15 threshold)

### 1.3 Multi-Model Inference & Confluence Engine
- `backend/ml/inference.py`:
  - Lines 94-114 (`load_all_ensemble_models`): Loads `"logistic"`, `"hist_gradient_boosting"`, and `"random_forest"` from `backend/ml/models/{sport}`.
  - Lines 116-204 (`_apply_ensemble`): Concurrently evaluates `predict_proba(X)` across all loaded models, averages probabilities, calculates `model_agreement` (count of models agreeing on favored team), determines `confluence_tier` (`"HIGH"`, `"MODERATE"`, `"NONE"`), and records `individual_models` breakdown.
  - Lines 386-395: Automatically activates ensemble for `sport == "nfl"` and passes all three models to `_apply_ensemble`.

### 1.4 High-Confidence Decision Rules & Minimum Odds Filter
- `backend/app/services/math/ev.py`:
  - Lines 47-52 & 91-100: Enforces `meets_odds = (odds_decimal >= 1.40)` and `meets_confluence = (model_agreement is None or model_agreement >= 2) and (confluence_tier is None or confluence_tier.upper() != "NONE")`.
  - Sets `has_edge = self.edge_pct >= 0.03 and self.ev_dollars > 0 and meets_odds and meets_confluence`. If thresholds are not met, assigns `tier = "SKIP"` and emits explanatory warnings.
- `backend/app/services/sports_analytics_service.py`:
  - Lines 91-96: `home_eligible = model_valid and confluence_ok and (dec_h >= 1.40) and (ev_h is not None and ev_h > 0)`.

### 1.5 Spread Margin & Cover Probability Modeling
- `backend/app/services/sports_analytics_service.py`:
  - Lines 66-74: Derives projected score margin using Gaussian quantile:
    `proj_margin_raw = float(norm.ppf(min(0.999, max(0.001, model_prob_home))) * 13.5)`
    `proj_home_score = round((t_base + proj_margin_raw) / 2.0, 1)`
    `proj_away_score = round((t_base - proj_margin_raw) / 2.0, 1)`
  - Lines 147-150: Derives cover probability:
    `z_cover = (proj_margin_raw + spread_line) / 13.5`
    `p_home_cover = float(norm.cdf(z_cover))`
  - Fails closed when `model_prob_home` is None, returning `spread["status"] = "unavailable"` and `spread["tier"] = "UNAVAILABLE"`.

### 1.6 Correlated Microparlays
- `backend/app/services/parlay_service.py`:
  - Lines 145-214 (`_compute_joint_probability`): Computes joint probability for intra-game legs. For same-team ML and spread:
    - Negative spread: $P(\text{win} \cap \text{cover } -S) = P(\text{cover})$.
    - Positive spread: $P(\text{win} \cap \text{cover } +S) = P(\text{win})$.
    - Opposing teams in same game: returns `0.0`.
  - Lines 419-432: Viability rule for microparlays requires `ev_dollars > 0.0`, `odds_decimal >= 2.0` (+100 American), and `prob_combined >= 0.40`.
  - Lines 466-480: Exposes `evaluate_correlated_microparlay(legs, bankroll, kelly_fraction)`.

### 1.7 Celery Prediction Persistence
- `backend/app/workers/celery_app.py`:
  - Lines 386-412: Filters `pred_dict` keys against `valid_cols = {c.name for c in Prediction.__table__.columns}`, stripping `model_variant` and `used_injury_adjustment` (which do not exist in the database schema) while preserving `model_agreement` and `confluence_tier`.

### 1.8 Quota Ledger Hash & Test Execution
- Quota ledger SHA-256:
  `sha256sum data/odds_quota_ledger.json` -> `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4` (100% identical before and after all test runs).
- Verification test execution:
  - `docker exec sports_api pytest tests/test_ensemble_and_ev_verification.py -v`:
    `15 passed in 1.30s` (100% pass rate).
  - `docker exec sports_api pytest tests/test_parlay.py -v`:
    `17 passed in 0.63s` (100% pass rate).
  - `docker exec sports_api pytest tests/test_nfl_availability_phase02.py -q`:
    `67 passed in 29.50s` (100% pass rate).
  - `docker exec sports_api pytest tests/test_challenger_m3_adversarial.py tests/test_challenger_m3_empirical.py -v`:
    `36 passed in 1.12s` (100% pass rate).
  - Frontend: `npm run lint && npm run build` -> `0 errors, 0 warnings`.

---

## 2. Logic Chain

1. **Ensemble Implementation (R3)**:
   - Observation 1.1 & 1.2 demonstrate 3 distinct ML models (`LogisticRegression`, `HistGradientBoostingClassifier`, `RandomForestClassifier`) were trained using chronological walk-forward splits.
   - The holdout lockbox confirmed Brier score of 0.2425 beating majority baseline 0.2478 and ECE of 0.0893 < 0.15, satisfying production model evaluation criteria.
   - Observation 1.3 confirms `load_all_ensemble_models` and `_apply_ensemble` evaluate all three models concurrently and compute mean probability, agreement count, and confluence tiers without bookmaker inputs.

2. **Confluence and Minimum Odds Filter (R3)**:
   - Heavy favorites below 1.40 (such as -300 or -400) offer poor risk-adjusted returns and elevate catastrophic drawdown risks.
   - Observation 1.4 confirms that `meets_odds = (odds_decimal >= 1.40)` and `meets_confluence = (model_agreement >= 2)` are strictly required before `has_edge = True` can be assigned. Heavy favorites and low-agreement picks are systematically demoted to `tier = "SKIP"`.

3. **Spread Margin & Cover Probability (R3)**:
   - In NFL football, margin of victory is approximately normally distributed with $\sigma \approx 13.5$.
   - Observation 1.5 confirms mapping win probability to projected differential via $\mu = \Phi^{-1}(p) \times 13.5$ and cover probability via $\Phi((\mu + S) / 13.5)$. Clamping at $[0.001, 0.999]$ guarantees finite outputs under extreme probability inputs.
   - Absence of model probability triggers fail-closed `tier = "UNAVAILABLE"` without default imputations.

4. **Correlated Microparlays (R3)**:
   - Observation 1.6 shows intra-game leg combinations use conditional probabilities rather than naive multiplication.
   - The return requirement of combined odds $\ge 2.0\text{x}$ and positive EV $> 0$ with $\ge 40\%$ joint probability enforces risk-managed microparlays.

5. **Database Persistence & Quota Ledger (R4)**:
   - Observation 1.7 confirms non-schema fields are filtered prior to SQLAlchemy persistence.
   - Observation 1.8 confirms zero live API credits consumed and 0 byte modifications to `data/odds_quota_ledger.json`.

---

## 3. Caveats

1. **Pre-existing Timestamp Expiration in `tests/test_espn_external_predictor.py`**:
   - Two tests in `test_espn_external_predictor.py` fail because `_game()` hardcoded kickoff at `2026-10-16 00:15 UTC`. At runtime, wall-clock time passed `00:15 UTC`, causing the service to correctly fail closed with `event_started_or_finished`.
   - This failure is completely unrelated to Milestone 3 changes and reflects proper fail-closed behavior on expired events.
2. **Production Gate Configuration**:
   - `"production_enabled": false` remains checked in at `backend/ml/models/nfl/model_metadata.json` to preserve baseline regression safety until Milestone 4 formal gate release.

---

## 4. Conclusion

Milestone 3 (Ensemble and Decision Engine) satisfies all functional requirements (R3, R4) and acceptance criteria:
1. Three independent models evaluated and loaded concurrently.
2. High-confidence picks require model agreement $\ge 2/3$, $EV > 0$, and decimal odds $\ge 1.40$.
3. Spread margin and cover probability modeling active and defensive against extreme inputs.
4. Correlated microparlays target combined return $\ge 2.0\text{x}$ and positive EV.
5. Quota ledger hash unmodified (`f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`).
6. Zero integrity violations detected.
7. Verification test suites pass 100%.

**Verdict**: **APPROVE**

---

## 5. Verification Method

To independently reproduce and verify this review:

1. **Verify M3 Dedicated Suite**:
   ```bash
   docker exec sports_api pytest tests/test_ensemble_and_ev_verification.py -v
   ```
   *Expected: 15 passed in ~1.3s.*

2. **Verify Challenger M3 Suites**:
   ```bash
   docker exec sports_api pytest tests/test_challenger_m3_adversarial.py tests/test_challenger_m3_empirical.py -v
   ```
   *Expected: 36 passed in ~1.1s.*

3. **Verify Parlay Service Suite**:
   ```bash
   docker exec sports_api pytest tests/test_parlay.py -v
   ```
   *Expected: 17 passed in ~0.6s.*

4. **Verify Quota Ledger Integrity**:
   ```bash
   sha256sum data/odds_quota_ledger.json
   ```
   *Expected hash: `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`.*

5. **Verify Frontend Build & Lint**:
   ```bash
   cd frontend && npm run lint && npm run build
   ```
   *Expected: 0 errors, 0 warnings.*
