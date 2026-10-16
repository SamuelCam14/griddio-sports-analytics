from datetime import datetime, timezone
from typing import Dict, Any, Optional
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Game, Team, ProviderGameCrosswalk
from ml.data_pipeline.nfl_source import load_nfl_upcoming_games

logger = structlog.get_logger(__name__)

TEAM_ABBR_CROSSWALK = {
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LAR",
    "LA": "LAR",
    "WSH": "WAS",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_provider_instant(left: datetime, right: datetime) -> bool:
    """Compare in UTC at provider precision (Odds API event times are second-granular)."""
    return _utc(left).replace(microsecond=0) == _utc(right).replace(microsecond=0)


async def sync_nfl_games_from_source(db: AsyncSession) -> Dict[str, Any]:
    """
    Sincroniza el calendario oficial de partidos NFL desde los snapshots verificados de NFLverse.
    Es una ingesta idempotente que mapea identidades canónicas, kickoff UTC, temporada y equipos.
    Registra la identidad canónica y el crosswalk de proveedores.
    """
    logger.info("sync_nfl_games_from_source: iniciando sincronización de calendario NFL")

    try:
        upcoming_df = load_nfl_upcoming_games()
    except Exception as exc:
        logger.error("Error al cargar calendario NFLverse", error=str(exc))
        return {
            "status": "error",
            "source": "nflverse schedules",
            "games_synced": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "rejected": 0,
            "detail": str(exc),
        }

    # Cargar catálogo de equipos NFL
    teams_res = await db.execute(select(Team).where(Team.sport == "NFL"))
    teams = teams_res.scalars().all()
    abbr_to_team: Dict[str, Team] = {}
    for t in teams:
        abbr_to_team[t.abbreviation] = t
        if t.abbreviation == "LAR":
            abbr_to_team["LA"] = t
        elif t.abbreviation == "WAS":
            abbr_to_team["WSH"] = t

    inserted = 0
    updated = 0
    unchanged = 0
    rejected = 0

    now_utc = datetime.now(timezone.utc)

    for row in upcoming_df.to_dict("records"):
        game_id = str(row.get("game_id") or "").strip()
        if not game_id or game_id.lower() == "nan":
            logger.error("Calendario NFL sin game_id verificable")
            rejected += 1
            continue
        home_abbr = TEAM_ABBR_CROSSWALK.get(str(row["home_team_id"]), str(row["home_team_id"]))
        away_abbr = TEAM_ABBR_CROSSWALK.get(str(row["away_team_id"]), str(row["away_team_id"]))

        home_team = abbr_to_team.get(home_abbr)
        away_team = abbr_to_team.get(away_abbr)

        if not home_team or not away_team:
            logger.warning(
                "sync_nfl_games_from_source: equipo no encontrado en catálogo",
                game_id=game_id,
                home_abbr=home_abbr,
                away_abbr=away_abbr,
            )
            rejected += 1
            continue

        kickoff_utc = row["game_start_utc"]
        if hasattr(kickoff_utc, "to_pydatetime"):
            kickoff_utc = kickoff_utc.to_pydatetime()
        if not isinstance(kickoff_utc, datetime):
            logger.error("Calendario NFL sin kickoff verificable", game_id=game_id)
            rejected += 1
            continue

        # Preferir la identidad NFLverse ya registrada. Si Odds API creó primero
        # el evento, adoptarlo solo ante equipos en orden y kickoff UTC exactos.
        stmt = select(Game).where(Game.external_id == game_id, Game.sport == "NFL")
        existing_res = await db.execute(stmt)
        existing_game = existing_res.scalar_one_or_none()
        if existing_game is None:
            candidates_res = await db.execute(
                select(Game).where(
                    Game.sport == "NFL",
                    Game.home_team_id == home_team.id,
                    Game.away_team_id == away_team.id,
                )
            )
            candidates = [
                candidate for candidate in candidates_res.scalars().all()
                if candidate.game_date is not None
                and _same_provider_instant(candidate.game_date, kickoff_utc)
            ]
            if len(candidates) == 1:
                existing_game = candidates[0]
            elif len(candidates) > 1:
                logger.error(
                    "Calendario NFL ambiguo; se rechaza enlace de proveedor",
                    game_id=game_id,
                    matches=len(candidates),
                )
                rejected += 1
                continue

        # Un identificador NFLverse ya vinculado a otro partido es conflicto,
        # no autorización para mover el crosswalk.
        cw_res = await db.execute(
            select(ProviderGameCrosswalk).where(
                ProviderGameCrosswalk.provider == "nflverse",
                ProviderGameCrosswalk.provider_event_id == game_id,
            )
        )
        nflverse_crosswalk = cw_res.scalar_one_or_none()
        if nflverse_crosswalk and existing_game and nflverse_crosswalk.game_id != existing_game.id:
            logger.error("Identidad NFLverse ya vinculada a otro partido", game_id=game_id)
            rejected += 1
            continue
        if nflverse_crosswalk and existing_game is None:
            linked_res = await db.execute(select(Game).where(Game.id == nflverse_crosswalk.game_id))
            existing_game = linked_res.scalar_one_or_none()
            if existing_game is None:
                logger.error("Crosswalk NFLverse apunta a partido inexistente", game_id=game_id)
                rejected += 1
                continue

        if existing_game is not None and (
            existing_game.home_team_id != home_team.id
            or existing_game.away_team_id != away_team.id
        ):
            logger.error("Equipos no coinciden con identidad NFLverse", game_id=game_id)
            rejected += 1
            continue

        if existing_game is None:
            # Nuevo partido canónico
            new_game = Game(
                external_id=game_id,
                sport="NFL",
                season=str(row["season"]),
                season_type="Regular Season",
                game_date=kickoff_utc,
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                status="scheduled",
                source=row.get("source", "nflverse schedules"),
                source_ref=row.get("source_ref"),
                observed_at=now_utc,
                fetched_at=now_utc,
            )
            db.add(new_game)
            await db.flush()
            existing_game = new_game
            inserted += 1
        else:
            # Verificar si cambió la hora de kickoff o la referencia
            changed = False
            if existing_game.game_date != kickoff_utc:
                existing_game.game_date = kickoff_utc
                changed = True
            if existing_game.source_ref != row.get("source_ref"):
                existing_game.source = row.get("source", "nflverse schedules")
                existing_game.source_ref = row.get("source_ref")
                changed = True
            else:
                existing_game.source = row.get("source", "nflverse schedules")
            existing_game.season = str(row["season"])
            existing_game.season_type = "Regular Season"
            if changed:
                existing_game.updated_at = now_utc
                updated += 1
            else:
                unchanged += 1

        if nflverse_crosswalk is None:
            db.add(ProviderGameCrosswalk(
                game_id=existing_game.id,
                provider="nflverse",
                provider_event_id=game_id,
            ))

    await db.commit()

    total_valid = inserted + updated + unchanged
    if total_valid == 0 and rejected > 0:
        sync_status = "failed"
    elif rejected > 0 and total_valid > 0:
        sync_status = "partial"
    elif total_valid == 0 and rejected == 0:
        sync_status = "no_usable_data"
    else:
        sync_status = "success"

    logger.info(
        "sync_nfl_games_from_source completado",
        status=sync_status,
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        rejected=rejected,
        total_synced=inserted + updated,
    )

    return {
        "status": sync_status,
        "source": "nflverse schedules via nflreadpy",
        "games_synced": inserted + updated,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": rejected,
    }
