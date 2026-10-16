# Handoff Report: Milestone 3 — Ensemble and Decision Engine

## 1. Observation

- **Artifacts on Disk**:
  - `backend/ml/models/nfl/logistic.joblib` (3.3 KB)
  - `backend/ml/models/nfl/hist_gradient_boosting.joblib` (559 KB)
  - `backend/ml/models/nfl/random_forest.joblib` (503 KB)
  - `backend/ml/models/nfl/model_metadata.json` (11.9 KB)
  - `backend/tests/test_ensemble_and_ev_verification.py` (440 lines, 15 tests)

- **Training Evaluation (`model_metadata.json`)**:
  - `model_selection.decision`: `"ensemble_selected"`
  - `model_selection.models`: `["logistic", "hist_gradient_boosting", "random_forest"]`
  - Folds: 4 chronological walk-forward folds.
  - 20% Holdout lockbox metrics:
    - Holdout Accuracy: `60.0%`
    - Holdout AUC: `0.6476`
    - Holdout Brier: `0.2425` (beating majority baseline Brier `0.2478`)
    - Holdout Expected Calibration Error (ECE): `0.0893` (< 0.10 calibration limit)

- **Vegas Independence**:
  - `backend/ml/data_pipeline/temporal_features.py` lines 122-155: feature vector composed strictly of team offensive/defensive EPA, win rates, days of rest, back-to-back flags, and injury impact (`ppr_lost_pct`, `off_snaps_lost_pct`, `def_snaps_lost_pct`). Zero sportsbook/betting metrics exist in the training pipeline or feature set.

- **Multi-Model Inference & Agreement (`backend/ml/inference.py`)**:
  - Exposes `load_all_ensemble_models(sport, model_variant)` and `_apply_ensemble(models, X, df)`.
  - Concurrently computes model probabilities for `logistic`, `hist_gradient_boosting`, and `random_forest`.
  - Calculates blended mean probability, `model_agreement` (1 to 3), and `confluence_tier`:
    - `agreement == 3` and `prob >= 0.65`: `"HIGH"`
    - `(agreement == 3 and prob >= 0.55) or (agreement == 2 and prob >= 0.55)`: `"MODERATE"`
    - Otherwise: `"NONE"`
  - Stores `individual_models` breakdown per game.

- **Confluence Decision Rule & Minimum Odds (`backend/app/services/math/ev.py`)**:
  - `calculate_ev(prob_model, odds_american, model_agreement, confluence_tier)`:
    - Enforces `odds_decimal >= 1.40` (American $\ge -250$). If `odds_decimal < 1.40`, sets `has_edge = False`, `tier = "SKIP"`, and adds warning `"Cuota decimal ... inferior al umbral mínimo de 1.40 (-250); pick descartado."`.
    - Enforces `model_agreement >= 2` and `confluence_tier != "NONE"`. If agreement < 2, sets `has_edge = False`, `tier = "SKIP"`.
    - Only emits `PREMIUM` (edge $\ge 8\%$, EV $\ge 0$) or `VALUE` (edge $\ge 3\%$, EV $\ge 0$) when confluence and minimum odds thresholds are met.
  - `build_parlay`: enforces combined return $\ge 2.0\text{x}$ (+100) and $EV > 0$.

- **Spread / Handicap Margin & Cover Probability (`backend/app/services/sports_analytics_service.py`)**:
  - Uses `scipy.stats.norm` to map model win probability into score margin distribution $\mu = \Phi^{-1}(p) \times 13.5$.
  - Derives cover probability $P(\text{cover}) = \Phi(\frac{\mu + S}{13.5})$ and $EV_{\text{spread}} = P(\text{cover}) \cdot (\text{decimal\_odds} - 1) - (1 - P(\text{cover}))$.
  - When `model_prob_home is None`, preserves fail-closed behavior returning `spread["status"] = "unavailable"` and `spread["tier"] = "UNAVAILABLE"`.

- **Correlated Microparlays (`backend/app/services/parlay_service.py`)**:
  - `evaluate_custom_parlay` updated with `allow_correlation: bool = False`.
  - When `has_correlated_legs` and `not allow_correlation`: strictly rejects with verbatim error `"No se permite combinar selecciones del mismo evento como patas independientes..."`, preserving existing test suite requirements.
  - When `has_correlated_legs` and `allow_correlation`: uses `_compute_joint_probability` implementing conditional probability rules (e.g., covering negative spread implies winning: $P(\text{win} \cap \text{cover -S}) = P(\text{cover})$).
  - Viability rule for microparlays: `ev_dollars > 0`, `odds_decimal >= 2.0`, `prob_combined >= 0.40`.
  - Exposes `evaluate_correlated_microparlay(legs, bankroll, kelly_fraction)`.

- **Celery Prediction Persistence (`backend/app/workers/celery_app.py`)**:
  - Filtered `pred_dict` before calling `append_predictions` against `valid_cols = {c.name for c in Prediction.__table__.columns}`.
  - Strips non-column fields `model_variant` and `used_injury_adjustment` (which caused database insertion crashes).
  - Preserves and persists `model_agreement` and `confluence_tier` in the database.

- **Test Suite Results**:
  - `docker exec sports_api pytest tests/test_ensemble_and_ev_verification.py -v`:
    `15 passed in 1.18s` (100% pass rate).
  - `docker exec sports_api pytest tests/test_parlay.py -v`:
    `17 passed in 0.63s` (100% pass rate).
  - `docker exec sports_api pytest tests/test_nfl_availability_phase02.py -v`:
    `67 passed in 25.85s` (100% pass rate).
  - `docker exec sports_api pytest -q`:
    `383 passed, 2 failed in 42.01s` (99.5% pass rate). The 2 failures belong exclusively to `tests/test_espn_external_predictor.py`.

- **Quota Ledger Hash**:
  - `sha256sum data/odds_quota_ledger.json`:
    `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4` (100% unchanged, zero external API credits consumed).

---

## 2. Logic Chain

1. **Ensemble Architecture**:
   - `train_temporal.py` was extended to train `RandomForestClassifier` alongside `LogisticRegression` and `HistGradientBoostingClassifier`.
   - Chronological evaluation across 4 walk-forward folds and a 20% holdout lockbox confirmed holdout accuracy of 60.0% and Brier score of 0.2425 (outperforming majority baseline 0.2478).
   - `load_all_ensemble_models` in `backend/ml/inference.py` loads all three `.joblib` files and executes concurrent inference.
   - Blended probability is computed as the mean of all models, and agreement (1 to 3) is determined by comparing each model's preference against the ensemble preference.
   - High confluence requires unanimous agreement (3/3) and probability $\ge 0.65$; moderate confluence requires $\ge 2/3$ agreement and probability $\ge 0.55$.

2. **Independence from Bookmaker Lines**:
   - Inspection of `temporal_features.py` and `model_metadata.json["features"]` confirmed zero betting or line features exist.
   - Vegas odds are ingested only downstream during the decision engine phase to evaluate market value against pure model probabilities.

3. **Confluence & Minimum Odds Rules**:
   - Heavy favorites with decimal odds $< 1.40$ (e.g. 1.25 / -400) offer low risk-adjusted value despite high hit rates.
   - In `backend/app/services/math/ev.py`, checking `odds_decimal >= 1.40` and `model_agreement >= 2` before allowing `has_edge = True` forces `tier = "SKIP"`, eliminating dangerous negative-utility favorites.

4. **Spread Cover Probability & Margin Modeling**:
   - In `backend/app/services/sports_analytics_service.py`, using Gaussian inverse CDF $\mu = \Phi^{-1}(p) \times 13.5$ derives a realistic projected point differential without arbitrary heuristics.
   - Calculating $P(\text{cover}) = \Phi(\frac{\mu + S}{13.5})$ provides continuous probability modeling against any observed market spread.

5. **Correlated Microparlays**:
   - In `backend/app/services/parlay_service.py`, standard parlays assume independent trials ($P = \prod p_i$). When combining same-game outcomes, independence fails.
   - `_compute_joint_probability` applies conditional probabilities: covering a negative spread strictly entails winning ($P = P(\text{cover})$), and winning entails covering a positive spread ($P = P(\text{win})$).
   - Requiring combined return $\ge 2.0\text{x}$ (+100) and $EV > 0$ satisfies the microparlay requirement.

6. **Celery Prediction Persistence Remediation**:
   - `Prediction.__table__.columns` contains `model_agreement` and `confluence_tier`, but not `model_variant` or `used_injury_adjustment`.
   - Filtering `pred_dict` against `Prediction.__table__.columns` prevents SQLAlchemy runtime mapping errors while successfully persisting confluence metrics.

7. **Production Gate Activation**:
   - As confirmed with the orchestrator, keeping `"production_enabled": false` in `model_metadata.json` preserves existing regression tests that explicitly assert the baseline gate is closed.
   - End-to-end production gate activation was verified in `test_ensemble_and_ev_verification.py::test_production_gate_activation_end_to_end_when_enabled` via monkeypatching `Path.read_text`, proving that setting `"production_enabled": true` activates `_production_model_enabled` and resolves `"nfl_temporal_v1"`.

---

## 3. Caveats

1. **Two External Failures in `tests/test_espn_external_predictor.py`**:
   - `test_external_predictor_requires_exact_event_and_team_identity` and `test_external_predictor_fails_closed_on_kickoff_mismatch` failed because `_game()` in that test hardcodes `game_date=datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)`.
   - At runtime, the server wall-clock time passed `00:15 UTC` (reaching `00:32 UTC`). Because `espn_service.py:197` checks `if kickoff <= datetime.now(timezone.utc): return {"status": "unavailable", "reason": "event_started_or_finished"}`, the hardcoded game is now treated as past kickoff.
   - `tests/test_espn_external_predictor.py` is outside our exclusive write ownership. It should be updated by the test owner to either use a future date (e.g. `2026-10-17 00:15 UTC`) or mock `datetime.now(timezone.utc)`.
2. **Production Gate Check-in**:
   - `"production_enabled": false` remains checked in at `backend/ml/models/nfl/model_metadata.json` per orchestrator instruction. Formal transition to `true` is planned for Milestone 4.

---

## 4. Conclusion

Milestone 3 (Requirement R3: Ensemble and Decision Engine) is fully implemented, mathematically verified, and operational:
1. 3 independent models (`LogisticRegression`, `HistGradientBoostingClassifier`, `RandomForestClassifier`) trained via walk-forward validation with lockbox holdout evaluation.
2. Complete mathematical independence from bookmaker lines preserved.
3. Multi-model inference engine computing individual probabilities, model agreement (1-3), and confluence tiers ("HIGH", "MODERATE", "NONE").
4. Confluence decision rule and +EV minimum odds filter ($\ge 1.40$, agreement $\ge 2/3$, $EV > 0$) strictly enforced.
5. Spread / handicap margin and cover probability modeling active and verified.
6. Correlated microparlays targeting return $\ge 2.0\text{x}$ and $EV > 0$ with conditional probability modeling implemented.
7. Celery prediction persistence bug remediated.
8. Quota ledger hash unmodified (`f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`).
9. Verification test suite `test_ensemble_and_ev_verification.py` passes 15/15 tests.

---

## 5. Verification Method

To independently reproduce and verify this work:

1. **Verify M3 Dedicated Test Suite**:
   ```bash
   docker exec sports_api pytest tests/test_ensemble_and_ev_verification.py -v
   ```
   *Expected output: 15 passed in ~1.2s.*

2. **Verify Parlay Service Tests**:
   ```bash
   docker exec sports_api pytest tests/test_parlay.py -v
   ```
   *Expected output: 17 passed in ~0.6s.*

3. **Verify NFL Availability Suite**:
   ```bash
   docker exec sports_api pytest tests/test_nfl_availability_phase02.py -v
   ```
   *Expected output: 67 passed in ~25s.*

4. **Verify Full Test Suite**:
   ```bash
   docker exec sports_api pytest -q
   ```
   *Expected output: 383 passed, 2 failed (only the expired kickoff timestamp in test_espn_external_predictor.py).*

5. **Verify Quota Ledger Integrity**:
   ```bash
   sha256sum data/odds_quota_ledger.json
   ```
   *Expected output: `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`.*
