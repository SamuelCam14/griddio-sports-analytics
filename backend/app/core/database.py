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
async def init_db(target_engine=None, target_conn=None) -> None:
    """Crea todas las tablas si no existen y asegura columnas de procedencia por lado y totales."""
    from sqlalchemy import text
    from app.models import Team, Game, OddsSnapshot, Prediction, BankrollEntry, SportsCard, ProviderGameCrosswalk  # noqa: F401

    async def _run_migrations(conn):
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "postgresql":
            await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='home_bookmaker') THEN
                    ALTER TABLE odds_snapshots ADD COLUMN home_bookmaker VARCHAR(50);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='away_bookmaker') THEN
                    ALTER TABLE odds_snapshots ADD COLUMN away_bookmaker VARCHAR(50);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='over_bookmaker') THEN
                    ALTER TABLE odds_snapshots ADD COLUMN over_bookmaker VARCHAR(50);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='under_bookmaker') THEN
                    ALTER TABLE odds_snapshots ADD COLUMN under_bookmaker VARCHAR(50);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='benchmark_bookmaker') THEN
                    ALTER TABLE odds_snapshots ADD COLUMN benchmark_bookmaker VARCHAR(50);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='benchmark_observed_at') THEN
                    ALTER TABLE odds_snapshots ADD COLUMN benchmark_observed_at TIMESTAMP WITH TIME ZONE;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='odds_snapshots' AND column_name='h2h_benchmark_bookmaker') THEN
                    ALTER TABLE odds_snapshots ADD COLUMN h2h_benchmark_bookmaker VARCHAR(50);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='predictions' AND column_name='projected_total') THEN
                    ALTER TABLE predictions ADD COLUMN projected_total DOUBLE PRECISION;
                END IF;
            END $$;
        """))
        elif conn.dialect.name == "sqlite":
            res = await conn.execute(text("PRAGMA table_info(predictions)"))
            cols = [row[1] for row in res.fetchall()]
            if "projected_total" not in cols:
                await conn.execute(text("ALTER TABLE predictions ADD COLUMN projected_total REAL"))

    if target_conn is not None:
        await _run_migrations(target_conn)
    elif target_engine is not None:
        async with target_engine.begin() as conn:
            await _run_migrations(conn)
    else:
        async with engine.begin() as conn:
            await _run_migrations(conn)


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
