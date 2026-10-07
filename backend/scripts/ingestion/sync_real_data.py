"""
Script de sincronización de datos 100% REALES desde The Odds API.
1. Limpia partidos y registros simulados o ficticios.
2. Ingesta los partidos reales actuales de NFL y NBA.
3. Almacena las líneas de momios oficiales (Moneyline, Spread, Totales).
4. Genera predicciones cuantitativas reales con EV y Kelly.
"""
import asyncio
import os
import structlog
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, delete, text

from app.models.game import Game, OddsSnapshot, Prediction
from app.services.live_ingestion_service import sync_real_games

logger = structlog.get_logger(__name__)

# Configuración de base de datos (puerto 5432 expuesto al host o en docker)
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_USER = os.getenv("POSTGRES_USER", "sports_user")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "sports_pass_dev")
DB_NAME = os.getenv("POSTGRES_DB", "sports_analytics")

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


async def run_sync():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        print("1. Limpiando partidos simulados o ficticios...")
        # Eliminar partidos que tengan prefijo simulado 'nfl_2026_'
        stmt_del_preds = text("""
            DELETE FROM predictions 
            WHERE game_id IN (SELECT id FROM games WHERE external_id LIKE 'nfl_2026_%');
        """)
        await db.execute(stmt_del_preds)

        stmt_del_odds = text("""
            DELETE FROM odds_snapshots 
            WHERE game_id IN (SELECT id FROM games WHERE external_id LIKE 'nfl_2026_%');
        """)
        await db.execute(stmt_del_odds)

        stmt_del_games = text("""
            DELETE FROM games WHERE external_id LIKE 'nfl_2026_%';
        """)
        await db.execute(stmt_del_games)
        await db.commit()
        print("   -> Datos simulados eliminados exitosamente.")

        print("2. Sincronizando cartelera REAL de la NFL desde The Odds API...")
        nfl_res = await sync_real_games(sport="NFL", db=db)
        print(f"   -> NFL sincronizada: {nfl_res.get('games_synced')} partidos reales, {nfl_res.get('predictions_synced')} predicciones.")

        print("3. Sincronizando cartelera REAL de la NBA desde The Odds API...")
        nba_res = await sync_real_games(sport="NBA", db=db)
        print(f"   -> NBA sincronizada: {nba_res.get('games_synced')} partidos reales, {nba_res.get('predictions_synced')} predicciones.")

    await engine.dispose()
    print("¡Sincronización de datos reales completada al 100%!")


if __name__ == "__main__":
    asyncio.run(run_sync())
