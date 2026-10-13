"""
Pruebas exhaustivas para la Fase 01 — Fuentes NFL, snapshots inmutables y presupuesto Odds API.
Sin llamadas de red no autorizadas; verificación de contratos, hashing, zonas horarias y cuotas.
"""
from datetime import datetime, timezone
import json
import asyncio
from pathlib import Path
import pytest
import pandas as pd
import polars as pl

from ml.data_pipeline.common import DataQualityError
from ml.data_pipeline.nfl_source import (
    resolve_nfl_kickoff_utc,
    load_nfl_snapshot,
    load_nfl_player_crosswalk,
    load_nfl_upcoming_games,
)
from ml.data_pipeline.injury_feature_builder import build_nfl_historical_injury_features
from app.services.odds_quota_service import (
    OddsBudgetConfig,
    OddsQuotaService,
    QuotaExceededError,
    CircuitBreakerOpenError,
    estimate_request_cost,
    mask_api_key,
    sanitize_params,
    normalize_odds_response,
)


# ==============================================================================
# 1. Kickoff UTC Resolution Tests
# ==============================================================================

def test_kickoff_utc_standard_time_est():
    """Enero está en Standard Time (EST = UTC-5). 13:00 EST -> 18:00 UTC."""
    ts = resolve_nfl_kickoff_utc("2024-01-07", "13:00", "2023_18_MIN_DET")
    assert ts.tzinfo is not None
    assert ts.hour == 18
    assert ts.minute == 0
    assert str(ts) == "2024-01-07 18:00:00+00:00"


def test_kickoff_utc_daylight_saving_edt():
    """Septiembre está en Daylight Saving Time (EDT = UTC-4). 13:00 EDT -> 17:00 UTC."""
    ts = resolve_nfl_kickoff_utc("2024-09-08", "13:00", "2024_01_PIT_ATL")
    assert ts.tzinfo is not None
    assert ts.hour == 17
    assert ts.minute == 0
    assert str(ts) == "2024-09-08 17:00:00+00:00"


def test_kickoff_utc_prime_time_thursday_night():
    """Thursday Night Football: 20:20 EDT -> 00:20 UTC al día siguiente."""
    ts = resolve_nfl_kickoff_utc("2024-09-05", "20:20", "2024_01_BAL_KC")
    assert ts.day == 6
    assert ts.hour == 0
    assert ts.minute == 20


@pytest.mark.parametrize("bad_day,bad_time", [
    (None, "13:00"),
    ("", "13:00"),
    ("   ", "13:00"),
    ("2024-09-08", None),
    ("2024-09-08", ""),
    ("2024-09-08", "TBD"),
    ("invalid_date", "13:00"),
])
def test_kickoff_utc_fails_closed_on_missing_or_invalid(bad_day, bad_time):
    """Falla cerrado si gameday o gametime faltan o son ambiguos."""
    with pytest.raises(DataQualityError):
        resolve_nfl_kickoff_utc(bad_day, bad_time, "game_fail_test")


# ==============================================================================
# 2. Immutable Snapshots & Hashing Tests
# ==============================================================================

def test_load_nfl_snapshot_validates_real_snapshot():
    """Carga snapshots de 2024 verificando el hash SHA-256 contra manifest.json."""
    injuries = load_nfl_snapshot("injuries", 2024)
    assert isinstance(injuries, pl.DataFrame)
    assert injuries.height > 0
    assert "player_id" in injuries.columns or "gsis_id" in injuries.columns


def test_load_nfl_player_crosswalk_integrity():
    """El crosswalk contiene identidades canónicas sin credenciales."""
    cw = load_nfl_player_crosswalk()
    assert isinstance(cw, pl.DataFrame)
    assert cw.height > 1000
    for col in ["gsis_id", "pfr_id"]:
        assert col in cw.columns


def test_load_nfl_snapshot_fails_closed_on_missing_dataset(tmp_path):
    """Falla cerrado con DataQualityError si el dataset/temporada no existe en el manifest."""
    with pytest.raises(DataQualityError, match="Snapshot no encontrado en manifest"):
        load_nfl_snapshot("dataset_inexistente", 2099)


def test_load_nfl_snapshot_detects_hash_tampering(tmp_path):
    """Si el archivo en disco no coincide con el SHA-256 del manifest, rechaza la carga."""
    fake_manifest = {
        "files": [{
            "dataset": "fake_test",
            "season": 2024,
            "path": str(tmp_path / "tampered.parquet"),
            "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        }]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(fake_manifest), encoding="utf-8")
    (tmp_path / "tampered.parquet").write_bytes(b"corrupted_data_payload")

    with pytest.raises(DataQualityError, match="hash SHA-256 no coincide"):
        load_nfl_snapshot("fake_test", 2024, raw_root=tmp_path)


# ==============================================================================
# 3. Upcoming Games Schedule Kickoff Verification
# ==============================================================================

def test_load_nfl_upcoming_games_has_concrete_utc_kickoff():
    """Los partidos futuros de la NFL tienen kickoff UTC resuelto y no NaT."""
    upcoming = load_nfl_upcoming_games(as_of=pd.Timestamp("2026-08-01", tz="UTC"))
    assert not upcoming.empty
    assert "game_start_utc" in upcoming.columns
    # Ningún partido debe tener NaT en game_start_utc
    assert upcoming["game_start_utc"].isna().sum() == 0
    # Todos deben ser timestamps conscientes de zona horaria UTC
    first = upcoming["game_start_utc"].iloc[0]
    assert first.tzinfo is not None


def test_load_nfl_upcoming_games_includes_same_day_future_kickoff():
    """Partidos del mismo día con kickoff UTC futuro respecto a as_of son elegibles como upcoming."""
    as_of = pd.Timestamp("2026-10-12 18:00:00", tz="UTC")
    upcoming = load_nfl_upcoming_games(as_of=as_of)
    buf_la = upcoming[upcoming["game_id"] == "2026_05_BUF_LA"]
    assert len(buf_la) == 1
    assert buf_la.iloc[0]["home_team_id"] == "LA"
    assert buf_la.iloc[0]["away_team_id"] == "BUF"



# ==============================================================================
# 4. Injury Pipeline from Snapshots (No Network Leak)
# ==============================================================================

def test_build_historical_injury_features_uses_snapshots():
    """Genera métricas de impacto de lesiones para 2024 exclusivamente desde snapshots locales."""
    df = build_nfl_historical_injury_features([2024])
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    expected_cols = {"season", "week", "team", "ppr_lost_pct", "off_snaps_lost_pct", "def_snaps_lost_pct"}
    assert expected_cols.issubset(df.columns)
    assert df["ppr_lost_pct"].isna().sum() == 0
    assert df["off_snaps_lost_pct"].isna().sum() == 0
    assert df["def_snaps_lost_pct"].isna().sum() == 0


def test_build_historical_injury_features_fails_on_unsynced_season():
    """Falla cerrado si se solicita una temporada no sincronizada."""
    with pytest.raises(DataQualityError):
        build_nfl_historical_injury_features([1990])


# ==============================================================================
# 5. Odds Quota & Budget Enforcement Tests
# ==============================================================================

def test_estimate_request_cost():
    # /sports/ cuesta 0 créditos
    assert estimate_request_cost("/sports") == 0
    assert estimate_request_cost("/v4/sports/") == 0

    # 1 mercado, 1 región = 1 crédito
    assert estimate_request_cost("/sports/americanfootball_nfl/odds", {"markets": "h2h", "regions": "us"}) == 1

    # 3 mercados, 1 región = 3 créditos
    assert estimate_request_cost(
        "/sports/americanfootball_nfl/odds",
        {"markets": "h2h,spreads,totals", "regions": "us"}
    ) == 3

    # 3 mercados, 2 regiones = 6 créditos
    assert estimate_request_cost(
        "/sports/americanfootball_nfl/odds",
        {"markets": "h2h,spreads,totals", "regions": "us,eu"}
    ) == 6

    # /events/{id}/markets cuesta 1
    assert estimate_request_cost("/events/abc123/markets") == 1

    # /historical prohibido en plan gratuito
    with pytest.raises(QuotaExceededError, match="prohibidos"):
        estimate_request_cost("/sports/americanfootball_nfl/odds-history")


def _make_test_qs(
    ledger_path: Path,
    raw_root: Path | None = None,
    config: OddsBudgetConfig | None = None,
    remaining: int = 500,
) -> OddsQuotaService:
    qs = OddsQuotaService(config=config, ledger_path=ledger_path, raw_root=raw_root, fail_closed=False)
    qs.bootstrap_ledger(requests_remaining=remaining, force=True)
    return qs


def test_quota_service_reserves_and_records(tmp_path):
    """Verifica el ciclo de reserva, consumo real y actualización del ledger."""
    ledger = tmp_path / "ledger.json"
    raw_root = tmp_path / "raw"
    config = OddsBudgetConfig(total_budget=500, protected_reserve=75)
    qs = _make_test_qs(ledger, raw_root=raw_root, config=config, remaining=500)

    assert qs.requests_remaining == 500
    assert qs.requests_used == 0

    # Reservar 3 créditos en main_lines
    res = qs.reserve_quota(
        endpoint="/sports/americanfootball_nfl/odds",
        params={"markets": "h2h,spreads,totals", "regions": "us", "apiKey": "secret_12345"},
        envelope="main_lines",
        api_key="secret_12345",
    )
    assert res.estimated_cost == 3
    assert "secret_12345" not in str(res.params)
    assert res.api_key_masked.startswith("key_sha_")

    # Registrar respuesta con headers oficiales (x-requests-last: 3)
    entry = qs.record_transaction(
        reservation=res,
        status_code=200,
        response_headers={
            "x-requests-used": "3",
            "x-requests-remaining": "497",
            "x-requests-last": "3",
        },
    )
    assert entry["actual_cost"] == 3
    assert qs.requests_remaining == 497
    assert qs.requests_used == 3
    assert qs.spent_by_envelope["main_lines"] == 3

    # Verificar que el ledger se persistió correctamente
    assert ledger.is_file()
    ledger_data = json.loads(ledger.read_text(encoding="utf-8"))
    assert ledger_data["summary"]["requests_remaining"] == 497
    assert "secret_12345" not in ledger.read_text(encoding="utf-8")


def test_quota_service_enforces_protected_reserve(tmp_path):
    """Si el costo deja el saldo por debajo de 75 créditos, la reserva es rechazada."""
    ledger = tmp_path / "ledger.json"
    config = OddsBudgetConfig(total_budget=500, protected_reserve=75, circuit_breaker_pct=0.10)
    qs = _make_test_qs(ledger, config=config, remaining=76)

    # Pedir 3 créditos dejaría 73 < 75 (reserva protegida)
    with pytest.raises(QuotaExceededError, match="violando la reserva protegida"):
        qs.reserve_quota(
            endpoint="/sports/americanfootball_nfl/odds",
            params={"markets": "h2h,spreads,totals", "regions": "us"},
            envelope="operational",
        )


def test_quota_service_enforces_envelope_limit(tmp_path):
    """Si el gasto en un sobre excede su tope (ej. operational=60), se rechaza."""
    ledger = tmp_path / "ledger.json"
    config = OddsBudgetConfig(operational_budget=60)
    qs = _make_test_qs(ledger, config=config, remaining=500)
    qs.spent_by_envelope["operational"] = 59
    qs._save_ledger()

    # Costo 3 sobrepasaría el tope de 60
    with pytest.raises(QuotaExceededError, match="Sobre 'operational' excedido"):
        qs.reserve_quota(
            endpoint="/sports/americanfootball_nfl/odds",
            params={"markets": "h2h,spreads,totals", "regions": "us"},
            envelope="operational",
        )


def test_circuit_breaker_trips_on_low_balance(tmp_path):
    """El circuit breaker se dispara automáticamente cuando remaining <= 20% o <= 75."""
    ledger = tmp_path / "ledger.json"
    config = OddsBudgetConfig(total_budget=500, protected_reserve=75)
    qs = _make_test_qs(ledger, config=config, remaining=75)

    with pytest.raises(CircuitBreakerOpenError):
        qs.reserve_quota(
            endpoint="/sports/americanfootball_nfl/odds",
            params={"markets": "h2h", "regions": "us"},
            envelope="operational",
        )
    assert qs.circuit_breaker_open is True


def test_circuit_breaker_trips_on_http_429(tmp_path):
    """Un HTTP 429 dispara el circuit breaker inmediatamente en el registro."""
    ledger = tmp_path / "ledger.json"
    qs = _make_test_qs(ledger, remaining=500)
    res = qs.reserve_quota("/sports/americanfootball_nfl/odds", {"markets": "h2h"}, "operational")

    qs.record_transaction(res, status_code=429, response_headers={"x-requests-remaining": "0"})
    assert qs.circuit_breaker_open is True

    # Siguiente petición es rechazada de inmediato
    with pytest.raises(CircuitBreakerOpenError):
        qs.reserve_quota("/sports/americanfootball_nfl/odds", {"markets": "h2h"}, "operational")


def test_circuit_breaker_trips_when_props_envelope_is_consumed(tmp_path):
    config = OddsBudgetConfig(props_budget=1)
    qs = _make_test_qs(tmp_path / "ledger.json", config=config, remaining=500)
    reservation = qs.reserve_quota(
        "/events/event-1/odds", {"markets": "player_pass_yds"}, envelope="props"
    )
    qs.record_transaction(
        reservation, status_code=200,
        response_headers={"x-requests-used": "1", "x-requests-remaining": "499", "x-requests-last": "1"},
    )
    assert qs.circuit_breaker_open is True
    assert qs.breaker_reason == "Sobre de props consumido (1 créditos)"
    with pytest.raises(CircuitBreakerOpenError):
        qs.reserve_quota("/sports/americanfootball_nfl/odds", {"markets": "h2h"}, "main_lines")


def test_raw_persistence_sanitization(tmp_path):
    """La persistencia de respuesta cruda elimina toda clave de API."""
    ledger = tmp_path / "ledger.json"
    raw_root = tmp_path / "raw"
    qs = _make_test_qs(ledger, raw_root=raw_root, remaining=500)
    raw_saved, canonical_saved = qs.persist_raw_response(
        endpoint="/sports/americanfootball_nfl/odds",
        params={"apiKey": "SECRET_KEY_NEVER_LOG", "sport": "nfl"},
        response_body=[{"id": "event_1", "home_team": "DET", "away_team": "GB"}],
        response_headers={"x-requests-remaining": "450", "apiKey": "SECRET_KEY"},
    )
    assert raw_saved.is_file()
    assert canonical_saved.is_file()
    content = raw_saved.read_text(encoding="utf-8")
    assert "SECRET_KEY_NEVER_LOG" not in content
    assert "SECRET_KEY" not in content
    assert "[REDACTED]" in content


# ==============================================================================
# 6. Canonical Odds Normalizer Tests
# ==============================================================================

def test_normalize_odds_response_canonical():
    """Normaliza estructura cruda de The Odds API a esquema canónico estricto."""
    raw = [{
        "id": "game_123",
        "sport_key": "americanfootball_nfl",
        "commence_time": "2024-09-08T17:00:00Z",
        "home_team": "Detroit Lions",
        "away_team": "Los Angeles Rams",
        "bookmakers": [{
            "key": "draftkings",
            "last_update": "2024-09-08T16:50:00Z",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Detroit Lions", "price": -175},
                        {"name": "Los Angeles Rams", "price": 150},
                    ],
                },
                {
                    "key": "spreads",
                    "outcomes": [
                        {"name": "Detroit Lions", "price": -110, "point": -3.5},
                        {"name": "Los Angeles Rams", "price": -110, "point": 3.5},
                    ],
                },
            ],
        }],
    }]

    normalized = normalize_odds_response(raw)
    assert len(normalized) == 4

    h2h_det = next(r for r in normalized if r["market_key"] == "h2h" and r["outcome_name"] == "Detroit Lions")
    assert h2h_det["price_american"] == -175
    assert round(h2h_det["price_decimal"], 2) == 1.57
    assert h2h_det["point_line"] is None

    spread_det = next(r for r in normalized if r["market_key"] == "spreads" and r["outcome_name"] == "Detroit Lions")
    assert spread_det["price_american"] == -110
    assert spread_det["point_line"] == -3.5


# ==============================================================================
# 7. Additional Phase 01 Acceptance Tests (Findings 1, 2, 4, 5, 6, 7)
# ==============================================================================

def test_quota_service_fails_closed_when_ledger_missing(tmp_path):
    """Sin ledger existente, el servicio falla cerrado con QuotaExceededError por defecto."""
    missing_ledger = tmp_path / "non_existent_ledger.json"
    with pytest.raises(QuotaExceededError, match="Ledger de cuota no encontrado"):
        OddsQuotaService(ledger_path=missing_ledger)


def test_quota_service_fails_closed_when_ledger_corrupted(tmp_path):
    """Ante ledger corrupto o JSON inválido, falla cerrado con QuotaExceededError."""
    bad_ledger = tmp_path / "bad_ledger.json"
    bad_ledger.write_text("{invalid json", encoding="utf-8")
    with pytest.raises(QuotaExceededError, match="corrupto o ilegible"):
        OddsQuotaService(ledger_path=bad_ledger)

    # También si falta la estructura requerida
    bad_ledger.write_text(json.dumps({"wrong": "format"}), encoding="utf-8")
    with pytest.raises(QuotaExceededError, match="corrupto o ilegible"):
        OddsQuotaService(ledger_path=bad_ledger)


def test_quota_service_conservative_timeout_accounting(tmp_path):
    """Timeout o error post-dispatch descuenta el costo estimado y no reembolsa."""
    ledger = tmp_path / "ledger.json"
    qs = _make_test_qs(ledger, remaining=275)
    res = qs.reserve_quota(
        endpoint="/sports/americanfootball_nfl/odds",
        params={"markets": "h2h,spreads,totals"},
        envelope="main_lines",
    )
    assert res.estimated_cost == 3

    # Registrar como timeout
    entry = qs.record_transaction(
        reservation=res,
        is_timeout=True,
        error_message="ReadTimeout after 15s",
    )
    assert entry["status"] == "uncertain_timeout"
    assert entry["actual_cost"] == 3
    assert qs.requests_remaining == 272
    assert qs.requests_used == 3
    assert qs.spent_by_envelope["main_lines"] == 3
    assert len(qs.pending_reservations) == 0

    # Verificar ledger persistido
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["summary"]["requests_remaining"] == 272


def test_quota_service_concurrency_and_file_locking(tmp_path):
    """Múltiples hilos reservando cuota concurrentemente no corrompen el ledger."""
    from concurrent.futures import ThreadPoolExecutor
    ledger = tmp_path / "ledger.json"
    _make_test_qs(ledger, remaining=300)

    def do_reserve(idx: int):
        service = OddsQuotaService(ledger_path=ledger)
        return service.reserve_quota(
            endpoint="/sports/americanfootball_nfl/odds",
            params={"markets": "h2h"},
            envelope="operational",
            api_key=f"worker_key_{idx}",
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        reservations = list(executor.map(do_reserve, range(5)))

    assert len(reservations) == 5
    # Recargar ledger y verificar que tiene 5 reservas pendientes registradas
    final_qs = OddsQuotaService(ledger_path=ledger)
    assert len(final_qs.pending_reservations) == 5


def test_quota_service_pending_reservations_persisted_across_restarts(tmp_path):
    """Las reservas pendientes persisten en disco y se recuperan tras reiniciar el servicio."""
    ledger = tmp_path / "ledger.json"
    qs1 = _make_test_qs(ledger, remaining=300)
    res = qs1.reserve_quota(
        endpoint="/sports/americanfootball_nfl/odds",
        params={"markets": "h2h"},
        envelope="operational",
    )
    assert res.reservation_id in qs1.pending_reservations

    # Simular reinicio creando nueva instancia independiente
    qs2 = OddsQuotaService(ledger_path=ledger)
    assert res.reservation_id in qs2.pending_reservations
    assert qs2.pending_reservations[res.reservation_id]["estimated_cost"] == 1


def test_sync_odds_endpoint_rejects_non_nfl():
    """POST /sync rechaza deportes no NFL con HTTP 400 antes de contactar proveedor."""
    import asyncio
    from fastapi import HTTPException
    from app.api.v1.odds import sync_live_data

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(sync_live_data(sport="NBA", db=None))
    assert exc_info.value.status_code == 400
    assert "restringida exclusivamente a NFL" in exc_info.value.detail


def test_fetch_odds_rejects_non_nfl():
    """fetch_odds rechaza deportes no NFL con ValueError cuando fallback=False."""
    import asyncio
    from app.services.odds_service import fetch_odds

    with pytest.raises(ValueError, match="Solo NFL está habilitado"):
        asyncio.run(fetch_odds(sport="NBA", allow_db_fallback=False))


def test_live_ingestion_and_props_reject_non_nfl():
    """fetch_real_player_props y sync_real_games rechazan deportes no NFL sin red."""
    import asyncio
    from app.services.live_ingestion_service import fetch_real_player_props, sync_real_games

    # Props NBA retorna [] inmediatamente
    props = asyncio.run(fetch_real_player_props(event_id="any_event", sport="NBA"))
    assert props == []

    # Sync real games NBA retorna error estructurado sin red
    res = asyncio.run(sync_real_games(sport="NBA", db=None))
    assert res["status"] == "error"
    assert res["error_type"] == "sport_not_allowed"


def test_repeated_snapshot_sync_deduplication(tmp_path, monkeypatch):
    """Sincronizar repetidamente un snapshot no duplica entradas en manifest.json."""
    import sys
    from unittest.mock import MagicMock
    from ml.data_pipeline.nfl_source import sync_nfl_source

    raw_dir = tmp_path / "nflverse"
    manifest_file = raw_dir / "manifest.json"

    dummy_df = pd.DataFrame({"gsis_id": ["p1", "p2"], "name": ["Player 1", "Player 2"]})
    mock_nflreadpy = MagicMock()
    mock_nflreadpy.load_players.return_value = dummy_df
    monkeypatch.setitem(sys.modules, "nflreadpy", mock_nflreadpy)

    # Primera sincronización
    sync_nfl_source(seasons=[2024], datasets=["players_crosswalk"], raw_root=raw_dir)
    data1 = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert len(data1["files"]) == 1

    # Segunda sincronización (repetida)
    sync_nfl_source(seasons=[2024], datasets=["players_crosswalk"], raw_root=raw_dir)
    data2 = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert len(data2["files"]) == 1
    assert data2["files"][0]["dataset"] == "players_crosswalk"
    assert data2["files"][0]["season"] is None


def test_nfl_quality_report_schema_and_completeness():
    """El reporte de calidad formal data/nfl_quality_report.json existe y es completo."""
    from ml.data_pipeline.common import project_root
    report_file = project_root() / "data" / "nfl_quality_report.json"
    assert report_file.is_file()

    report = json.loads(report_file.read_text(encoding="utf-8"))
    assert report["provenance"]["python_library"] == "nflreadpy"
    assert report["provenance"]["library_version"] == "0.1.5"
    assert report["totals"]["all_files_exist"] is True
    assert report["totals"]["all_hashes_match"] is True
    assert report["totals"]["total_files_audited"] == 55
    assert report["totals"]["all_primary_keys_configured"] is True
    assert report["totals"]["primary_key_duplicate_rows"] == 0
    assert all("observed_seasons_count" in d for d in report["datasets_summary"].values())

    limitations = report["library_and_dataset_limitations"]
    assert "snap_counts" in limitations
    assert limitations["snap_counts"]["historical_cutoff"] == 2012
    assert "players_crosswalk" in limitations
    assert limitations["players_crosswalk"]["temporal_partitioning"] is None


def test_raw_persistence_immutable_permissions_and_canonical(tmp_path):
    """persist_raw_response aplica permisos de solo lectura (0o444) y genera canonical."""
    import os
    import stat
    ledger = tmp_path / "ledger.json"
    raw_root = tmp_path / "raw"
    qs = _make_test_qs(ledger, raw_root=raw_root, remaining=500)

    raw_path, canonical_path = qs.persist_raw_response(
        endpoint="/sports/americanfootball_nfl/odds",
        params={"sport": "nfl", "markets": "h2h"},
        response_body=[{
            "id": "game_sample",
            "sport_key": "americanfootball_nfl",
            "commence_time": "2024-09-08T17:00:00Z",
            "home_team": "DET",
            "away_team": "LAR",
            "bookmakers": [{
                "key": "draftkings",
                "markets": [{"key": "h2h", "outcomes": [{"name": "DET", "price": -150}]}],
            }],
        }],
        response_headers={"x-requests-remaining": "400"},
    )
    assert raw_path.is_file()
    assert canonical_path.is_file()

    mode_raw = stat.S_IMODE(os.stat(raw_path).st_mode)
    if os.name != "nt":
        assert mode_raw & 0o222 == 0, f"Raw file {raw_path} no es read-only (mode: {oct(mode_raw)})"
        mode_canon = stat.S_IMODE(os.stat(canonical_path).st_mode)
        assert mode_canon & 0o222 == 0, f"Canonical file {canonical_path} no es read-only (mode: {oct(mode_canon)})"


def test_raw_persistence_append_only_temporal_retention(tmp_path):
    """persist_raw_response conserva distintas capturas en el tiempo (append-only con nombres temporales)."""
    ledger = tmp_path / "ledger.json"
    raw_root = tmp_path / "raw"
    qs = _make_test_qs(ledger, raw_root=raw_root, remaining=500)

    raw1, canon1 = qs.persist_raw_response(
        endpoint="/sports/americanfootball_nfl/odds",
        params={"sport": "nfl", "markets": "h2h"},
        response_body=[{"id": "g1", "home_team": "KC", "away_team": "BAL"}],
        captured_at_utc="2024-09-08T17:00:00+00:00",
    )

    raw2, canon2 = qs.persist_raw_response(
        endpoint="/sports/americanfootball_nfl/odds",
        params={"sport": "nfl", "markets": "h2h"},
        response_body=[{"id": "g1", "home_team": "KC", "away_team": "BAL"}],
        captured_at_utc="2024-09-08T18:00:00+00:00",
    )

    assert raw1 != raw2
    assert canon1 != canon2
    assert raw1.is_file()
    assert raw2.is_file()
    assert canon1.is_file()
    assert canon2.is_file()


def test_raw_persistence_idempotency_on_repeated_capture(tmp_path):
    """Llamar persist_raw_response con idéntico payload y timestamp es idempotente y no falla por permisos 0o444."""
    ledger = tmp_path / "ledger.json"
    raw_root = tmp_path / "raw"
    qs = _make_test_qs(ledger, raw_root=raw_root, remaining=500)

    payload = [{"id": "g1", "home_team": "KC", "away_team": "BAL", "bookmakers": []}]
    t_iso = "2024-09-08T17:00:00+00:00"

    raw1, canon1 = qs.persist_raw_response(
        endpoint="/sports/americanfootball_nfl/odds",
        params={"sport": "nfl"},
        response_body=payload,
        captured_at_utc=t_iso,
    )

    raw2, canon2 = qs.persist_raw_response(
        endpoint="/sports/americanfootball_nfl/odds",
        params={"sport": "nfl"},
        response_body=payload,
        captured_at_utc=t_iso,
    )

    assert raw1 == raw2
    assert canon1 == canon2
    assert raw1.is_file()
    assert canon1.is_file()


def test_raw_persistence_distinguishes_request_identity_and_preserves_existing(tmp_path):
    """Capturas de endpoint/mercado distintos no colisionan aunque compartan payload y segundo UTC."""
    qs = _make_test_qs(tmp_path / "ledger.json", raw_root=tmp_path / "raw", remaining=500)
    payload = [{"id": "g1", "home_team": "KC", "away_team": "BAL"}]
    captured = "2024-09-08T17:00:00+00:00"

    first, _ = qs.persist_raw_response(
        "/sports/americanfootball_nfl/odds", {"sport": "nfl", "markets": "h2h"}, payload,
        captured_at_utc=captured,
    )
    second, _ = qs.persist_raw_response(
        "/events/g1/odds", {"sport": "nfl", "markets": "player_pass_yds"}, payload,
        captured_at_utc=captured,
    )
    assert first != second

    first.write_text("tampered", encoding="utf-8")
    with pytest.raises(OSError, match="inválida"):
        qs.persist_raw_response(
            "/sports/americanfootball_nfl/odds", {"sport": "nfl", "markets": "h2h"}, payload,
            captured_at_utc=captured,
        )
    assert first.read_text(encoding="utf-8") == "tampered"


def test_normalize_odds_response_player_props_dict_root_non_empty(tmp_path):
    """normalize_odds_response soporta raíz dict (props de partido único) y el canónico no queda vacío."""
    ledger = tmp_path / "ledger.json"
    raw_root = tmp_path / "raw"
    qs = _make_test_qs(ledger, raw_root=raw_root, remaining=500)

    props_payload = {
        "id": "evt_nfl_456",
        "sport_key": "americanfootball_nfl",
        "commence_time": "2024-09-08T17:00:00Z",
        "home_team": "DET",
        "away_team": "LAR",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "last_update": "2024-09-08T16:55:00Z",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "outcomes": [
                            {"name": "Over", "description": "Jared Goff", "price": -115, "point": 265.5},
                            {"name": "Under", "description": "Jared Goff", "price": -115, "point": 265.5},
                        ],
                    }
                ],
            }
        ],
    }

    canonical_records = normalize_odds_response(props_payload, captured_at_utc="2024-09-08T17:00:00Z")
    assert len(canonical_records) == 2
    assert canonical_records[0]["outcome_description"] == "Jared Goff"
    assert canonical_records[0]["point_line"] == 265.5
    assert canonical_records[0]["bookmaker"] == "draftkings"
    assert canonical_records[0]["market_key"] == "player_pass_yds"

    raw_path, canonical_path = qs.persist_raw_response(
        endpoint="/events/evt_nfl_456/odds",
        params={"sport": "nfl", "event_id": "evt_nfl_456"},
        response_body=props_payload,
        captured_at_utc="2024-09-08T17:00:00Z",
    )
    canon_data = json.loads(canonical_path.read_text(encoding="utf-8"))
    assert canon_data["records_count"] == 2
    assert len(canon_data["records"]) == 2


def test_separation_of_network_and_persistence_errors(tmp_path, monkeypatch):
    """fetch_odds concilia una respuesta HTTP antes del guardado y no la debita otra vez si falla el disco."""
    import app.services.odds_service as odds_service
    ledger = tmp_path / "ledger.json"
    raw_root = tmp_path / "raw"
    qs = _make_test_qs(ledger, raw_root=raw_root, remaining=500)
    monkeypatch.setattr(odds_service, "quota_service", qs)
    monkeypatch.setattr(odds_service, "API_KEY", "test-api-key")
    monkeypatch.setattr(odds_service, "_ODDS_CACHE", {})

    def mock_persist_error(*args, **kwargs):
        raise OSError("Disco de solo lectura simulado")
    monkeypatch.setattr(qs, "persist_raw_response", mock_persist_error)

    class MockResponse:
        status_code = 200
        headers = {"x-requests-used": "1", "x-requests-remaining": "499", "x-requests-last": "1"}
        def raise_for_status(self):
            return None
        def json(self):
            return []

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, *args, **kwargs):
            return MockResponse()
    monkeypatch.setattr(odds_service.httpx, "AsyncClient", MockClient)

    assert asyncio.run(odds_service.fetch_odds(sport="NFL", markets="h2h")) == []
    assert qs.requests_remaining == 499
    assert len(qs.transactions) == 1
    assert qs.transactions[0]["status"] == "success"
    assert qs.transactions[0]["actual_cost"] == 1
    assert len(qs.pending_reservations) == 0


def test_ledger_reconciliation_breakdown_and_provisional_balance():
    """El ledger reconciliado data/odds_quota_ledger.json distingue las dos api_key y conserva 275 de saldo."""
    from ml.data_pipeline.common import project_root
    prod_ledger = project_root() / "data" / "odds_quota_ledger.json"
    assert prod_ledger.is_file()
    data = json.loads(prod_ledger.read_text(encoding="utf-8"))

    assert "reconciliation" in data
    rec = data["reconciliation"]
    assert rec["status"] == "reconciled_provisional"
    assert rec["provisional_requests_remaining"] == 275

    keys = rec["keys_breakdown"]
    assert "key_sha_b5a2c962" in keys
    assert "key_sha_de093685" in keys

    assert keys["key_sha_de093685"]["provider_reported_used_at_tx"] == 219
    assert "218 créditos" in keys["key_sha_de093685"]["historical_debt_note"]

    assert data["summary"]["requests_remaining"] == 275
    assert data["summary"]["requests_used"] == 225


def test_celery_ingest_odds_task_early_guard_rejects_non_nfl():
    """ingest_odds_task rechaza deportes distintos de NFL al inicio sin tocar BD ni red."""
    from app.workers.celery_app import ingest_odds_task

    res_nba = ingest_odds_task.run(sport="NBA")
    assert res_nba["status"] == "skipped"
    assert res_nba["sport"] == "NBA"
    assert res_nba["error_type"] == "sport_not_allowed"

    res_mlb = ingest_odds_task.run(sport="MLB")
    assert res_mlb["status"] == "skipped"
    assert res_mlb["error_type"] == "sport_not_allowed"


def test_production_ledger_remains_at_275():
    """El ledger real de producción data/odds_quota_ledger.json no fue alterado por las pruebas."""
    from ml.data_pipeline.common import project_root
    prod_ledger = project_root() / "data" / "odds_quota_ledger.json"
    assert prod_ledger.is_file()
    data = json.loads(prod_ledger.read_text(encoding="utf-8"))
    assert data["summary"]["requests_remaining"] == 275
    assert data["summary"]["requests_used"] == 225
    assert data["summary"]["circuit_breaker_open"] is False

