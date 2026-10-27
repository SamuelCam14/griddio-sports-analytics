"""
Pruebas integrales de auditoría de fuentes NBA, desacoplamiento y pipeline de features diario (TAREA-11).
Verifica:
1. Procedencia de fuentes y cero llamadas a APIs de pago (ledger intacto, sports_beat detenido).
2. Manejo de fallos, reintentos y fallback resiliente en nba_ingestion y nba_schedule_service.
3. Validación estricta de timezone awareness y fail-closed ante timestamps naive.
4. Cálculo riguroso de posesiones de Dean Oliver, Pace, OffRating, DefRating y NetRating.
5. Pipeline de features diario exclusivo de NBA: fatiga (days_rest, is_back_to_back, is_3_in_4),
   ratings rodantes con .shift(1), cero data leakage y cero variables semanales de NFL.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.game import Game, Team, ProviderGameCrosswalk
from app.services.nba_ingestion import (
    _get_current_season,
    _safe_fetch,
    _fetch_team_stats_fallback,
    fetch_team_stats,
    fetch_espn_nba_standings,
    NBA_API_HEADERS,
)
from app.services.nba_schedule_service import (
    _utc,
    _same_provider_instant,
    sync_nba_games_from_source,
    NBA_TEAM_ABBR_CROSSWALK,
)
from ml.data_pipeline.common import DataQualityError, project_root, backend_root
from ml.data_pipeline.nba_source import (
    compute_nba_game_possessions_and_ratings,
    load_nba_games,
    load_nba_upcoming_games,
)
from ml.data_pipeline.temporal_features import (
    nba_daily_feature_columns,
    build_nba_daily_temporal_dataset,
    build_nba_daily_matchup_features,
    NBA_DAILY_GAME_STATS,
    NBA_DAILY_FATIGUE_STATS,
)


LEDGER_SHA256 = "a09c01acc3412a31066b2e1f557d3279fc7d1713e969b770be0f8562e6ef35ff"
PRODUCTION_MODEL_SHA256 = "c672f6a3cd5de9a23fa76a8351005a557c0e106cc008d4c22f09c34986fa28c0"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. AUDITORÍA DE FUENTES, PROCEDENCIA Y GOBERNANZA DE RED
# ═══════════════════════════════════════════════════════════════════════════════

def test_odds_quota_ledger_and_production_model_hashes_unaltered():
    """Invariante absoluta: data/odds_quota_ledger.json y logistic.joblib intactos."""
    ledger_path = project_root() / "data" / "odds_quota_ledger.json"
    assert ledger_path.is_file(), "El ledger de cuotas debe existir"
    current_ledger_hash = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    assert current_ledger_hash == LEDGER_SHA256, (
        f"El hash del ledger cambió: {current_ledger_hash} != {LEDGER_SHA256}"
    )

    model_path = backend_root() / "ml" / "models" / "nfl" / "logistic.joblib"
    assert model_path.is_file(), f"El modelo de producción de NFL debe existir en {model_path}"
    current_model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    assert current_model_hash == PRODUCTION_MODEL_SHA256, (
        f"El hash del modelo de producción cambió: {current_model_hash} != {PRODUCTION_MODEL_SHA256}"
    )


def test_nba_ingestion_headers_and_safe_fetch_retries():
    """Verifica que _safe_fetch aplique headers de navegador, timeout y reintentos ante errores."""
    mock_endpoint = MagicMock(side_effect=[Exception("Connection timeout"), Exception("Rate limit"), MagicMock(get_data_frames=lambda: [pd.DataFrame({"test": [1]})])])
    df = _safe_fetch(mock_endpoint)
    assert df is not None
    assert len(df) == 1
    assert mock_endpoint.call_count == 3
    # Comprobar que headers y timeout fueron inyectados
    _, call_kwargs = mock_endpoint.call_args
    assert "headers" in call_kwargs
    assert call_kwargs["headers"] == NBA_API_HEADERS
    assert call_kwargs.get("timeout") == 5


def test_nba_ingestion_safe_fetch_fails_closed_to_none_after_exhaustion():
    """Verifica que _safe_fetch retorne None tras agotar los 3 intentos fallidos."""
    mock_endpoint = MagicMock(side_effect=Exception("Blocked 403"))
    df = _safe_fetch(mock_endpoint)
    assert df is None
    assert mock_endpoint.call_count == 3


@pytest.mark.asyncio
async def test_nba_ingestion_fetch_team_stats_fallback():
    """Verifica que fetch_team_stats recurra al fallback verificado cuando nba_api falla."""
    with patch("app.services.nba_ingestion._safe_fetch", return_value=None):
        df = await fetch_team_stats(season="2025-26")
        assert df is not None
        assert len(df) >= 30, f"Se esperaban al menos 30 equipos en fallback, obtenidos {len(df)}"
        assert "team_id" in df.columns
        assert "off_rating" in df.columns
        assert "def_rating" in df.columns
        assert "net_rating" in df.columns
        assert "pace" in df.columns
        assert "w_pct" in df.columns
        assert (df["pace"] > 0).all()


def test_fetch_espn_nba_standings():
    """Verifica que fetch_espn_nba_standings retorne standings de los 30 equipos."""
    df = fetch_espn_nba_standings()
    assert df is not None
    assert len(df) >= 30
    assert "off_rating" in df.columns
    assert "net_rating" in df.columns


def test_fetch_espn_nba_standings_fallback_on_network_error():
    """Verifica que si la llamada de red a ESPN falla, recurra fail-closed al fallback local."""
    with patch("httpx.Client.get", side_effect=Exception("Network timeout")):
        df = fetch_espn_nba_standings()
        assert df is not None
        assert len(df) >= 30
        assert "off_rating" in df.columns
        assert "net_rating" in df.columns


def test_fetch_team_stats_fallback_season_parsing():
    """Verifica que _fetch_team_stats_fallback resuelva temporadas en varios formatos."""
    # Temporada por string con guion
    df1 = _fetch_team_stats_fallback(season="2024-25")
    assert df1 is not None and len(df1) >= 30

    # Temporada por entero
    df2 = _fetch_team_stats_fallback(season=2024)
    assert df2 is not None and len(df2) >= 30

    # Temporada por string numérico
    df3 = _fetch_team_stats_fallback(season="2024")
    assert df3 is not None and len(df3) >= 30


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CÁLCULO DE POSESIONES Y RATINGS DE DEAN OLIVER EN NBA_SOURCE
# ═══════════════════════════════════════════════════════════════════════════════

def test_compute_nba_game_possessions_and_ratings_valid():
    """Valida el cálculo de posesiones de 4 factores de Dean Oliver y ratings."""
    res = compute_nba_game_possessions_and_ratings(
        home_fga=90.0, home_fta=20.0, home_oreb=10.0, home_tov=14.0, home_score=110.0,
        away_fga=88.0, away_fta=24.0, away_oreb=8.0, away_tov=12.0, away_score=105.0,
    )
    # home_poss = 90 - 10 + 14 + 0.44 * 20 = 102.8
    # away_poss = 88 - 8 + 12 + 0.44 * 24 = 102.56
    # game_poss = 0.5 * (102.8 + 102.56) = 102.68
    assert math.isclose(res["home_possessions"], 102.8, abs_tol=1e-2)
    assert math.isclose(res["away_possessions"], 102.56, abs_tol=1e-2)
    assert math.isclose(res["home_pace"], 102.68, abs_tol=1e-2)
    assert math.isclose(res["away_pace"], 102.68, abs_tol=1e-2)

    # off_rtg = 110 / 102.68 * 100 = 107.1289
    # def_rtg = 105 / 102.68 * 100 = 102.2594
    assert math.isclose(res["home_off_rating"], (110.0 / 102.68) * 100.0, abs_tol=1e-2)
    assert math.isclose(res["home_def_rating"], (105.0 / 102.68) * 100.0, abs_tol=1e-2)
    assert math.isclose(res["away_off_rating"], res["home_def_rating"], abs_tol=1e-6)
    assert math.isclose(res["away_def_rating"], res["home_off_rating"], abs_tol=1e-6)
    assert math.isclose(res["home_net_rating"], res["home_off_rating"] - res["home_def_rating"], abs_tol=1e-6)
    assert math.isclose(res["away_net_rating"], -res["home_net_rating"], abs_tol=1e-6)


@pytest.mark.parametrize("invalid_val", [
    np.nan,
    np.inf,
    -np.inf,
    True,
    False,
    "not_a_number",
    -5.0,
])
def test_compute_nba_game_possessions_fail_closed_on_invalid_inputs(invalid_val):
    """Valida que entradas no finitas, booleanas, no numéricas o negativas levanten DataQualityError."""
    with pytest.raises(DataQualityError):
        compute_nba_game_possessions_and_ratings(
            home_fga=invalid_val, home_fta=20.0, home_oreb=10.0, home_tov=14.0, home_score=110.0,
            away_fga=88.0, away_fta=24.0, away_oreb=8.0, away_tov=12.0, away_score=105.0,
        )
    with pytest.raises(DataQualityError):
        compute_nba_game_possessions_and_ratings(
            home_fga=90.0, home_fta=20.0, home_oreb=10.0, home_tov=14.0, home_score=invalid_val,
            away_fga=88.0, away_fta=24.0, away_oreb=8.0, away_tov=12.0, away_score=105.0,
        )


def test_compute_nba_game_possessions_fail_closed_on_zero_fga_or_negative_possessions():
    """Valida que intentos de campo cero o posesiones no positivas levanten DataQualityError."""
    # FGA = 0
    with pytest.raises(DataQualityError):
        compute_nba_game_possessions_and_ratings(
            home_fga=0.0, home_fta=20.0, home_oreb=10.0, home_tov=14.0, home_score=110.0,
            away_fga=88.0, away_fta=24.0, away_oreb=8.0, away_tov=12.0, away_score=105.0,
        )
    # Posesiones negativas (oreb excesivo)
    with pytest.raises(DataQualityError):
        compute_nba_game_possessions_and_ratings(
            home_fga=10.0, home_fta=0.0, home_oreb=20.0, home_tov=0.0, home_score=50.0,
            away_fga=88.0, away_fta=24.0, away_oreb=8.0, away_tov=12.0, away_score=105.0,
        )


def test_load_nba_games_contains_advanced_metrics():
    """Verifica que load_nba_games incorpore posesiones, pace, ratings y net_rating."""
    games = load_nba_games()
    assert len(games) > 0
    expected_cols = [
        "home_possessions", "away_possessions", "home_pace", "away_pace",
        "home_off_rating", "away_off_rating", "home_def_rating", "away_def_rating",
        "home_net_rating", "away_net_rating",
    ]
    for col in expected_cols:
        assert col in games.columns, f"Columna {col} debe estar presente en load_nba_games"
        assert games[col].notna().all(), f"Columna {col} tiene valores nulos"
        assert np.isfinite(games[col]).all(), f"Columna {col} tiene valores no finitos"

    # Verificar que el pace medio esté en el rango histórico de la NBA (90-115)
    mean_pace = games["home_pace"].mean()
    assert 90.0 <= mean_pace <= 115.0, f"Pace promedio anómalo: {mean_pace}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SCHEDULE SERVICE: TIMEZONE-AWARENESS Y FALLBACK LIVE
# ═══════════════════════════════════════════════════════════════════════════════

def test_nba_schedule_utc_helper():
    """Verifica el helper de conversión de zona horaria UTC."""
    dt_aware = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
    assert _utc(dt_aware) == dt_aware

    # Same instant comparison
    dt1 = datetime(2026, 1, 15, 20, 0, 0, 123456, tzinfo=timezone.utc)
    dt2 = datetime(2026, 1, 15, 20, 0, 0, 654321, tzinfo=timezone.utc)
    assert _same_provider_instant(dt1, dt2) is True


@pytest.mark.asyncio
async def test_nba_schedule_service_rejects_naive_kickoff_fail_closed():
    """Verifica que sync_nba_games_from_source rechace partidos con kickoff naive sin tzinfo."""
    naive_kickoff = datetime(2026, 11, 10, 19, 0)  # Sin tzinfo
    mock_upcoming = pd.DataFrame([{
        "game_id": "naive_test_game_999",
        "game_date": date(2026, 11, 10),
        "game_start_utc": naive_kickoff,
        "season": 2027,
        "home_team_id": "1",
        "away_team_id": "2",
        "home_team_abbreviation": "ATL",
        "away_team_abbreviation": "BOS",
        "source": "Mock source",
        "source_ref": "mock:ref",
    }])

    async with AsyncSessionLocal() as s:
        with patch("app.services.nba_schedule_service.load_nba_upcoming_games", return_value=mock_upcoming):
            res = await sync_nba_games_from_source(s)
            assert res["status"] == "success"
            assert res["rejected"] == 1, "Kickoff naive debe ser rechazado fail-closed"


@pytest.mark.asyncio
async def test_nba_schedule_service_fallback_to_live_espn():
    """Verifica que si la fuente local de upcoming games falla, recurra a ESPN scoreboard live sin persistir fixtures compartidos."""
    mock_events = [{
        "id": "live_espn_99901",
        "date": "2026-11-20T23:00:00Z",
        "season": {"year": 2027},
        "competitions": [{
            "competitors": [
                {"id": "1", "homeAway": "home", "team": {"abbreviation": "ATL", "displayName": "Atlanta Hawks"}},
                {"id": "2", "homeAway": "away", "team": {"abbreviation": "BOS", "displayName": "Boston Celtics"}},
            ]
        }]
    }]

    async with AsyncSessionLocal() as s:
        # Mock commit to ensure test games are NEVER committed to the shared database
        s.commit = AsyncMock()
        try:
            with patch("app.services.nba_schedule_service.load_nba_upcoming_games", side_effect=FileNotFoundError("Local missing")):
                with patch("ml.data_pipeline.nba_source.fetch_espn_nba_scoreboard_live", return_value=mock_events):
                    res = await sync_nba_games_from_source(s)
                    assert res["status"] == "success"
                    assert res["inserted"] >= 1 or res["unchanged"] >= 1
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_nba_schedule_service_empty_local_and_empty_espn_returns_error():
    """Verifica que si ambas fuentes están vacías, sync_nba_games_from_source retorne error explícito."""
    async with AsyncSessionLocal() as s:
        s.commit = AsyncMock()
        try:
            with patch("app.services.nba_schedule_service.load_nba_upcoming_games", return_value=pd.DataFrame()):
                with patch("ml.data_pipeline.nba_source.fetch_espn_nba_scoreboard_live", return_value=[]):
                    res = await sync_nba_games_from_source(s)
                    assert res["status"] == "error"
                    assert res["games_synced"] == 0
                    assert "No se encontraron partidos" in res["detail"]
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_nba_schedule_service_empty_local_and_malformed_espn_returns_error():
    """Verifica que si la fuente local está vacía y ESPN devuelve payload malformado, retorne error explícito."""
    malformed_events = [
        {"id": "broken_1", "competitions": []},
        {"invalid": "data"},
    ]
    async with AsyncSessionLocal() as s:
        s.commit = AsyncMock()
        try:
            with patch("app.services.nba_schedule_service.load_nba_upcoming_games", side_effect=FileNotFoundError("Local missing")):
                with patch("ml.data_pipeline.nba_source.fetch_espn_nba_scoreboard_live", return_value=malformed_events):
                    res = await sync_nba_games_from_source(s)
                    assert res["status"] == "error"
                    assert res["games_synced"] == 0
        finally:
            await s.rollback()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PIPELINE DE FEATURES NBA PROPIO: CERO LEAKAGE Y CERO VARIABLES NFL
# ═══════════════════════════════════════════════════════════════════════════════

def test_nba_daily_feature_columns_schema_and_no_nfl_leakage():
    """Valida que las features diarias de NBA no contengan variables semanales de NFL."""
    cols = nba_daily_feature_columns()
    assert len(cols) == 44, f"Se esperaban 44 features NBA, obtenidas {len(cols)}"

    # Verificar que NINGUNA variable de NFL esté presente
    forbidden_nfl_substrings = ["ppr", "snap", "epa", "success_rate", "injury"]
    for c in cols:
        for sub in forbidden_nfl_substrings:
            assert sub not in c.lower(), f"Variable de NFL detectada en features NBA: {c}"

    # Verificar presencia de fatiga y ratings rodantes
    for prefix in ("home_", "away_"):
        assert f"{prefix}days_rest" in cols
        assert f"{prefix}is_back_to_back" in cols
        assert f"{prefix}is_3_in_4" in cols
        assert f"{prefix}games_in_last_4_days" in cols
        assert f"{prefix}rolling_pace" in cols
        assert f"{prefix}rolling_off_rating" in cols
        assert f"{prefix}rolling_def_rating" in cols
        assert f"{prefix}rolling_net_rating" in cols

    # Diferenciales
    for diff in ["diff_days_rest", "diff_is_back_to_back", "diff_is_3_in_4", "diff_pace", "diff_off_rating", "diff_def_rating", "diff_net_rating"]:
        assert diff in cols


def test_nba_schedule_fatigue_and_shift_1_isolation():
    """
    Verifica que days_rest, is_back_to_back e is_3_in_4 se calculen fielmente
    y que .shift(1) garantice que las estadísticas del partido actual no contaminen
    las métricas del mismo partido (cero data leakage).
    """
    mock_box = {
        "home_fg_pct": 0.45, "away_fg_pct": 0.42,
        "home_offensive_rebounds": 10.0, "away_offensive_rebounds": 8.0,
        "home_total_rebounds": 44.0, "away_total_rebounds": 40.0,
        "home_turnovers": 12.0, "away_turnovers": 14.0,
    }
    # Secuencia de 5 juegos para el equipo A:
    # Juego 1: 2026-01-01 (1er juego)
    # Juego 2: 2026-01-02 (B2B: days_rest=1, is_3_in_4=0)
    # Juego 3: 2026-01-04 (3-en-4: previo 2 juegos en días 1 y 2 -> 3 juegos en días 1, 2, 4 -> is_3_in_4=1)
    # Juego 4: 2026-01-07 (days_rest=3, is_3_in_4=0)
    # Juego 5: 2026-01-10 (days_rest=3, is_3_in_4=0)
    mock_games = [
        {"game_id": "g1", "game_date": date(2026, 1, 1), "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90, "home_win": 1, "home_pace": 100.0, "away_pace": 100.0, "home_off_rating": 100.0, "away_off_rating": 90.0, "home_def_rating": 90.0, "away_def_rating": 100.0, "home_net_rating": 10.0, "away_net_rating": -10.0, **mock_box},
        {"game_id": "g2", "game_date": date(2026, 1, 2), "home_team_id": "A", "away_team_id": "C", "home_score": 110, "away_score": 105, "home_win": 1, "home_pace": 105.0, "away_pace": 105.0, "home_off_rating": 104.7, "away_off_rating": 100.0, "home_def_rating": 100.0, "away_def_rating": 104.7, "home_net_rating": 4.7, "away_net_rating": -4.7, **mock_box},
        {"game_id": "g3", "game_date": date(2026, 1, 4), "home_team_id": "A", "away_team_id": "D", "home_score": 120, "away_score": 115, "home_win": 1, "home_pace": 110.0, "away_pace": 110.0, "home_off_rating": 109.0, "away_off_rating": 104.5, "home_def_rating": 104.5, "away_def_rating": 109.0, "home_net_rating": 4.5, "away_net_rating": -4.5, **mock_box},
        {"game_id": "g4", "game_date": date(2026, 1, 7), "home_team_id": "A", "away_team_id": "E", "home_score": 95, "away_score": 98, "home_win": 0, "home_pace": 95.0, "away_pace": 95.0, "home_off_rating": 100.0, "away_off_rating": 103.1, "home_def_rating": 103.1, "away_def_rating": 100.0, "home_net_rating": -3.1, "away_net_rating": 3.1, **mock_box},
        {"game_id": "g5", "game_date": date(2026, 1, 10), "home_team_id": "A", "away_team_id": "F", "home_score": 108, "away_score": 100, "home_win": 1, "home_pace": 102.0, "away_pace": 102.0, "home_off_rating": 105.8, "away_off_rating": 98.0, "home_def_rating": 98.0, "away_def_rating": 105.8, "home_net_rating": 7.8, "away_net_rating": -7.8, **mock_box},
    ]
    # Añadir juegos para B..F para que tengan historial
    extra_games = [
        {"game_id": f"ge_{t}", "game_date": date(2025, 12, 28), "home_team_id": t, "away_team_id": "Z", "home_score": 100, "away_score": 95, "home_win": 1, "home_pace": 100.0, "away_pace": 100.0, "home_off_rating": 100.0, "away_off_rating": 95.0, "home_def_rating": 95.0, "away_def_rating": 100.0, "home_net_rating": 5.0, "away_net_rating": -5.0, **mock_box}
        for t in ["B", "C", "D", "E", "F"]
    ]
    df_games = pd.DataFrame(extra_games + mock_games)

    ds, _ = build_nba_daily_temporal_dataset(df_games, window=5, minimum_history=1)
    g_a = ds[ds["home_team_id"] == "A"].sort_values("game_date").reset_index(drop=True)

    # g2 (2026-01-02): B2B
    row_g2 = g_a[g_a["game_id"] == "g2"].iloc[0]
    assert row_g2["home_days_rest"] == 1.0
    assert row_g2["home_is_back_to_back"] == 1
    assert row_g2["home_is_3_in_4"] == 0

    # g3 (2026-01-04): 3 en 4
    row_g3 = g_a[g_a["game_id"] == "g3"].iloc[0]
    assert row_g3["home_days_rest"] == 2.0
    assert row_g3["home_is_back_to_back"] == 0
    assert row_g3["home_is_3_in_4"] == 1, "Juegos en días 1, 2 y 4 deben disparar is_3_in_4=1"

    # g4 (2026-01-07): descanso 3 días, no 3 en 4
    row_g4 = g_a[g_a["game_id"] == "g4"].iloc[0]
    assert row_g4["home_days_rest"] == 3.0
    assert row_g4["home_is_3_in_4"] == 0

    # Verificar que el rolling pace de g3 sea la media de g1 y g2 ((100 + 105) / 2 = 102.5)
    # y NO incluya el pace de g3 (110.0) -> prueba de .shift(1) estricto
    assert math.isclose(row_g3["home_rolling_pace"], 102.5, abs_tol=1e-3)
    # Verificar que feature_max_observed_game_date sea estrictamente menor a game_date
    for _, r in g_a.iterrows():
        assert pd.to_datetime(r["feature_max_observed_game_date"]) < pd.to_datetime(r["game_date"])


def test_nba_days_rest_capped_at_10():
    """Verifica que descansos prolongados (offseason/all-star) se acoten estrictamente a 10 días."""
    mock_box = {
        "home_fg_pct": 0.45, "away_fg_pct": 0.42,
        "home_offensive_rebounds": 10.0, "away_offensive_rebounds": 8.0,
        "home_total_rebounds": 44.0, "away_total_rebounds": 40.0,
        "home_turnovers": 12.0, "away_turnovers": 14.0,
    }
    mock_games = [
        {"game_id": "g1", "game_date": date(2026, 1, 1), "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90, "home_win": 1, "home_pace": 100.0, "away_pace": 100.0, "home_off_rating": 100.0, "away_off_rating": 90.0, "home_def_rating": 90.0, "away_def_rating": 100.0, "home_net_rating": 10.0, "away_net_rating": -10.0, **mock_box},
        {"game_id": "g2", "game_date": date(2026, 2, 15), "home_team_id": "A", "away_team_id": "B", "home_score": 105, "away_score": 95, "home_win": 1, "home_pace": 102.0, "away_pace": 102.0, "home_off_rating": 102.0, "away_off_rating": 93.0, "home_def_rating": 93.0, "away_def_rating": 102.0, "home_net_rating": 9.0, "away_net_rating": -9.0, **mock_box},
    ]
    df_games = pd.DataFrame(mock_games)
    ds, _ = build_nba_daily_temporal_dataset(df_games, window=5, minimum_history=1)
    row = ds[ds["game_id"] == "g2"].iloc[0]
    # 45 días de descanso entre Jan 1 y Feb 15 deben ser acotados a 10.0
    assert row["home_days_rest"] == 10.0
    assert row["away_days_rest"] == 10.0

    # En inferencia (matchup features)
    upcoming = pd.DataFrame([{
        "game_id": "g_up", "game_date": date(2026, 4, 1), "home_team_id": "A", "away_team_id": "B",
    }])
    matchups, _ = build_nba_daily_matchup_features(df_games, upcoming, window=5, minimum_history=1, as_of="2026-03-31")
    assert matchups.iloc[0]["home_days_rest"] == 10.0
    assert matchups.iloc[0]["away_days_rest"] == 10.0


def test_build_nba_daily_matchup_features_inference_contract():
    """Valida el cálculo de features de inferencia para partidos programados."""
    games = load_nba_games().sort_values("game_start_utc").reset_index(drop=True)
    past = games.iloc[:1000].copy()
    upcoming = games.iloc[1000:1010].copy()

    min_target_date = pd.to_datetime(upcoming["game_date"].min()).date()
    as_of_date = min_target_date - timedelta(days=1)
    as_of_ts = datetime.combine(as_of_date, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc)
    as_of = as_of_ts.isoformat()
    past_filtered = past[pd.to_datetime(past["game_start_utc"], utc=True) <= as_of_ts].copy()

    matchups, meta = build_nba_daily_matchup_features(past_filtered, upcoming, window=5, minimum_history=1, as_of=as_of)
    assert len(matchups) > 0
    assert meta["sport"] if "sport" in meta else True

    # Verificar que todas las features 44 existan y sean finitas
    cols = nba_daily_feature_columns()
    assert all(c in matchups.columns for c in cols)
    assert not matchups[cols].isna().any().any()
    assert np.isfinite(matchups[cols].to_numpy()).all()

    # Verificar corte temporal estricto
    assert (pd.to_datetime(matchups["feature_max_observed_game_date"]) < pd.to_datetime(matchups["game_date"])).all()


def test_nba_team_boundary_isolation_and_train_inference_parity():
    """
    Verifica que el rolling dentro de build_nba_daily_temporal_dataset nunca fugue entre equipos
    (aislamiento estricto por grupo con transform) y produzca resultados idénticos 1:1 a la ruta de inferencia.
    """
    # Dos equipos con estadísticas deliberadamente divergentes:
    # Equipo HIGH: Pace alto (120), OffRtg alto (130), DefRtg bajo (90)
    # Equipo LOW:  Pace bajo (80),  OffRtg bajo (85),  DefRtg alto (120)
    games_high = [
        {"game_id": f"gh_{i}", "game_date": date(2026, 1, 1 + i * 2), "home_team_id": "HIGH", "away_team_id": f"OPP_{i}",
         "home_score": 130, "away_score": 90, "home_win": 1, "home_pace": 120.0, "away_pace": 120.0,
         "home_off_rating": 130.0, "away_off_rating": 90.0, "home_def_rating": 90.0, "away_def_rating": 130.0,
         "home_net_rating": 40.0, "away_net_rating": -40.0, "home_fg_pct": 0.55, "away_fg_pct": 0.38,
         "home_offensive_rebounds": 12.0, "away_offensive_rebounds": 6.0, "home_total_rebounds": 50.0, "away_total_rebounds": 35.0,
         "home_turnovers": 8.0, "away_turnovers": 16.0}
        for i in range(4)
    ]
    games_low = [
        {"game_id": f"gl_{i}", "game_date": date(2026, 1, 1 + i * 2), "home_team_id": "LOW", "away_team_id": f"OPP_L{i}",
         "home_score": 85, "away_score": 120, "home_win": 0, "home_pace": 80.0, "away_pace": 80.0,
         "home_off_rating": 85.0, "away_off_rating": 120.0, "home_def_rating": 120.0, "away_def_rating": 85.0,
         "home_net_rating": -35.0, "away_net_rating": 35.0, "home_fg_pct": 0.35, "away_fg_pct": 0.52,
         "home_offensive_rebounds": 6.0, "away_offensive_rebounds": 14.0, "home_total_rebounds": 32.0, "away_total_rebounds": 48.0,
         "home_turnovers": 18.0, "away_turnovers": 9.0}
        for i in range(4)
    ]
    opp_games = [
        {"game_id": f"go_{o}", "game_date": date(2025, 12, 20), "home_team_id": o, "away_team_id": "Z",
         "home_score": 100, "away_score": 95, "home_win": 1, "home_pace": 100.0, "away_pace": 100.0,
         "home_off_rating": 100.0, "away_off_rating": 95.0, "home_def_rating": 95.0, "away_def_rating": 100.0,
         "home_net_rating": 5.0, "away_net_rating": -5.0, "home_fg_pct": 0.45, "away_fg_pct": 0.42,
         "home_offensive_rebounds": 10.0, "away_offensive_rebounds": 8.0, "home_total_rebounds": 44.0, "away_total_rebounds": 40.0,
         "home_turnovers": 12.0, "away_turnovers": 14.0}
        for o in [f"OPP_{i}" for i in range(4)] + [f"OPP_L{i}" for i in range(4)]
    ]
    all_games = pd.DataFrame(opp_games + games_high + games_low)

    ds, _ = build_nba_daily_temporal_dataset(all_games, window=5, minimum_history=1)

    # Evaluar el juego 4 de HIGH (gh_3, fecha 2026-01-07)
    row_high_train = ds[ds["game_id"] == "gh_3"].iloc[0]
    # No debe tener NINGUNA contaminación de LOW:
    assert math.isclose(row_high_train["home_rolling_pace"], 120.0, abs_tol=1e-3)
    assert math.isclose(row_high_train["home_rolling_off_rating"], 130.0, abs_tol=1e-3)
    assert math.isclose(row_high_train["home_rolling_def_rating"], 90.0, abs_tol=1e-3)
    assert math.isclose(row_high_train["home_rolling_net_rating"], 40.0, abs_tol=1e-3)

    # Evaluar el juego 4 de LOW (gl_3, fecha 2026-01-07)
    row_low_train = ds[ds["game_id"] == "gl_3"].iloc[0]
    # No debe tener NINGUNA contaminación de HIGH:
    assert math.isclose(row_low_train["home_rolling_pace"], 80.0, abs_tol=1e-3)
    assert math.isclose(row_low_train["home_rolling_off_rating"], 85.0, abs_tol=1e-3)
    assert math.isclose(row_low_train["home_rolling_def_rating"], 120.0, abs_tol=1e-3)
    assert math.isclose(row_low_train["home_rolling_net_rating"], -35.0, abs_tol=1e-3)

    # Ahora verificar paridad 1:1 con la ruta de inferencia (build_nba_daily_matchup_features):
    past_for_inf = all_games[all_games["game_id"] != "gh_3"].copy()
    upcoming_target = pd.DataFrame([{
        "game_id": "gh_3",
        "game_date": date(2026, 1, 7),
        "home_team_id": "HIGH",
        "away_team_id": "OPP_3",
    }])
    matchups, _ = build_nba_daily_matchup_features(
        past_for_inf, upcoming_target, window=5, minimum_history=1, as_of="2026-01-06"
    )
    row_high_inf = matchups.iloc[0]

    # Paridad estricta 1:1 entre entrenamiento e inferencia para HIGH:
    assert math.isclose(row_high_train["home_rolling_pace"], row_high_inf["home_rolling_pace"], abs_tol=1e-3)
    assert math.isclose(row_high_train["home_rolling_off_rating"], row_high_inf["home_rolling_off_rating"], abs_tol=1e-3)
    assert math.isclose(row_high_train["home_rolling_def_rating"], row_high_inf["home_rolling_def_rating"], abs_tol=1e-3)
    assert math.isclose(row_high_train["home_rolling_net_rating"], row_high_inf["home_rolling_net_rating"], abs_tol=1e-3)
    assert math.isclose(row_high_train["home_days_rest"], row_high_inf["home_days_rest"], abs_tol=1e-3)
    assert row_high_train["home_is_back_to_back"] == row_high_inf["home_is_back_to_back"]
    assert row_high_train["home_is_3_in_4"] == row_high_inf["home_is_3_in_4"]


def test_fetch_team_stats_fallback_fails_closed_on_missing_season():
    """Verifica que si se pide una temporada ausente, falle cerrado devolviendo None sin inventar datos."""
    assert _fetch_team_stats_fallback(season="2099-00") is None
    assert _fetch_team_stats_fallback(season="invalid_season") is None


def test_fetch_espn_nba_standings_unobserved_metrics_are_none():
    """Verifica que fetch_espn_nba_standings devuelva None en métricas no observadas (sin números inventados)."""
    df = fetch_espn_nba_standings()
    assert df is not None
    assert len(df) == 30
    for col in ["ts_pct", "ast_ratio", "reb_pct", "pace", "off_rating", "def_rating", "net_rating"]:
        assert col in df.columns
        assert df[col].isna().all(), f"Columna {col} debe ser None/NaN en standings ESPN directos"
