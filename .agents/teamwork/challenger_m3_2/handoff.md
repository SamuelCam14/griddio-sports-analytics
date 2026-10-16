# Empirical Challenge Report & Handoff: Milestone 3

**Agent**: Challenger 2 (`challenger_m3_2`)  
**Role**: Empirical Challenger (critic, specialist)  
**Verdict**: **APPROVE**  
**Overall Risk Assessment**: LOW  
**Timestamp**: 2026-10-16T00:44:30Z  

---

## 1. Observation

### 1.1 Spread / Handicap Margin & Cover Probability Math
- **Implementation**: `backend/app/services/sports_analytics_service.py` (lines 66-80, 146-193, 343-357).
  - Score projection: Gaussian inverse CDF $\mu = \Phi^{-1}(\text{clip}(p, 0.001, 0.999)) \times 13.5$.
  - Projected score breakdown: $\text{proj\_home\_score} = \frac{t_{\text{base}} + \mu}{2.0}$, $\text{proj\_away\_score} = \frac{t_{\text{base}} - \mu}{2.0}$.
  - Projected margin: $\text{proj\_margin} = \text{proj\_home\_score} - \text{proj\_away\_score}$.
  - Cover probability: $z = \frac{\mu + S}{13.5}$, $P(\text{home cover}) = \Phi(z)$, $P(\text{away cover}) = 1.0 - P(\text{home cover})$.
  - Standard spread pricing: American -110 (1.9091 decimal); $EV = P \cdot (1.9091 - 1.0) \cdot 100 - (1 - P) \cdot 100$.
- **Empirical Execution Observations**:
  - Moderate Home Favorite ($p=0.70$, spread $-3.5$, total $44.0$):
    - `projected_margin`: $+7.0$ pts (Home $26.0$, Away $19.0$).
    - `cover_probability`: $0.6045$ ($60.5\%$).
    - `edge_pct`: $+10.5\%$, `ev_dollars`: $+\$15.41$, `tier`: `PREMIUM`.
    - Pick emitted: `"Denver Broncos -3.5"`.
  - Moderate Home Underdog ($p=0.35$, spread $+7.5$, total $42.0$):
    - `projected_margin`: $-5.2$ pts (Home $18.4$, Away $23.6$).
    - `cover_probability`: $0.5676$ ($56.8\%$).
    - Pick emitted: `"Denver Broncos +7.5"`.
  - Inversion Symmetry: Away favorite with $p=0.70$ vs Home spread $+3.5$ yielded identical cover probability ($0.6045$), identical pick (`"Denver Broncos -3.5"`), and mirrored margin ($-7.0$ pts vs $+7.0$ pts).
  - Extreme Blowout Spread (spread $-28.5$, $p=0.99$, total $50.0$):
    - `projected_margin`: $+31.4$ pts (Home $40.7$, Away $9.3$).
    - `cover_probability`: $0.5852$ ($58.5\%$).
    - No numerical overflow, no infinite/NaN values.
  - Super-Extreme Spread (spread $-50.0$, toss-up $p=0.50$):
    - Pick emitted: `"Kansas City Chiefs +50.0"`.
    - `cover_probability`: $0.9999$, `ev_dollars`: $+\$90.89$.
  - Missing or Invalid Probabilities ($p=\text{None}$, $p=0.0$, $p=1.0$, $p_{\text{home}} + p_{\text{away}} \ne 1.0$):
    - Preserves strict fail-closed behavior: `status: "available"` (or `"unavailable"` if no bookmaker), `tier: "UNAVAILABLE"`, `projected_margin: None`, `pick: None`, `cover_probability: None`.

### 1.2 Correlated Microparlays & Standard Rejection
- **Implementation**: `backend/app/services/parlay_service.py` (lines 145-215, 217-482).
  - Microparlay pricing & EV rule: `is_viable = (ev_dollars > 0.0) and (odds_decimal >= 2.0) and (prob_combined >= 0.40) and not legs_have_heavy_negative`.
  - Conditional probability modeling (`_compute_joint_probability`):
    - For same-team ML favorite + negative spread (e.g. KC ML & KC -3.5): covering $-3.5$ strictly guarantees winning $\implies P(\text{win} \cap \text{cover -3.5}) = P(\text{cover}) = 0.60$ (not naive $0.70 \times 0.60 = 0.42$).
    - For opposing teams in the same game (e.g. KC ML & DEN +3.5): returns $P = 0.0$.
- **Empirical Execution Observations**:
  - Valid Microparlay (KC ML at -150, KC -3.5 at -110):
    - Combined decimal odds: $3.182\text{x}$ (American $+218$).
    - Joint probability: $60.0\%$.
    - Net EV: $+\$90.91$ per $\$100$ stake.
    - Result: `viable: True`, `is_correlated_microparlay: True`.
  - Sub-2.0x Odds Rejection: Combined odds of $1.667\text{x}$ (KC ML -400 + KC +7.5 -300) with $p=0.85$ and EV $+\$41.67$:
    - Result: `viable: False`, `razon: "Cuota combinada (1.67x) inferior al objetivo mínimo (2.0x / +100)."`.
  - Negative EV Rejection: Combined odds $2.333\text{x}$ (DEN ML -150 + DEN +3.5 -250) with $p=0.40$ yielding EV $-\$6.67$:
    - Result: `viable: False`, `razon: "EV no positivo (-$6.67). La correlación no supera la ventaja de la casa."`.
  - Opposing Teams Rejection: KC ML (-150) + DEN +3.5 (-110):
    - Result: `viable: False`, `prob_combinada: 0.0`, `ev_dolares: -100.0`.
  - Standard Evaluator Rejection (`allow_correlation=False`):
    - Calling `evaluate_custom_parlay` on the identical same-game legs strictly rejects:
    - `viable: False`, `has_correlated_legs: True`, `razon: "No se permite combinar selecciones del mismo evento como patas independientes. Al no disponer de un modelo de probabilidad conjunta para eventos correlacionados, nos abstenemos de ofrecer este parlay."`.

### 1.3 Celery Column Persistence in PostgreSQL
- **Implementation**:
  - `backend/app/workers/celery_app.py` (lines 386-414):
    ```python
    valid_cols = {c.name for c in Prediction.__table__.columns}
    pred_dict = { ... }
    if "model_agreement" in row and pd.notna(row["model_agreement"]):
        pred_dict["model_agreement"] = int(row["model_agreement"])
    if "confluence_tier" in row and pd.notna(row["confluence_tier"]):
        pred_dict["confluence_tier"] = str(row["confluence_tier"])

    filtered_pred_dict = {k: v for k, v in pred_dict.items() if k in valid_cols}
    predictions_data.append(filtered_pred_dict)
    await append_predictions(session, predictions_data)
    ```
- **Empirical Execution Observations**:
  - Direct execution without filtering:
    - Dictionary with `model_variant: "injury_aware"` and `used_injury_adjustment: True` passed to `pg_insert(Prediction).values(...)` raised verbatim:
      `sqlalchemy.exc.CompileError: Unconsumed column names: model_variant, used_injury_adjustment`.
  - Filtered execution against live PostgreSQL container (`sports_db` via `AsyncSessionLocal()`):
    - Filter removed `model_variant` and `used_injury_adjustment`.
    - Executed `append_predictions(session, [sanitized_dict])` and committed cleanly.
    - Query from PostgreSQL confirmed row existence with `model_agreement: 3` and `confluence_tier: "HIGH"`.
    - Executed second `append_predictions` with identical deterministic keys: handled cleanly via `ON CONFLICT (game_id, model_version, artifact_sha256, inference_input_sha256) DO NOTHING`.
    - Clean teardown confirmed transaction rollback and deletion work as expected.

### 1.4 Test Suite & Ledger Verification
- `docker exec sports_api pytest tests/test_ensemble_and_ev_verification.py -v`:
  - `15 passed in 1.44s` ($100\%$).
- `docker exec sports_api pytest tests/test_challenger_m3_adversarial.py -v`:
  - `9 passed in 1.21s` ($100\%$).
- `docker exec sports_api pytest tests/test_parlay.py -v`:
  - `17 passed in 0.72s` ($100\%$).
- `docker exec sports_api pytest tests/test_e2e_requirements.py -k "r3" -v`:
  - `10 passed in 1.22s` ($100\%$).
- Full Test Suite (`docker exec sports_api pytest -q`):
  - `419 passed, 2 failed in 46.94s` ($99.5\%$).
  - The 2 failures belong exclusively to `tests/test_espn_external_predictor.py` where a hardcoded kickoff datetime (`2026-10-16 00:15 UTC`) expired relative to server wall-clock time (`00:43 UTC`).
- Quota Ledger Integrity:
  - `sha256sum data/odds_quota_ledger.json`:
    `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4` (unmodified).

---

## 2. Logic Chain

1. **Spread / Handicap Margin Formulation**:
   - In NFL games, the distribution of scoring differentials is approximately Gaussian with standard deviation $\sigma \approx 13.5$ pts.
   - Mapping model win probability to projected margin via inverse normal CDF ($\mu = \Phi^{-1}(p) \times 13.5$) is mathematically well-conditioned, avoiding arbitrary step functions.
   - Testing across positive, negative, and extreme spread lines demonstrated consistent score projections ($\text{Home} + \text{Away} = \text{Total}$ and $\text{Home} - \text{Away} = \text{Margin}$) and correct cover probabilities.
   - Guarding against $p \le 0$ or $p \ge 1$ via clipping ($0.001 \le p \le 0.999$) prevents infinite $z$-scores and runtime exceptions.
   - Fail-closed behavior on missing or uncalibrated probabilities prevents false positive picks.

2. **Microparlays and Independence Separation**:
   - Combining outcomes from the same event violates the product rule of independent events ($P(A \cap B) \ne P(A)P(B)$).
   - In standard parlay construction (`allow_correlation=False`), attempting to combine legs from the same event is strictly prohibited and returns a clear descriptive rejection.
   - In microparlays (`evaluate_correlated_microparlay`), applying conditional probability logic ($P(\text{win} \cap \text{cover -S}) = P(\text{cover})$) correctly accounts for intraday game dependence without over-inflating odds or underestimating win likelihood.
   - Enforcing both return $\ge 2.0\text{x}$ (+100) and $EV > 0$ guarantees that recommendations offer genuine mathematical advantage.

3. **Celery Database Persistence Integrity**:
   - SQLAlchemy PostgreSQL compiler strictly verifies dictionary keys against mapped table columns when building multi-value `INSERT` expressions.
   - Passing metadata fields not present in the `Prediction` table (`model_variant`, `used_injury_adjustment`) previously led to runtime `CompileError` exceptions.
   - Implementing explicit column filtering against `valid_cols = {c.name for c in Prediction.__table__.columns}` strips extraneous metadata while preserving new first-class columns (`model_agreement`, `confluence_tier`).
   - Direct empirical execution against the PostgreSQL container proved clean insertion, persistence, query retrieval, and idempotent conflict handling.

4. **Production Gate & Vegas Independence**:
   - Features in `backend/ml/data_pipeline/temporal_features.py` contain zero sportsbook or betting line metrics. Pure football performance drives model win probabilities.
   - The production gate remains safely closed (`production_enabled: false` in `model_metadata.json`), ensuring baseline regression tests pass while end-to-end activation capability is verified via mock tests.

---

## 3. Caveats

1. **Two External Failures in `tests/test_espn_external_predictor.py`**:
   - Pre-existing test file hardcodes kickoff at `2026-10-16 00:15 UTC`. Because server time passed `00:15 UTC`, ESPN service correctly fails closed on games that have already started. This test is outside M3 scope and requires test owner update.
2. **Production Gate Check-in State**:
   - `"production_enabled": false` is intentional per orchestrator instructions to maintain backward compatibility during Milestone 3 review; Milestone 4 will formally flip the gate.
3. **No other caveats.**

---

## 4. Conclusion

**Verdict: APPROVE**

Milestone 3 deliverables have been thoroughly challenged and empirically verified:
1. **Spread / Handicap Margin Model**: Projected margins, cover probabilities, and score consistency are mathematically verified across normal, extreme, and inverted spread lines.
2. **Correlated Microparlays**: Conditional probability math, minimum odds threshold ($\ge 2.0\text{x}$), positive EV requirement, and opposing team rejection operate correctly. Standard evaluator strictly rejects same-game legs.
3. **Celery Column Persistence**: Column filtering resolves the unconsumed column `CompileError` and successfully persists `model_agreement` and `confluence_tier` into PostgreSQL.
4. **Vegas Independence & Confluence**: Model probabilities are derived solely from football metrics, and decision rules enforce minimum decimal odds $\ge 1.40$ and model agreement $\ge 2$.
5. **Test Execution**: `test_ensemble_and_ev_verification.py` (15/15), `test_challenger_m3_adversarial.py` (9/9), `test_parlay.py` (17/17), and E2E R3 tests (10/10) pass 100%. Quota ledger hash remains pristine.

---

## 5. Verification Method

To independently verify these conclusions:

1. **Execute Challenger Adversarial Stress Suite**:
   ```bash
   docker exec sports_api pytest tests/test_challenger_m3_adversarial.py -v
   ```
   *Expected: 9 passed in ~1.2s.*

2. **Execute Milestone 3 Dedicated Suite**:
   ```bash
   docker exec sports_api pytest tests/test_ensemble_and_ev_verification.py -v
   ```
   *Expected: 15 passed in ~1.4s.*

3. **Execute Parlay Service Test Suite**:
   ```bash
   docker exec sports_api pytest tests/test_parlay.py -v
   ```
   *Expected: 17 passed in ~0.7s.*

4. **Verify Quota Ledger Immutability**:
   ```bash
   sha256sum data/odds_quota_ledger.json
   ```
   *Expected: `f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4`.*
