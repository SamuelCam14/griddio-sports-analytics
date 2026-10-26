"""
Servicio de ingesta de momios desde The Odds API.
Documentación: https://the-odds-api.com/lob/sports-odds-api/

Plan gratuito: 500 requests/mes
Estrategia: Cache de 15min para no desperdiciar requests.
"""
from datetime import datetime, timezone
from time import monotonic
from typing import Optional, Any, Tuple
import math
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
    """Convierte momio americano a decimal de forma segura."""
    if american is None or american == 0 or not math.isfinite(american) or (-100 < american < 100):
        return 1.0
    if american > 0:
        return (american / 100.0) + 1.0
    return (100.0 / abs(american)) + 1.0


def _implied_probability(american: int) -> Optional[float]:
    """
    Calcula probabilidad implícita (con vig incluido).
    Para remover el vig: normalizar por la suma de probabilidades implícitas.
    """
    if american is None or american == 0 or not math.isfinite(american) or (-100 < american < 100):
        return None
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


def _remove_vig(prob_home: float, prob_away: float) -> Tuple[Optional[float], Optional[float]]:
    """
    Elimina el jugo (vig) de las probabilidades implícitas de forma segura.
    La suma bruta suele ser ~1.05 (5% vig).
    """
    if prob_home is None or prob_away is None:
        return None, None
    if not (math.isfinite(prob_home) and math.isfinite(prob_away)):
        return None, None
    if not (0.0 < prob_home < 1.0) or not (0.0 < prob_away < 1.0):
        return None, None
    total = prob_home + prob_away
    if total <= 0:
        return None, None
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
            h2h_snaps = [
                s for s in g.odds_snapshots
                if getattr(s, "market", None) == "h2h"
                or (getattr(s, "market", None) not in ("spreads", "totals") and getattr(s, "home_ml", None) is not None)
            ]
            spread_snaps = [
                s for s in g.odds_snapshots
                if getattr(s, "market", None) == "spreads"
                or (getattr(s, "market", None) not in ("h2h", "totals") and getattr(s, "home_spread", None) is not None)
            ]
            total_snaps = [
                s for s in g.odds_snapshots
                if getattr(s, "market", None) == "totals"
                or (getattr(s, "market", None) not in ("h2h", "spreads") and getattr(s, "total_line", None) is not None)
            ]

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
                if s_dt > now_utc:
                    return round(age, 2), True, "Cuota observada en el futuro"
                stale = age > freshness_limit or s_dt >= g_kickoff
                reason = f"Cuota observada hace {round(age, 1)}h (límite {freshness_limit}h)" if age > freshness_limit else ("Cuota observada post-kickoff" if s_dt >= g_kickoff else None)
                return round(age, 2), stale, reason

            h2h_stale = False
            h2h_reason = None
            spread_stale = False
            spread_reason = None
            total_stale = False
            total_reason = None

            # Validar integridad estructural de snapshots de DB
            if latest_spread:
                try:
                    h_spr = float(latest_spread.home_spread) if latest_spread.home_spread is not None else None
                    a_spr = float(latest_spread.away_spread) if latest_spread.away_spread is not None else None
                    if (
                        h_spr is None
                        or not math.isfinite(h_spr)
                        or (a_spr is not None and (not math.isfinite(a_spr) or not math.isclose(h_spr + a_spr, 0.0, abs_tol=1e-3)))
                    ):
                        spread_stale = True
                        spread_reason = "Línea de spread asimétrica o no finita en base de datos"
                        latest_spread = None
                except (TypeError, ValueError):
                    spread_stale = True
                    spread_reason = "Línea de spread no numérica en base de datos"
                    latest_spread = None

            if latest_total:
                try:
                    tot_line = float(latest_total.total_line) if latest_total.total_line is not None else None
                    if tot_line is None or not math.isfinite(tot_line) or tot_line <= 0:
                        total_stale = True
                        total_reason = "Línea de totales no positiva o no finita en base de datos"
                        latest_total = None
                except (TypeError, ValueError):
                    total_stale = True
                    total_reason = "Línea de totales no numérica en base de datos"
                    latest_total = None

            if latest_h2h:
                try:
                    h_ml_val = float(latest_h2h.home_ml) if latest_h2h.home_ml is not None else None
                    a_ml_val = float(latest_h2h.away_ml) if latest_h2h.away_ml is not None else None
                    if (
                        h_ml_val is None or a_ml_val is None
                        or not math.isfinite(h_ml_val) or not math.isfinite(a_ml_val)
                        or h_ml_val == 0 or a_ml_val == 0
                        or (-100 < h_ml_val < 100) or (-100 < a_ml_val < 100)
                    ):
                        h2h_stale = True
                        h2h_reason = "Momios moneyline fuera de rango o inválidos en base de datos"
                        latest_h2h = None
                except (TypeError, ValueError):
                    h2h_stale = True
                    h2h_reason = "Momios moneyline no numéricos en base de datos"
                    latest_h2h = None

            h2h_age, h2h_stale_calc, h2h_reason_calc = compute_market_freshness(latest_h2h)
            spread_age, spread_stale_calc, spread_reason_calc = compute_market_freshness(latest_spread)
            total_age, total_stale_calc, total_reason_calc = compute_market_freshness(latest_total)

            h2h_stale = h2h_stale or h2h_stale_calc
            h2h_reason = h2h_reason or h2h_reason_calc
            spread_stale = spread_stale or spread_stale_calc
            spread_reason = spread_reason or spread_reason_calc
            total_stale = total_stale or total_stale_calc
            total_reason = total_reason or total_reason_calc

            # La frescura del partido para apuestas principales se evalúa contra moneyline (h2h)
            if latest_h2h:
                is_stale = h2h_stale
                staleness_hours = h2h_age
                staleness_reason = h2h_reason
            else:
                is_stale = True
                staleness_hours = None
                staleness_reason = h2h_reason or "Sin cuota moneyline válida registrada"

            # Extracción rigurosa de procedencia por lado
            latest_any_bm = latest_any.bookmaker if latest_any else None

            # H2H / Moneyline
            if latest_h2h:
                h_ml_bm = getattr(latest_h2h, "home_bookmaker", None) or latest_h2h.bookmaker
                a_ml_bm = getattr(latest_h2h, "away_bookmaker", None) or latest_h2h.bookmaker
                h2h_bench_bm = getattr(latest_h2h, "h2h_benchmark_bookmaker", None) or getattr(latest_h2h, "benchmark_bookmaker", None) or h_ml_bm
                bench_obs_dt = getattr(latest_h2h, "benchmark_observed_at", None) or (latest_h2h.observed_at if latest_h2h else None)
                h2h_bookmaker = h_ml_bm if h_ml_bm == a_ml_bm else (f"{h_ml_bm}/{a_ml_bm}" if (h_ml_bm and a_ml_bm) else latest_h2h.bookmaker)
            else:
                h_ml_bm = None
                a_ml_bm = None
                h2h_bench_bm = None
                bench_obs_dt = None
                h2h_bookmaker = None

            # Spreads
            if latest_spread:
                h_spr_bm = getattr(latest_spread, "home_bookmaker", None) or latest_spread.bookmaker
                a_spr_bm = getattr(latest_spread, "away_bookmaker", None) or latest_spread.bookmaker
                spr_bookmaker = h_spr_bm if h_spr_bm == a_spr_bm else (f"{h_spr_bm}/{a_spr_bm}" if (h_spr_bm and a_spr_bm) else latest_spread.bookmaker)
            else:
                h_spr_bm = None
                a_spr_bm = None
                spr_bookmaker = None

            # Totals
            if latest_total:
                o_tot_bm = getattr(latest_total, "over_bookmaker", None) or getattr(latest_total, "home_bookmaker", None) or latest_total.bookmaker
                u_tot_bm = getattr(latest_total, "under_bookmaker", None) or getattr(latest_total, "away_bookmaker", None) or latest_total.bookmaker
                tot_bookmaker = o_tot_bm if o_tot_bm == u_tot_bm else (f"{o_tot_bm}/{u_tot_bm}" if (o_tot_bm and u_tot_bm) else latest_total.bookmaker)
            else:
                o_tot_bm = None
                u_tot_bm = None
                tot_bookmaker = None

            result.append({
                "external_id": g.external_id,
                "home_team": g.home_team.name,
                "away_team": g.away_team.name,
                "commence_time": g.game_date.isoformat(),
                "is_stale": is_stale,
                "staleness_hours": staleness_hours,
                "staleness_reason": staleness_reason,
                "best_bookmaker": h2h_bookmaker or latest_any_bm,
                "ml_home_bookmaker": h_ml_bm,
                "ml_away_bookmaker": a_ml_bm,
                "h2h_benchmark_bookmaker": h2h_bench_bm,
                "benchmark_bookmaker": h2h_bench_bm,
                "benchmark_observed_at": bench_obs_dt.isoformat() if bench_obs_dt else None,
                "h2h_benchmark_observed_at": bench_obs_dt.isoformat() if bench_obs_dt else None,
                "spread_bookmaker": spr_bookmaker,
                "spread_home_bookmaker": h_spr_bm,
                "spread_away_bookmaker": a_spr_bm,
                "total_bookmaker": tot_bookmaker,
                "total_over_bookmaker": o_tot_bm,
                "total_under_bookmaker": u_tot_bm,
                "h2h_bookmaker": h2h_bookmaker,
                "h2h_observed_at": latest_h2h.observed_at.isoformat() if latest_h2h and latest_h2h.observed_at else None,
                "spread_observed_at": latest_spread.observed_at.isoformat() if latest_spread and latest_spread.observed_at else None,
                "total_observed_at": latest_total.observed_at.isoformat() if latest_total and latest_total.observed_at else None,
                "markets_provenance": {
                    "moneyline": {
                        "bookmaker": h2h_bookmaker,
                        "home_bookmaker": h_ml_bm,
                        "away_bookmaker": a_ml_bm,
                        "h2h_benchmark_bookmaker": h2h_bench_bm,
                        "benchmark_bookmaker": h2h_bench_bm,
                        "benchmark_observed_at": bench_obs_dt.isoformat() if bench_obs_dt else None,
                        "observed_at": latest_h2h.observed_at.isoformat() if latest_h2h and latest_h2h.observed_at else None,
                        "captured_at": latest_h2h.captured_at.isoformat() if latest_h2h and latest_h2h.captured_at else None,
                        "is_stale": h2h_stale,
                        "staleness_hours": h2h_age,
                        "staleness_reason": h2h_reason,
                    },
                    "spread": {
                        "bookmaker": spr_bookmaker,
                        "home_bookmaker": h_spr_bm,
                        "away_bookmaker": a_spr_bm,
                        "observed_at": latest_spread.observed_at.isoformat() if latest_spread and latest_spread.observed_at else None,
                        "captured_at": latest_spread.captured_at.isoformat() if latest_spread and latest_spread.captured_at else None,
                        "is_stale": spread_stale,
                        "staleness_hours": spread_age,
                        "staleness_reason": spread_reason,
                    },
                    "total": {
                        "bookmaker": tot_bookmaker,
                        "over_bookmaker": o_tot_bm,
                        "under_bookmaker": u_tot_bm,
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


def _is_valid_american_odds(val: Any) -> bool:
    """Verifica si un momio americano es finito, no nulo, y fuera del intervalo imposible (-100, 100)."""
    if val is None:
        return False
    try:
        f_val = float(val)
        return math.isfinite(f_val) and f_val != 0 and (-100 >= f_val or f_val >= 100)
    except (TypeError, ValueError):
        return False


def _parse_odds_response(raw_games: list[dict]) -> list[dict]:
    """
    Parsea la respuesta cruda de The Odds API a formato estandarizado.
    Selecciona el mejor momio disponible (best line shopping) condicionado
    estrictamente a la línea de consenso calculada de forma independiente del orden de los feeds.
    """
    parsed = []

    for game in raw_games:
        game_id    = game.get("id")
        home_team  = game.get("home_team")
        away_team  = game.get("away_team")
        commence   = game.get("commence_time")

        # 1. Recolección de cuotas válidas por mercado y casa
        valid_h2h_offerings = []
        valid_spread_offerings = []
        valid_total_offerings = []

        for bookmaker in game.get("bookmakers", []):
            book_key = bookmaker.get("key")
            if book_key not in PRIORITY_BOOKMAKERS:
                continue
                
            book_last_update = bookmaker.get("last_update")
            priority_idx = PRIORITY_BOOKMAKERS.index(book_key)

            for market in bookmaker.get("markets", []):
                market_key = market.get("key")
                outcomes = {o.get("name"): o for o in market.get("outcomes", [])}
                outcomes_lower = {str(o.get("name", "")).strip().lower(): o for o in market.get("outcomes", [])}

                if market_key == "h2h":
                    h_ml = outcomes.get(home_team, {}).get("price")
                    a_ml = outcomes.get(away_team, {}).get("price")
                    if _is_valid_american_odds(h_ml) and _is_valid_american_odds(a_ml):
                        valid_h2h_offerings.append({
                            "book_key": book_key,
                            "last_update": book_last_update,
                            "priority_idx": priority_idx,
                            "home_ml": int(h_ml),
                            "away_ml": int(a_ml),
                            "home_dec": _american_to_decimal(int(h_ml)),
                            "away_dec": _american_to_decimal(int(a_ml)),
                        })

                elif market_key == "spreads":
                    home_out = outcomes.get(home_team, {})
                    away_out = outcomes.get(away_team, {})
                    h_price = home_out.get("price")
                    a_price = away_out.get("price")
                    h_point = home_out.get("point")
                    a_point = away_out.get("point")
                    if (
                        _is_valid_american_odds(h_price) and _is_valid_american_odds(a_price)
                        and h_point is not None and a_point is not None
                        and math.isfinite(h_point) and math.isfinite(a_point)
                        and math.isclose(h_point + a_point, 0.0, abs_tol=1e-3)
                    ):
                        valid_spread_offerings.append({
                            "book_key": book_key,
                            "last_update": book_last_update,
                            "priority_idx": priority_idx,
                            "h_point": float(h_point),
                            "a_point": float(a_point),
                            "h_price": int(h_price),
                            "a_price": int(a_price),
                            "h_dec": _american_to_decimal(int(h_price)),
                            "a_dec": _american_to_decimal(int(a_price)),
                        })

                elif market_key == "totals":
                    over_out = outcomes_lower.get("over") or outcomes_lower.get("o") or outcomes.get("Over", {})
                    under_out = outcomes_lower.get("under") or outcomes_lower.get("u") or outcomes.get("Under", {})
                    o_price = over_out.get("price")
                    u_price = under_out.get("price")
                    o_point = over_out.get("point")
                    u_point = under_out.get("point")
                    if (
                        _is_valid_american_odds(o_price) and _is_valid_american_odds(u_price)
                        and o_point is not None and u_point is not None
                        and math.isfinite(o_point) and math.isfinite(u_point)
                        and o_point > 0 and math.isclose(o_point, u_point, abs_tol=1e-3)
                    ):
                        valid_total_offerings.append({
                            "book_key": book_key,
                            "last_update": book_last_update,
                            "priority_idx": priority_idx,
                            "point": float(o_point),
                            "over_ml": int(o_price),
                            "under_ml": int(u_price),
                            "over_dec": _american_to_decimal(int(o_price)),
                            "under_dec": _american_to_decimal(int(u_price)),
                        })

        # 2. Resolución de H2H (Moneyline Shopping por lado)
        best_home_ml = None
        best_away_ml = None
        best_book = None
        ml_home_book = None
        ml_away_book = None
        ml_last_update = None
        h2h_benchmark_book = None
        h2h_benchmark_last_update = None
        home_prob = None
        away_prob = None

        if valid_h2h_offerings:
            # Line shopping por lado para obtener los mejores momios comprables
            best_home_offering = max(valid_h2h_offerings, key=lambda x: (x["home_dec"], -x["priority_idx"]))
            best_away_offering = max(valid_h2h_offerings, key=lambda x: (x["away_dec"], -x["priority_idx"]))
            best_home_ml = best_home_offering["home_ml"]
            best_away_ml = best_away_offering["away_ml"]
            ml_home_book = best_home_offering["book_key"]
            ml_away_book = best_away_offering["book_key"]
            best_book = ml_home_book if ml_home_book == ml_away_book else f"{ml_home_book}/{ml_away_book}"
            valid_updates = [u for u in [best_home_offering.get("last_update"), best_away_offering.get("last_update")] if u]
            ml_last_update = max(valid_updates) if valid_updates else None

            # 2b. Cálculo de probabilidades no-vig coherentes:
            # Queda estrictamente prohibido normalizar un par sintético mezclado de distintas casas.
            # Se selecciona una única oferta H2H coherente de una sola casa como benchmark de referencia.
            # Regla de desempate documentada:
            #   1) Prioridad según PRIORITY_BOOKMAKERS (menor priority_idx: DraftKings > FanDuel > ...)
            #   2) Menor sobremargen/vig (mercado más ajustado y eficiente)
            #   3) Timestamp de actualización más reciente
            candidates = []
            for o in valid_h2h_offerings:
                p_h_cand = _implied_probability(o["home_ml"])
                p_a_cand = _implied_probability(o["away_ml"])
                if p_h_cand is not None and p_a_cand is not None and 0.0 < p_h_cand < 1.0 and 0.0 < p_a_cand < 1.0:
                    cand_vig = (p_h_cand + p_a_cand) - 1.0
                    ts_val = 0.0
                    if o.get("last_update"):
                        try:
                            ts_val = datetime.fromisoformat(o["last_update"].replace("Z", "+00:00")).timestamp()
                        except Exception:
                            ts_val = 0.0
                    candidates.append({
                        "offering": o,
                        "priority_idx": o["priority_idx"],
                        "vig": cand_vig,
                        "ts": ts_val,
                        "p_h": p_h_cand,
                        "p_a": p_a_cand,
                    })

            if candidates:
                benchmark_candidate = min(
                    candidates,
                    key=lambda c: (c["priority_idx"], c["vig"], -c["ts"])
                )
                h2h_benchmark_book = benchmark_candidate["offering"]["book_key"]
                h2h_benchmark_last_update = benchmark_candidate["offering"].get("last_update")
                h_fair, a_fair = _remove_vig(benchmark_candidate["p_h"], benchmark_candidate["p_a"])
                if h_fair is not None and a_fair is not None:
                    home_prob, away_prob = round(h_fair, 4), round(a_fair, 4)

        # 3. Resolución de Spread Consensus y Line Shopping por lado
        best_home_spread = None
        best_home_spread_ml = None
        best_away_spread = None
        best_away_spread_price = None
        spread_book = None
        spread_home_book = None
        spread_away_book = None
        spread_last_update = None
        if valid_spread_offerings:
            # Determinar el punto de hándicap consenso por moda (frecuencia)
            from collections import Counter
            point_counts = Counter(round(o["h_point"], 2) for o in valid_spread_offerings)
            # Desempatar por menor prioridad de bookmaker
            consensus_point = max(
                point_counts.keys(),
                key=lambda p: (
                    point_counts[p],
                    -min(o["priority_idx"] for o in valid_spread_offerings if math.isclose(o["h_point"], p, abs_tol=1e-2))
                )
            )

            # Filtrar ofertas sobre el punto consenso y buscar el mejor precio por lado
            offerings_at_consensus = [
                o for o in valid_spread_offerings
                if math.isclose(o["h_point"], consensus_point, abs_tol=1e-2)
            ]
            best_h_spread_offering = max(offerings_at_consensus, key=lambda x: (x["h_dec"], -x["priority_idx"]))
            best_a_spread_offering = max(offerings_at_consensus, key=lambda x: (x["a_dec"], -x["priority_idx"]))

            best_home_spread = best_h_spread_offering["h_point"]
            best_home_spread_ml = best_h_spread_offering["h_price"]
            best_away_spread = best_a_spread_offering["a_point"]
            best_away_spread_price = best_a_spread_offering["a_price"]
            spread_home_book = best_h_spread_offering["book_key"]
            spread_away_book = best_a_spread_offering["book_key"]
            spread_book = spread_home_book if spread_home_book == spread_away_book else f"{spread_home_book}/{spread_away_book}"
            valid_updates = [u for u in [best_h_spread_offering.get("last_update"), best_a_spread_offering.get("last_update")] if u]
            spread_last_update = max(valid_updates) if valid_updates else None

        # 4. Resolución de Totales Consensus y Line Shopping por lado
        total_line = None
        over_ml = None
        under_ml = None
        total_book = None
        total_over_book = None
        total_under_book = None
        totals_last_update = None
        if valid_total_offerings:
            from collections import Counter
            point_counts = Counter(round(o["point"], 2) for o in valid_total_offerings)
            consensus_total = max(
                point_counts.keys(),
                key=lambda p: (
                    point_counts[p],
                    -min(o["priority_idx"] for o in valid_total_offerings if math.isclose(o["point"], p, abs_tol=1e-2))
                )
            )

            offerings_at_consensus = [
                o for o in valid_total_offerings
                if math.isclose(o["point"], consensus_total, abs_tol=1e-2)
            ]
            best_over_offering = max(offerings_at_consensus, key=lambda x: (x["over_dec"], -x["priority_idx"]))
            best_under_offering = max(offerings_at_consensus, key=lambda x: (x["under_dec"], -x["priority_idx"]))

            total_line = consensus_total
            over_ml = best_over_offering["over_ml"]
            under_ml = best_under_offering["under_ml"]
            total_over_book = best_over_offering["book_key"]
            total_under_book = best_under_offering["book_key"]
            total_book = total_over_book if total_over_book == total_under_book else f"{total_over_book}/{total_under_book}"
            valid_updates = [u for u in [best_over_offering.get("last_update"), best_under_offering.get("last_update")] if u]
            totals_last_update = max(valid_updates) if valid_updates else None

        parsed.append({
            "external_id":            game_id,
            "home_team":              home_team,
            "away_team":              away_team,
            "commence_time":          commence,
            "best_bookmaker":         best_book,
            "ml_home_bookmaker":      ml_home_book,
            "ml_away_bookmaker":      ml_away_book,
            "h2h_benchmark_bookmaker": h2h_benchmark_book,
            "benchmark_bookmaker":     h2h_benchmark_book,
            "h2h_benchmark_last_update": h2h_benchmark_last_update,
            "benchmark_last_update":   h2h_benchmark_last_update,
            "spread_bookmaker":       spread_book,
            "spread_home_bookmaker":  spread_home_book,
            "spread_away_bookmaker":  spread_away_book,
            "total_bookmaker":        total_book,
            "total_over_bookmaker":   total_over_book,
            "total_under_bookmaker":  total_under_book,
            "home_ml":                best_home_ml,
            "away_ml":                best_away_ml,
            "home_spread":            best_home_spread,
            "home_spread_ml":         best_home_spread_ml,
            "away_spread":            best_away_spread,
            "away_spread_ml":         best_away_spread_price,
            "total_line":             total_line,
            "over_ml":                over_ml,
            "under_ml":               under_ml,
            "home_implied_prob":      home_prob,
            "away_implied_prob":      away_prob,
            "captured_at":            datetime.now(timezone.utc).isoformat(),
            "ml_last_update":         ml_last_update,
            "spread_last_update":     spread_last_update,
            "totals_last_update":     totals_last_update,
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
