# 🏆 Sports Analytics Platform

> Sistema de análisis deportivo con ventaja matemática (EV+), predicción NBA/NFL y Sports Card Analytics.

## 🗂 Estructura del Monorepo

```
sports_analytics/
├── backend/          # FastAPI + Python — ML Engine + API REST
│   ├── app/
│   │   ├── api/v1/   # Endpoints: games, odds, predictions, ev, parlay, cards
│   │   ├── core/     # Config, Database (PostgreSQL async)
│   │   ├── models/   # SQLAlchemy: Game, Team, OddsSnapshot, Prediction, Bankroll
│   │   ├── services/ # NBA ingestion, Odds service, EV Engine (Kelly + Monte Carlo)
│   │   └── workers/  # Celery tasks (ingesta cada 15min, retrain semanal)
│   ├── ml/           # Feature engineering + Training scripts (Fase 2)
│   └── Dockerfile
│
├── frontend/         # React 18 + TypeScript + TailwindCSS
│   └── src/
│       ├── pages/    # DashboardPage (+ Analytics, Parlay, Cards en Fase 2)
│       ├── components/
│       │   ├── games/  # PickCard, OddsTable
│       │   ├── ui/     # EVCalculator, BankrollWidget
│       │   └── charts/ # (Fase 2: BankrollChart, CalibrationPlot)
│       ├── services/   # api.ts — tipado completo de todos los endpoints
│       └── store/      # Zustand — bankroll, picks, settings (persistido)
│
├── docker-compose.yml  # PostgreSQL/TimescaleDB + Redis + API + Celery
├── .env.example        # Variables de entorno (copiar como .env)
└── .gitignore
```

## 🚀 Quick Start

### 1. Configurar variables de entorno
```bash
cp .env.example .env
# Editar .env y añadir ODDS_API_KEY (gratuito en https://the-odds-api.com)
```

### 2. Levantar con Docker
```bash
docker compose up -d
```

### 3. Frontend (desarrollo)
```bash
cd frontend
npm install
npm run dev
# Abre http://localhost:5173
```

### 4. Backend solo (sin Docker)
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# API en http://localhost:8000/docs
```

## 📡 API Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/v1/games/today` | Partidos de hoy |
| GET | `/api/v1/games/date/{YYYY-MM-DD}` | Partidos por fecha |
| GET | `/api/v1/odds/nba` | Momios NBA + prob. implícita sin vig |
| GET | `/api/v1/predictions/today?sport=NBA` | Picks del día por tier |
| GET | `/api/v1/predictions/top?limit=5` | Mejores picks EV+ |
| **POST** | `/api/v1/ev/calculate` | **Calcular EV individual** |
| **POST** | `/api/v1/ev/parlay` | **Construir micro-parlay EV+** |
| **POST** | `/api/v1/ev/montecarlo` | **Simular 10k temporadas** |
| GET | `/api/v1/ev/quick/{prob}/{odds}` | EV rápido sin body |
| GET | `/api/v1/odds/remaining` | Requests restantes API gratuita |

## 🧮 Ejemplo: Calcular EV

```bash
# Tienes un pick con 72% de prob y momio -130
curl -X POST http://localhost:8000/api/v1/ev/calculate \
  -H "Content-Type: application/json" \
  -d '{"prob_model": 0.72, "odds_american": -130, "bankroll": 1000}'

# Respuesta:
# {
#   "ev_dolares": 27.38,
#   "tier": "PREMIUM",
#   "ventaja": "15.48%",
#   "kelly_monto": 50.00,
#   "recomendacion": "✅ APOSTAR"
# }
```

## 📐 Modelo Matemático

### Expected Value
```
EV = (p_modelo × pago_neto × stake) - ((1 - p_modelo) × stake)
Umbral mínimo: EV > 0 AND ventaja > 3% sobre prob. implícita
```

### Micro-Parlay (2-3 legs)
```
P_parlay = p₁ × p₂ [× p₃]
Solo si: cada leg tiene EV+ individual y prob_leg ≥ 65%
```

### Kelly Fraccional (25%)
```
f* = (b×p - q) / b  →  f_aplicado = f* × 0.25
Cap máximo: 5% del bankroll por apuesta
```

## 🗺 Roadmap

- [x] **Fase 1** — Infraestructura + Data Pipeline + EV Engine ← *Estás aquí*
- [ ] **Fase 2** — Motor ML (CatBoost + XGBoost ensemble, confluencia 3 modelos)
- [ ] **Fase 3** — Parlay Builder integrado + Sports Card Analytics + Prophet NIL
- [ ] **Fase 4** — Frontend completo (gráficas Recharts, Analytics page)
- [ ] **Fase 5** — Deploy (Railway + Vercel) + Sistema de alertas

## 📋 Stack

- **Backend:** FastAPI 0.115 · Python 3.12 · SQLAlchemy 2.0 async · PostgreSQL 16 + TimescaleDB
- **Frontend:** React 18 · TypeScript · Vite · TailwindCSS · Zustand · React Query v5
- **ML:** CatBoost · XGBoost · scikit-learn · Prophet
- **Infra:** Docker · Redis · Celery · Railway/Render (deploy)
- **Fuentes:** `nba_api` (NBA Stats; endpoints sujetos a cambios y bloqueos) · `nflreadpy` (datos públicos de nflverse; integración backend pendiente) · The Odds API (requiere clave; cuota gratuita sujeta a límites del proveedor)

## Estado del pipeline de datos

La documentación de arquitectura describe el diseño previsto. La integración de fuentes, la calidad temporal de features y las métricas de modelos siguen pendientes de validación reproducible. Consulta [Plan de refactorización de datos y ML](docs/DATA_ML_REFACTOR_PLAN.md). No se deben interpretar ejemplos de picks o métricas guardadas como resultados vigentes.
