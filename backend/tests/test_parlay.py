import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.models.game import Game, Prediction, Team
from app.services.parlay_service import (
    evaluate_custom_parlay,
    generate_recommended_parlays,
)
from app.api.v1.parlay import get_recommended_parlays
from app.api.v1.predictions import _production_model_enabled, get_approved_model_version


def test_nfl_gate_is_closed_precondition():
    """Verify pre-condition: NFL production gate is closed and no approved version returned."""
    assert _production_model_enabled("NFL") is False
    assert get_approved_model_version("NFL") is None


@pytest.mark.asyncio
async def test_generate_recommended_parlays_empty_when_fewer_than_two_candidates():
    """
    Regression test for former mock fallback:
    Previously, if len(candidates) < 2, parlay_service injected 4 fictitious NBA teams.
    This test verifies that with 0 or 1 candidate, it returns [] with zero mock teams.
    """
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_db.execute.return_value = mock_result

    parlays = await generate_recommended_parlays(db=mock_db)
    assert parlays == []

    # Verify with 1 candidate: must still return [] and NEVER inject mock teams
    pred1 = MagicMock(spec=Prediction)
    pred1.home_win_prob = 0.75
    pred1.away_win_prob = 0.25
    pred1.best_home_ml = -150
    pred1.best_away_ml = 130
    pred1.confluence_tier = "PREMIUM"
    pred1.model_version = "nfl_temporal_v1"

    game1 = MagicMock(spec=Game)
    game1.id = 1
    game1.sport = "NFL"
    game1.status = "scheduled"

    team1 = MagicMock(spec=Team)
    team1.name = "Kansas City Chiefs"
    team1.abbreviation = "KC"

    mock_result.all.return_value = [(pred1, game1, team1)]
    parlays_one = await generate_recommended_parlays(db=mock_db)
    assert parlays_one == []


@pytest.mark.asyncio
async def test_generate_recommended_parlays_rejects_nfl_candidates_when_gate_is_closed():
    """
    Verify that even if 2 scheduled NFL games with high win probability exist in DB,
    they are filtered out because _production_model_enabled('NFL') is False.
    """
    pred1 = MagicMock(spec=Prediction)
    pred1.home_win_prob = 0.75
    pred1.away_win_prob = 0.25
    pred1.best_home_ml = -140
    pred1.best_away_ml = 120
    pred1.confluence_tier = "PREMIUM"
    pred1.model_version = "nfl_temporal_v1"

    game1 = MagicMock(spec=Game)
    game1.id = 1
    game1.sport = "NFL"
    game1.status = "scheduled"

    team1 = MagicMock(spec=Team)
    team1.name = "Kansas City Chiefs"
    team1.abbreviation = "KC"

    pred2 = MagicMock(spec=Prediction)
    pred2.home_win_prob = 0.72
    pred2.away_win_prob = 0.28
    pred2.best_home_ml = -130
    pred2.best_away_ml = 110
    pred2.confluence_tier = "PREMIUM"
    pred2.model_version = "nfl_temporal_v1"

    game2 = MagicMock(spec=Game)
    game2.id = 2
    game2.sport = "NFL"
    game2.status = "scheduled"

    team2 = MagicMock(spec=Team)
    team2.name = "Buffalo Bills"
    team2.abbreviation = "BUF"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(pred1, game1, team1), (pred2, game2, team2)]
    mock_db.execute.return_value = mock_result

    # With sport="NFL"
    parlays_nfl = await generate_recommended_parlays(db=mock_db, sport="NFL")
    assert parlays_nfl == []

    # Without sport filter (defaults to NFL)
    parlays_all = await generate_recommended_parlays(db=mock_db)
    assert parlays_all == []


@pytest.mark.asyncio
async def test_generate_recommended_parlays_rejects_legacy_versions_even_if_gate_open(monkeypatch):
    """
    Verify that if the NFL gate is open with approved version 'nfl_temporal_v1',
    any predictions with legacy versions ('catboost_v1', 'xgboost_v1', 'legacy')
    are strictly rejected and never become parlay candidates.
    """
    # Mock approved model version to 'nfl_temporal_v1'
    monkeypatch.setattr(
        "app.services.parlay_service.get_approved_model_version",
        lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
    )

    pred_legacy1 = MagicMock(spec=Prediction)
    pred_legacy1.home_win_prob = 0.80
    pred_legacy1.away_win_prob = 0.20
    pred_legacy1.best_home_ml = -150
    pred_legacy1.best_away_ml = 130
    pred_legacy1.confluence_tier = "PREMIUM"
    pred_legacy1.model_version = "catboost_legacy_v1"  # Legacy version

    game1 = MagicMock(spec=Game)
    game1.id = 1
    game1.sport = "NFL"
    game1.status = "scheduled"

    team1 = MagicMock(spec=Team)
    team1.name = "Kansas City Chiefs"
    team1.abbreviation = "KC"

    pred_legacy2 = MagicMock(spec=Prediction)
    pred_legacy2.home_win_prob = 0.78
    pred_legacy2.away_win_prob = 0.22
    pred_legacy2.best_home_ml = -140
    pred_legacy2.best_away_ml = 120
    pred_legacy2.confluence_tier = "PREMIUM"
    pred_legacy2.model_version = "xgboost_v1"  # Legacy version

    game2 = MagicMock(spec=Game)
    game2.id = 2
    game2.sport = "NFL"
    game2.status = "scheduled"

    team2 = MagicMock(spec=Team)
    team2.name = "San Francisco 49ers"
    team2.abbreviation = "SF"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(pred_legacy1, game1, team1), (pred_legacy2, game2, team2)]
    mock_db.execute.return_value = mock_result

    # Both predictions have legacy versions, so neither qualifies as candidate
    parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
    assert parlays == []

    # Now verify that when predictions match 'nfl_temporal_v1', they ARE accepted
    pred_valid1 = MagicMock(spec=Prediction)
    pred_valid1.home_win_prob = 0.75
    pred_valid1.away_win_prob = 0.25
    pred_valid1.best_home_ml = -140
    pred_valid1.best_away_ml = 120
    pred_valid1.confluence_tier = "PREMIUM"
    pred_valid1.model_version = "nfl_temporal_v1"  # Exact approved version

    pred_valid2 = MagicMock(spec=Prediction)
    pred_valid2.home_win_prob = 0.70
    pred_valid2.away_win_prob = 0.30
    pred_valid2.best_home_ml = -130
    pred_valid2.best_away_ml = 110
    pred_valid2.confluence_tier = "PREMIUM"
    pred_valid2.model_version = "nfl_temporal_v1"  # Exact approved version

    mock_result.all.return_value = [(pred_valid1, game1, team1), (pred_valid2, game2, team2)]
    parlays_valid = await generate_recommended_parlays(db=mock_db, sport="NFL")
    assert len(parlays_valid) > 0
    assert parlays_valid[0]["viable"] is True


@pytest.mark.asyncio
async def test_generate_recommended_parlays_never_includes_nba_games():
    """Verify that NBA games are excluded even if requested or present in DB."""
    pred_nba = MagicMock(spec=Prediction)
    pred_nba.home_win_prob = 0.85
    pred_nba.away_win_prob = 0.15
    pred_nba.best_home_ml = -200
    pred_nba.best_away_ml = 170
    pred_nba.confluence_tier = "PREMIUM"
    pred_nba.model_version = "nba_temporal_v1"

    game_nba = MagicMock(spec=Game)
    game_nba.id = 100
    game_nba.sport = "NBA"
    game_nba.status = "scheduled"

    team_nba = MagicMock(spec=Team)
    team_nba.name = "Boston Celtics"
    team_nba.abbreviation = "BOS"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(pred_nba, game_nba, team_nba)]
    mock_db.execute.return_value = mock_result

    # sport="NBA" must be rejected immediately in Phase 00
    parlays_nba = await generate_recommended_parlays(db=mock_db, sport="NBA")
    assert parlays_nba == []

    # Default sport="NFL" must also return []
    parlays_default = await generate_recommended_parlays(db=mock_db)
    assert parlays_default == []


@pytest.mark.asyncio
async def test_parlay_recommended_route_defaults_to_nfl_and_excludes_nba():
    """
    Verify route behavior without 'sport' parameter:
    - Defaults to NFL
    - With gate closed, returns status='unavailable', sport='NFL', parlays=[]
    - If sport='NBA' is passed, rejects with status='unavailable'
    """
    mock_db = AsyncMock()

    # 1. Without sport parameter: must default to NFL and return unavailable with gate closed
    response_default = await get_recommended_parlays(db=mock_db)
    assert response_default["status"] == "unavailable"
    assert response_default["sport"] == "NFL"
    assert response_default["total"] == 0
    assert response_default["parlays"] == []
    assert "no hay un modelo aprobado" in response_default["reason"].lower()

    # 2. With sport="NBA": must reject because phase is exclusively NFL
    response_nba = await get_recommended_parlays(sport="NBA", db=mock_db)
    assert response_nba["status"] == "unavailable"
    assert response_nba["sport"] == "NBA"
    assert response_nba["total"] == 0
    assert response_nba["parlays"] == []
    assert "exclusivamente a nfl" in response_nba["reason"].lower()


@pytest.mark.asyncio
async def test_parlay_recommended_route_nfl_gate_closed():
    """Verify route returns status='unavailable' when NFL gate is closed."""
    mock_db = AsyncMock()
    response = await get_recommended_parlays(sport="NFL", db=mock_db)
    assert response["status"] == "unavailable"
    assert response["sport"] == "NFL"
    assert response["total"] == 0
    assert response["parlays"] == []
    assert "no hay un modelo aprobado" in response["reason"].lower()


def test_evaluate_custom_parlay_insufficient_legs():
    """Single leg or empty legs must fail viability."""
    res = evaluate_custom_parlay(legs=[{"team_name": "Chiefs", "prob_model": 0.7, "odds_american": -150}])
    assert res["viable"] is False
    assert res["n_legs"] == 1
    assert "al menos 2 eventos" in res["razon"].lower()


def test_evaluate_custom_parlay_rejects_correlated_same_event_legs():
    """Combining sides/markets of the same event as independent legs must be rejected."""
    # Two legs from game 61 (Moneyline and Total Over)
    res = evaluate_custom_parlay(legs=[
        {"game_id": "61", "event_id": "game_61", "team_name": "Buffalo Bills", "prob_model": 0.57, "odds_american": 145},
        {"game_id": "61", "event_id": "game_61", "team_name": "OVER 54.5", "prob_model": 0.52, "odds_american": -110},
    ])
    assert res["viable"] is False
    assert res["has_correlated_legs"] is True
    assert res["ev_dolares"] is None
    assert "mismo evento" in res["razon"].lower()


def test_evaluate_custom_parlay_totals_lacks_model_prob_and_omits_ev():
    """Totals Over/Under lack corresponding model probabilities; EV must NOT be calculated."""
    res = evaluate_custom_parlay(legs=[
        {"game_id": "61", "event_id": "game_61", "team_name": "Buffalo Bills", "prob_model": 0.57, "odds_american": 145},
        {"game_id": "62", "event_id": "game_62", "team_name": "OVER 48.5", "market": "totals", "prob_model": None, "odds_american": -110},
    ])
    assert res["viable"] is False
    assert res["has_sufficient_model_data"] is False
    assert res["ev_dolares"] is None
    assert res["prob_combinada"] is None
    assert "datos insuficientes" in res["razon"].lower() or "totales" in res["razon"].lower()


def test_evaluate_custom_parlay_manual_calculator_is_clearly_segregated():
    """Manual inputs are clearly tagged as manual calculator, not presented as model picks."""
    res = evaluate_custom_parlay(legs=[
        {"team_name": "Chiefs ML", "prob_model": 0.65, "odds_american": -120, "source": "manual"},
        {"team_name": "Eagles ML", "prob_model": 0.62, "odds_american": -110, "source": "manual"},
    ])
    assert res["is_manual_calculation"] is True
    assert "calculadora manual" in res["razon"].lower()
    assert "no constituyen picks del modelo" in res["razon"].lower()


def test_team_normalization_and_unambiguous_matching_without_hardcoded_exception():
    """Normalize team aliases and reject ambiguous multi-matches without hardcoded LA/BUF exceptions."""
    import pandas as pd
    from app.services.parlay_service import normalize_nfl_team, match_game_in_predictions
    from datetime import datetime

    # Verify normalization across abbreviations and full names
    assert normalize_nfl_team("Los Angeles Rams") == "LAR"
    assert normalize_nfl_team("LA") == "LAR"
    assert normalize_nfl_team("LAR") == "LAR"
    assert normalize_nfl_team("Los Angeles Chargers") == "LAC"
    assert normalize_nfl_team("LAC") == "LAC"
    assert normalize_nfl_team("Buffalo Bills") == "BUF"
    assert normalize_nfl_team("BUF") == "BUF"

    # Synthetic test fixture: unambiguous 1 match
    df_single = pd.DataFrame([
        {
            "game_id": "2026_05_BUF_LA",
            "home_team_name": "Los Angeles Rams",
            "away_team_name": "Buffalo Bills",
            "game_date": "2026-10-12",
            "home_win_probability": 0.4283,
            "away_win_probability": 0.5717,
        }
    ])
    matched = match_game_in_predictions("Los Angeles Rams", "Buffalo Bills", datetime(2026, 10, 13), df_single)
    assert matched is not None
    assert matched["game_id"] == "2026_05_BUF_LA"

    # Synthetic test fixture: ambiguous 2 matches -> must reject
    df_ambiguous = pd.DataFrame([
        {
            "game_id": "2026_05_BUF_LA_1",
            "home_team_name": "Los Angeles Rams",
            "away_team_name": "Buffalo Bills",
            "game_date": "2026-10-12",
        },
        {
            "game_id": "2026_05_BUF_LA_2",
            "home_team_name": "Los Angeles Rams",
            "away_team_name": "Buffalo Bills",
            "game_date": "2026-10-12",
        },
    ])
    matched_ambiguous = match_game_in_predictions("Los Angeles Rams", "Buffalo Bills", datetime(2026, 10, 13), df_ambiguous)
    assert matched_ambiguous is None  # Ambiguous match strictly rejected


@pytest.mark.asyncio
async def test_resolve_and_evaluate_parlay_db_resolution():
    """Verify backend SSOT resolves real game 61 odds and flags correlated same-game legs."""
    from app.core.database import AsyncSessionLocal, engine
    from app.services.parlay_service import resolve_and_evaluate_parlay

    await engine.dispose()
    try:
        async with AsyncSessionLocal() as session:
            # Same event: game 61 moneyline + game 61 totals
            res = await resolve_and_evaluate_parlay(
                legs=[
                    {"game_id": "61", "side": "away", "market": "h2h"},
                    {"game_id": "61", "side": "over", "market": "totals"},
                ],
                db=session,
            )
    finally:
        await engine.dispose()

    assert res["viable"] is False
    assert res["has_correlated_legs"] is True
    assert res["ev_dolares"] is None


def test_all_32_nfl_teams_normalize_unambiguously_without_collisions():
    """Verify all 32 NFL teams normalize to unique canonical codes with zero substring collisions."""
    from app.services.parlay_service import normalize_nfl_team

    expected_32 = [
        ("ARI", "Arizona Cardinals"), ("ATL", "Atlanta Falcons"), ("BAL", "Baltimore Ravens"),
        ("BUF", "Buffalo Bills"), ("CAR", "Carolina Panthers"), ("CHI", "Chicago Bears"),
        ("CIN", "Cincinnati Bengals"), ("CLE", "Cleveland Browns"), ("DAL", "Dallas Cowboys"),
        ("DEN", "Denver Broncos"), ("DET", "Detroit Lions"), ("GB", "Green Bay Packers"),
        ("HOU", "Houston Texans"), ("IND", "Indianapolis Colts"), ("JAX", "Jacksonville Jaguars"),
        ("KC", "Kansas City Chiefs"), ("LV", "Las Vegas Raiders"), ("LAC", "Los Angeles Chargers"),
        ("LAR", "Los Angeles Rams"), ("MIA", "Miami Dolphins"), ("MIN", "Minnesota Vikings"),
        ("NE", "New England Patriots"), ("NO", "New Orleans Saints"), ("NYG", "New York Giants"),
        ("NYJ", "New York Jets"), ("PHI", "Philadelphia Eagles"), ("PIT", "Pittsburgh Steelers"),
        ("SF", "San Francisco 49ers"), ("SEA", "Seattle Seahawks"), ("TB", "Tampa Bay Buccaneers"),
        ("TEN", "Tennessee Titans"), ("WAS", "Washington Commanders"),
    ]

    for code, full_name in expected_32:
        assert normalize_nfl_team(full_name) == code, f"{full_name} failed to normalize to {code}"
        assert normalize_nfl_team(code) == code, f"{code} abbreviation failed to normalize"

    # Specific collision regression checks:
    # 1. New Orleans Saints must NOT match NE
    assert normalize_nfl_team("New Orleans Saints") == "NO"
    # 2. New York Giants and Jets must NOT match NE
    assert normalize_nfl_team("New York Giants") == "NYG"
    assert normalize_nfl_team("New York Jets") == "NYJ"
    # 3. Kansas City Chiefs must NOT match CHI
    assert normalize_nfl_team("Kansas City Chiefs") == "KC"
    # 4. Philadelphia Eagles must NOT match LAR
    assert normalize_nfl_team("Philadelphia Eagles") == "PHI"
    # 5. Tampa Bay Buccaneers must NOT match NE
    assert normalize_nfl_team("Tampa Bay Buccaneers") == "TB"
    # 6. Tennessee Titans must NOT match NE
    assert normalize_nfl_team("Tennessee Titans") == "TEN"


def test_match_game_in_predictions_accepts_iso_string_date():
    """Verify match_game_in_predictions handles string ISO target_date without TypeError."""
    import pandas as pd
    from app.services.parlay_service import match_game_in_predictions

    df = pd.DataFrame([
        {
            "game_id": "2026_05_BUF_LA",
            "home_team_name": "Los Angeles Rams",
            "away_team_name": "Buffalo Bills",
            "game_date": "2026-10-12",
            "home_win_probability": 0.4283,
            "away_win_probability": 0.5717,
        }
    ])
    # Target date passed as string "2026-10-12"
    matched = match_game_in_predictions("Los Angeles Rams", "Buffalo Bills", "2026-10-12", df)
    assert matched is not None
    assert matched["game_id"] == "2026_05_BUF_LA"


@pytest.mark.asyncio
async def test_resolve_and_evaluate_parlay_omits_ev_and_rejects_client_odds_when_snapshot_missing():
    """Verify backend SSOT ignores client-provided odds for real legs if DB lacks snapshot, omitting EV."""
    from app.core.database import AsyncSessionLocal, engine
    from app.services.parlay_service import resolve_and_evaluate_parlay

    await engine.dispose()
    try:
        async with AsyncSessionLocal() as session:
            # Game 999999 does not exist in odds_snapshots, but client tries to supply odds_american: +500
            res = await resolve_and_evaluate_parlay(
                legs=[
                    {"game_id": "999999", "side": "home", "market": "h2h", "odds_american": 500, "source": "real"},
                    {"game_id": "61", "side": "away", "market": "h2h", "source": "real"},
                ],
                db=session,
            )
    finally:
        await engine.dispose()

    # The unobserved leg must have odds_american: None, causing all_odds_present: False and ev_dolares: None
    assert res["viable"] is False
    assert res["ev_dolares"] is None
    evaluated_999999 = [l for l in res["legs_evaluadas"] if "999999" in str(l.get("game_id", "")) or "999999" in str(l.get("team_name", ""))]
    if evaluated_999999:
        assert evaluated_999999[0]["odds_american"] is None


def test_evaluate_custom_parlay_rejects_correlated_legs_from_matchup_pair():
    """Verify evaluate_custom_parlay detects correlated legs sharing the same game matchup pair."""
    from app.services.parlay_service import evaluate_custom_parlay

    res = evaluate_custom_parlay(legs=[
        {"team_name": "Buffalo Bills", "matchup": "Bills @ Rams", "prob_model": 0.57, "odds_american": 145},
        {"team_name": "Los Angeles Rams", "matchup": "Bills @ Rams", "prob_model": 0.43, "odds_american": -165},
    ])
    assert res["viable"] is False
    assert res["has_correlated_legs"] is True
    assert res["ev_dolares"] is None

