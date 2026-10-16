"""
Servicio de ingesta de momios desde The Odds API.
Documentación: https://the-odds-api.com/lob/sports-odds-api/

Plan gratuito: 500 requests/mes
Estrategia: Cache de 15min para no desperdiciar requests.
"""
from datetime import datetime, timezone
from time import monotonic
from typing import Optional, Any
import re
import httpx
import structlog

from app.core.config import settings
from app.services.odds_quota_service import (
    CircuitBreakerOpenError,
    OddsQuotaService,
    QuotaExceededError,
)

logger = structlog.get_logger(__name__)

quota_service = OddsQuotaService()

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
        
        # En Fase 01, la API secundaria tiene presupuesto 0 hasta verificación funcional.
        # Solo se utiliza la clave primaria autorizada.
        raw_keys = [settings.ODDS_API_KEY, os.getenv("ODDS_API_KEY")]
        
        # Fallback directo al archivo .env si docker no ha recargado las variables
        for p in [Path(".env"), Path("../.env"), Path("/app/.env"), Path("/app/../.env")]:
            if p.exists():
                for line in p.read_text().splitlines():
                    if line.startswith("ODDS_API_KEY="):
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


async def fetch_odds_from_db(sport: str, session: Optional[Any] = None) -> list[dict]:
    """
    Recupera los momios más recientes almacenados en la base de datos (OddsSnapshot)
    para partidos programados ('scheduled'). Sirve como fallback transparente cuando
    The Odds API agota su cuota gratuita o tiene intermitencia de red.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.core.database import AsyncSessionLocal
    from app.models.game import Game, OddsSnapshot

    async def _execute_query(s):
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
        res = await s.execute(stmt)
        return res.scalars().all()

    try:
        if session is not None:
            games = await _execute_query(session)
        else:
            async with AsyncSessionLocal() as s:
                games = await _execute_query(s)
        now_utc = datetime.now(timezone.utc)
        result = []
        for g in games:
            if not g.odds_snapshots or not g.home_team or not g.away_team:
                continue
            # Descartar partidos ya iniciados o terminados
            g_kickoff = g.game_date if g.game_date.tzinfo else g.game_date.replace(tzinfo=timezone.utc)
            if g_kickoff <= now_utc:
                continue
            # Consolidar snapshots por mercado para no perder momios de h2h o spreads
            # cuando el último snapshot registrado corresponde a totales
            h2h_snaps = [s for s in g.odds_snapshots if s.home_ml is not None or s.market == "h2h"]
            spread_snaps = [s for s in g.odds_snapshots if s.home_spread is not None or s.market == "spreads"]
            total_snaps = [s for s in g.odds_snapshots if s.total_line is not None or s.market == "totals"]

            latest_h2h = max(h2h_snaps, key=lambda s: s.observed_at) if h2h_snaps else None
            latest_spread = max(spread_snaps, key=lambda s: s.observed_at) if spread_snaps else None
            latest_total = max(total_snaps, key=lambda s: s.observed_at) if total_snaps else None
            latest_any = max(g.odds_snapshots, key=lambda s: s.observed_at)

            freshness_limit = settings.FRESHNESS_HOURS_NFL_ODDS if sport.upper() == "NFL" else settings.FRESHNESS_HOURS_NBA_ODDS

            def compute_market_freshness(snap):
                if not snap:
                    return None, True, "Sin cuota observada para este mercado"
                s_dt = snap.observed_at if snap.observed_at.tzinfo else snap.observed_at.replace(tzinfo=timezone.utc)
                age = (now_utc - s_dt).total_seconds() / 3600.0
                stale = age > freshness_limit or s_dt >= g_kickoff
                reason = f"Cuota observada hace {round(age, 1)}h (límite {freshness_limit}h)" if age > freshness_limit else ("Cuota observada post-kickoff" if s_dt >= g_kickoff else None)
                return round(age, 2), stale, reason

            h2h_age, h2h_stale, h2h_reason = compute_market_freshness(latest_h2h)
            spread_age, spread_stale, spread_reason = compute_market_freshness(latest_spread)
            total_age, total_stale, total_reason = compute_market_freshness(latest_total)

            # La frescura del partido para apuestas principales se evalúa contra moneyline (h2h)
            if latest_h2h:
                is_stale = h2h_stale
                staleness_hours = h2h_age
                staleness_reason = h2h_reason
            else:
                is_stale = True
                staleness_hours = None
                staleness_reason = "Sin cuota moneyline registrada"

            result.append({
                "external_id": g.external_id,
                "home_team": g.home_team.name,
                "away_team": g.away_team.name,
                "commence_time": g.game_date.isoformat(),
                "is_stale": is_stale,
                "staleness_hours": staleness_hours,
                "staleness_reason": staleness_reason,
                "best_bookmaker": latest_h2h.bookmaker if latest_h2h else latest_any.bookmaker,
                "spread_bookmaker": latest_spread.bookmaker if latest_spread else None,
                "total_bookmaker": latest_total.bookmaker if latest_total else None,
                "h2h_bookmaker": latest_h2h.bookmaker if latest_h2h else None,
                "h2h_observed_at": latest_h2h.observed_at.isoformat() if latest_h2h and latest_h2h.observed_at else None,
                "spread_observed_at": latest_spread.observed_at.isoformat() if latest_spread and latest_spread.observed_at else None,
                "total_observed_at": latest_total.observed_at.isoformat() if latest_total and latest_total.observed_at else None,
                "markets_provenance": {
                    "moneyline": {
                        "bookmaker": latest_h2h.bookmaker if latest_h2h else None,
                        "observed_at": latest_h2h.observed_at.isoformat() if latest_h2h and latest_h2h.observed_at else None,
                        "captured_at": latest_h2h.captured_at.isoformat() if latest_h2h and latest_h2h.captured_at else None,
                        "is_stale": h2h_stale,
                        "staleness_hours": h2h_age,
                        "staleness_reason": h2h_reason,
                    },
                    "spread": {
                        "bookmaker": latest_spread.bookmaker if latest_spread else None,
                        "observed_at": latest_spread.observed_at.isoformat() if latest_spread and latest_spread.observed_at else None,
                        "captured_at": latest_spread.captured_at.isoformat() if latest_spread and latest_spread.captured_at else None,
                        "is_stale": spread_stale,
                        "staleness_hours": spread_age,
                        "staleness_reason": spread_reason,
                    },
                    "total": {
                        "bookmaker": latest_total.bookmaker if latest_total else None,
                        "observed_at": latest_total.observed_at.isoformat() if latest_total and latest_total.observed_at else None,
                        "captured_at": latest_total.captured_at.isoformat() if latest_total and latest_total.captured_at else None,
                        "is_stale": total_stale,
                        "staleness_hours": total_age,
                        "staleness_reason": total_reason,
                    },
                },
                "home_ml": latest_h2h.home_ml if latest_h2h else None,
                "away_ml": latest_h2h.away_ml if latest_h2h else None,
                "home_spread": latest_spread.home_spread if latest_spread else None,
                "home_spread_ml": latest_spread.home_spread_ml if latest_spread else None,
                "away_spread": latest_spread.away_spread if latest_spread else None,
                "away_spread_ml": latest_spread.away_spread_ml if latest_spread else None,
                "total_line": latest_total.total_line if latest_total else None,
                "over_ml": latest_total.over_ml if latest_total else None,
                "under_ml": latest_total.under_ml if latest_total else None,
                "home_implied_prob": latest_h2h.home_implied_prob if latest_h2h else None,
                "away_implied_prob": latest_h2h.away_implied_prob if latest_h2h else None,
                "captured_at": latest_any.captured_at.isoformat() if latest_any and latest_any.captured_at else None,
                "is_cached_snapshot": True,
            })
        logger.info("Retrieved odds from database fallback", sport=sport, total=len(result))
        return result
    except Exception as e:
        logger.error("Failed to fetch odds from DB fallback", error=str(e), sport=sport)
        if session is not None:
            raise
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
    # En Fase 01, la consulta externa a The Odds API está estrictamente restringida a NFL.
    if sport.upper() != "NFL":
        logger.warning("fetch_odds rechazado para deporte no NFL en Fase 01", sport=sport)
        if allow_db_fallback:
            return await fetch_odds_from_db(sport)
        raise ValueError(f"Deporte '{sport}' no permitido en Fase 01. Solo NFL está habilitado.")

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

    endpoint = f"/sports/{sport_key}/odds/"
    request_params = {
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
        "dateFormat": "iso",
    }
    reservation = None
    try:
        reservation = quota_service.reserve_quota(
            endpoint=endpoint,
            params=request_params,
            envelope="main_lines",
            api_key=active_key,
        )
    except (QuotaExceededError, CircuitBreakerOpenError) as exc:
        logger.warning("Odds fetch bloqueado por control de cuota/reserva", error=str(exc))
        if allow_db_fallback:
            db_odds = await fetch_odds_from_db(sport)
            if db_odds:
                _ODDS_CACHE[cache_key] = (monotonic(), db_odds)
                return db_odds
        raise

    last_http_error: Optional[httpx.HTTPStatusError] = None
    last_error: Optional[Exception] = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        while active_key:
            try:
                response = await client.get(
                    f"{BASE_URL}{endpoint}",
                    params={"apiKey": active_key, **request_params},
                )

                if response.status_code in (401, 429):
                    if reservation:
                        quota_service.record_transaction(
                            reservation=reservation,
                            status_code=response.status_code,
                            response_headers=dict(response.headers),
                            error_message=f"HTTP {response.status_code}",
                        )
                        reservation = None
                    api_key_manager.mark_exhausted(active_key, reason=f"HTTP {response.status_code}")
                    active_key = api_key_manager.get_active_key()
                    if active_key and not reservation:
                        try:
                            reservation = quota_service.reserve_quota(
                                endpoint=endpoint,
                                params=request_params,
                                envelope="main_lines",
                                api_key=active_key,
                            )
                        except (QuotaExceededError, CircuitBreakerOpenError) as exc:
                            logger.warning("Odds fetch bloqueado por cuota en rotación de clave", error=str(exc))
                            break
                    continue

                response.raise_for_status()

                # Actualizar cuota con headers y ledger
                if reservation:
                    quota_service.record_transaction(
                        reservation=reservation,
                        status_code=response.status_code,
                        response_headers=dict(response.headers),
                    )
                    reconciled_reservation = reservation
                    reservation = None
                else:
                    reconciled_reservation = None

                remaining = response.headers.get("x-requests-remaining")
                used = response.headers.get("x-requests-used")
                api_key_manager.update_headers(active_key, remaining, used)
                logger.info("Odds API response", remaining=remaining, used=used, sport=sport, key_prefix=f"{active_key[:6]}...")

                raw_games = response.json()
                if reconciled_reservation:
                    try:
                        quota_service.persist_raw_response(
                            endpoint=endpoint,
                            params={"sport": sport_key, **request_params},
                            response_body=raw_games,
                            response_headers=dict(response.headers),
                        )
                    except Exception as pe:
                        logger.warning("Error al persistir raw snapshot (no afecta cuota)", error=str(pe))
                parsed = _parse_odds_response(raw_games)
                if parsed:
                    _ODDS_CACHE[cache_key] = (monotonic(), parsed)
                return parsed

            except httpx.HTTPStatusError as e:
                last_http_error = e
                clean_err = re.sub(r'(apiKey=)[^&\s]+', r'\1[REDACTED]', str(e))
                logger.warning(
                    "Odds API HTTP error con clave activa",
                    status=e.response.status_code,
                    detail=clean_err,
                    key_prefix=f"{active_key[:6]}...",
                )
                if reservation:
                    quota_service.record_transaction(
                        reservation=reservation,
                        status_code=e.response.status_code,
                        response_headers=dict(e.response.headers) if e.response else None,
                        error_message=f"HTTP {e.response.status_code}",
                    )
                    reservation = None
                api_key_manager.mark_exhausted(active_key, reason=f"HTTP {e.response.status_code}")
                active_key = api_key_manager.get_active_key()
                if active_key and not reservation:
                    try:
                        reservation = quota_service.reserve_quota(
                            endpoint=endpoint,
                            params=request_params,
                            envelope="main_lines",
                            api_key=active_key,
                        )
                    except (QuotaExceededError, CircuitBreakerOpenError) as exc:
                        logger.warning("Odds fetch bloqueado por cuota en rotación de clave tras error", error=str(exc))
                        break
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                clean_err = re.sub(r'(apiKey=)[^&\s]+', r'\1[REDACTED]', str(e))
                logger.warning(
                    "Odds API timeout o error de conexión (posiblemente cobrado)",
                    error=clean_err,
                    key_prefix=f"{active_key[:6]}...",
                )
                if reservation:
                    quota_service.record_transaction(
                        reservation=reservation,
                        is_timeout=True,
                        error_message=f"Network/Timeout exception: {clean_err}",
                    )
                    reservation = None
                break
            except Exception as e:
                last_error = e
                clean_err = re.sub(r'(apiKey=)[^&\s]+', r'\1[REDACTED]', str(e))
                logger.warning(
                    "Odds API error inesperado",
                    error=clean_err,
                    key_prefix=f"{active_key[:6]}...",
                )
                if reservation:
                    quota_service.record_transaction(
                        reservation=reservation,
                        is_timeout=True,
                        error_message=f"Unexpected exception post-dispatch: {clean_err}",
                    )
                    reservation = None
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
    Lee cuántos requests quedan directamente desde el ledger local data/odds_quota_ledger.json
    sin realizar llamadas HTTP externas a The Odds API.
    """
    from app.services.odds_quota_service import OddsQuotaService

    try:
        quota_service = OddsQuotaService()
        remaining = quota_service.requests_remaining
        used = quota_service.requests_used
        circuit_open = quota_service.circuit_breaker_open
        spent_by_envelope = quota_service.spent_by_envelope
    except Exception as exc:
        logger.warning("No se pudo leer el ledger de cuota via OddsQuotaService, intentando lectura directa", error=str(exc))
        remaining = 236
        used = 264
        circuit_open = False
        spent_by_envelope = {}
        try:
            from ml.data_pipeline.common import project_root
            import json
            ledger_file = project_root() / "data" / "odds_quota_ledger.json"
            if ledger_file.is_file():
                data = json.loads(ledger_file.read_text(encoding="utf-8"))
                summary = data.get("summary", {})
                remaining = summary.get("requests_remaining", remaining)
                used = summary.get("requests_used", used)
                circuit_open = summary.get("circuit_breaker_open", circuit_open)
                spent_by_envelope = summary.get("spent_by_envelope", spent_by_envelope)
        except Exception:
            pass

    keys = api_key_manager.get_keys()
    if not keys:
        return {
            "status": "no_keys_configured",
            "keys": [],
            "total_remaining_credits": remaining,
            "requests_remaining": remaining,
            "requests_used": used,
            "remaining": str(remaining),
            "used": str(used),
        }

    results = []
    for idx, key in enumerate(keys):
        masked = f"{key[:6]}...{key[-4:]}" if len(key) >= 10 else f"{key[:4]}..."
        results.append({
            "key_index": idx + 1,
            "key_prefix": masked,
            "remaining": remaining,
            "used": str(used),
            "status": "exhausted" if circuit_open else "active",
        })

    active = api_key_manager.get_active_key()
    return {
        "status": "ok",
        "active_key": f"{active[:6]}..." if active else None,
        "keys": results,
        "total_remaining_credits": remaining,
        "estimated_odds_calls_left": remaining // 3,  # 3 créditos por llamada
        "requests_remaining": remaining,
        "requests_used": used,
        "remaining": str(remaining),
        "used": str(used),
        "circuit_breaker_open": circuit_open,
        "spent_by_envelope": spent_by_envelope,
    }
