from datetime import datetime, timezone
from typing import Dict, Any, Optional
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Game, Team, ProviderGameCrosswalk
from ml.data_pipeline.nba_source import load_nba_upcoming_games

logger = structlog.get_logger(__name__)

NBA_TEAM_ABBR_CROSSWALK = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}

NBA_ESPN_ID_TO_ABBR = {
    "1": "ATL",
    "2": "BOS",
    "3": "NOP",
    "4": "CHI",
    "5": "CLE",
    "6": "DAL",
    "7": "DEN",
    "8": "DET",
    "9": "GSW",
    "10": "HOU",
    "11": "IND",
    "12": "LAC",
    "13": "LAL",
    "14": "MIA",
    "15": "MIL",
    "16": "MIN",
    "17": "BKN",
    "18": "NYK",
    "19": "ORL",
    "20": "PHI",
    "21": "PHX",
    "22": "POR",
    "23": "SAC",
    "24": "SAS",
    "25": "OKC",
    "26": "UTA",
    "27": "WAS",
    "28": "TOR",
    "29": "MEM",
    "30": "CHA",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_provider_instant(left: datetime, right: datetime) -> bool:
    """Compare in UTC at provider precision (event times are second-granular)."""
    return _utc(left).replace(microsecond=0) == _utc(right).replace(microsecond=0)


async def sync_nba_games_from_source(db: AsyncSession) -> Dict[str, Any]:
    """
    Sincroniza el calendario oficial de partidos NBA desde los snapshots verificados de ESPN / SportsDataverse.
    Es una ingesta idempotente que mapea identidades canónicas, kickoff UTC, temporada y equipos.
    Registra la identidad canónica y el crosswalk de proveedores.
    """
    logger.info("sync_nba_games_from_source: iniciando sincronización de calendario NBA")

    upcoming_df = None
    source_name = "SportsDataverse ESPN schedule"
    try:
        upcoming_df = load_nba_upcoming_games()
    except Exception as exc:
        logger.warning("No se pudo cargar calendario NBA desde snapshots locales; intentando fallback live de ESPN", error=str(exc))

    if upcoming_df is None or upcoming_df.empty:
        logger.info("Activando fallback a endpoint en vivo de ESPN Scoreboard para calendario NBA")
        try:
            from ml.data_pipeline.nba_source import fetch_espn_nba_scoreboard_live
            import pandas as pd
            events = fetch_espn_nba_scoreboard_live()
            records = []
            for ev in events:
                comp = ev.get("competitions", [{}])[0]
                competitors = comp.get("competitors", [])
                h_comp = next((c for c in competitors if c.get("homeAway") == "home"), None)
                a_comp = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not h_comp or not a_comp:
                    continue
                dt_str = ev.get("date")
                start_dt = pd.to_datetime(dt_str, utc=True, errors="coerce")
                if pd.isna(start_dt) or start_dt.tzinfo is None:
                    continue
                records.append({
                    "game_id": str(ev.get("id")),
                    "game_date": start_dt.date(),
                    "game_start_utc": start_dt,
                    "season": int(ev.get("season", {}).get("year", 2026)),
                    "home_team_id": str(h_comp.get("id")),
                    "away_team_id": str(a_comp.get("id")),
                    "home_team_name": h_comp.get("team", {}).get("displayName"),
                    "away_team_name": a_comp.get("team", {}).get("displayName"),
                    "home_team_abbreviation": h_comp.get("team", {}).get("abbreviation"),
                    "away_team_abbreviation": a_comp.get("team", {}).get("abbreviation"),
                    "source": "ESPN public API live",
                    "source_ref": f"espn:scoreboard:{ev.get('id')}",
                })
            if records:
                upcoming_df = pd.DataFrame(records)
                source_name = "ESPN public API live"
        except Exception as live_err:
            logger.warning("Fallo en fallback en vivo de ESPN Scoreboard", error=str(live_err))

    if upcoming_df is None or upcoming_df.empty:
        return {
            "status": "error",
            "sport": "NBA",
            "source": "sportsdataverse espn schedules + live fallback",
            "games_synced": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "rejected": 0,
            "detail": "No se encontraron partidos en snapshots locales ni en fallback en vivo",
        }

    # Cargar catálogo de equipos NBA
    teams_res = await db.execute(select(Team).where(Team.sport == "NBA"))
    teams = teams_res.scalars().all()
    abbr_to_team: Dict[str, Team] = {t.abbreviation.upper(): t for t in teams}

    # Pre-cargar juegos y crosswalks existentes en memoria para evitar miles de queries individuales
    existing_games_res = await db.execute(select(Game).where(Game.sport == "NBA"))
    existing_games_list = existing_games_res.scalars().all()
    games_by_ext_id = {g.external_id: g for g in existing_games_list if g.external_id}
    games_by_matchup_instant = {
        (g.home_team_id, g.away_team_id, _utc(g.game_date).replace(microsecond=0)): g
        for g in existing_games_list
        if g.game_date is not None
    }

    cw_res = await db.execute(
        select(ProviderGameCrosswalk).where(ProviderGameCrosswalk.provider.in_(["sportsdataverse", "espn"]))
    )
    cws_by_key = {(cw.provider, cw.provider_event_id): cw for cw in cw_res.scalars().all()}

    inserted = 0
    updated = 0
    unchanged = 0
    rejected = 0

    now_utc = datetime.now(timezone.utc)

    for row in upcoming_df.to_dict("records"):
        game_id = str(row.get("game_id") or "").strip()
        if not game_id or game_id.lower() == "nan":
            logger.error("Calendario NBA sin game_id verificable")
            rejected += 1
            continue

        raw_home_abbr = str(row.get("home_team_abbreviation") or "").upper().strip()
        raw_away_abbr = str(row.get("away_team_abbreviation") or "").upper().strip()

        # Si falta la abreviatura, resolver por ESPN team ID
        if not raw_home_abbr:
            raw_home_abbr = NBA_ESPN_ID_TO_ABBR.get(str(row.get("home_team_id")), "")
        if not raw_away_abbr:
            raw_away_abbr = NBA_ESPN_ID_TO_ABBR.get(str(row.get("away_team_id")), "")

        home_abbr = NBA_TEAM_ABBR_CROSSWALK.get(raw_home_abbr, raw_home_abbr)
        away_abbr = NBA_TEAM_ABBR_CROSSWALK.get(raw_away_abbr, raw_away_abbr)

        home_team = abbr_to_team.get(home_abbr)
        away_team = abbr_to_team.get(away_abbr)

        if not home_team or not away_team:
            logger.warning(
                "sync_nba_games_from_source: equipo no encontrado en catálogo",
                game_id=game_id,
                home_abbr=home_abbr,
                away_abbr=away_abbr,
            )
            rejected += 1
            continue

        kickoff_utc = row.get("game_start_utc")
        if hasattr(kickoff_utc, "to_pydatetime"):
            kickoff_utc = kickoff_utc.to_pydatetime()
        if not isinstance(kickoff_utc, datetime):
            logger.error("Calendario NBA sin kickoff verificable", game_id=game_id)
            rejected += 1
            continue
        if kickoff_utc.tzinfo is None or kickoff_utc.utcoffset() is None:
            logger.error("Calendario NBA con kickoff naive sin timezone UTC explícito", game_id=game_id)
            rejected += 1
            continue

        kickoff_utc = _utc(kickoff_utc)
        instant_key = (home_team.id, away_team.id, kickoff_utc.replace(microsecond=0))

        # Buscar en caché por external_id o coincidencia exacta de matchup + kickoff instantáneo
        existing_game = games_by_ext_id.get(game_id) or games_by_matchup_instant.get(instant_key)

        # Validar consistencia de crosswalk
        sdv_crosswalk = cws_by_key.get(("sportsdataverse", game_id))
        if sdv_crosswalk and existing_game and sdv_crosswalk.game_id != existing_game.id:
            logger.error("Identidad NBA ya vinculada a otro partido", game_id=game_id)
            rejected += 1
            continue
        if sdv_crosswalk and existing_game is None:
            linked_res = await db.execute(select(Game).where(Game.id == sdv_crosswalk.game_id))
            existing_game = linked_res.scalar_one_or_none()
            if existing_game is None:
                logger.error("Crosswalk NBA apunta a partido inexistente", game_id=game_id)
                rejected += 1
                continue

        if existing_game is not None and (
            existing_game.home_team_id != home_team.id
            or existing_game.away_team_id != away_team.id
        ):
            logger.error("Equipos no coinciden con identidad NBA", game_id=game_id)
            rejected += 1
            continue

        if existing_game is None:
            # Crear nuevo partido canónico
            new_game = Game(
                external_id=game_id,
                sport="NBA",
                season=str(row.get("season", "2027")),
                season_type="Regular Season",
                game_date=kickoff_utc,
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                status="scheduled",
                source=row.get("source", source_name),
                source_ref=row.get("source_ref"),
                observed_at=now_utc,
                fetched_at=now_utc,
            )
            db.add(new_game)
            await db.flush()
            existing_game = new_game
            inserted += 1
        else:
            # Actualizar campos si cambiaron
            is_modified = False
            if existing_game.game_date is None or not _same_provider_instant(existing_game.game_date, kickoff_utc):
                existing_game.game_date = kickoff_utc
                is_modified = True
            if existing_game.status != "final" and existing_game.status != "scheduled":
                existing_game.status = "scheduled"
                is_modified = True
            if is_modified:
                existing_game.observed_at = now_utc
                updated += 1
            else:
                unchanged += 1

        # Asegurar registro de crosswalk para providers de forma estrictamente idempotente
        if existing_game is not None:
            if ("sportsdataverse", game_id) not in cws_by_key:
                cw_sdv = ProviderGameCrosswalk(
                    game_id=existing_game.id,
                    provider="sportsdataverse",
                    provider_event_id=game_id,
                )
                db.add(cw_sdv)
                cws_by_key[("sportsdataverse", game_id)] = cw_sdv
            if ("espn", game_id) not in cws_by_key:
                cw_espn = ProviderGameCrosswalk(
                    game_id=existing_game.id,
                    provider="espn",
                    provider_event_id=game_id,
                )
                db.add(cw_espn)
                cws_by_key[("espn", game_id)] = cw_espn

    await db.commit()

    total_synced = inserted + updated + unchanged
    logger.info(
        "sync_nba_games_from_source: finalizado",
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        rejected=rejected,
        total=total_synced,
    )

    return {
        "status": "success",
        "sport": "NBA",
        "games_synced": total_synced,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": rejected,
    }
