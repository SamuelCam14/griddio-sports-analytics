"""Ingesta cuotas publicadas por The Odds API sin fabricar valores ausentes."""
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.core.config import settings
from app.models.game import Team, Game, OddsSnapshot, Prediction
from app.services.odds_service import fetch_odds
from app.services.math.odds_converter import (
    american_to_decimal,
    american_to_implied_prob,
    remove_vig,
)

logger = structlog.get_logger(__name__)

BASE_URL = settings.ODDS_API_BASE_URL
API_KEY = settings.ODDS_API_KEY

async def fetch_real_player_props(event_id: str, sport: str = "NFL") -> List[Dict[str, Any]]:
    """
    Obtiene líneas de Player Props en VIVO desde The Odds API para un partido específico.
    Si las casas de apuestas aún no publican props (o si el plan no tiene acceso),
    retorna lista vacía []. NUNCA inventa jugadores ni líneas ficticias.
    """
    if not API_KEY or not event_id:
        return []

    sport_key = "americanfootball_nfl" if sport.upper() == "NFL" else "basketball_nba"
    markets = (
        "player_pass_yds,player_rush_yds,player_reception_yds,player_receptions"
        if sport.upper() == "NFL"
        else "player_points,player_rebounds,player_assists"
    )

    url = f"{BASE_URL}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": markets,
        "oddsFormat": "american",
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning("Player props not available from API", status=resp.status_code, event_id=event_id)
                return []

            data = resp.json()
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
                            "player_name": player,
                            "prop_type": prop_type,
                            "market_line": line,
                            "over_odds_american": price_over,
                            "under_odds_american": under_data["price"],
                            "over_implied_probability": round(p_over, 4),
                            "under_implied_probability": round(p_under, 4),
                            "bookmaker": book_title,
                            "data_source": "The Odds API",
                            "reasoning": "Cuotas de mercado publicadas; no hay proyección de jugador validada.",
                        })

            return parsed_props[:100]

    except Exception as e:
        logger.error("Error fetching live player props", event_id=event_id, error=str(e))
        return []


async def sync_real_games(sport: str, db: AsyncSession) -> Dict[str, Any]:
    """
    Sincroniza partidos 100% reales desde The Odds API a la base de datos PostgreSQL.
    Persiste solo cuotas recibidas; no estima ni rellena mercados ausentes.
    """
    sport_upper = sport.upper()
    from app.services.freshness_service import ProviderError
    
    try:
        odds_list = await fetch_odds(sport=sport_upper)
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
    name_to_team = {t.name.lower(): t for t in teams}

    def find_team(query_name: str) -> Optional[Team]:
        q = query_name.lower().strip()
        if q in name_to_team:
            return name_to_team[q]
        for t in teams:
            if t.name.lower() in q or q in t.name.lower():
                return t
            if t.city and t.city.lower() in q:
                return t
        return None

    synced_games = 0
    stale_discarded = 0
    snapshots_saved = 0
    unknown_teams = 0

    for item in odds_list:
        home_team = find_team(item["home_team"])
        away_team = find_team(item["away_team"])

        if not home_team or not away_team:
            logger.warning("Could not match teams in DB", home=item["home_team"], away=item["away_team"])
            unknown_teams += 1
            continue

        ext_id = item["external_id"]
        commence_dt = datetime.fromisoformat(item["commence_time"].replace("Z", "+00:00"))

        # 1. Upsert Game
        game_res = await db.execute(select(Game).where(Game.external_id == ext_id))
        game = game_res.scalar_one_or_none()

        if not game:
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
            )
            db.add(game)
            await db.flush()
        else:
            game.game_date = commence_dt
            game.home_team_id = home_team.id
            game.away_team_id = away_team.id
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
    return {
        "status": "success",
        "sport": sport_upper,
        "games_synced": synced_games,
        "snapshots_saved": snapshots_saved,
        "stale_discarded": stale_discarded,
        "unknown_teams": unknown_teams,
        "predictions_synced": 0,
        "scores_updated_from_espn": espn_result.get("updated", 0),
        "note": "Predictions disabled until temporally valid, trained models are available.",
    }
