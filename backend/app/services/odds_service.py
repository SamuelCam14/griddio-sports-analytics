"""
Servicio de ingesta de momios desde The Odds API.
Documentación: https://the-odds-api.com/lob/sports-odds-api/

Plan gratuito: 500 requests/mes
Estrategia: Cache de 15min para no desperdiciar requests.
"""
from datetime import datetime, timezone
from time import monotonic
from typing import Optional

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

BASE_URL = settings.ODDS_API_BASE_URL
API_KEY  = settings.ODDS_API_KEY

# Sports keys en The Odds API
SPORT_KEYS = {
    "NBA": "basketball_nba",
    "NFL": "americanfootball_nfl",
    "MLB": "baseball_mlb",
    "NHL": "icehockey_nhl",
    "NCAAB": "basketball_ncaab",
    "NCAAF": "americanfootball_ncaaf",
}

# Bookmakers a priorizar (los más líquidos en USA)
PRIORITY_BOOKMAKERS = ["draftkings", "fanduel", "betmgm", "caesars", "pointsbet"]
_CACHE_TTL_SECONDS = 900
_ODDS_CACHE: dict[tuple[str, str, str, str], tuple[float, list[dict]]] = {}


def _american_to_decimal(american: int) -> float:
    """Convierte momio americano a decimal."""
    if american > 0:
        return (american / 100) + 1
    return (100 / abs(american)) + 1


def _implied_probability(american: int) -> float:
    """
    Calcula probabilidad implícita (con vig incluido).
    Para remover el vig: normalizar por la suma de probabilidades implícitas.
    """
    if american > 0:
        return 100 / (american + 100)
    return abs(american) / (abs(american) + 100)


def _remove_vig(prob_home: float, prob_away: float) -> tuple[float, float]:
    """
    Elimina el jugo (vig) de las probabilidades implícitas.
    La suma bruta suele ser ~1.05 (5% vig).
    """
    total = prob_home + prob_away
    return prob_home / total, prob_away / total


async def fetch_odds(
    sport: str = "NBA",
    regions: str = "us",
    markets: str = "h2h,spreads,totals",
    odds_format: str = "american",
) -> list[dict]:
    """
    Obtiene momios en tiempo real para todos los partidos del deporte dado.
    
    Returns: Lista de partidos con estructura estandarizada.
    """
    if not API_KEY:
        logger.warning("ODDS_API_KEY not configured — skipping odds fetch")
        from app.services.freshness_service import ConfigurationError
        raise ConfigurationError("ODDS_API_KEY not configured")

    sport_key = SPORT_KEYS.get(sport.upper(), sport)
    cache_key = (sport_key, regions, markets, odds_format)
    cached = _ODDS_CACHE.get(cache_key)
    if cached and monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/sports/{sport_key}/odds/",
                params={
                    "apiKey": API_KEY,
                    "regions": regions,
                    "markets": markets,
                    "oddsFormat": odds_format,
                    "dateFormat": "iso",
                },
            )
            response.raise_for_status()

            # Log requests restantes (plan gratuito)
            remaining = response.headers.get("x-requests-remaining", "unknown")
            used = response.headers.get("x-requests-used", "unknown")
            logger.info("Odds API response", remaining=remaining, used=used, sport=sport)

            raw_games = response.json()
            parsed = _parse_odds_response(raw_games)
            if parsed:
                _ODDS_CACHE[cache_key] = (monotonic(), parsed)
            return parsed

        except httpx.HTTPStatusError as e:
            logger.error("Odds API HTTP error", status=e.response.status_code, detail=str(e))
            from app.services.freshness_service import ProviderError
            raise ProviderError(f"HTTP error {e.response.status_code}: {e}")
        except Exception as e:
            logger.error("Odds API unexpected error", error=str(e))
            from app.services.freshness_service import ProviderError
            raise ProviderError(f"Unexpected provider error: {e}")


def _parse_odds_response(raw_games: list[dict]) -> list[dict]:
    """
    Parsea la respuesta cruda de The Odds API a formato estandarizado.
    Selecciona el mejor momio disponible (best line shopping).
    """
    parsed = []

    for game in raw_games:
        game_id    = game.get("id")
        home_team  = game.get("home_team")
        away_team  = game.get("away_team")
        commence   = game.get("commence_time")

        # Inicializar best lines (line shopping)
        best_home_ml = None
        best_away_ml = None
        best_home_spread = None
        best_home_spread_ml = None
        best_away_spread = None
        total_line  = None
        over_ml     = None
        under_ml    = None
        best_book   = None
        best_home_spread_price = None
        best_away_spread_price = None
        spread_book = None
        total_book = None
        
        # Track last_update per market
        ml_last_update = None
        spread_last_update = None
        totals_last_update = None

        for bookmaker in game.get("bookmakers", []):
            book_key = bookmaker.get("key")
            if book_key not in PRIORITY_BOOKMAKERS:
                continue
                
            book_last_update = bookmaker.get("last_update")

            for market in bookmaker.get("markets", []):
                market_key = market.get("key")
                outcomes = {o["name"]: o for o in market.get("outcomes", [])}

                if market_key == "h2h":
                    h_ml = outcomes.get(home_team, {}).get("price")
                    a_ml = outcomes.get(away_team, {}).get("price")
                    if h_ml is not None and a_ml is not None:
                        if best_home_ml is None or h_ml > best_home_ml:
                            best_home_ml = h_ml
                            best_away_ml = a_ml
                            best_book = book_key
                            ml_last_update = book_last_update

                elif market_key == "spreads":
                    home_out = outcomes.get(home_team, {})
                    away_out = outcomes.get(away_team, {})
                    h_price = home_out.get("price")
                    a_price = away_out.get("price")
                    if h_price is not None and a_price is not None:
                        if best_home_spread_price is None or h_price > best_home_spread_price:
                            best_home_spread_price = h_price
                            best_home_spread = home_out.get("point")
                            best_home_spread_ml = h_price
                            best_away_spread = away_out.get("point")
                            best_away_spread_price = a_price
                            spread_book = book_key
                            spread_last_update = book_last_update

                elif market_key == "totals":
                    over_out = outcomes.get("Over", {})
                    under_out = outcomes.get("Under", {})
                    o_price = over_out.get("price")
                    u_price = under_out.get("price")
                    if o_price is not None and u_price is not None:
                        # Grab the first valid line or highest over
                        if total_line is None or o_price > (over_ml or -9999):
                            total_line = over_out.get("point")
                            over_ml = o_price
                            under_ml = u_price
                            total_book = book_key
                            totals_last_update = book_last_update

        # Calcular probabilidades implícitas sin vig
        home_prob_raw = _implied_probability(best_home_ml) if best_home_ml else None
        away_prob_raw = _implied_probability(best_away_ml) if best_away_ml else None
        home_prob, away_prob = (None, None)
        if home_prob_raw and away_prob_raw:
            home_prob, away_prob = _remove_vig(home_prob_raw, away_prob_raw)

        parsed.append({
            "external_id":        game_id,
            "home_team":          home_team,
            "away_team":          away_team,
            "commence_time":      commence,
            "best_bookmaker":     best_book,
            "spread_bookmaker":   spread_book,
            "total_bookmaker":    total_book,
            "home_ml":            best_home_ml,
            "away_ml":            best_away_ml,
            "home_spread":        best_home_spread,
            "home_spread_ml":     best_home_spread_ml,
            "away_spread":        best_away_spread,
            "away_spread_ml":     best_away_spread_price,
            "total_line":         total_line,
            "over_ml":            over_ml,
            "under_ml":           under_ml,
            "home_implied_prob":  round(home_prob, 4) if home_prob else None,
            "away_implied_prob":  round(away_prob, 4) if away_prob else None,
            "captured_at":        datetime.now(timezone.utc).isoformat(),
            "ml_last_update":     ml_last_update,
            "spread_last_update": spread_last_update,
            "totals_last_update": totals_last_update,
        })

    logger.info("Odds parsed", total_games=len(parsed))
    return parsed


async def fetch_remaining_requests() -> dict:
    """Consulta cuántos requests quedan en el plan gratuito (no consume quota)."""
    if not API_KEY:
        return {"remaining": "unknown", "used": "unknown"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/sports/",
                params={"apiKey": API_KEY},
            )
            return {
                "remaining": response.headers.get("x-requests-remaining"),
                "used": response.headers.get("x-requests-used"),
            }
        except Exception as e:
            return {"error": str(e)}
