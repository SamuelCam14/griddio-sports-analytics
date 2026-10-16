## 2026-10-16T00:00:23Z
You are the Ensemble and Decision Engine Worker (teamwork_preview_worker) for Milestone 3 of the NFL Analytics MVP project.

Your working directory is: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/worker_m3_1
Project root: /home/samuelc/Documents/Progreso/griddio-sports-analytics
Authoritative Request: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/ORIGINAL_REQUEST.md
Master Project Specification: /home/samuelc/Documents/Progreso/griddio-sports-analytics/PROJECT.md
Survey Evidence Report: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/explorer_survey_2/survey_ml_report.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY INSTRUCTIONS:
1. Read ORIGINAL_REQUEST.md, PROJECT.md, and the Survey Evidence Report in full.
2. File Write Ownership:
   You exclusively own:
   - backend/ml/training/train_temporal.py
   - backend/ml/inference.py
   - backend/ml/models/nfl/ (trained model artifacts & model_metadata.json)
   - backend/app/services/math/ev.py
   - backend/app/services/sports_analytics_service.py
   - backend/app/services/parlay_service.py
   - backend/app/workers/celery_app.py
   - backend/tests/test_ensemble_and_ev_verification.py
   Do NOT touch frontend files.
3. Scope & Implementation Tasks (Requirement R3):
   - Task 1: 2-3 Independent Models with Walk-Forward Validation:
     * In train_temporal.py, train 3 independent models with distinct architectures (e.g. LogisticRegression, HistGradientBoostingClassifier, and RandomForestClassifier or CalibratedClassifierCV) evaluated across 4 chronological folds + 20% holdout lockbox without temporal leakage. Save the artifacts in backend/ml/models/nfl/.
     * In backend/ml/inference.py, load and run all 3 models concurrently. Compute win probabilities 100% independently of Vegas odds. Generate individual probabilities, ensemble probability, model_agreement (integer 1-3), and confluence_tier ("HIGH", "MODERATE", "NONE").
   - Task 2: Confluence Decision Rule & +EV Minimum Odds:
     * In backend/app/services/math/ev.py and sports_analytics_service.py, require model_agreement >= 2/3 and EV > 0 and decimal odds >= 1.40 (American >= -250) to emit PREMIUM or VALUE picks. If odds < 1.40 or EV <= 0 or agreement < 2/3, emit SKIP or omit pick.
   - Task 3: Spread / Handicap Value Detection:
     * In sports_analytics_service.py, implement projected margin and cover probability modeling (e.g. using team offensive/defensive EPA and score differential distributions) to determine if home or away has positive EV against market spread lines. Populate spread_cover_prob, projected scores, and spread_pick when edge exists.
   - Task 4: Correlated Microparlays:
     * In parlay_service.py, build microparlays targeting combined return >= 2.0x (+100) and combined EV > 0. Support correlated advantages (e.g. within-game correlated legs evaluated with conditional margins rather than pure independent multiplication).
   - Task 5: Remediate Latent Celery Bug:
     * In celery_app.py, filter pred_dict before calling append_predictions so only valid Prediction table column names are passed (strip out model_variant, used_injury_adjustment).
   - Task 6: Production Gate Activation:
     * Update backend/ml/models/nfl/model_metadata.json with `"production_enabled": true`.
   - Task 7: Automated Verification Test Suite:
     * Author backend/tests/test_ensemble_and_ev_verification.py to verify:
       - Multi-model ensemble produces probabilities independent of Vegas odds.
       - High-confidence picks require confluence (>=2/3 agreement) and odds >= 1.40 and EV > 0.
       - Spread cover modeling calculates genuine margins and cover probabilities.
       - Microparlays achieve combined odds >= 2.0x and EV > 0.
       - Quota ledger is preserved.
     * Run `docker exec sports_api pytest tests/test_ensemble_and_ev_verification.py -v` and `docker exec sports_api pytest -q`.
4. Output: Write handoff.md in your working directory with all validation commands and results, then send a message to the orchestrator.


## 2026-10-16T00:11:58Z
[Message from Orchestrator 9cd6cbfe-1463-4387-a3bc-691ae49aa9f2]
**Context**: Milestone 3 Production Gate Activation vs Legacy Phase 04 Assertion Tests
**Content**: Excellent catch. Keep `"production_enabled": false` in the checked-in `backend/ml/models/nfl/model_metadata.json` for Milestone 3, so that the 3 legacy safety-lock tests continue to pass and maintain 100% regression suite integrity (370/370 passing). In `test_ensemble_and_ev_verification.py`, thoroughly test and verify the entire end-to-end pipeline with the production gate enabled (via fixture/monkeypatch), proving that multi-model confluence, +EV (odds >= 1.40), spread margins, and microparlays (>= 2.0x) all execute and validate flawlessly. In Milestone 4 (Frontend Integration & Real-time Delivery), we will formally transition the checked-in gate to `true` and update the test assertions to reflect production approval.
**Action**: Proceed with completing Milestone 3 using this approach, run all test suites, and deliver handoff.md.
