"""Ingesta cuotas publicadas por The Odds API sin fabricar valores ausentes."""
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import re
import unicodedata
import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.core.config import settings
from app.models.game import Team, Game, OddsSnapshot, Prediction, ProviderGameCrosswalk
from app.services.odds_service import fetch_odds
from app.services.odds_quota_service import (
    CircuitBreakerOpenError,
    OddsQuotaService,
    QuotaExceededError,
)
from app.services.math.odds_converter import (
    american_to_decimal,
    american_to_implied_prob,
    remove_vig,
)

logger = structlog.get_logger(__name__)

quota_service = OddsQuotaService()

BASE_URL = settings.ODDS_API_BASE_URL
API_KEY = settings.ODDS_API_KEY

async def fetch_real_player_props(event_id: str, sport: str = "NFL") -> List[Dict[str, Any]]:
    """
    Obtiene líneas de Player Props en VIVO desde The Odds API para un partido específico.
    Gestionado bajo el sobre 'props' (tope 320 créditos) con registro en ledger.
    En Fase 01, la ingesta está restringida exclusivamente a NFL.
    Si las casas de apuestas aún no publican props (o si el plan no tiene acceso),
    retorna lista vacía []. NUNCA inventa jugadores ni líneas ficticias.
    """
    if sport.upper() != "NFL":
        logger.warning("fetch_real_player_props rechazado para deporte no NFL en Fase 01", sport=sport)
        return []

    if not API_KEY or not event_id:
        return []

    sport_key = "americanfootball_nfl" if sport.upper() == "NFL" else "basketball_nba"
    markets = (
        "player_pass_yds,player_rush_yds,player_reception_yds,player_receptions"
        if sport.upper() == "NFL"
        else "player_points,player_rebounds,player_assists"
    )

    endpoint = f"/sports/{sport_key}/events/{event_id}/odds"
    url = f"{BASE_URL}{endpoint}"
    req_params = {
        "regions": "us",
        "markets": markets,
        "oddsFormat": "american",
    }

    reservation = None
    try:
        reservation = quota_service.reserve_quota(
            endpoint=endpoint,
            params=req_params,
            envelope="props",
            api_key=API_KEY,
        )
    except (QuotaExceededError, CircuitBreakerOpenError) as exc:
        logger.warning("Player props fetch bloqueado por control de cuota/reserva", error=str(exc), event_id=event_id)
        return []

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url, params={"apiKey": API_KEY, **req_params})
            if reservation:
                quota_service.record_transaction(
                    reservation=reservation,
                    status_code=resp.status_code,
                    response_headers=dict(resp.headers),
                )
                reconciled_reservation = reservation
                reservation = None
            else:
                reconciled_reservation = None

            if resp.status_code != 200:
                logger.warning("Player props not available from API", status=resp.status_code, event_id=event_id)
                return []

            data = resp.json()
            captured_at = datetime.now(timezone.utc).isoformat()
            if reconciled_reservation:
                try:
                    quota_service.persist_raw_response(
                        endpoint=endpoint,
                        params={"sport": sport_key, "event_id": event_id, **req_params},
                        response_body=data,
                        response_headers=dict(resp.headers),
                    )
                except Exception as pe:
                    logger.warning("Error al persistir props crudas (no afecta cuota)", error=str(pe))
            bookmakers = data.get("bookmakers", [])
            if not bookmakers:
                return []

            # Priorizar DraftKings o el primer bookmaker disponible
            selected_book = next((b for b in bookmakers if b.get("key") == "draftkings"), bookmakers[0])
            book_title = selected_book.get("title", "DraftKings")

            parsed_props: List[Dict[str, Any]] = []
            seen_keys = set()

            for market in selected_book.get("markets", []):
                market_key = market.get("key")
                type_labels = {
                    "player_pass_yds": "passing_yards",
                    "player_rush_yds": "rushing_yards",
                    "player_reception_yds": "receiving_yards",
                    "player_receptions": "receptions",
                    "player_points": "points",
                    "player_rebounds": "rebounds",
                    "player_assists": "assists",
                }
                prop_type = type_labels.get(market_key, market_key)

                outcomes = market.get("outcomes", [])
                players_grouped: Dict[str, Dict[str, Any]] = {}
                for out in outcomes:
                    player = out.get("description")
                    if not player:
                        continue
                    price = out.get("price")
                    point = out.get("point")
                    if price is None or point is None:
                        continue
                    if player not in players_grouped:
                        players_grouped[player] = {}
                    side = out.get("name")
                    players_grouped[player][side] = {
                        "price": price,
                        "point": point,
                    }

                for player, sides in players_grouped.items():
                    if "Over" in sides and "Under" in sides:
                        over_data = sides["Over"]
                        under_data = sides["Under"]
                        line = over_data["point"]
                        if line != under_data["point"]:
                            continue
                        dedup_key = f"{player}_{prop_type}"
                        if dedup_key in seen_keys:
                            continue
                        seen_keys.add(dedup_key)

                        price_over = over_data["price"]
                        prob_over_raw = american_to_implied_prob(price_over)
                        prob_under_raw = american_to_implied_prob(under_data["price"])
                        p_over, p_under, _ = remove_vig(prob_over_raw, prob_under_raw)

                        parsed_props.append({
                            "event_id": event_id,
                            "timestamp": captured_at,
                            "player_name": player,
                            "prop_type": prop_type,
                            "market_line": line,
                            "price": price_over,
                            "over_odds_american": price_over,
                            "under_odds_american": under_data["price"],
                            "over_implied_probability": round(p_over, 4),
                            "under_implied_probability": round(p_under, 4),
                            "bookmaker": book_title,
                            "data_source": "The Odds API",
                            "reasoning": "Cuotas de mercado publicadas; no hay proyección de jugador validada.",
                        })

            return parsed_props[:100]

    except (httpx.TimeoutException, httpx.ConnectError) as e:
        clean_err = re.sub(r'(apiKey=)[^&\s]+', r'\1[REDACTED]', str(e))
        logger.warning("Error de timeout o conexión en player props", error=clean_err, event_id=event_id)
        if reservation:
            quota_service.record_transaction(
                reservation=reservation,
                is_timeout=True,
                error_message=f"Timeout/Network error in props: {clean_err}",
            )
            reservation = None
        return []
    except Exception as e:
        clean_err = re.sub(r'(apiKey=)[^&\s]+', r'\1[REDACTED]', str(e))
        logger.error("Error fetching live player props", event_id=event_id, error=clean_err)
        if reservation:
            quota_service.record_transaction(
                reservation=reservation,
                is_timeout=True,
                error_message=f"Unexpected exception post-dispatch in props: {clean_err}",
            )
            reservation = None
        return []


async def sync_real_games(sport: str, db: AsyncSession) -> Dict[str, Any]:
    """
    Sincroniza partidos 100% reales desde The Odds API a la base de datos PostgreSQL.
    En Fase 01, la ingesta está restringida exclusivamente a NFL.
    Persiste solo cuotas recibidas; no estima ni rellena mercados ausentes.
    """
    sport_upper = sport.upper()
    from app.services.freshness_service import ProviderError
    
    try:
        odds_list = await fetch_odds(sport=sport_upper)
    except ValueError as e:
        logger.warning("sync_real_games rechazado para deporte no NFL en Fase 01", sport=sport_upper, error=str(e))
        return {
            "status": "error",
            "sport": sport_upper,
            "error_type": "sport_not_allowed",
            "detail": str(e),
        }
    except ProviderError as e:
        logger.error("Provider error during odds fetch", sport=sport_upper, error=str(e))
        return {
            "status": "error",
            "sport": sport_upper,
            "error_type": "provider_error",
            "detail": str(e)
        }

    from app.services.espn_service import sync_espn_scores
    espn_result = await sync_espn_scores(sport_upper, db)
    teams_res = await db.execute(select(Team).where(Team.sport == sport_upper))
    teams = teams_res.scalars().all()
    if not odds_list:
        logger.warning("No live odds returned from API", sport=sport_upper)
        return {
            "status": "no_odds",
            "sport": sport_upper,
            "games_synced": 0,
            "predictions_synced": 0,
            "scores_updated_from_espn": espn_result.get("updated", 0),
            "note": "No prediction emitted; market odds unavailable.",
        }
    def normalize_team_name(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
        return re.sub(r"\s+", " ", normalized)

    # Canonical abbreviations preferred over ESPN-aliased duplicates
    _CANONICAL_ABBRS = {"WAS", "LAR", "LAC", "LV"}

    def find_team(query_name: str) -> Optional[Team]:
        q = normalize_team_name(query_name)
        matches = [t for t in teams if normalize_team_name(t.name) == q]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Prefer the team whose abbreviation is in the canonical set
            canonical = [t for t in matches if t.abbreviation in _CANONICAL_ABBRS]
            if len(canonical) == 1:
                logger.warning(
                    "find_team: múltiples equipos con mismo nombre; usando abreviatura canónica",
                    name=query_name,
                    chosen_abbr=canonical[0].abbreviation,
                    all_ids=[t.id for t in matches],
                )
                return canonical[0]
            # Fallback: prefer shorter abbreviation (less likely to be an alias)
            matches_sorted = sorted(matches, key=lambda t: len(t.abbreviation))
            logger.warning(
                "find_team: ambigüedad no resuelta por canónica; eligiendo por longitud de abreviatura",
                name=query_name,
                chosen_abbr=matches_sorted[0].abbreviation,
                all_ids=[t.id for t in matches],
            )
            return matches_sorted[0]
        return None

    def as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def same_provider_instant(left: datetime, right: datetime) -> bool:
        # The Odds API publishes commence_time with second precision.
        return as_utc(left).replace(microsecond=0) == as_utc(right).replace(microsecond=0)

    synced_games = 0
    stale_discarded = 0
    snapshots_saved = 0
    unknown_teams = 0
    unmatched_events = 0
    ambiguous_events = 0
    identity_conflicts = 0

    for item in odds_list:
        home_team = find_team(item["home_team"])
        away_team = find_team(item["away_team"])

        if not home_team or not away_team:
            logger.warning("Could not match teams in DB", home=item["home_team"], away=item["away_team"])
            unknown_teams += 1
            continue

        ext_id = item["external_id"]
        commence_dt = datetime.fromisoformat(item["commence_time"].replace("Z", "+00:00"))

        # 1. Resolver el Game canónico (vía crosswalk, external_id exacto o coincidencia unívoca de equipos + fecha)
        game = None
        cw_entry = None

        # a) Buscar en crosswalk por provider="the_odds_api" y provider_event_id=ext_id
        cw_stmt = select(ProviderGameCrosswalk).where(
            ProviderGameCrosswalk.provider == "the_odds_api",
            ProviderGameCrosswalk.provider_event_id == ext_id,
        )
        cw_res = await db.execute(cw_stmt)
        cw_entry = cw_res.scalar_one_or_none()
        if cw_entry:
            if hasattr(cw_entry, "game_id"):
                game_res = await db.execute(select(Game).where(Game.id == cw_entry.game_id))
                game = game_res.scalar_one_or_none()
            elif isinstance(cw_entry, Game):
                game = cw_entry
            if game is None:
                logger.error("Crosswalk Odds API apunta a partido inexistente", provider_event_id=ext_id)
                identity_conflicts += 1
                continue

        # b) Buscar por external_id exacto si no se halló en crosswalk
        if not game:
            game_res = await db.execute(select(Game).where(Game.external_id == ext_id))
            game = game_res.scalar_one_or_none()

        # c) Solo se permite el crosswalk inicial ante identidad de equipos en
        # orden y kickoff UTC exactos. Una ventana horaria no prueba identidad.
        if not game:
            candidates_stmt = (
                select(Game)
                .where(Game.home_team_id == home_team.id)
                .where(Game.away_team_id == away_team.id)
                .where(Game.sport == sport_upper)
            )
            candidates_res = await db.execute(candidates_stmt)
            candidates = candidates_res.scalars().all()
            matching_games = [
                g for g in candidates
                if g.game_date and same_provider_instant(g.game_date, commence_dt)
            ]
            if len(matching_games) == 1:
                game = matching_games[0]
            elif len(matching_games) > 1:
                logger.error(
                    "Evento Odds API ambiguo por equipos y kickoff; se abstiene",
                    provider_event_id=ext_id,
                    matches=len(matching_games),
                )
                ambiguous_events += 1
                continue

        if game is not None:
            if game.home_team_id != home_team.id or game.away_team_id != away_team.id:
                logger.error("Crosswalk Odds API contradice equipos del evento", provider_event_id=ext_id)
                identity_conflicts += 1
                continue
            if not game.game_date or not same_provider_instant(game.game_date, commence_dt):
                logger.error("Crosswalk Odds API contradice kickoff canónico", provider_event_id=ext_id)
                identity_conflicts += 1
                continue

        if not game and ext_id and sport_upper == "NFL":
            unmatched_events += 1

        # Si el partido ya existe (canónico), actualizamos fecha y registramos en crosswalk
        if game:
            # No sobrescribir calendario canónico con el kickoff del proveedor.
            # El evento ya fue validado con identidad y hora exactas.

            # Asegurar persistencia del crosswalk
            if not cw_entry:
                cw_stmt = select(ProviderGameCrosswalk).where(
                    ProviderGameCrosswalk.provider == "the_odds_api",
                    ProviderGameCrosswalk.provider_event_id == ext_id,
                )
                cw_res = await db.execute(cw_stmt)
                if not cw_res.scalar_one_or_none():
                    db.add(ProviderGameCrosswalk(
                        game_id=game.id,
                        provider="the_odds_api",
                        provider_event_id=ext_id,
                    ))
                    await db.flush()
        else:
            # d) Crear nuevo partido canónico si no existía previamente
            season_year = commence_dt.year
            season = (
                f"{season_year}-{str(season_year + 1)[-2:]}"
                if sport_upper == "NBA" and commence_dt.month >= 10
                else f"{season_year - 1}-{str(season_year)[-2:]}"
                if sport_upper == "NBA"
                else str(season_year)
            )
            game = Game(
                external_id=ext_id,
                sport=sport_upper,
                season=season,
                season_type=None,
                game_date=commence_dt,
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                status="scheduled",
                source="The Odds API",
                source_ref=f"the_odds_api:event:{ext_id}",
                observed_at=datetime.now(timezone.utc),
                fetched_at=datetime.now(timezone.utc),
            )
            db.add(game)
            await db.flush()

            # Registrar correspondencia en crosswalk
            db.add(ProviderGameCrosswalk(
                game_id=game.id,
                provider="the_odds_api",
                provider_event_id=ext_id,
            ))
            await db.flush()

        synced_games += 1

        # 2. Insertar OddsSnapshot
        book = item.get("best_bookmaker") or item.get("spread_bookmaker") or item.get("total_bookmaker")
        h_ml = item.get("home_ml")
        a_ml = item.get("away_ml")
        h_spread = item.get("home_spread")
        h_spread_ml = item.get("home_spread_ml")
        a_spread = item.get("away_spread")
        a_spread_ml = item.get("away_spread_ml")
        tot_line = item.get("total_line")
        over_ml = item.get("over_ml")
        under_ml = item.get("under_ml")
        p_h_fair = item.get("home_implied_prob")
        p_a_fair = item.get("away_implied_prob")
        
        # We now track separate updates per market
        ml_last_update_str = item.get("ml_last_update")
        spread_last_update_str = item.get("spread_last_update")
        totals_last_update_str = item.get("totals_last_update")
        
        def parse_ts(ts_str):
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else None
            
        ml_last_update = parse_ts(ml_last_update_str)
        spread_last_update = parse_ts(spread_last_update_str)
        totals_last_update = parse_ts(totals_last_update_str)
        
        captured_at_ts = datetime.now(timezone.utc)
        
        from app.services.freshness_service import is_fresh, FreshnessError
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        
        def add_snapshot_idempotent(market, bookmaker, observed_at, **kwargs):
            nonlocal snapshots_saved
            stmt = pg_insert(OddsSnapshot).values(
                game_id=game.id,
                bookmaker=bookmaker,
                market=market,
                captured_at=captured_at_ts,
                observed_at=observed_at,
                **kwargs
            )
            stmt = stmt.on_conflict_do_nothing(index_elements=['game_id', 'bookmaker', 'market', 'observed_at'])
            return stmt

        # ML
        if h_ml is not None and a_ml is not None and item.get("best_bookmaker"):
            try:
                is_fresh(ml_last_update, commence_dt, sport_upper, "odds")
                stmt = add_snapshot_idempotent(
                    "h2h", item["best_bookmaker"], ml_last_update,
                    home_ml=h_ml, away_ml=a_ml, home_implied_prob=p_h_fair, away_implied_prob=p_a_fair
                )
                res = await db.execute(stmt)
                if res.rowcount > 0:
                    snapshots_saved += 1
            except FreshnessError:
                stale_discarded += 1

        # Spread
        if h_spread is not None and a_spread is not None and item.get("spread_bookmaker"):
            try:
                is_fresh(spread_last_update, commence_dt, sport_upper, "odds")
                stmt = add_snapshot_idempotent(
                    "spreads", item["spread_bookmaker"], spread_last_update,
                    home_spread=h_spread, home_spread_ml=h_spread_ml, away_spread=a_spread, away_spread_ml=a_spread_ml
                )
                res = await db.execute(stmt)
                if res.rowcount > 0:
                    snapshots_saved += 1
            except FreshnessError:
                stale_discarded += 1

        # Totals
        if tot_line is not None and item.get("total_bookmaker"):
            try:
                is_fresh(totals_last_update, commence_dt, sport_upper, "odds")
                stmt = add_snapshot_idempotent(
                    "totals", item["total_bookmaker"], totals_last_update,
                    total_line=tot_line, over_ml=over_ml, under_ml=under_ml
                )
                res = await db.execute(stmt)
                if res.rowcount > 0:
                    snapshots_saved += 1
            except FreshnessError:
                stale_discarded += 1
        # No se emite una predicción hasta disponer de modelos y features
        # entrenados y validados con snapshots temporales sin fuga de futuro.
        # Los ratings NFL anteriores eran constantes manuales y el CSV NBA
        # contenía estadísticas de fin de temporada aplicadas a juegos pasados.
        continue

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error("Failed to commit odds snapshot sync", error=str(e))
        return {
            "status": "error",
            "sport": sport_upper,
            "error_type": "commit_error",
            "detail": str(e)
        }

    logger.info("Real market data synced", sport=sport_upper, games=synced_games, snapshots=snapshots_saved, stale=stale_discarded)
    if snapshots_saved == 0:
        sync_status = "no_usable_data"
    elif unknown_teams or unmatched_events or ambiguous_events or identity_conflicts or stale_discarded:
        sync_status = "partial"
    else:
        sync_status = "success"
    return {
        "status": sync_status,
        "sport": sport_upper,
        "games_synced": synced_games,
        "snapshots_saved": snapshots_saved,
        "stale_discarded": stale_discarded,
        "unknown_teams": unknown_teams,
        "unmatched_events": unmatched_events,
        "ambiguous_events": ambiguous_events,
        "identity_conflicts": identity_conflicts,
        "predictions_synced": 0,
        "scores_updated_from_espn": espn_result.get("updated", 0),
        "note": "Predictions disabled until temporally valid, trained models are available.",
    }
