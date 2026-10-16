"""
Pruebas exhaustivas para el servicio de calendario NFL (Fase 01 de producción).
Garantiza:
1. Ingesta idempotente desde NFLverse schedules sin fabricar partidos ni fechas.
2. Identidad canónica 1:1 por game_id real.
3. Normalización unívoca de equipos (incluyendo franquicias históricas y aliases LA/LAR).
4. Replay sin duplicados (inserted vs unchanged vs updated).
5. Fail-closed ante fuentes corruptas o ausentes.
"""
from datetime import datetime, timezone
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import pytest

from app.models.game import Game, ProviderGameCrosswalk, Team
from app.services.nfl_schedule_service import (
    sync_nfl_games_from_source,
    TEAM_ABBR_CROSSWALK,
)
from ml.data_pipeline.nfl_source import load_nfl_schedule_results
from ml.data_pipeline.common import DataQualityError


@pytest.fixture
def mock_nfl_teams():
    team_kc = MagicMock(spec=Team)
    team_kc.id = 1
    team_kc.sport = "NFL"
    team_kc.abbreviation = "KC"
    team_kc.name = "Kansas City Chiefs"

    team_lar = MagicMock(spec=Team)
    team_lar.id = 2
    team_lar.sport = "NFL"
    team_lar.abbreviation = "LAR"
    team_lar.name = "Los Angeles Rams"

    team_buf = MagicMock(spec=Team)
    team_buf.id = 3
    team_buf.sport = "NFL"
    team_buf.abbreviation = "BUF"
    team_buf.name = "Buffalo Bills"

    return [team_kc, team_lar, team_buf]


def test_schedule_results_uses_only_hash_verified_completed_regular_games(tmp_path):
    schedule_path = tmp_path / "schedule.parquet"
    pd.DataFrame([
        {"game_id": "2026_01_KC_DEN", "season": 2026, "game_type": "REG", "gameday": "2026-09-10", "gametime": "20:15", "home_team": "KC", "away_team": "DEN", "home_score": 21, "away_score": 17},
        {"game_id": "2026_06_DEN_SEA", "season": 2026, "game_type": "REG", "gameday": "2026-10-15", "gametime": "20:15", "home_team": "DEN", "away_team": "SEA", "home_score": None, "away_score": None},
        {"game_id": "2025_22_KC_BUF", "season": 2025, "game_type": "SB", "gameday": "2026-02-08", "gametime": "18:30", "home_team": "KC", "away_team": "BUF", "home_score": 31, "away_score": 27},
    ]).to_parquet(schedule_path, index=False)
    digest = hashlib.sha256(schedule_path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps({"files": [{
        "dataset": "schedules", "season": 2026, "path": str(schedule_path), "sha256": digest,
    }]}), encoding="utf-8")

    result = load_nfl_schedule_results(
        as_of=pd.Timestamp("2026-10-15T23:59:00Z"), raw_root=tmp_path,
    )

    assert result["game_id"].tolist() == ["2026_01_KC_DEN"]
    assert result.iloc[0]["home_score"] == 21
    assert bool(result.iloc[0]["home_win"]) is True
    assert result.attrs["schedule_source_refs"] == [f"nflverse:schedule:2026:{digest}"]


def test_schedule_results_rejects_corrupted_source_hash(tmp_path):
    schedule_path = tmp_path / "schedule.parquet"
    pd.DataFrame(columns=["game_id", "season", "game_type", "gameday", "gametime", "home_team", "away_team", "home_score", "away_score"]).to_parquet(schedule_path, index=False)
    (tmp_path / "manifest.json").write_text(json.dumps({"files": [{
        "dataset": "schedules", "season": 2026, "path": str(schedule_path), "sha256": "invalid",
    }]}), encoding="utf-8")

    with pytest.raises(DataQualityError, match="hash SHA-256"):
        load_nfl_schedule_results(raw_root=tmp_path)


@pytest.mark.asyncio
async def test_sync_nfl_games_idempotent_insert_and_replay(mock_nfl_teams):
    """Verifica que la primera corrida inserta y la segunda corrida marca unchanged sin duplicados."""
    sample_df = pd.DataFrame([
        {
            "game_id": "2026_06_BUF_LA",
            "game_date": datetime(2026, 10, 18).date(),
            "game_start_utc": datetime(2026, 10, 18, 20, 25, tzinfo=timezone.utc),
            "season": 2026,
            "home_team_id": "LA",
            "away_team_id": "BUF",
            "source": "nflverse schedule",
            "source_ref": "nflverse:schedule:2026:testsha",
        }
    ])

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    # Primera corrida: teams query retorna equipos, select Game retorna None
    mock_teams_res = MagicMock()
    mock_teams_res.scalars.return_value.all.return_value = mock_nfl_teams

    mock_game_res_1 = MagicMock()
    mock_game_res_1.scalar_one_or_none.return_value = None
    mock_candidates_res = MagicMock()
    mock_candidates_res.scalars.return_value.all.return_value = []
    mock_crosswalk_res = MagicMock()
    mock_crosswalk_res.scalar_one_or_none.return_value = None

    mock_db.execute.side_effect = [mock_teams_res, mock_game_res_1, mock_candidates_res, mock_crosswalk_res]

    with patch("app.services.nfl_schedule_service.load_nfl_upcoming_games", return_value=sample_df):
        res1 = await sync_nfl_games_from_source(mock_db)

    assert res1["status"] == "success"
    assert res1["inserted"] == 1
    assert res1["updated"] == 0
    assert res1["unchanged"] == 0
    assert res1["rejected"] == 0
    assert mock_db.add.called
    assert mock_db.commit.called

    # Segunda corrida: Game ya existe con idénticos kickoff y source_ref -> unchanged
    existing_game = MagicMock(spec=Game)
    existing_game.external_id = "2026_06_BUF_LA"
    existing_game.home_team_id = 2
    existing_game.away_team_id = 3
    existing_game.game_date = datetime(2026, 10, 18, 20, 25, tzinfo=timezone.utc)
    existing_game.source_ref = "nflverse:schedule:2026:testsha"

    mock_game_res_2 = MagicMock()
    mock_game_res_2.scalar_one_or_none.return_value = existing_game
    mock_crosswalk_res_2 = MagicMock()
    mock_crosswalk_res_2.scalar_one_or_none.return_value = None
    mock_db.execute.side_effect = [mock_teams_res, mock_game_res_2, mock_crosswalk_res_2]
    mock_db.add.reset_mock()

    with patch("app.services.nfl_schedule_service.load_nfl_upcoming_games", return_value=sample_df):
        res2 = await sync_nfl_games_from_source(mock_db)

    assert res2["status"] == "success"
    assert res2["inserted"] == 0
    assert res2["updated"] == 0
    assert res2["unchanged"] == 1
    assert res2["rejected"] == 0
    assert any(isinstance(call.args[0], ProviderGameCrosswalk) for call in mock_db.add.call_args_list)


@pytest.mark.asyncio
async def test_sync_nfl_games_updates_on_rescheduled_kickoff(mock_nfl_teams):
    """Si el kickoff cambia en el calendario oficial, el registro existente se actualiza."""
    sample_df = pd.DataFrame([
        {
            "game_id": "2026_06_BUF_LA",
            "game_date": datetime(2026, 10, 18).date(),
            "game_start_utc": datetime(2026, 10, 18, 23, 15, tzinfo=timezone.utc),  # Cambio de hora
            "season": 2026,
            "home_team_id": "LAR",
            "away_team_id": "BUF",
            "source": "nflverse schedule",
            "source_ref": "nflverse:schedule:2026:newsha",
        }
    ])

    existing_game = MagicMock(spec=Game)
    existing_game.external_id = "2026_06_BUF_LA"
    existing_game.home_team_id = 2
    existing_game.away_team_id = 3
    existing_game.game_date = datetime(2026, 10, 18, 20, 25, tzinfo=timezone.utc)
    existing_game.source_ref = "nflverse:schedule:2026:oldsha"

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_teams_res = MagicMock()
    mock_teams_res.scalars.return_value.all.return_value = mock_nfl_teams
    mock_game_res = MagicMock()
    mock_game_res.scalar_one_or_none.return_value = existing_game
    mock_crosswalk_res = MagicMock()
    mock_crosswalk_res.scalar_one_or_none.return_value = None
    mock_db.execute.side_effect = [mock_teams_res, mock_game_res, mock_crosswalk_res]

    with patch("app.services.nfl_schedule_service.load_nfl_upcoming_games", return_value=sample_df):
        res = await sync_nfl_games_from_source(mock_db)

    assert res["status"] == "success"
    assert res["updated"] == 1
    assert res["inserted"] == 0
    assert res["unchanged"] == 0
    assert existing_game.game_date == datetime(2026, 10, 18, 23, 15, tzinfo=timezone.utc)
    assert existing_game.source_ref == "nflverse:schedule:2026:newsha"


@pytest.mark.asyncio
async def test_sync_nfl_games_rejects_unresolved_team(mock_nfl_teams):
    """Partidos con equipos no existentes en catálogo son rechazados sin crear partido corrupto."""
    sample_df = pd.DataFrame([
        {
            "game_id": "2026_99_XYZ_KC",
            "game_date": datetime(2026, 11, 1).date(),
            "game_start_utc": datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc),
            "season": 2026,
            "home_team_id": "KC",
            "away_team_id": "XYZ_UNKNOWN",  # Equipo desconocido
            "source": "nflverse schedule",
            "source_ref": "nflverse:schedule:2026:testsha",
        }
    ])

    mock_db = AsyncMock()
    mock_teams_res = MagicMock()
    mock_teams_res.scalars.return_value.all.return_value = mock_nfl_teams
    mock_db.execute.side_effect = [mock_teams_res]

    with patch("app.services.nfl_schedule_service.load_nfl_upcoming_games", return_value=sample_df):
        res = await sync_nfl_games_from_source(mock_db)

    assert res["status"] == "failed"
    assert res["inserted"] == 0
    assert res["rejected"] == 1
    assert not mock_db.add.called


@pytest.mark.asyncio
async def test_sync_nfl_games_fails_closed_when_source_errors():
    """Falla cerrado con status error si la fuente lanza excepción."""
    mock_db = AsyncMock()
    with patch("app.services.nfl_schedule_service.load_nfl_upcoming_games", side_effect=RuntimeError("Corrupted manifest")):
        res = await sync_nfl_games_from_source(mock_db)

    assert res["status"] == "error"
    assert res["games_synced"] == 0
    assert "Corrupted manifest" in res["detail"]
