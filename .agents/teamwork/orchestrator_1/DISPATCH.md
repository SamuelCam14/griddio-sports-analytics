# Dispatch Instructions

## 2026-10-15T22:57:30Z

You are the Project Orchestrator (teamwork_preview_orchestrator) for the project: Reevaluación y Entrega MVP NFL Analytics.

Working directory: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/orchestrator_1
Project root: /home/samuelc/Documents/Progreso/griddio-sports-analytics
Authoritative Request: /home/samuelc/Documents/Progreso/griddio-sports-analytics/.agents/teamwork/ORIGINAL_REQUEST.md

Please review ORIGINAL_REQUEST.md in full. Your objectives:
1. Initialize your BRIEFING.md, plan.md, and progress.md in your working directory.
2. Coordinate and dispatch specialist agents to fulfill all project requirements (R1 to R5) and acceptance criteria:
   - R1: Audit APIs and ensure 100% real data (zero-synthetic, zero-imputation; coherent statistical handling of missing metrics; no hardcoded/fabricated data).
   - R2: Dynamic injury impact model based on fantasy points from strictly prior weeks (no temporal data leaks; no fixed positional weights; continuous injury tracking & pre-kickoff recalculation).
   - R3: Multi-model ensemble (2-3 independent models), walk-forward validation, positive expected value (+EV, min odds 1.40) upon model confluence, spread/handicap value detection, correlated microparlays (target >= 2.0x, +EV).
   - R4: Existing Docker infrastructure (sports_db, sports_redis, sports_api, sports_worker, sports_beat). Strictly preserve data/odds_quota_ledger.json without consuming live Odds API quota during test runs. Ensure frontend GET queries do not trigger blocking/sync external calls.
   - R5: Connect backend predictions to the existing frontend dashboard with timestamps (observed_at, predicted_at, as_of), fully renderable with real backend data.
3. Verify with full test suite (pytest in sports_api container, npm run lint & npm run build in frontend).
4. Update progress.md continuously. When all acceptance criteria are met and verified, report completion to the Sentinel with a comprehensive summary so victory audit can proceed.
