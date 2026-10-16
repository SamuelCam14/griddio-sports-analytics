"""
Sports Analytics Platform — FastAPI Application Entry Point
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

from app.core.config import settings
from app.core.database import init_db
from app.api.v1 import games, predictions, ev_calculator, parlay, odds, cards

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Starting Sports Analytics API", env=settings.APP_ENV)
    settings.validate_security()
    await init_db()
    logger.info("Database initialized")
    yield
    logger.info("Shutting down Sports Analytics API")


app = FastAPI(
    debug=settings.debug,
    title="Sports Analytics Platform",
    description=(
        "API para análisis deportivo con ventaja matemática (EV+), "
        "predicción de resultados NBA/NFL y valoración de sports cards."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── CORS ───────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ────────────────────────────────────────────────────────────────
app.include_router(games.router,        prefix="/api/v1/games",       tags=["Games"])
app.include_router(odds.router,         prefix="/api/v1/odds",        tags=["Odds"])
app.include_router(predictions.router,  prefix="/api/v1/predictions", tags=["Predictions"])
app.include_router(ev_calculator.router,prefix="/api/v1/ev",          tags=["EV Calculator"])
app.include_router(parlay.router,       prefix="/api/v1/parlay",      tags=["Parlay Builder"])
app.include_router(cards.router,        prefix="/api/v1/cards",       tags=["Sports Cards"])


@app.get("/", tags=["Health"])
async def root():
    return {
        "status": "ok",
        "service": "Sports Analytics Platform",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "env": settings.APP_ENV}
