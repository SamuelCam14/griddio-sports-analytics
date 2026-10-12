"""
Servicio de Presupuesto, Cuota, Circuit Breaker, Persistencia Inmutable y Normalización para The Odds API.
Cumple estrictamente con las especificaciones de Fase 01 (docs/nfl_model_execution/01-data-and-quota.md).

Reglas duras:
1. Presupuesto mensual: 500 créditos (o 1000 si se habilita).
2. Reserva protegida: 75 créditos (15%) que NUNCA se consumen salvo aprobación explícita.
3. Sobres: Líneas principales (45), Props dirigidas (320), Operación/reintento (60).
4. Circuit breaker: remaining <= 20% total (100 en plan 500), remaining <= 75, HTTP 429, o sobre agotado.
5. Falla cerrado ante ledger ausente, corrupto o ilegible; NUNCA inicializa 500 sin confirmación.
6. Reservas atómicas y compartidas entre instancias/procesos con file locking cross-process.
7. Registro de reservas pendientes y contabilidad conservadora ante timeouts (petición potencialmente cobrada).
8. Capturas raw estrictamente inmutables (read-only chmod 0o444).
9. Normalizador canónico integrado y conectado al flujo de datos.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid
from typing import Any, Optional
import structlog

from ml.data_pipeline.common import project_root

logger = structlog.get_logger(__name__)


class QuotaExceededError(RuntimeError):
    """Lanzada cuando una solicitud excede el presupuesto o viola la reserva protegida."""


class CircuitBreakerOpenError(QuotaExceededError):
    """Lanzada cuando el circuit breaker de cuota está activo."""


@dataclass
class OddsBudgetConfig:
    total_budget: int = 500
    protected_reserve: int = 75
    main_lines_budget: int = 45
    props_budget: int = 320
    operational_budget: int = 60
    circuit_breaker_pct: float = 0.20  # 20% del saldo total

    @property
    def max_spendable(self) -> int:
        return self.total_budget - self.protected_reserve

    @property
    def breaker_threshold(self) -> int:
        return max(self.protected_reserve, int(self.total_budget * self.circuit_breaker_pct))


def mask_api_key(key: str | None) -> str:
    """Retorna una huella hash segura de la clave, nunca el valor original."""
    if not key or not str(key).strip():
        return "none"
    clean = str(key).strip()
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]
    return f"key_sha_{digest}"


def sanitize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Elimina credenciales de los query params."""
    if not params:
        return {}
    sanitized = {}
    for k, v in params.items():
        if k.lower() in {"apikey", "api_key", "key"}:
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = v
    return sanitized


def estimate_request_cost(endpoint: str, params: dict[str, Any] | None = None) -> int:
    """Calcula el costo esperado en créditos según la documentación V4 de The Odds API.

    /sports/{sport}/odds: mercados devueltos únicos × regiones
    /events/{eventId}/odds: mercados devueltos únicos × regiones
    /events/{eventId}/markets: 1 crédito
    /sports: 0 créditos
    Endpoints históricos (/historical): prohibidos en periodo gratuito.
    """
    endpoint_clean = endpoint.lower()
    params = params or {}

    if "historical" in endpoint_clean or "history" in endpoint_clean:
        raise QuotaExceededError(
            "Endpoints históricos prohibidos en plan gratuito de 500 créditos (costo 10x)."
        )

    if endpoint_clean.rstrip("/") in {"/sports", "/v4/sports", "sports"}:
        return 0

    if "/markets" in endpoint_clean and "/events/" in endpoint_clean:
        return 1

    if "/odds" in endpoint_clean:
        markets_raw = str(params.get("markets", "h2h")).strip()
        regions_raw = str(params.get("regions", "us")).strip()
        unique_markets = {m.strip() for m in markets_raw.split(",") if m.strip()} or {"h2h"}
        unique_regions = {r.strip() for r in regions_raw.split(",") if r.strip()} or {"us"}
        return max(1, len(unique_markets) * len(unique_regions))

    return 1


@contextmanager
def file_lock(lock_path: Path, timeout: float = 10.0):
    """Context manager para exclusión mutua cross-process y cross-thread."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start_time = time.monotonic()
    file_handle = None
    try:
        file_handle = open(lock_path, "a+")
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    file_handle.seek(0)
                    msvcrt.locking(file_handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() - start_time >= timeout:
                    raise TimeoutError(f"No se pudo adquirir el lock de cuota en {lock_path} tras {timeout}s")
                time.sleep(0.02)
        yield
    finally:
        if file_handle:
            try:
                if os.name == "nt":
                    import msvcrt
                    file_handle.seek(0)
                    msvcrt.locking(file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                file_handle.close()
            except Exception:
                pass


@dataclass
class QuotaReservation:
    reservation_id: str
    envelope: str
    endpoint: str
    params: dict[str, Any]
    estimated_cost: int
    created_at_utc: str
    api_key_masked: str


class OddsQuotaService:
    """Gestiona el ledger, sobres de presupuesto, circuit breaker y reservas persistentes."""

    def __init__(
        self,
        config: OddsBudgetConfig | None = None,
        ledger_path: Path | None = None,
        raw_root: Path | None = None,
        fail_closed: bool = True,
    ):
        self.config = config or OddsBudgetConfig()
        root = project_root()
        self.ledger_path = ledger_path or (root / "data" / "odds_quota_ledger.json")
        self.lock_path = self.ledger_path.with_suffix(".lock")
        self.raw_root = raw_root or (root / "data" / "raw" / "odds_api")
        self.fail_closed = fail_closed
        self._load_ledger()

    def _load_ledger(self) -> None:
        """Carga el estado acumulado desde el ledger en disco.
        Falla cerrado si el ledger no existe, está corrupto o es inválido.
        """
        if not self.ledger_path.is_file():
            if self.fail_closed:
                raise QuotaExceededError(
                    f"Ledger de cuota no encontrado en {self.ledger_path}. "
                    "Falla cerrado: no se permite consumo de API sin ledger verificado."
                )
            # Inicialización solo si fail_closed es False (modo bootstrap explícito)
            self.requests_used = 0
            self.requests_remaining = self.config.total_budget
            self.spent_by_envelope = {"main_lines": 0, "props": 0, "operational": 0}
            self.circuit_breaker_open = False
            self.breaker_reason = None
            self.reconciliation = None
            self.pending_reservations: dict[str, dict] = {}
            self.transactions = []
            return

        try:
            content = self.ledger_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if not isinstance(data, dict) or "summary" not in data:
                raise ValueError("Estructura raíz inválida en ledger")

            summary = data["summary"]
            if "requests_remaining" not in summary:
                raise ValueError("Campo 'requests_remaining' ausente en summary de ledger")

            self.requests_used: int = summary.get("requests_used", 0)
            self.requests_remaining: int = summary.get("requests_remaining", 0)
            self.spent_by_envelope: dict[str, int] = summary.get(
                "spent_by_envelope", {"main_lines": 0, "props": 0, "operational": 0}
            )
            self.circuit_breaker_open: bool = summary.get("circuit_breaker_open", False)
            self.breaker_reason: Optional[str] = summary.get("circuit_breaker_reason", None)
            self.reconciliation: Optional[dict] = data.get("reconciliation", None)
            self.pending_reservations: dict[str, dict] = data.get("pending_reservations", {})
            self.transactions: list[dict] = data.get("transactions", [])
        except Exception as exc:
            raise QuotaExceededError(
                f"Ledger de cuota corrupto o ilegible en {self.ledger_path}: {exc}. Falla cerrado."
            ) from exc

    def _save_ledger(self) -> None:
        """Persiste atómicamente el estado del ledger a disco."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.0",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "config": asdict(self.config),
            "summary": {
                "requests_used": self.requests_used,
                "requests_remaining": self.requests_remaining,
                "spent_by_envelope": self.spent_by_envelope,
                "circuit_breaker_open": self.circuit_breaker_open,
                "circuit_breaker_reason": self.breaker_reason,
            },
            "reconciliation": self.reconciliation,
            "pending_reservations": self.pending_reservations,
            "transactions": self.transactions,
        }
        temp = self.ledger_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp.replace(self.ledger_path)

    def bootstrap_ledger(
        self,
        requests_remaining: int,
        requests_used: int = 0,
        spent_by_envelope: dict[str, int] | None = None,
        force: bool = False,
    ) -> None:
        """Inicializa explícitamente el ledger con saldo verificado (solo para setup/tests)."""
        if self.ledger_path.is_file() and not force:
            return
        with file_lock(self.lock_path):
            self.requests_remaining = requests_remaining
            self.requests_used = requests_used
            self.spent_by_envelope = spent_by_envelope or {"main_lines": 0, "props": 0, "operational": 0}
            self.circuit_breaker_open = False
            self.breaker_reason = None
            self.reconciliation = None
            self.pending_reservations = {}
            self.transactions = []
            self._save_ledger()

    def check_circuit_breaker(self, effective_remaining: int | None = None) -> None:
        """Verifica si el circuit breaker debe activarse o ya está activo."""
        rem = effective_remaining if effective_remaining is not None else self.requests_remaining

        if self.circuit_breaker_open:
            raise CircuitBreakerOpenError(
                f"Circuit breaker activo: {self.breaker_reason or 'Cuota agotada/restringida'}"
            )

        if rem <= self.config.protected_reserve:
            self.circuit_breaker_open = True
            self.breaker_reason = f"Saldo restante ({rem}) alcanzó la reserva protegida ({self.config.protected_reserve})"
            self._save_ledger()
            raise CircuitBreakerOpenError(self.breaker_reason)

        if rem <= self.config.breaker_threshold:
            self.circuit_breaker_open = True
            self.breaker_reason = f"Saldo restante ({rem}) cayó por debajo del 20% ({self.config.breaker_threshold})"
            self._save_ledger()
            raise CircuitBreakerOpenError(self.breaker_reason)

    def reserve_quota(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        envelope: str = "operational",
        api_key: str | None = None,
    ) -> QuotaReservation:
        """Calcula el costo, valida reservas y registra la reserva pendiente atómicamente."""
        envelope = envelope.lower()
        if envelope not in {"main_lines", "props", "operational"}:
            raise ValueError(f"Sobre inválido: '{envelope}'. Use main_lines, props u operational.")

        with file_lock(self.lock_path):
            # Recargar estado fresco bajo lock
            self._load_ledger()

            # 1. Estimar costo
            cost = estimate_request_cost(endpoint, params)

            # 2. Calcular saldo efectivo deduciendo reservas pendientes
            pending_cost = sum(r.get("estimated_cost", 0) for r in self.pending_reservations.values())
            effective_remaining = self.requests_remaining - pending_cost

            # 3. Comprobar circuit breaker sobre saldo efectivo
            self.check_circuit_breaker(effective_remaining)

            # 4. Validar reserva protegida
            if effective_remaining - cost < self.config.protected_reserve:
                raise QuotaExceededError(
                    f"La petición costaría {cost} créditos, dejando el saldo disponible en "
                    f"{effective_remaining - cost}, violando la reserva protegida de "
                    f"{self.config.protected_reserve} créditos."
                )

            # 5. Validar tope del sobre específico sumando reservas pendientes del sobre
            pending_in_env = sum(
                r.get("estimated_cost", 0)
                for r in self.pending_reservations.values()
                if r.get("envelope") == envelope
            )
            spent_in_env = self.spent_by_envelope.get(envelope, 0) + pending_in_env
            env_limit = {
                "main_lines": self.config.main_lines_budget,
                "props": self.config.props_budget,
                "operational": self.config.operational_budget,
            }[envelope]

            if spent_in_env + cost > env_limit:
                raise QuotaExceededError(
                    f"Sobre '{envelope}' excedido: gastado+pendiente={spent_in_env}, costo={cost}, tope={env_limit}"
                )

            # 6. Generar y registrar reserva pendiente persistente
            res_id = hashlib.sha256(
                f"{endpoint}:{datetime.now(timezone.utc).isoformat()}:{cost}:{len(self.pending_reservations)}".encode()
            ).hexdigest()[:12]

            reservation = QuotaReservation(
                reservation_id=res_id,
                envelope=envelope,
                endpoint=endpoint,
                params=sanitize_params(params),
                estimated_cost=cost,
                created_at_utc=datetime.now(timezone.utc).isoformat(),
                api_key_masked=mask_api_key(api_key),
            )

            self.pending_reservations[res_id] = asdict(reservation)
            self._save_ledger()
            return reservation

    def record_transaction(
        self,
        reservation: QuotaReservation,
        status_code: int = 0,
        response_headers: dict[str, Any] | None = None,
        is_timeout: bool = False,
        is_pre_dispatch_error: bool = False,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Concilia la reserva pendiente con el costo real o contabilidad conservadora ante timeout."""
        with file_lock(self.lock_path):
            self._load_ledger()

            # Liberar reserva pendiente
            self.pending_reservations.pop(reservation.reservation_id, None)

            # Caso 1: Error antes de enviar por red (ej. validación cliente) -> No hubo costo
            if is_pre_dispatch_error:
                self._save_ledger()
                return {
                    "reservation_id": reservation.reservation_id,
                    "status": "cancelled_pre_dispatch",
                    "actual_cost": 0,
                    "error_message": error_message,
                }

            headers = {k.lower(): str(v) for k, v in (response_headers or {}).items()}
            used_header = headers.get("x-requests-used")
            remaining_header = headers.get("x-requests-remaining")
            last_header = headers.get("x-requests-last")

            # Caso 2: Timeout o desconexión tras despacho HTTP (petición potencialmente cobrada)
            if is_timeout:
                actual_cost = reservation.estimated_cost
                self.requests_remaining = max(0, self.requests_remaining - actual_cost)
                self.requests_used += actual_cost
                self.spent_by_envelope[reservation.envelope] = (
                    self.spent_by_envelope.get(reservation.envelope, 0) + actual_cost
                )
                status = "uncertain_timeout"
            else:
                actual_cost = reservation.estimated_cost
                if last_header and last_header.isdigit():
                    actual_cost = int(last_header)

                if remaining_header and remaining_header.isdigit():
                    self.requests_remaining = int(remaining_header)
                else:
                    self.requests_remaining = max(0, self.requests_remaining - actual_cost)

                if used_header and used_header.isdigit():
                    self.requests_used = int(used_header)
                else:
                    self.requests_used += actual_cost

                self.spent_by_envelope[reservation.envelope] = (
                    self.spent_by_envelope.get(reservation.envelope, 0) + actual_cost
                )
                status = "success" if 200 <= status_code < 300 else "error"

            props_limit = self.config.props_budget
            if (
                reservation.envelope == "props"
                and self.spent_by_envelope.get("props", 0) >= props_limit
            ):
                self.circuit_breaker_open = True
                self.breaker_reason = f"Sobre de props consumido ({props_limit} créditos)"

            # Disparar circuit breaker si 429 o saldo insuficiente
            if status_code == 429:
                self.circuit_breaker_open = True
                self.breaker_reason = "HTTP 429: Rate limit o cuota mensual agotada en proveedor"
            elif self.requests_remaining <= self.config.protected_reserve:
                self.circuit_breaker_open = True
                self.breaker_reason = f"Saldo restante ({self.requests_remaining}) alcanzó la reserva protegida ({self.config.protected_reserve})"
            elif self.requests_remaining <= self.config.breaker_threshold:
                self.circuit_breaker_open = True
                self.breaker_reason = f"Saldo restante ({self.requests_remaining}) cayó por debajo del 20% ({self.config.breaker_threshold})"

            entry = {
                "reservation_id": reservation.reservation_id,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "api_key_id": reservation.api_key_masked,
                "envelope": reservation.envelope,
                "endpoint": reservation.endpoint,
                "params": reservation.params,
                "estimated_cost": reservation.estimated_cost,
                "actual_cost": actual_cost,
                "status_code": status_code,
                "status": status,
                "requests_used": self.requests_used,
                "requests_remaining": self.requests_remaining,
                "error_message": error_message,
            }
            self.transactions.append(entry)
            self._save_ledger()
            return entry

    def persist_raw_response(
        self,
        endpoint: str,
        params: dict[str, Any],
        response_body: Any,
        response_headers: dict[str, Any] | None = None,
        captured_at_utc: str | None = None,
    ) -> tuple[Path, Path]:
        """Guarda la respuesta cruda y canónica de The Odds API con persistencia atómica append-only.
        
        - Escritura atómica vía archivo temporal y rename en el mismo directorio.
        - Nombres temporales y criptográficos que conservan distintas capturas históricas.
        - Idempotencia: si la captura idéntica ya existe, no se sobrescribe ni falla por permisos.
        - Permisos estrictos de solo lectura (chmod 0o444).
        """
        now = datetime.fromisoformat(captured_at_utc) if captured_at_utc else datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("captured_at_utc debe incluir zona horaria")
        now = now.astimezone(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        time_tag = now.strftime("%Y%m%dT%H%M%SZ")
        sanitized = sanitize_params(params)
        payload_bytes = json.dumps(response_body, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(payload_bytes).hexdigest()

        target_dir = self.raw_root / date_str
        target_dir.mkdir(parents=True, exist_ok=True)

        stem = sanitized.get("sport", "odds").replace("/", "_")
        request_identity = hashlib.sha256(
            json.dumps({"endpoint": endpoint, "params": sanitized}, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        target_file = target_dir / f"{stem}_{time_tag}_{request_identity}_{digest[:12]}.json"
        canonical_file = target_dir / f"{stem}_canonical_{time_tag}_{request_identity}_{digest[:12]}.json"

        headers_filtered = {
            k: v for k, v in (response_headers or {}).items()
            if k.lower().startswith("x-requests-") or k.lower() in {"date", "content-type"}
        }

        wrapped = {
            "source": "the_odds_api_v4",
            "endpoint": endpoint,
            "params": sanitized,
            "captured_at_utc": now.isoformat(),
            "sha256": digest,
            "bytes": len(payload_bytes),
            "quota_headers": headers_filtered,
            "data": response_body,
        }

        canonical_records = normalize_odds_response(response_body, now.isoformat())
        canonical_wrapped = {
            "source": "the_odds_api_v4_canonical",
            "endpoint": endpoint,
            "raw_sha256": digest,
            "captured_at_utc": now.isoformat(),
            "records_count": len(canonical_records),
            "records": canonical_records,
        }

        # Serializa escritores de distintos servicios/procesos y evita reemplazar evidencia existente.
        with file_lock(target_dir / ".persist.lock"):
            raw_exists = target_file.exists()
            canonical_exists = canonical_file.exists()
            if raw_exists or canonical_exists:
                if not (raw_exists and canonical_exists):
                    raise OSError("Captura parcial existente; se preserva y no se sobrescribe evidencia raw")
                try:
                    existing_raw = json.loads(target_file.read_text(encoding="utf-8"))
                    existing_canonical = json.loads(canonical_file.read_text(encoding="utf-8"))
                    existing_digest = hashlib.sha256(
                        json.dumps(existing_raw["data"], sort_keys=True).encode("utf-8")
                    ).hexdigest()
                    if (
                        existing_raw.get("sha256") == digest == existing_digest
                        and existing_raw.get("endpoint") == endpoint
                        and existing_raw.get("params") == sanitized
                        and existing_raw.get("captured_at_utc") == now.isoformat()
                        and existing_canonical.get("raw_sha256") == digest
                        and existing_canonical.get("endpoint") == endpoint
                        and existing_canonical.get("captured_at_utc") == now.isoformat()
                        and existing_canonical.get("records") == canonical_records
                    ):
                        return target_file, canonical_file
                except Exception as exc:
                    raise OSError("Captura existente inválida; no se sobrescribe evidencia raw") from exc
                raise OSError("Colisión de captura con contenido distinto; no se sobrescribe evidencia raw")

            # Escritura atómica a disco vía archivos temporales únicos.
            temp_suffix = uuid.uuid4().hex
            raw_content = json.dumps(wrapped, indent=2)
            canon_content = json.dumps(canonical_wrapped, indent=2)
            tmp_raw = target_dir / f".tmp_{temp_suffix}_{target_file.name}"
            tmp_canon = target_dir / f".tmp_{temp_suffix}_{canonical_file.name}"
            try:
                tmp_raw.write_text(raw_content, encoding="utf-8")
                tmp_canon.write_text(canon_content, encoding="utf-8")
                for path in (tmp_raw, tmp_canon):
                    try:
                        os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
                    except Exception as exc:
                        logger.warning("No se pudo aplicar modo read-only a snapshot temporal", error=str(exc))
                tmp_raw.replace(target_file)
                tmp_canon.replace(canonical_file)
            finally:
                for path in (tmp_raw, tmp_canon):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass

        return target_file, canonical_file


def american_to_decimal(price: int) -> float:
    """Convierte precio americano a cuota decimal."""
    if price > 0:
        return round((price / 100.0) + 1.0, 4)
    elif price < 0:
        return round((100.0 / abs(price)) + 1.0, 4)
    return 1.0


def normalize_odds_response(
    raw_data: list[dict[str, Any]] | dict[str, Any],
    captured_at_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Normaliza respuestas de The Odds API a un esquema canónico sin secretos.

    Soporta listas de partidos (líneas principales) y objetos de partido único (player props).
    """
    captured_at = captured_at_utc or datetime.now(timezone.utc).isoformat()
    canonical_records = []

    if isinstance(raw_data, dict):
        raw_games = [raw_data]
    elif isinstance(raw_data, list):
        raw_games = raw_data
    else:
        raw_games = []

    for game in raw_games:
        if not isinstance(game, dict):
            continue
        event_id = str(game.get("id", ""))
        sport_key = str(game.get("sport_key", ""))
        commence_time = str(game.get("commence_time", ""))
        home_team = str(game.get("home_team", ""))
        away_team = str(game.get("away_team", ""))

        bookmakers = game.get("bookmakers", [])
        for bm in bookmakers:
            if not isinstance(bm, dict):
                continue
            bookmaker_key = str(bm.get("key", ""))
            bm_last_update = str(bm.get("last_update", ""))

            for market in bm.get("markets", []):
                if not isinstance(market, dict):
                    continue
                market_key = str(market.get("key", ""))
                market_last_update = str(market.get("last_update", bm_last_update))

                for outcome in market.get("outcomes", []):
                    if not isinstance(outcome, dict):
                        continue
                    name = str(outcome.get("name", ""))
                    description = outcome.get("description")
                    price = outcome.get("price")
                    point = outcome.get("point")

                    if price is None:
                        continue

                    # Determinar precio americano y decimal
                    if isinstance(price, int) or (isinstance(price, float) and abs(price) >= 100):
                        american_price = int(price)
                        decimal_price = american_to_decimal(american_price)
                    else:
                        decimal_price = float(price)
                        if decimal_price >= 2.0:
                            american_price = int(round((decimal_price - 1.0) * 100))
                        elif decimal_price > 1.0:
                            american_price = int(round(-100.0 / (decimal_price - 1.0)))
                        else:
                            american_price = 100

                    canonical_records.append({
                        "event_id": event_id,
                        "sport_key": sport_key,
                        "commence_time_utc": commence_time,
                        "home_team": home_team,
                        "away_team": away_team,
                        "bookmaker": bookmaker_key,
                        "market_key": market_key,
                        "outcome_name": name,
                        "outcome_description": str(description) if description is not None else None,
                        "price_american": american_price,
                        "price_decimal": decimal_price,
                        "point_line": float(point) if point is not None else None,
                        "last_update_utc": market_last_update,
                        "captured_at_utc": captured_at,
                    })

    return canonical_records
