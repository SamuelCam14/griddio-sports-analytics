# Handoff Report: ML Models, Feature Engineering, Injury Dynamics, and Decision Engine Survey

**Agent**: `teamwork_preview_explorer` (ML Models Explorer)  
**Target Milestone**: Survey Phase for NFL Analytics MVP (R2 and R3)  
**Report Artifact**: `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/explorer_survey_2/survey_ml_report.md`  

---

## 1. Observation

1. **Model Architectures**:
   - `backend/ml/training/train_temporal.py:67-74` defines only two models: `LogisticRegression` and `HistGradientBoostingClassifier`.
   - `backend/ml/models/nfl/model_metadata.json:254-259`: `"model_selection": {"decision": "single_model_retained", "ensemble_evaluated": true, "rationale": "LogisticRegression out-performed HistGradientBoosting and Ensemble blend...", "selected_model": "logistic"}`.
   - `backend/ml/inference.py:28-91` loads only a single model via `load_temporal_model(sport, model_name="logistic")`.
   - `backend/app/services/ml_engine.py:24-160` references CatBoost, XGBoost, and Logistic Regression with NBA features, but its `.joblib` files do not exist and `MLEngine` is never imported.
   - No Elo rating or Poisson models exist in code anywhere in the repo.
2. **Win Probability Independence**:
   - `backend/ml/data_pipeline/temporal_features.py:181-199` lists all 32 features used by `injury_aware` NFL model (`win_rate`, `avg_margin`, `avg_points_for`, `days_rest`, `avg_off_epa_per_play`, `avg_off_success_rate`, `ppr_lost_pct`, etc.). Zero market odds, lines, or casino prices enter the feature matrix.
   - `backend/ml/inference.py:295-299`: probabilities are computed strictly via `model.predict_proba(X)[:, 1]`.
3. **Injury Impact & Fantasy Points**:
   - `backend/ml/data_pipeline/injury_feature_builder.py:113-130`: loads `fantasy_points_ppr` and computes rolling average `rolling_ppr`.
   - `backend/ml/data_pipeline/injury_feature_builder.py:132-146`: joins rosters with previous week logs via `time_idx_search = time_idx - 1` and `pd.merge_asof(direction="backward")`, strictly eliminating same-week temporal leakage.
   - `backend/app/services/nfl_availability_service.py:1214`: sets `"role_context": "descriptive_only_no_fixed_multiplier"`. No fixed positional weights (e.g. QB 4.0) exist; impact is proportional to actual player production.
   - `backend/app/services/nfl_availability_service.py:375-381`: Confirmed OUT players have $P(\text{absence}) = 1.0$ and are strictly barred from querying player props. Questionable players start at $P(\text{absence}) = 0.25$ and are modulated to $0.15$ or $0.75$ via verified open props.
4. **Pre-Kickoff Inactives & Recalculation**:
   - `backend/app/workers/celery_app.py:520-605`: `schedule_nfl_gameday_captures_task` schedules `near_kickoff` captures at $\text{kickoff} - 90\text{ min}$.
   - `backend/app/workers/celery_app.py:496-502`: When `near_kickoff` capture occurs, `evaluate_predictions_task` is triggered.
   - `backend/app/workers/celery_app.py:211-221`: skips database insertion because `meta.get("production_eligible")` is False due to `"production_enabled": false` in `model_metadata.json:262`.
5. **+EV Calculations & Confluence**:
   - `backend/app/services/math/ev.py:72-115`: computes $EV = (p \times \text{net\_payout} \times 100) - ((1-p) \times 100)$ and Kelly fraction.
   - `backend/app/services/math/ev.py:86`: checks `edge_pct >= min_edge_threshold and ev_dollars > 0`, but has **no check for $\text{odds} \ge 1.40$**.
   - `backend/app/api/v1/predictions.py:288-340`: emits picks based on single-model probability without checking multi-model agreement.
6. **Spread / Handicap**:
   - `backend/app/services/sports_analytics_service.py:127-137`: `spread_pick = None`, `spread_cover_prob = None`, `spread_tier = "UNAVAILABLE"`.
   - `backend/tests/test_production_readiness_audit.py:80-100`: tests assert spread picks and cover probabilities remain `None`. Alternate spread lines are completely absent in ingestion, models, and DB schema.
7. **Microparlays**:
   - `backend/app/services/parlay_service.py:173-230`: `has_correlated_legs = True` causes outright rejection if legs are from the same game.
   - `backend/app/services/parlay_service.py:336`: checks $EV > \$2.0$ and $P \ge 0.42$, but **does not enforce target combined return $\ge 2.0\text{x}$ (+100)**.
8. **Walk-Forward Validation**:
   - `backend/ml/training/train_temporal.py:77-111`: implements `chronological_folds` with 4 folds and a 20% holdout lockbox with zero-overlap and chronological assertions.

---

## 2. Logic Chain

1. From Observation 1, `train_temporal.py` only defines 2 models, and `inference.py` runs only 1 model (Logistic Regression). Therefore, the requirement in R3 to run an ensemble of 2-3 independent models with differentiated architectures is not met in production.
2. From Observation 2, all 32 features are historical game stats, EPA efficiency, and player availability. No odds or betting lines are present. Therefore, win probabilities are 100% independent of Vegas odds (fulfills R3).
3. From Observation 3, player production is tracked through rolling PPR fantasy points and snap shares, strictly joined with $t-1$ weeks, and no fixed positional multipliers exist. Therefore, the dynamic injury model fulfills R2's data leakage and non-fixed-weight requirements.
4. From Observation 4, Celery tasks automate pregame inactives capture at $t-90\text{m}$ and trigger recalculation, but predictions are blocked from DB persistence by the closed production gate (`production_enabled: false`).
5. From Observation 5, EV calculations exist, but omitting the $\text{odds} \ge 1.40$ filter and model confluence checks creates a gap against R3.
6. From Observation 6, spread/handicap value detection is hardcoded to `UNAVAILABLE` and alternate lines are absent, creating a complete gap against R3.
7. From Observation 7, `parlay_service.py` explicitly rejects same-game correlated legs and lacks a $\ge 2.0\text{x}$ combined odds filter, creating a gap against R3.

---

## 3. Caveats

- NBA pipelines were not deeply surveyed, as the project focus is strictly the NFL MVP.
- Physical model training was not re-executed during this turn (as this is a read-only survey), though training scripts and serialized metadata were thoroughly audited.
- `data/odds_quota_ledger.json` was strictly preserved and untouched.

---

## 4. Conclusion

The repository possesses a well-engineered foundation:
- Pure temporal feature engineering free from Vegas leakage.
- A dynamic, fantasy-points-based injury impact model without fixed positional weights or temporal leakage.
- Automated Celery scheduling for pre-kickoff inactives captures.
- Robust chronological walk-forward cross-validation.

However, four major functional gaps exist against R2 and R3:
1. **Multi-Model Inference & Confluence**: Inference currently runs only a single Logistic Regression model; needs 2-3 independent models running concurrently with confluence agreement.
2. **Spread & Alternate Line Value Detection**: Currently disabled (`UNAVAILABLE`); needs an algorithmic cover probability model and alternate line evaluation.
3. **Correlated Microparlays & $\ge 2.0\text{x}$ Target Return**: Same-game correlated legs are currently forbidden and the $\ge 2.0\text{x}$ combined odds constraint is not enforced.
4. **EV Filter Thresholds**: The $\text{odds} \ge 1.40$ filter and model confluence requirement must be wired into pick selection.
5. **Production Gate**: `model_metadata.json` has `production_enabled: false`, causing predictions and parlays endpoints to fail closed and Celery to skip DB writes.

---

## 5. Verification Method

To independently verify these findings, run:
1. Model inventory & metadata:
   - `view_file` on `backend/ml/training/train_temporal.py:67-74`
   - `view_file` on `backend/ml/models/nfl/model_metadata.json:254-267`
   - `view_file` on `backend/ml/inference.py:28-91`
2. Independence from Vegas:
   - `view_file` on `backend/ml/data_pipeline/temporal_features.py:181-199`
3. Injury dynamics & anti-leakage:
   - `view_file` on `backend/ml/data_pipeline/injury_feature_builder.py:113-146`
   - `view_file` on `backend/app/services/nfl_availability_service.py:1210-1225`
4. Spread UNAVAILABLE status:
   - `view_file` on `backend/app/services/sports_analytics_service.py:127-137`
5. Correlation rejection in parlays:
   - `view_file` on `backend/app/services/parlay_service.py:173-230`
6. Pytest suite:
   ```bash
   cd backend && pytest tests/test_nfl_models_phase04.py tests/test_nfl_availability_phase02.py tests/test_parlay.py tests/test_production_readiness_audit.py
   ```
