"""
Router: Games — /api/v1/games

Endpoints:
  GET /today          → Partidos de hoy con stats de equipos
  GET /date/{date}    → Partidos de una fecha específica
  GET /upcoming       → Próximos partidos (siguiente semana)
"""
from datetime import date, datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.game import Game, Team

router = APIRouter()


@router.get("/today", summary="Partidos de hoy")
async def get_today_games(db: AsyncSession = Depends(get_db)):
    """Obtiene todos los partidos programados para hoy."""
    today = date.today()
    return await _get_games_by_date(today, db)


@router.get("/date/{game_date}", summary="Partidos por fecha")
async def get_games_by_date(
    game_date: date,
    db: AsyncSession = Depends(get_db)
):
    """
    Obtiene partidos de una fecha específica.
    Formato: YYYY-MM-DD (ej: 2026-01-15)
    """
    return await _get_games_by_date(game_date, db)


@router.get("/{game_id}/context", summary="Contexto deportivo estilo SofaScore (H2H, L5, lesiones, métricas)")
async def get_game_context(
    game_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Obtiene el historial H2H, forma reciente, reporte de lesiones y duelo estadístico."""
    from app.services.matchup_context_service import get_matchup_context
    context = await get_matchup_context(game_id, db)
    if not context:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    return context


@router.get("/upcoming", summary="Próximos partidos (7 días)")
async def get_upcoming_games(
    days: int = Query(default=7, ge=1, le=30),
    sport: str = Query(default="NBA"),
    db: AsyncSession = Depends(get_db),
):
    """Obtiene partidos de los próximos N días."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    stmt = (
        select(Game)
        .where(Game.sport == sport.upper())
        .where(Game.game_date >= now)
        .where(Game.game_date <= end)
        .where(Game.status == "scheduled")
        .order_by(Game.game_date)
    )
    result = await db.execute(stmt)
    games = result.scalars().all()

    return {
        "sport": sport.upper(),
        "range_days": days,
        "total": len(games),
        "games": [_serialize_game(g) for g in games],
    }


async def _get_games_by_date(target_date: date, db: AsyncSession) -> dict:
    start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end   = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    stmt = (
        select(Game)
        .where(Game.game_date >= start)
        .where(Game.game_date <= end)
        .order_by(Game.game_date)
    )
    result = await db.execute(stmt)
    games = result.scalars().all()

    return {
        "date": target_date.isoformat(),
        "total": len(games),
        "games": [_serialize_game(g) for g in games],
    }


def _serialize_game(game: Game) -> dict:
    return {
        "id":            game.id,
        "external_id":   game.external_id,
        "sport":         game.sport,
        "season":        game.season,
        "game_date":     game.game_date.isoformat() if game.game_date else None,
        "status":        game.status,
        "home_team_id":  game.home_team_id,
        "away_team_id":  game.away_team_id,
        "home_score":    game.home_score,
        "away_score":    game.away_score,
        "home_win":      game.home_win,
        "team_stats": {
            "home": {
                "wpct":    game.home_wpct,
                "net_rtg": game.home_net_rtg,
                "off_rtg": game.home_off_rtg,
                "def_rtg": game.home_def_rtg,
                "pace":    game.home_pace,
                "l10":     game.home_l10_wpct,
                "days_rest": game.home_days_rest,
                "b2b":     game.back_to_back_home,
            },
            "away": {
                "wpct":    game.away_wpct,
                "net_rtg": game.away_net_rtg,
                "off_rtg": game.away_off_rtg,
                "def_rtg": game.away_def_rtg,
                "pace":    game.away_pace,
                "l10":     game.away_l10_wpct,
                "days_rest": game.away_days_rest,
                "b2b":     game.back_to_back_away,
            },
        },
    }
