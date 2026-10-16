## 2026-10-15T23:12:11Z
You are the Data & API Integrity Worker (teamwork_preview_worker) for Milestone 1 of the NFL Analytics MVP project.

Your working directory is: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/worker_m1_1
Project root: /home/samuelc/Documents/Progreso/griddio-sports-analytics
Authoritative Request: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/ORIGINAL_REQUEST.md
Master Project Specification: /home/samuelc/Documents/Progreso/griddio-sports-analytics/PROJECT.md
Survey Evidence Report: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/explorer_survey_1/survey_data_report.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY INSTRUCTIONS:
1. Read ORIGINAL_REQUEST.md, PROJECT.md, and the Survey Evidence Report before starting work.
2. File Write Ownership:
   You exclusively own:
   - backend/app/api/v1/games.py
   - backend/app/services/espn_service.py
   - backend/app/services/matchup_context_service.py
   - backend/app/api/v1/odds.py
   - backend/app/services/odds_service.py
   - docker-compose.yml
   Do NOT modify frontend files or ML model training files.
3. Scope & Implementation Tasks:
   - Task 1: Refactor GET /api/v1/games/{game_id}/external-prediction.
     Eliminate live, blocking HTTP calls to ESPN in the request thread. Read from pre-ingested database/cache snapshots or return status 'unavailable' if not cached.
   - Task 2: Refactor GET /api/v1/games/{game_id}/context.
     Eliminate synchronous urllib.request.urlopen in the request thread on cache miss. Read from stored snapshot or return available context without blocking the thread.
   - Task 3: Refactor GET /api/v1/odds/remaining.
     Eliminate external HTTP calls to The Odds API in the GET route. Read remaining quota directly from data/odds_quota_ledger.json (via OddsQuotaService or local ledger read).
   - Task 4: In docker-compose.yml, ensure the sports_beat service mounts ./data:/app/data (matching api and worker). Start/restart beat with `docker compose up -d beat` and verify it stays up.
   - Task 5: Check PostgreSQL database and purge any legacy synthetic rows (e.g., nfl_2026_%). Ensure 100% of data in the DB comes from real sources.
   - Task 6: Quota Ledger Protection: Verify that data/odds_quota_ledger.json is completely preserved (sha256sum f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4).
   - Task 7: Run backend test suite via `docker exec sports_api pytest -q` and verify all tests pass.
4. Output: Write handoff.md in your working directory with all validation commands and results, then send a message to the orchestrator.
