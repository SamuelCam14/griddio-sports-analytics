# Comprehensive ML Models, Feature Engineering, Injury Dynamics, and Decision Engine Survey Report

**Project**: NFL Analytics MVP Platform (Griddio Sports Analytics)  
**Surveyor**: ML Models Explorer (`teamwork_preview_explorer`)  
**Date**: 2026-10-15  
**Working Directory**: `/home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/explorer_survey_2`  
**Reference Document**: `.agents/teamwork/ORIGINAL_REQUEST.md` (Requirements R2 & R3)  

---

## Executive Summary

This report delivers an exhaustive, evidence-based code and architecture audit of the Machine Learning models, feature engineering pipelines, injury impact evaluation, and decision engine within `backend/ml/`, `backend/app/services/`, and related test suites.

### Key Highlights
1. **Model Inventory & Ensemble (R3)**: Currently, only **two** models are implemented in the active pipeline (`LogisticRegression` and `HistGradientBoostingClassifier` in `backend/ml/training/train_temporal.py`). A naive 50/50 blend was evaluated during training, but **discarded** in `backend/ml/models/nfl/model_metadata.json` in favor of a single retained model (`logistic`). At inference time (`backend/ml/inference.py`), only **one single model** runs. An orphaned file `backend/app/services/ml_engine.py` references CatBoost, XGBoost, and Logistic Regression with NBA features, but its weights do not exist and it is completely unreferenced. No Elo or Poisson models exist.
2. **Win Probability Independence from Vegas (R3)**: Win probabilities are **100% independent of Vegas/casino odds**. Feature engineering in `backend/ml/data_pipeline/temporal_features.py` relies exclusively on rolling game stats (margins, points, rest days), advanced football efficiency (EPA per play, success rate), and roster availability percentages. Zero market odds enter the model feature matrix.
3. **Dynamic Injury Evaluation & Fantasy Points (R2)**: The injury pipeline in `backend/ml/data_pipeline/injury_feature_builder.py` and `backend/app/services/nfl_availability_service.py` is **fully dynamic and based on rolling prior-week PPR fantasy points** (`fantasy_points_ppr`). Temporal leakage is strictly prevented via `time_idx_search = time_idx - 1` and `pd.merge_asof(direction="backward")`. **No fixed positional weight multipliers exist**; player loss is proportional to rolling production. Questionable and Doubtful statuses use empirical base probabilities modulated by verified player props signals.
4. **Pre-Kickoff Inactives Tracking & Recalculation (R2)**: Celery beat and worker orchestration in `backend/app/workers/celery_app.py` coordinates dynamic `near_kickoff` captures scheduled at $t - 90\text{ min}$ before kickoff. When inactives are captured, `evaluate_predictions_task` is triggered automatically. However, predictions fail closed and skip DB persistence because the production gate is locked (`production_enabled: false`).
5. **Expected Value (+EV) Engine (R3)**: The mathematical formula $EV = (p \times \text{net\_payout} \times 100) - ((1-p) \times 100)$ is implemented in `backend/app/services/math/ev.py` and `sports_analytics_service.py`. However, **the minimum odds threshold ($\text{odds} \ge 1.40$) is not enforced**, and **multi-model confluence is not checked** before emitting picks.
6. **Spread / Handicap Value Detection (R3)**: **Completely absent / marked UNAVAILABLE**. In `backend/app/services/sports_analytics_service.py`, spread cover probabilities, projected margins, and spread picks are set to `None` and tier `UNAVAILABLE`. No alternate line queries or algorithms exist anywhere in the codebase.
7. **Correlated Microparlays (R3)**: In `backend/app/services/parlay_service.py`, **correlated legs from the same game are strictly rejected and forbidden** because no joint probability model exists. Furthermore, the viability threshold does **not enforce the target combined return $\ge 2.0\text{x}$ (+100)**.
8. **Walk-Forward Validation**: Chronological walk-forward validation with 4 folds and a 20% holdout lockbox is robustly implemented with zero temporal leakage assertions in `train_temporal.py`.

---

## 1. Inventory of ML Models & Ensemble Architecture

### 1.1 Existing Models in Codebase

| Model Name | Implementation File & Line | Architecture / Details | Status |
| :--- | :--- | :--- | :--- |
| **Logistic Regression** | `backend/ml/training/train_temporal.py:69` | `make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight=None))` | **Active in inference** (`backend/ml/inference.py:28-91`). Saved at `backend/ml/models/nfl/logistic.joblib`. |
| **HistGradientBoosting** | `backend/ml/training/train_temporal.py:70-73` | `HistGradientBoostingClassifier(max_iter=200, learning_rate=0.06, l2_regularization=1.0, random_state=42)` | **Trained offline**, saved at `backend/ml/models/nfl/hist_gradient_boosting.joblib`. Discarded during model selection. |
| **Ensemble Blend (Offline)** | `backend/ml/training/train_temporal.py:215-271` | Naive 50/50 probability blend: $P_{\text{ens}} = 0.5 \times P_{\text{log}} + 0.5 \times P_{\text{hgb}}$ | **Evaluated offline only**, not saved as an artifact or executable pipeline. |
| **MLEngine (Legacy / Dead)** | `backend/app/services/ml_engine.py:24-160` | CatBoost (45%) + XGBoost (35%) + Logistic (20%) with threshold voting for confluence (3/3 or 2/3) | **Orphaned / Non-functional**. Expects `.joblib` files that do not exist; uses NBA features (`H_OFF_RTG`, `H_PACE`); never imported in `backend/app`. |
| **Poisson Model** | N/A | No code exists | **Absent**. |
| **Elo Rating Model** | N/A | Mentioned only conceptually in `docs/nfl_model_execution/04-model-validation.md:16` | **Absent** in code. |

### 1.2 Inspection of `backend/ml/models/nfl/model_metadata.json`
Lines 254-267 of `backend/ml/models/nfl/model_metadata.json`:
```json
"model_selection": {
  "decision": "single_model_retained",
  "ensemble_evaluated": true,
  "rationale": "LogisticRegression out-performed HistGradientBoosting and Ensemble blend (Holdout Brier: 0.2480 vs Ensemble 0.2536). Per Phase 04 guidelines, parsimonious model is retained.",
  "selected_model": "logistic"
},
"model_variant": "injury_aware",
"model_version": "nfl_temporal_v1",
"production_enabled": false,
"sample_count": 672,
"validation_status": "temporal_evaluation_complete_manual_review_required"
```

### 1.3 Inspection of `backend/ml/inference.py`
In `backend/ml/inference.py` (lines 94-126 and 295-299):
```python
model, metadata, manifest = load_temporal_model(
    sport, model_name, allow_experimental=allow_experimental,
    model_variant=model_variant,
)
...
probabilities = model.predict_proba(X)[:, 1]
features["home_win_probability"] = probabilities
features["away_win_probability"] = 1.0 - probabilities
```
*Finding*: `inference.py` only loads **one** model (`model_name="logistic"` by default). It does **not** run multi-model inference or ensemble voting.

### 1.4 Gaps Against R3
- **Requirement R3**: *"Diseñar, entrenar y evaluar de 2 a 3 modelos matemáticos o de machine learning independientes con arquitecturas y enfoques metodológicos diferenciados... Emitir pronósticos y recomendaciones de alta confianza exclusivamente cuando exista confluencia (acuerdo entre los modelos independientes)."*
- **Current Reality**: Only 1 model runs in production inference. The offline ensemble was discarded in Phase 04. There is no active 3rd model (e.g. Elo, Poisson, or gradient boosting ensemble with true confluence voting).

---

## 2. Independence of Win Probabilities from Vegas/Casino Odds

### 2.1 Feature Pipeline Inspection (`backend/ml/data_pipeline/temporal_features.py`)
Lines 181-199 define the canonical feature columns:
```python
def feature_columns(
    game_stat_names: Iterable[str], *, include_availability: bool = True,
) -> list[str]:
    columns = []
    for side in ("home", "away"):
        columns.extend([
            f"{side}_win_rate", f"{side}_avg_margin",
            f"{side}_avg_points_for", f"{side}_avg_points_against",
            f"{side}_days_rest", f"{side}_is_back_to_back", f"{side}_games_prior",
        ])
        if include_availability:
            columns.extend([
                f"{side}_ppr_lost_pct", f"{side}_off_snaps_lost_pct",
                f"{side}_def_snaps_lost_pct",
            ])
        columns.extend(f"{side}_avg_{metric}" for metric in game_stat_names)
    columns.extend(["diff_win_rate", "diff_avg_margin", "diff_avg_points_for", "diff_days_rest"])
    return columns
```
Where `NFL_GAME_STATS` (lines 16-19) are:
- `off_epa_per_play`, `off_success_rate`, `def_epa_allowed_per_play`, `def_success_allowed`.

Total Features: **32 features** for `injury_aware` (26 for `team_only`).

### 2.2 Verification of Vegas Independence
- **Input Data**: Built exclusively from past game results in `nflverse` parquets (scores, EPA, snap counts) and official ESPN injury reports.
- **Odds Excluded**: Moneylines, spreads, totals, and implied probabilities are **completely excluded** from `X`.
- **Downstream Coupling**: Market odds from The Odds API are strictly ingested downstream in `services/math/ev.py` and `services/sports_analytics_service.py` to compare fair model probabilities against bookmaker prices.
- **Verdict**: **COMPLIANT with R3**. Model win probabilities are generated 100% independently of casino odds.

---

## 3. Dynamic Injury Impact Evaluation (R2)

### 3.1 ESPN Injury Report Ingestion
- Handled in `backend/app/services/nfl_availability_service.py` (lines 200-265) via `save_espn_injury_snapshot` and `ESPNInjurySnapshot`.
- Strict point-in-time provenance fields:
  - `snapshot_id`: e.g. `espn_near_kickoff_2026-10-15T...`
  - `captured_at_utc`: UTC timestamp when system received the payload.
  - `source_observed_at_utc`: Preserved without tampering.
  - `known_by_system_at_utc`: Time when system could act on it.
  - `payload_sha256`: SHA-256 hash of raw payload bytes.
- Coverage states (`CoverageState`, lines 44-50):
  - `COVERED_HEALTHY`: Verified clean report (0 injuries).
  - `COVERED_WITH_INJURIES`: Roster has verified injured players with observed prior stats.
  - `NO_INJURIES_LISTED`: Omitted team in capture; explicitly marked without guessing.
  - `UNKNOWN`: Unresolved IDs, timestamps post-cutoff, or missing history.
  - `UNAVAILABLE_NO_REPORT`: Feed down or unreachable. **Zero-imputation is strictly forbidden.**

### 3.2 Fantasy Points (PPR) Utilization
In `backend/ml/data_pipeline/injury_feature_builder.py` (lines 111-131):
- Uses `fantasy_points_ppr` from historical `player_stats` snapshots.
- Computes rolling mean over past games:
  ```python
  game_logs["rolling_ppr"] = game_logs.groupby("player_id")["fantasy_points_ppr"].transform(
      lambda x: x.rolling(window=rolling_window, min_periods=1).mean()
  )
  game_logs["rolling_off_pct"] = game_logs.groupby("player_id")["offense_pct"].transform(
      lambda x: x.rolling(window=rolling_window, min_periods=1).mean()
  )
  game_logs["rolling_def_pct"] = game_logs.groupby("player_id")["defense_pct"].transform(
      lambda x: x.rolling(window=rolling_window, min_periods=1).mean()
  )
  ```
- In `nfl_availability_service.py` (lines 842-880):
  ```python
  total_ppr_lost += p_ppr * p_abs
  total_off_snaps_lost += p_off * p_abs
  total_def_snaps_lost += p_def * p_abs

  ppr_lost_pct = min(1.0, round(total_ppr_lost / team_potential_ppr, 4))
  off_snaps_lost_pct = min(1.0, round(total_off_snaps_lost / 11.0, 4))
  def_snaps_lost_pct = min(1.0, round(total_def_snaps_lost / 11.0, 4))
  ```
  Where `11.0` represents the 11 on-field players per snap.

### 3.3 Prevention of Temporal Data Leakage
- In `injury_feature_builder.py` (lines 132-146):
  ```python
  game_logs["time_idx"] = game_logs["season"] * 100 + game_logs["week"]
  rosters["time_idx"] = rosters["season"] * 100 + rosters["week"]
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
  *Key observation*: `time_idx_search = time_idx - 1` with `direction="backward"` guarantees that same-week statistics are **never** joined. Only weeks strictly prior ($t-1$) are used.
- In `nfl_availability_service.py` (lines 1037-1038, 1159-1160):
  Checks `if g_dt is None or g_dt >= as_of_dt: continue`.

### 3.4 Elimination of Fixed Positional Weight Factors
- Fixed positional weight factors (such as $QB = 4.0, WR = 1.5, RB = 1.2$) **do not exist** in the feature or availability pipelines.
- In `injury_feature_builder.py` (lines 45-61), `_position_metrics(position)` only returns boolean masks (`ppr_applicable`, `off_applicable`, `def_applicable`) indicating whether offensive/defensive/fantasy stats apply to a player's roster role.
- In `nfl_availability_service.py` (line 1214), `pos_rank` context is marked `"role_context": "descriptive_only_no_fixed_multiplier"`.
- A player's impact is determined dynamically by their individual recent contribution:
  $$\text{Impact} = \text{rolling\_ppr} \times P(\text{absence})$$
  Normalized by the team's total potential prior PPR denominator ($\sum \text{rolling\_ppr}$).

### 3.5 Handling of Questionable and Out Players
In `nfl_availability_service.py` (lines 66-100, 350-396):
1. **Confirmed OUT / IR / PUP**:
   - $P(\text{absence}) = 1.0$ (immutable).
   - Rules strictly forbid querying player props for OUT players (`checked_props=False`, line 377).
2. **Questionable / Doubtful**:
   - **Empirical Base Probability**:
     - Questionable: $P(\text{absence}) = 0.25$
     - Doubtful: $P(\text{absence}) = 0.85$
   - **Market Modulation via Verified Player Props**:
     - Questionable + active open line in market: $P(\text{absence}) \to 0.15$
     - Questionable + unlisted when teammates have lines: $P(\text{absence}) \to 0.75$
     - Doubtful + active open line in market: $P(\text{absence}) \to 0.60$
     - Doubtful + unlisted with demonstrated teammate coverage: $P(\text{absence}) \to 0.98$

---

## 4. Pre-Kickoff Inactives Tracking & Recalculation

### 4.1 Orchestration Workflow (`backend/app/workers/celery_app.py`)
1. **Gameday Scheduling (`schedule_nfl_gameday_captures_task`, lines 520-605)**:
   - Dispatches immediate `start_of_day` injury capture.
   - Loads upcoming games for today (`load_nfl_upcoming_games(as_of=now)`).
   - Groups games by kickoff time (`game_start_utc`).
   - For each distinct kickoff window, schedules an asynchronous pre-kickoff task:
     $$\text{pregame\_capture\_dt} = \text{kickoff\_dt} - 90\text{ minutes}$$
     Using `capture_nfl_injuries_task.apply_async(args=("near_kickoff",), kwargs={"affected_game_ids": window_games}, eta=pregame_capture_py)`.
   - 90 minutes before kickoff is the exact official NFL deadline for teams to release inactives.
2. **Pregame Capture Execution (`capture_nfl_injuries_task`, lines 480-505)**:
   - Fetches live ESPN injury data and persists snapshot with `cadence="near_kickoff"`.
   - Lines 496-502:
     ```python
     if cadence in ("near_kickoff", "pre_kickoff"):
         if affected_game_ids:
             evaluate_predictions_task.apply_async(args=("NFL",), kwargs={"game_ids": affected_game_ids}, queue="ml")
         else:
             evaluate_predictions_task.apply_async(args=("NFL",), queue="ml")
     ```
3. **Inference Acceptance Window (`backend/ml/inference.py`, lines 210-225)**:
   - In `inference.py`, `near_kickoff` snapshots are accepted when cutoff is within $[ \text{kickoff} - 90\text{m}, \text{kickoff} )$.
   - `captured_at` must be strictly prior to kickoff.

### 4.2 Current Obstacle / Gap
In `backend/app/workers/celery_app.py` (lines 211-221):
```python
if not meta.get("production_eligible"):
    logger.warning(f"Model for {sp} is not production eligible. Skipping DB insert.")
    results.append({
        "sport": sp,
        "predicted": len(preds_df) if preds_df is not None else 0,
        "production_eligible": False,
        "skipped_db_insert": True,
    })
    continue
```
Because `backend/ml/models/nfl/model_metadata.json` has `"production_enabled": false`, `meta.get("production_eligible")` returns `False`. The celery task **skips inserting predictions into PostgreSQL**, and the API gate remains closed (`predictions_enabled: false`).

---

## 5. Expected Value (+EV) Calculations & Confluence Requirements

### 5.1 Mathematical Formulation
Implemented in `backend/app/services/math/ev.py` (lines 72-115) and `backend/app/services/sports_analytics_service.py` (lines 69-123):
$$\text{odds\_decimal} = \text{american\_to\_decimal}(\text{odds\_american})$$
$$\text{implied\_prob} = \text{american\_to\_implied\_prob}(\text{odds\_american})$$
$$\text{net\_payout} = \text{odds\_decimal} - 1.0$$
$$EV_{\text{dollars}} = (P_{\text{model}} \times \text{net\_payout} \times 100) - ((1.0 - P_{\text{model}}) \times 100)$$
$$\text{Edge}_{\text{pct}} = P_{\text{model}} - \text{fair\_prob\_no\_vig}$$

### 5.2 Fractional Kelly Staking
$$\text{Kelly}_{\text{full}} = \frac{P_{\text{model}} \times (\text{odds\_decimal} - 1.0) - (1.0 - P_{\text{model}})}{\text{odds\_decimal} - 1.0}$$
$$\text{Kelly}_{\text{applied}} = \max(0.0, \min(\text{Kelly}_{\text{full}} \times 0.25, 0.05))$$

### 5.3 Gaps Against R3
1. **Minimum Odds Threshold ($\text{odds} \ge 1.40$)**:
   - **Requirement R3**: *"Emitir pronósticos y recomendaciones de alta confianza exclusivamente cuando exista confluencia (acuerdo entre los modelos independientes) y exista EV positivo (+EV) con cuotas mínimas de 1.40."*
   - **Current Implementation**: In `ev.py:86`, `calculate_ev` only checks:
     `has_edge = edge_pct >= min_edge_threshold and ev_dollars > 0` (where `min_edge_threshold = 0.03`).
     There is **no check** verifying `odds_decimal >= 1.40` (American odds $\ge -250$).
2. **Model Confluence Requirement**:
   - **Requirement R3**: Requires agreement among 2 to 3 independent models.
   - **Current Implementation**: In `backend/app/api/v1/predictions.py` and `sports_analytics_service.py`, picks are emitted based on single-model probability vs market odds.
   - In `backend/app/models/game.py:182`, the column `model_agreement` exists, but in `celery_app.py:386-407`, `model_agreement` and `confluence_tier` are **not populated** into `pred_dict`, defaulting to `None`.

---

## 6. Spread / Handicap Value Detection (Main & Alternate Lines)

### 6.1 Current Spread Implementation
In `backend/app/services/sports_analytics_service.py` (lines 127-137, 269-283):
```python
spread_pick = None
spread_cover_prob = None
spread_tier = "UNAVAILABLE"
spread_edge = None
ev_spread = None
spread_kelly_frac = None
spread_kelly_stake = None
```
And explanation provided in factors (lines 223-228):
```python
if spread_line is not None:
    factors.append(
        f"Línea de Hándicap en {spread_line:+0.1f}: Sin modelo validado de spread; se omiten proyecciones de margen y picks."
    )
```

In `backend/tests/test_production_readiness_audit.py` (lines 80-100), automated tests explicitly enforce that spread cover probability, projected margins, and picks **must remain None**.

### 6.2 Alternate Spread Lines
- **The Odds API Ingestion**: `OddsService` (`backend/app/services/odds_service.py:298`) only queries standard markets: `markets: str = "h2h,spreads,totals"`. Alternate spread lines (e.g. `alternate_spreads`) are not requested from The Odds API.
- **Database Schema**: `OddsSnapshot` (`backend/app/models/game.py:138-142`) only has columns for the main spread: `home_spread`, `home_spread_ml`, `away_spread`, `away_spread_ml`. There are no columns or tables for alternate handicap lines.
- **Mathematical Modeling**: No normal/logistic distribution model or margin simulation is implemented to calculate cover probabilities across variable spread lines.

### 6.3 Gaps Against R3
- **Requirement R3**: *"Analizar líneas de handicap / spread (principales y alternas) para detectar dónde reside la mayor probabilidad con ventaja matemática para superar la línea del casino."*
- **Status**: **100% GAP**. Spread value detection is disabled, and alternate lines are not supported in schema, ingestion, or models.

---

## 7. Correlated Microparlays (Return $\ge 2.0\text{x}$, +EV)

### 7.1 Existing Parlay Implementation (`backend/app/services/parlay_service.py`)
- Independent multi-game parlay builder:
  - Scans database for candidate games where $P(\text{win}) \ge 0.62$ and $\text{edge} \ge 0.01$ (lines 590-618).
  - Evaluates 2-leg and 3-leg combinations (`itertools.combinations`).
  - Viability rule (line 336):
    `is_viable = (ev_dollars > 2.0) and (prob_combined >= 0.42) and not legs_have_heavy_negative`
  - Formula:
    $P_{\text{combined}} = \prod P_i$, $\text{odds\_dec} = \prod \text{odds\_dec}_i$.

### 7.2 Correlation Rejection vs Requirement R3
- In `parlay_service.py` (lines 173-230 and 632-646):
  ```python
  if combo[0].get("game_id") and combo[0].get("game_id") == combo[1].get("game_id"):
      continue
  ```
  And custom parlay evaluator:
  ```python
  if has_correlated_legs:
      return {
          "viable": False,
          "has_correlated_legs": True,
          "razon": "No se permite combinar selecciones del mismo evento como patas independientes. Al no disponer de un modelo de probabilidad conjunta para eventos correlacionados, nos abstenemos de ofrecer este parlay."
      }
  ```
- **Requirement R3**: *"Construir microparlays con ventaja matemática correlacionada con objetivo de retorno combinado >= 2.0x (+100) y EV positivo."*
- **Gap Analysis**:
  1. The existing code **forbids correlated legs** (e.g. Moneyline + Spread or Moneyline + Player Prop from the same game) because it assumes independence $P(A \cap B) = P(A) \times P(B)$. Generating correlated microparlays requires a joint bivariate or copula/conditional model.
  2. The viability check in line 336 checks $EV > \$2.0$ and $P \ge 0.42$, but **does not enforce that combined odds $\ge 2.0\text{x}$ (+100)**. A 2-leg favorite parlay with decimal odds of 1.70 could be marked viable.

---

## 8. Walk-Forward Validation & Training Pipelines

### 8.1 Implementation (`backend/ml/training/train_temporal.py`)
- Chronological splitting via `chronological_folds(df, folds=4, holdout_frac=0.20)` (lines 77-111):
  - 80% expanding/sliding chronological cross-validation.
  - 20% holdout lockbox (most recent seasons, 2022-2025).
  - Strict assertions enforce:
    1. Zero index overlap between train and test.
    2. Zero index overlap between test and holdout.
    3. Train dates strictly earlier than test dates.
    4. Test dates strictly earlier than holdout dates.
- Out-of-Fold (OOF) and Holdout Metrics:
  - Accuracy, ROC-AUC, Brier score, Log Loss.
  - Majority class baseline comparison (`majority_baseline_brier`, `majority_baseline_log_loss`).
  - Expected Calibration Error (ECE) computed over 10 calibration bins.
- Holdout results in `backend/ml/models/nfl/model_metadata.json`:
  - `logistic`: Accuracy 60.0%, AUC 0.6346, Brier 0.2480 (beats majority 0.2478? Close; ECE: 0.1004).
  - `hist_gradient_boosting`: Accuracy 54.7%, AUC 0.6216, Brier 0.2807, ECE 0.2225.
  - `ensemble_blend`: Accuracy 56.8%, AUC 0.6315, Brier 0.2536, ECE 0.1459.

### 8.2 Gaps Against R3
- Only two models were trained in this pipeline.
- Model selection chose Logistic Regression alone and turned off the ensemble.
- Production gate was left closed: `"production_enabled": false`.

---

## 9. Comprehensive Traceability Matrix: Existing Features vs Gaps Against R2 & R3

| Requirement Item | Existing Implementation & File Reference | Status | Gap Against R2 & R3 |
| :--- | :--- | :--- | :--- |
| **2-3 Independent Models (R3)** | `train_temporal.py:67-74` (Logistic + HistGradientBoosting). `inference.py:28-91` (loads only Logistic). | ⚠️ Partial | Only 2 architectures evaluated; only 1 deployed at inference. Need 2-3 independent models running concurrently. |
| **Ensemble Confluence (R3)** | `ml_engine.py:134` had legacy voting (unused). `predictions.py:367` passes `model_agreement`, but celery omits it. | ❌ Gap | No live multi-model confluence check before pick emission. |
| **Independence from Vegas Odds (R3)** | `temporal_features.py:181-199`, `inference.py:288-299`. Features contain 0 odds metrics. | ✅ Compliant | Model probabilities are 100% independent of Vegas odds. |
| **+EV Calculation (R3)** | `math/ev.py:66-115`, `sports_analytics_service.py:69-123`, `nfl_market_pricing_service.py:197-233`. | ✅ Compliant | Rigorous formula $EV = P \times (\text{odds}-1) - (1-P)$, with de-vigging and fractional Kelly. |
| **Minimum Odds $\ge 1.40$ (R3)** | `math/ev.py:86` only checks `edge >= 0.03 and ev > 0`. | ❌ Gap | No filter enforcing $\text{odds\_decimal} \ge 1.40$ ($\text{American} \ge -250$). |
| **Spread Value Detection (R3)** | `sports_analytics_service.py:127-137` sets spread cover prob and pick to `None` / `UNAVAILABLE`. | ❌ Gap | No spread cover probability or margin model. |
| **Alternate Spread Lines (R3)** | Not queried in `odds_service.py`, no columns in `OddsSnapshot`, no analysis function. | ❌ Gap | Alternate spread lines are completely unsupported. |
| **Correlated Microparlays (R3)** | `parlay_service.py:173-230` explicitly rejects same-game correlated legs. | ❌ Gap | Only independent cross-game parlays supported; correlated same-game combinations are blocked. |
| **Target Return $\ge 2.0\text{x}$ (+100) (R3)** | `parlay_service.py:336` checks $EV > \$2.0$ but no check on combined odds $\ge 2.0$. | ❌ Gap | Must enforce combined decimal odds $\ge 2.0$ (+100). |
| **Dynamic Injury Impact (R2)** | `injury_feature_builder.py:113-130`, `nfl_availability_service.py:842-880`. | ✅ Compliant | Production lost calculated dynamically using rolling fantasy points and snap shares. |
| **Prior Fantasy PPR Only (R2)** | `injury_feature_builder.py:132-146` (`time_idx - 1`), `nfl_availability_service.py:1037` ($g < \text{as\_of}$). | ✅ Compliant | Strict anti-leakage join; same-week data is never accessed. |
| **No Fixed Positional Weights (R2)** | `injury_feature_builder.py:45-61`, `nfl_availability_service.py:1214`. | ✅ Compliant | No arbitrary positional multipliers (e.g. QB 4.0); production is player-specific. |
| **Pre-Kickoff Inactives Tracking (R2)** | `celery_app.py:520-605` schedules task 90m before kickoff; `496-502` triggers recalculation. | ⚠️ Partial | Celery pipeline exists, but DB insertion skipped because `production_enabled=false`. |
| **Walk-Forward Validation (R3)** | `train_temporal.py:77-111` 4 chronological folds + 20% holdout lockbox with leakage assertions. | ✅ Compliant | Rigorous temporal validation methodology. |

---

## 10. Recommendations for the Implementation Team

1. **Implement 2-3 Independent Models in Production Inference**:
   - Add a 3rd distinct architecture in `train_temporal.py` (e.g., Elo rating model or calibrated gradient boosting / CatBoost / Random Forest).
   - Update `backend/ml/inference.py` to run all 2-3 models concurrently on the feature vector.
   - Output individual model probabilities: $\{P_1, P_2, P_3\}$, the ensemble probability $P_{\text{ens}}$, and a confluence flag (e.g. $3/3$ or $2/3$ agreement).
2. **Enforce the +EV & Minimum Odds Filter**:
   - In `backend/app/services/math/ev.py` and `sports_analytics_service.py`, enforce:
     $$\text{is\_pick\_eligible} = (\text{odds\_decimal} \ge 1.40) \land (EV > 0) \land (\text{confluence\_agreement} \ge 2/3)$$
3. **Implement Spread / Handicap Modeling**:
   - Develop a margin distribution model (e.g. using projected score differential from offensive/defensive EPA and rolling margins) to estimate $P(\text{home\_margin} > \text{spread})$.
   - Calculate EV on the main spread line.
   - For alternate lines, extend `OddsService` and the model to evaluate where the expected value maximizes across spread shifts.
4. **Implement Correlated Microparlay Generation**:
   - Develop a joint probability evaluation for within-game correlated legs (e.g. Team ML + Spread or Team ML + Player Prop) using conditional probability estimates rather than independence assumption.
   - Add filter in `parlay_service.py` to ensure combined odds $\ge 2.0\text{x}$ (+100) and combined $EV > 0$.
5. **Unlock Production Gate & Wire Pre-Kickoff DB Persistence**:
   - After retraining the 2-3 models and validating holdout calibration, set `"production_enabled": true` in `backend/ml/models/nfl/model_metadata.json`.
   - Update `celery_app.py:386-407` to populate `confluence_tier` and `model_agreement` into `pred_dict`.
   - Verify that `/api/v1/predictions/today?sport=NFL` and `/api/v1/parlay/recommended?sport=NFL` serve live data to the frontend.
