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
API_KEY = settings.ODDS_API_KEY

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


class OddsApiKeyManager:
    """
    Gestiona la rotación y fallback inteligente entre múltiples claves de The Odds API.
    Monitorea los encabezados x-requests-remaining y conmuta automáticamente
    si una clave se agota (OUT_OF_USAGE_CREDITS / 401).
    """

    def __init__(self):
        self._keys: list[str] = []
        self._exhausted: set[str] = set()
        self._current_index: int = 0
        self._reload_keys()

    def _reload_keys(self):
        import os
        from pathlib import Path
        
        raw_keys = [settings.ODDS_API_KEY, getattr(settings, "ODDS_API_KEY_2", ""), os.getenv("ODDS_API_KEY"), os.getenv("ODDS_API_KEY_2")]
        
        # Fallback directo al archivo .env si docker no ha recargado las variables
        for p in [Path(".env"), Path("../.env"), Path("/app/.env"), Path("/app/../.env")]:
            if p.exists():
                for line in p.read_text().splitlines():
                    if line.startswith("ODDS_API_KEY=") or line.startswith("ODDS_API_KEY_2="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            raw_keys.append(val)

        # Preservar orden único
        seen = set()
        deduped = []
        for k in raw_keys:
            if k and k.strip() and k.strip() not in seen:
                seen.add(k.strip())
                deduped.append(k.strip())
        self._keys = deduped

    def get_keys(self) -> list[str]:
        if not self._keys:
            self._reload_keys()
        return self._keys

    def get_active_key(self) -> Optional[str]:
        keys = self.get_keys()
        if not keys:
            return None
        for i in range(len(keys)):
            idx = (self._current_index + i) % len(keys)
            k = keys[idx]
            if k not in self._exhausted:
                self._current_index = idx
                return k
        return None

    def mark_exhausted(self, key: str, reason: str = "quota_exhausted"):
        logger.warning(
            "Marcando clave de The Odds API como agotada",
            key_prefix=f"{key[:6]}...",
            reason=reason,
        )
        self._exhausted.add(key)
        keys = self.get_keys()
        for i, k in enumerate(keys):
            if k not in self._exhausted:
                self._current_index = i
                logger.info("Rotado con éxito a nueva clave activa", new_key_prefix=f"{k[:6]}...")
                return
        logger.error("Todas las claves de The Odds API están agotadas para este periodo")

    def update_headers(self, key: str, remaining: Optional[str], used: Optional[str]):
        if remaining is not None:
            try:
                rem_int = int(remaining)
                if rem_int <= 3:  # Menos de 1 llamada estándar (3 mercados = 3 créditos)
                    self.mark_exhausted(key, reason=f"remaining_credits_low ({rem_int})")
            except ValueError:
                pass


api_key_manager = OddsApiKeyManager()


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


async def fetch_odds_from_db(sport: str) -> list[dict]:
    """
    Recupera los momios más recientes almacenados en la base de datos (OddsSnapshot)
    para partidos programados ('scheduled'). Sirve como fallback transparente cuando
    The Odds API agota su cuota gratuita o tiene intermitencia de red.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.core.database import AsyncSessionLocal
    from app.models.game import Game, OddsSnapshot

    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Game)
                .where(Game.sport == sport.upper(), Game.status == "scheduled")
                .options(
                    selectinload(Game.home_team),
                    selectinload(Game.away_team),
                    selectinload(Game.odds_snapshots),
                )
                .order_by(Game.game_date.asc())
            )
            res = await session.execute(stmt)
            games = res.scalars().all()
            result = []
            for g in games:
                if not g.odds_snapshots or not g.home_team or not g.away_team:
                    continue
                latest = max(g.odds_snapshots, key=lambda s: s.observed_at)
                result.append({
                    "external_id": g.external_id,
                    "home_team": g.home_team.name,
                    "away_team": g.away_team.name,
                    "commence_time": g.game_date.isoformat(),
                    "best_bookmaker": latest.bookmaker,
                    "spread_bookmaker": latest.bookmaker,
                    "total_bookmaker": latest.bookmaker,
                    "home_ml": latest.home_ml,
                    "away_ml": latest.away_ml,
                    "home_spread": latest.home_spread,
                    "home_spread_ml": latest.home_spread_ml,
                    "away_spread": latest.away_spread,
                    "away_spread_ml": latest.away_spread_ml,
                    "total_line": latest.total_line,
                    "over_ml": latest.over_ml,
                    "under_ml": latest.under_ml,
                    "home_implied_prob": latest.home_implied_prob,
                    "away_implied_prob": latest.away_implied_prob,
                    "captured_at": latest.captured_at.isoformat() if latest.captured_at else None,
                    "is_cached_snapshot": True,
                })
            logger.info("Retrieved odds from database fallback", sport=sport, total=len(result))
            return result
    except Exception as e:
        logger.error("Failed to fetch odds from DB fallback", error=str(e), sport=sport)
        return []


async def fetch_odds(
    sport: str = "NBA",
    regions: str = "us",
    markets: str = "h2h,spreads,totals",
    odds_format: str = "american",
    allow_db_fallback: bool = False,
) -> list[dict]:
    """
    Obtiene momios en tiempo real para todos los partidos del deporte dado.
    Utiliza rotación multi-clave inteligente y fallback opcional a PostgreSQL
    cuando todas las cuotas externas se agotan o fallan.
    
    Returns: Lista de partidos con estructura estandarizada.
    """
    if API_KEY == "":
        logger.warning("ODDS_API_KEY not configured — skipping odds fetch")
        from app.services.freshness_service import ConfigurationError
        raise ConfigurationError("ODDS_API_KEY not configured")

    sport_key = SPORT_KEYS.get(sport.upper(), sport)
    cache_key = (sport_key, regions, markets, odds_format)
    cached = _ODDS_CACHE.get(cache_key)
    if cached and monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    active_key = API_KEY or api_key_manager.get_active_key()
    if not active_key:
        if allow_db_fallback:
            logger.warning("No hay claves activas con cuota disponible — recurriendo a BD", sport=sport)
            db_odds = await fetch_odds_from_db(sport)
            if db_odds:
                _ODDS_CACHE[cache_key] = (monotonic(), db_odds)
                return db_odds
        from app.services.freshness_service import ConfigurationError
        raise ConfigurationError("ODDS_API_KEY not configured")

    last_http_error: Optional[httpx.HTTPStatusError] = None
    last_error: Optional[Exception] = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        while active_key:
            try:
                response = await client.get(
                    f"{BASE_URL}/sports/{sport_key}/odds/",
                    params={
                        "apiKey": active_key,
                        "regions": regions,
                        "markets": markets,
                        "oddsFormat": odds_format,
                        "dateFormat": "iso",
                    },
                )

                if response.status_code in (401, 429):
                    api_key_manager.mark_exhausted(active_key, reason=f"HTTP {response.status_code}")
                    active_key = api_key_manager.get_active_key()
                    continue

                response.raise_for_status()

                # Actualizar cuota con headers
                remaining = response.headers.get("x-requests-remaining")
                used = response.headers.get("x-requests-used")
                api_key_manager.update_headers(active_key, remaining, used)
                logger.info("Odds API response", remaining=remaining, used=used, sport=sport, key_prefix=f"{active_key[:6]}...")

                raw_games = response.json()
                parsed = _parse_odds_response(raw_games)
                if parsed:
                    _ODDS_CACHE[cache_key] = (monotonic(), parsed)
                return parsed

            except httpx.HTTPStatusError as e:
                last_http_error = e
                logger.warning(
                    "Odds API HTTP error con clave activa",
                    status=e.response.status_code,
                    detail=str(e),
                    key_prefix=f"{active_key[:6]}...",
                )
                api_key_manager.mark_exhausted(active_key, reason=f"HTTP {e.response.status_code}")
                active_key = api_key_manager.get_active_key()
            except Exception as e:
                last_error = e
                logger.warning(
                    "Odds API error inesperado",
                    error=str(e),
                    key_prefix=f"{active_key[:6]}...",
                )
                break

    # Si todas las claves fallaron, fallback opcional a la base de datos
    if allow_db_fallback:
        db_odds = await fetch_odds_from_db(sport)
        if db_odds:
            _ODDS_CACHE[cache_key] = (monotonic(), db_odds)
            return db_odds

    if last_http_error is not None:
        from app.services.freshness_service import ProviderError
        raise ProviderError(f"HTTP error {last_http_error.response.status_code}") from last_http_error
    if last_error is not None:
        from app.services.freshness_service import ProviderError
        raise ProviderError("Unexpected provider error") from last_error

    return []


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
    """
    Consulta cuántos requests quedan en el plan gratuito para todas las claves configuradas.
    El endpoint /sports/ no consume cuota (costo: 0 créditos).
    """
    keys = api_key_manager.get_keys()
    if not keys:
        return {"status": "no_keys_configured", "keys": [], "total_remaining_credits": 0}

    results = []
    total_remaining = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        for idx, key in enumerate(keys):
            try:
                response = await client.get(
                    f"{BASE_URL}/sports/",
                    params={"apiKey": key},
                )
                rem = response.headers.get("x-requests-remaining")
                used = response.headers.get("x-requests-used")
                rem_int = int(rem) if rem is not None and rem.isdigit() else 0
                total_remaining += rem_int
                is_exhausted = rem_int <= 3
                if is_exhausted:
                    api_key_manager.mark_exhausted(key, reason="quota_check_empty")
                results.append({
                    "key_index": idx + 1,
                    "key_prefix": f"{key[:6]}...{key[-4:]}",
                    "remaining": rem_int,
                    "used": used,
                    "status": "exhausted" if is_exhausted else "active",
                })
            except Exception as e:
                results.append({
                    "key_index": idx + 1,
                    "key_prefix": f"{key[:6]}...",
                    "error": str(e),
                    "status": "error",
                })

    active = api_key_manager.get_active_key()
    return {
        "status": "ok",
        "active_key": f"{active[:6]}..." if active else None,
        "keys": results,
        "total_remaining_credits": total_remaining,
        "estimated_odds_calls_left": total_remaining // 3,  # 3 créditos por llamada
    }
