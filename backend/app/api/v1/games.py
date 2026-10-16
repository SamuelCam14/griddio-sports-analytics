"""
Router: Games — /api/v1/games

Endpoints:
  GET /today          → Partidos de hoy con stats de equipos
  GET /date/{date}    → Partidos de una fecha específica
  GET /upcoming       → Próximos partidos (siguiente semana)
"""
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.database import get_db
from app.models.game import Game, Team

router = APIRouter()


@router.get("/standings", summary="Récords de temporada calculados con resultados almacenados")
async def get_standings(
    sport: str = Query(default="NFL"),
    db: AsyncSession = Depends(get_db),
):
    """Return observed W/L records; do not claim official ranking or tie-breakers."""
    sport_upper = sport.strip().upper() if isinstance(sport, str) else ""
    if sport_upper not in {"NFL", "NBA"}:
        raise HTTPException(status_code=400, detail="Deporte no soportado")

    if sport_upper == "NFL":
        from ml.data_pipeline.common import DataQualityError
        from ml.data_pipeline.nfl_source import load_nfl_schedule_results
        try:
            schedule_results = load_nfl_schedule_results()
        except (DataQualityError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=503, detail=f"NFL standings source unavailable: {type(exc).__name__}")
        if schedule_results.empty:
            return {
                "sport": "NFL", "status": "unavailable", "season": None,
                "as_of": None, "teams_with_results": 0, "teams_in_catalog": 0,
                "standings": [], "reason": "El snapshot NFLverse verificado no contiene resultados completados.",
            }

        season = int(schedule_results["season"].max())
        season_games = schedule_results[schedule_results["season"] == season]
        teams_result = await db.execute(select(Team).where(Team.sport == "NFL"))
        from app.services.nfl_schedule_service import TEAM_ABBR_CROSSWALK
        teams_by_abbr = {}
        for team in teams_result.scalars().all():
            raw_abbr = team.abbreviation.upper()
            canonical_abbr = TEAM_ABBR_CROSSWALK.get(raw_abbr, raw_abbr)
            current = teams_by_abbr.get(canonical_abbr)
            if current is None or raw_abbr == canonical_abbr:
                teams_by_abbr[canonical_abbr] = team
        records: dict[str, dict[str, int]] = {}
        for game in season_games.to_dict("records"):
            home_raw, away_raw = str(game["home_team_id"]).upper(), str(game["away_team_id"]).upper()
            home_abbr = TEAM_ABBR_CROSSWALK.get(home_raw, home_raw)
            away_abbr = TEAM_ABBR_CROSSWALK.get(away_raw, away_raw)
            home = records.setdefault(home_abbr, {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0})
            away = records.setdefault(away_abbr, {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0})
            home["pf"] += game["home_score"]; home["pa"] += game["away_score"]
            away["pf"] += game["away_score"]; away["pa"] += game["home_score"]
            if game["home_score"] > game["away_score"]:
                home["w"] += 1; away["l"] += 1
            elif game["away_score"] > game["home_score"]:
                away["w"] += 1; home["l"] += 1
            else:
                home["t"] += 1; away["t"] += 1

        rows = []
        for abbr, record in records.items():
            team = teams_by_abbr.get(abbr)
            if team is None:
                continue
            played = record["w"] + record["l"] + record["t"]
            rows.append({
                "team_id": team.id, "name": team.name, "abbreviation": team.abbreviation,
                "conference": team.conference, "division": team.division,
                **record, "played": played,
                "pct": (record["w"] + 0.5 * record["t"]) / played if played else None,
            })
        rows.sort(key=lambda row: (-(row["pct"] or 0), -row["w"], row["conference"] or "", row["name"]))
        return {
            "sport": "NFL", "status": "partial" if len(rows) < len(teams_by_abbr) else "available",
            "season": season,
            "as_of": season_games["game_start_utc"].max().isoformat(),
            "source": "NFLverse verified schedule snapshots",
            "source_refs": schedule_results.attrs.get("schedule_source_refs", []),
            "source_retrieved_at": schedule_results.attrs.get("schedule_retrieved_at"),
            "teams_with_results": len(rows), "teams_in_catalog": len(teams_by_abbr),
            "ranking_note": "Récord calculado de resultados de calendario; desempates oficiales no aplicados.",
            "standings": [{"position": idx + 1, **row} for idx, row in enumerate(rows)],
        }

    games_result = await db.execute(
        select(Game)
        .where(Game.sport == sport_upper, Game.status == "final")
        .where(Game.home_score.is_not(None), Game.away_score.is_not(None))
        .order_by(Game.game_date.desc())
    )
    finished = games_result.scalars().all()
    if not finished:
        return {
            "sport": sport_upper, "status": "unavailable", "season": None,
            "as_of": None, "teams_with_results": 0, "teams_in_catalog": 0,
            "standings": [], "reason": "No hay resultados finales verificados en la base de datos.",
        }

    season = max(finished, key=lambda game: game.game_date).season
    season_games = [game for game in finished if game.season == season]
    teams_result = await db.execute(select(Team).where(Team.sport == sport_upper))
    teams = {team.id: team for team in teams_result.scalars().all()}
    records: dict[int, dict[str, int]] = {}

    for game in season_games:
        home = records.setdefault(game.home_team_id, {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0})
        away = records.setdefault(game.away_team_id, {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0})
        home["pf"] += game.home_score
        home["pa"] += game.away_score
        away["pf"] += game.away_score
        away["pa"] += game.home_score
        if game.home_score > game.away_score:
            home["w"] += 1; away["l"] += 1
        elif game.away_score > game.home_score:
            away["w"] += 1; home["l"] += 1
        else:
            home["t"] += 1; away["t"] += 1

    rows = []
    for team_id, record in records.items():
        team = teams.get(team_id)
        if team is None:
            continue
        played = record["w"] + record["l"] + record["t"]
        pct = (record["w"] + 0.5 * record["t"]) / played if played else None
        rows.append({
            "team_id": team.id, "name": team.name, "abbreviation": team.abbreviation,
            "conference": team.conference, "division": team.division,
            "w": record["w"], "l": record["l"], "t": record["t"],
            "played": played, "pct": pct, "points_for": record["pf"], "points_against": record["pa"],
        })
    rows.sort(key=lambda row: (-(row["pct"] or 0), -row["w"], row["conference"] or "", row["name"]))

    return {
        "sport": sport_upper, "status": "partial" if len(rows) < len(teams) else "available",
        "season": season,
        "as_of": max(game.game_date for game in season_games).isoformat(),
        "teams_with_results": len(rows), "teams_in_catalog": len(teams),
        "ranking_note": "Récord calculado de resultados guardados; desempates oficiales no aplicados.",
        "standings": [{"position": idx + 1, **row} for idx, row in enumerate(rows)],
    }


@router.get("/today", summary="Partidos de hoy")
async def get_today_games(db: AsyncSession = Depends(get_db)):
    """Obtiene todos los partidos programados para hoy."""
    today = datetime.now(ZoneInfo("America/New_York")).date()
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


@router.get("/{game_id}/external-prediction", summary="Pronóstico externo ESPN con identidad de evento exacta")
async def get_external_game_prediction(
    game_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Expose an explicitly external forecast; never treat it as a production Griddio pick."""
    result = await db.execute(
        select(Game)
        .options(joinedload(Game.home_team), joinedload(Game.away_team))
        .where(Game.id == game_id)
    )
    game = result.scalars().unique().one_or_none()
    if game is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    from app.services.espn_service import get_cached_espn_prediction
    return await get_cached_espn_prediction(game)


@router.get("/upcoming", summary="Próximos partidos (7 días)")
async def get_upcoming_games(
    days: int = Query(default=7, ge=1, le=30),
    sport: str = Query(default="NBA"),
    db: AsyncSession = Depends(get_db),
):
    """Obtiene partidos de los próximos N días."""
    now = datetime.now(timezone.utc)
    start = now
    end = now + timedelta(days=days)

    stmt = (
        select(Game)
        .where(Game.sport == sport.upper())
        .where(Game.game_date >= start)
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
    # Las fechas oficiales de partidos NFL/NBA se anclan a America/New_York (Eastern Time)
    et_tz = ZoneInfo("America/New_York")
    start_et = datetime.combine(target_date, datetime.min.time(), tzinfo=et_tz)
    end_et = datetime.combine(target_date, datetime.max.time(), tzinfo=et_tz)
    start_utc = start_et.astimezone(timezone.utc)
    end_utc = end_et.astimezone(timezone.utc)

    stmt = (
        select(Game)
        .where(Game.game_date >= start_utc)
        .where(Game.game_date <= end_utc)
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
