"""
Router: Games — /api/v1/games

Endpoints:
  GET /today          → Partidos de hoy con stats de equipos
  GET /date/{date}    → Partidos de una fecha específica
  GET /upcoming       → Próximos partidos (siguiente semana)
"""
from datetime import date, datetime, timezone, timedelta
from typing import Any, Optional, Dict, List
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
                **record,
                "points_for": record["pf"],
                "points_against": record["pa"],
                "played": played,
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

    if sport_upper == "NBA":
        from ml.data_pipeline.common import DataQualityError
        from ml.data_pipeline.nba_source import load_nba_games
        from app.services.nba_schedule_service import NBA_ESPN_ID_TO_ABBR, NBA_TEAM_ABBR_CROSSWALK

        teams_result = await db.execute(select(Team).where(Team.sport == "NBA"))
        nba_teams = teams_result.scalars().all()
        teams_by_abbr = {team.abbreviation.upper(): team for team in nba_teams}
        teams_by_id = {team.id: team for team in nba_teams}

        games_result = await db.execute(
            select(Game)
            .where(Game.sport == "NBA", Game.status == "final")
            .where(Game.home_score.is_not(None), Game.away_score.is_not(None))
            .order_by(Game.game_date.desc())
        )
        finished_db = games_result.scalars().all()

        try:
            historical_games = load_nba_games()
        except (DataQualityError, FileNotFoundError, OSError) as exc:
            historical_games = None
            if not finished_db:
                raise HTTPException(status_code=503, detail=f"NBA standings source unavailable: {type(exc).__name__}")

        if (historical_games is None or historical_games.empty) and not finished_db:
            return {
                "sport": "NBA", "status": "unavailable", "season": None,
                "as_of": None, "teams_with_results": 0, "teams_in_catalog": len(teams_by_abbr),
                "standings": [], "reason": "El snapshot NBA verificado no contiene resultados completados.",
            }

        def _parse_season(val: Any) -> Optional[int]:
            if val is None:
                return None
            try:
                s_str = str(val).strip()
                if "-" in s_str:
                    parts = s_str.split("-")
                    if len(parts[0]) == 4 and len(parts[1]) == 2:
                        return int(parts[0][:2] + parts[1])
                    return int(parts[0])
                return int(s_str)
            except (ValueError, TypeError):
                return None

        parquet_season = int(historical_games["season"].max()) if historical_games is not None and not historical_games.empty else None

        db_by_season: Dict[int, List[Game]] = {}
        for g in finished_db:
            s_parsed = _parse_season(g.season)
            if s_parsed is not None:
                db_by_season.setdefault(s_parsed, []).append(g)

        target_season = parquet_season
        if target_season is None and db_by_season:
            target_season = max(db_by_season.keys())
        elif db_by_season:
            max_db_season = max(db_by_season.keys())
            db_teams_in_max = set()
            for g in db_by_season[max_db_season]:
                if g.home_team_id in teams_by_id:
                    db_teams_in_max.add(teams_by_id[g.home_team_id].abbreviation.upper())
                if g.away_team_id in teams_by_id:
                    db_teams_in_max.add(teams_by_id[g.away_team_id].abbreviation.upper())
            if len(db_teams_in_max) >= len(teams_by_abbr) and len(teams_by_abbr) > 0:
                target_season = max_db_season

        merged_games: Dict[str, Dict[str, Any]] = {}
        matchup_to_key: Dict[tuple[str, str, str], str] = {}

        if historical_games is not None and not historical_games.empty and target_season is not None:
            parquet_season_games = historical_games[historical_games["season"] == target_season]
            for row in parquet_season_games.to_dict("records"):
                gid = str(row["game_id"])
                home_raw = NBA_ESPN_ID_TO_ABBR.get(str(row["home_team_id"]), str(row["home_team_id"]))
                away_raw = NBA_ESPN_ID_TO_ABBR.get(str(row["away_team_id"]), str(row["away_team_id"]))
                h_abbr = NBA_TEAM_ABBR_CROSSWALK.get(home_raw, home_raw)
                a_abbr = NBA_TEAM_ABBR_CROSSWALK.get(away_raw, away_raw)
                g_date = row.get("game_date")
                date_str = str(g_date) if g_date is not None else ""
                merged_games[gid] = {
                    "home_abbr": h_abbr,
                    "away_abbr": a_abbr,
                    "home_score": int(row["home_score"]),
                    "away_score": int(row["away_score"]),
                    "game_start_utc": row["game_start_utc"],
                    "source": "parquet",
                }
                matchup_to_key[(h_abbr, a_abbr, date_str)] = gid

        db_season_games = db_by_season.get(target_season, [])
        for g in db_season_games:
            home_t = teams_by_id.get(g.home_team_id)
            away_t = teams_by_id.get(g.away_team_id)
            if not home_t or not away_t:
                continue
            h_abbr = home_t.abbreviation.upper()
            a_abbr = away_t.abbreviation.upper()
            g_date = g.game_date.date() if isinstance(g.game_date, datetime) else g.game_date
            date_str = str(g_date) if g_date is not None else ""

            existing_key = None
            if g.external_id and str(g.external_id) in merged_games:
                existing_key = str(g.external_id)
            elif (h_abbr, a_abbr, date_str) in matchup_to_key:
                existing_key = matchup_to_key[(h_abbr, a_abbr, date_str)]

            key = existing_key or (str(g.external_id) if g.external_id else f"db_{g.id}_{h_abbr}_{a_abbr}")
            merged_games[key] = {
                "home_abbr": h_abbr,
                "away_abbr": a_abbr,
                "home_score": int(g.home_score),
                "away_score": int(g.away_score),
                "game_start_utc": g.game_date,
                "source": "database",
            }
            matchup_to_key[(h_abbr, a_abbr, date_str)] = key

        records: Dict[str, Dict[str, int]] = {
            abbr: {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0}
            for abbr in teams_by_abbr
        }
        for game in merged_games.values():
            h = game["home_abbr"]
            a = game["away_abbr"]
            if h in records and a in records:
                records[h]["pf"] += game["home_score"]
                records[h]["pa"] += game["away_score"]
                records[a]["pf"] += game["away_score"]
                records[a]["pa"] += game["home_score"]
                if game["home_score"] > game["away_score"]:
                    records[h]["w"] += 1
                    records[a]["l"] += 1
                elif game["away_score"] > game["home_score"]:
                    records[a]["w"] += 1
                    records[h]["l"] += 1
                else:
                    records[h]["t"] += 1
                    records[a]["t"] += 1

        rows = []
        for abbr, record in records.items():
            team = teams_by_abbr.get(abbr)
            if team is None:
                continue
            played = record["w"] + record["l"] + record["t"]
            rows.append({
                "team_id": team.id,
                "name": team.name,
                "abbreviation": team.abbreviation,
                "conference": team.conference,
                "division": team.division,
                **record,
                "points_for": record["pf"],
                "points_against": record["pa"],
                "played": played,
                "pct": (record["w"] + 0.5 * record["t"]) / played if played else None,
            })

        rows.sort(key=lambda row: (-(row["pct"] or 0), -row["w"], row["conference"] or "", row["name"]))
        teams_with_results = sum(1 for r in rows if r["played"] > 0)

        def _to_utc(val: Any) -> Optional[datetime]:
            if val is None:
                return None
            if hasattr(val, "to_pydatetime"):
                val = val.to_pydatetime()
            if isinstance(val, datetime):
                if val.tzinfo is None:
                    return val.replace(tzinfo=timezone.utc)
                return val.astimezone(timezone.utc)
            if isinstance(val, date):
                return datetime(val.year, val.month, val.day, tzinfo=timezone.utc)
            return None

        max_dt: Optional[datetime] = None
        for g in merged_games.values():
            dt = _to_utc(g.get("game_start_utc"))
            if dt is not None:
                if max_dt is None or dt > max_dt:
                    max_dt = dt

        has_db = any(g.get("source") == "database" for g in merged_games.values())
        has_parquet = any(g.get("source") == "parquet" for g in merged_games.values())
        if has_db and has_parquet:
            src = "ESPN verified schedule and box score snapshots with database synchronization"
        elif has_db:
            src = "Griddio database verified games"
        else:
            src = "ESPN verified schedule and box score snapshots"

        return {
            "sport": "NBA",
            "status": "partial" if teams_with_results < len(teams_by_abbr) else "available",
            "season": target_season,
            "as_of": max_dt.isoformat() if max_dt is not None else None,
            "source": src,
            "teams_with_results": teams_with_results,
            "teams_in_catalog": len(teams_by_abbr),
            "ranking_note": "Récord calculado de resultados de calendario; desempates oficiales no aplicados.",
            "standings": [{"position": idx + 1, **row} for idx, row in enumerate(rows)],
        }

    return {
        "sport": sport_upper, "status": "unavailable", "season": None,
        "as_of": None, "teams_with_results": 0, "teams_in_catalog": 0,
        "standings": [], "reason": "No hay resultados finales verificados en la base de datos.",
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
        .options(joinedload(Game.home_team), joinedload(Game.away_team))
        .where(Game.sport == sport.upper())
        .where(Game.game_date >= start)
        .where(Game.game_date <= end)
        .where(Game.status == "scheduled")
        .order_by(Game.game_date)
    )
    result = await db.execute(stmt)
    games = result.scalars().unique().all()

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
        .options(joinedload(Game.home_team), joinedload(Game.away_team))
        .where(Game.game_date >= start_utc)
        .where(Game.game_date <= end_utc)
        .order_by(Game.game_date)
    )
    result = await db.execute(stmt)
    games = result.scalars().unique().all()

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
        "home_team_name": game.home_team.name if game.home_team else None,
        "away_team_name": game.away_team.name if game.away_team else None,
        "home_team_abbreviation": game.home_team.abbreviation if game.home_team else None,
        "away_team_abbreviation": game.away_team.abbreviation if game.away_team else None,
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
