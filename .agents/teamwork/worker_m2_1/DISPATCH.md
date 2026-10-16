## 2026-10-15T23:36:32Z

You are the Dynamic Injury Model Worker (teamwork_preview_worker) for Milestone 2 of the NFL Analytics MVP project.

Your working directory is: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/worker_m2_1
Project root: /home/samuelc/Documents/Progreso/griddio-sports-analytics
Authoritative Request: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/ORIGINAL_REQUEST.md
Master Project Specification: /home/samuelc/Documents/Progreso/griddio-sports-analytics/PROJECT.md
Survey Evidence Report: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/explorer_survey_2/survey_ml_report.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY INSTRUCTIONS:
1. Read ORIGINAL_REQUEST.md, PROJECT.md, and the Survey Evidence Report before starting work.
2. File Write Ownership:
   You exclusively own:
   - backend/ml/data_pipeline/injury_feature_builder.py
   - backend/app/services/nfl_availability_service.py
   - backend/app/workers/celery_app.py
   - backend/tests/test_dynamic_injury_verification.py
   Do NOT modify frontend files or unrelated API routers.
3. Scope & Implementation Tasks (Requirement R2):
   - Task 1: Dynamic Injury Impact based on Prior Fantasy Points:
     Verify and ensure in nfl_availability_service.py and injury_feature_builder.py that injury impact is computed using rolling PPR fantasy points from strictly prior weeks (t-1). Zero temporal leakage: never access current week stats.
   - Task 2: Elimination of Positional Weight Multipliers:
     Ensure that no fixed positional multipliers (such as QB=4.0, WR=1.5, RB=1.2) exist. A player's impact must be dynamically proportional to their specific recent individual production relative to the team's total potential prior production.
   - Task 3: Pre-Kickoff Inactives Workflow & Recalculation:
     In celery_app.py, verify that schedule_nfl_gameday_captures_task schedules near_kickoff captures at t - 90 min before kickoff, and capture_nfl_injuries_task triggers evaluate_predictions_task for affected games. Ensure prediction recalculation runs smoothly and updates predictions with updated inactives.
   - Task 4: Automated Verification Test Suite:
     Create backend/tests/test_dynamic_injury_verification.py to prove:
     * A high-impact recent producer (e.g. 25.0 PPR) causes significantly greater dynamic degradation than a low-impact producer (e.g. 2.0 PPR).
     * Positional hierarchy is eliminated: a high-performing WR/RB penalizes the team more than a low-performing backup QB.
     * Strict temporal leakage assertion: stats from the evaluated game's week or future weeks are never accessible.
     * Pre-kickoff inactives trigger prediction recalculation with proper timestamp progression.
   - Task 5: Quota Ledger Protection:
     Verify that data/odds_quota_ledger.json is completely preserved (sha256sum f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4).
   - Task 6: Execution & Verification:
     Run `docker exec sports_api pytest tests/test_dynamic_injury_verification.py -v` and `docker exec sports_api pytest -q` to confirm 100% pass rate.
4. Output: Write handoff.md in your working directory with all validation commands and results, then send a message to the orchestrator.
