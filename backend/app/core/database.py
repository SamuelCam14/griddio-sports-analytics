"""
Conexión asíncrona a PostgreSQL usando SQLAlchemy 2.0 + asyncpg.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

import os
import sys
from sqlalchemy.pool import NullPool

# ─── Engine ─────────────────────────────────────────────────────────────────
if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=not settings.is_production,   # Muestra SQL en desarrollo
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,                # Verifica conexiones antes de usarlas
    )

# ─── Session factory ────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ─── Base para todos los modelos SQLAlchemy ──────────────────────────────────
class Base(DeclarativeBase):
    pass


# ─── Inicialización de tablas ────────────────────────────────────────────────
async def init_db() -> None:
    """Crea todas las tablas si no existen (usa Alembic para producción)."""
    async with engine.begin() as conn:
        from app.models import Team, Game, OddsSnapshot, Prediction, BankrollEntry, SportsCard, ProviderGameCrosswalk  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)


# ─── Dependency para FastAPI ─────────────────────────────────────────────────
async def get_db() -> AsyncSession:
    """Inyectado vía Depends(get_db) en los endpoints."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
