# Repository Guidelines

## Project Structure & Module Organization

This sports analytics monorepo has a FastAPI application in `backend/app/`, ML pipelines and inference in `backend/ml/`, operational scripts in `backend/scripts/`, and backend tests in `backend/tests/`. The React and TypeScript frontend is in `frontend/src/`, with static assets in `frontend/public/`. Shared datasets and generated reports are under `data/`; design and execution documentation is in `docs/`. Treat generated data and model artifacts as outputs; commit them only when intentionally updating a reviewed baseline.

## Build, Test, and Development Commands

- Backend setup: from `backend/`, create a virtualenv and run `pip install -r requirements.txt`.
- Run the API from `backend/`: `uvicorn app.main:app --reload` (docs at `/docs`).
- Run backend tests from `backend/`: `pytest` or `pytest tests/test_math.py`.
- Frontend: from `frontend/`, run `npm install`, then `npm run dev`. `npm run lint` runs ESLint; `npm run build` type-checks and builds with Vite.
- Start supporting services from the root with `docker compose up -d` when required.

## Coding Style & Naming Conventions

Follow surrounding code. Use four-space Python indentation, `snake_case` for modules/functions, and `PascalCase` for classes. Frontend components and types use `PascalCase`; variables and functions use `camelCase`. Keep TypeScript types aligned with backend API contracts. ESLint is configured for the frontend; no Python formatter or linter is configured repository-wide.

## Testing Guidelines

Backend tests use pytest and are named `test_*.py`; add focused coverage for changed behavior, especially data timing, API contracts, and model-input validation. The frontend has lint and build checks but no test script; run both for frontend changes. Saved reports and model metrics are not current performance claims unless reproduced using their documented validation process.

## Commit & Pull Request Guidelines

Recent history mixes Conventional Commit subjects (for example, `feat(api): ...`) with descriptive imperative summaries. Use a concise imperative subject and optional scope, such as `fix(frontend):`. Pull requests should explain the change, link related issues or docs, list validation commands and results, and include screenshots for UI changes. Call out dataset, model artifact, configuration, and API contract changes.

## Security & Configuration

Use `.env.example` as the template; keep credentials out of commits and logs. Configure provider keys and database settings through environment variables.
