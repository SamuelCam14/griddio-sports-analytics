"""
Tests de regresión para Modo Preview vs Modo Cohorte Oficial NFL (TAREA-8).

Verifica:
1. Juegos futuros con cuotas antiguas/en caché (>36h) generan picks con pick_status="preview"
   y status_reason descriptivo ("Early Lean").
2. Juegos dentro de la ventana de 2h con cuotas frescas generan picks con
   pick_status="official_cohort" y status_reason oficial.
3. Filtrado por query param `status`:
   - status="preview" retorna exclusivamente picks preliminares.
   - status="official_cohort" retorna exclusivamente picks oficiales confirmados.
   - status="all" retorna ambos.
4. Endpoints de Dashboard/UI (get_today_picks, get_game_prediction) NO registran
   intentos en `nfl_cohort_registry.jsonl` (aislamiento estricto de UI frente a cohorte).
5. get_game_prediction expone pick_status y status_reason para partidos individuales.
6. Manejo fail-closed defensivo ante cuotas de mercado corruptas o imposibles (momios 0, -50, etc.).
"""
import pytest
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from unittest.mock import MagicMock, AsyncMock, patch

from app.models.game import Game, Team, Prediction, OddsSnapshot
from app.api.v1.predictions import get_today_picks, get_game_prediction, get_top_picks
from app.services.nfl_prospective_cohort_service import (
    NFLProspectiveCohortService,
    CohortAttemptRecord,
    DEFAULT_VARIANT_TAG,
)


_DEFAULT_CUTOFF = object()


def _build_mock_cohort_attempt(
    game_id: int | str,
    attempt_status: str = "evaluated",
    variant_tag: str = DEFAULT_VARIANT_TAG,
    model_version: str = "nfl_temporal_v1",
    attempt_timestamp_utc: Optional[str | datetime] = None,
    as_of_cutoff_utc: Any = _DEFAULT_CUTOFF,
    home_win_probability: Optional[float] = None,
    away_win_probability: Optional[float] = None,
    market_odds: Optional[dict] = None,
):
    rec = MagicMock(spec=CohortAttemptRecord)
    rec.record_id = f"mock_{game_id}_{variant_tag}"
    rec.game_id = str(game_id)
    rec.attempt_status = attempt_status
    rec.variant_tag = variant_tag
    rec.model_version = model_version
    if isinstance(attempt_timestamp_utc, datetime):
        rec.attempt_timestamp_utc = attempt_timestamp_utc.isoformat()
    else:
        rec.attempt_timestamp_utc = attempt_timestamp_utc
    if as_of_cutoff_utc is _DEFAULT_CUTOFF:
        rec.as_of_cutoff_utc = rec.attempt_timestamp_utc
    elif isinstance(as_of_cutoff_utc, datetime):
        rec.as_of_cutoff_utc = as_of_cutoff_utc.isoformat()
    else:
        rec.as_of_cutoff_utc = as_of_cutoff_utc
    rec.home_win_probability = home_win_probability
    rec.away_win_probability = away_win_probability
    rec.market_odds = market_odds
    rec.is_settled = False
    return rec


def _build_mock_game(game_id: int, kickoff_dt: datetime, sport: str = "NFL"):
    game = MagicMock(spec=Game)
    game.id = game_id
    game.sport = sport
    game.game_date = kickoff_dt
    game.status = "scheduled"
    game.home_team_id = 100 + game_id
    game.away_team_id = 200 + game_id
    return game


def _build_mock_team(team_id: int, name: str, abbr: str):
    team = MagicMock(spec=Team)
    team.id = team_id
    team.name = name
    team.abbreviation = abbr
    team.city = "City"
    team.conference = "AFC"
    team.division = "East"
    return team


def _build_mock_prediction(game_id: int, home_prob: float = 0.65, away_prob: float = 0.35, pred_dt: datetime = None):
    pred = MagicMock(spec=Prediction)
    pred.id = 1000 + game_id
    pred.game_id = game_id
    pred.model_version = "nfl_temporal_v1"
    now = datetime.now(timezone.utc)
    pred.predicted_at = pred_dt or (now - timedelta(hours=12))
    pred.as_of = pred.predicted_at
    pred.home_win_prob = home_prob
    pred.away_win_prob = away_prob
    pred.model_agreement = 3
    pred.confluence_tier = "HIGH"
    pred.best_bookmaker = "draftkings"
    pred.best_home_ml = -150
    pred.best_away_ml = 130
    pred.home_ev = 12.5
    pred.away_ev = -15.0
    pred.home_kelly = 0.045
    pred.away_kelly = 0.0
    pred.home_has_edge = True
    pred.away_has_edge = False
    pred.key_factors = ["Modelo temporal optimizado", "Confluencia 3/3"]
    return pred


def _build_mock_snapshot(
    game_id: int,
    observed_dt: datetime,
    home_ml: int = -150,
    away_ml: int = +130,
    home_fair: float = 0.58,
    away_fair: float = 0.42,
    bookmaker: str = "draftkings",
    is_stale: bool = False,
):
    snap = MagicMock(spec=OddsSnapshot)
    snap.game_id = game_id
    snap.observed_at = observed_dt
    snap.captured_at = observed_dt
    snap.bookmaker = bookmaker
    snap.home_bookmaker = bookmaker
    snap.away_bookmaker = bookmaker
    snap.home_ml = home_ml
    snap.away_ml = away_ml
    snap.home_spread = -3.5
    snap.away_spread = +3.5
    snap.home_spread_ml = -110
    snap.away_spread_ml = -110
    snap.total_line = 47.5
    snap.over_ml = -110
    snap.under_ml = -110
    snap.home_implied_prob = home_fair
    snap.away_implied_prob = away_fair
    snap.is_stale = is_stale
    return snap


@pytest.mark.asyncio
async def test_preview_mode_picks_returned_with_old_or_cached_odds():
    """
    Juego entre semana a 72h del kickoff con cuota observada hace 48h (>36h límite).
    Antes de TAREA-8, este juego se descartaba (`continue`).
    Con Modo Preview, se retorna con pick_status='preview' y badge 'Early Lean'.
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(days=3)  # 72h en el futuro
    obs = now - timedelta(hours=48)    # 48h de antigüedad (> 36h)

    game = _build_mock_game(101, kickoff)
    pred = _build_mock_prediction(101, home_prob=0.68, away_prob=0.32)
    home_team = _build_mock_team(201, "Kansas City Chiefs", "KC")
    away_team = _build_mock_team(301, "Buffalo Bills", "BUF")
    snap = _build_mock_snapshot(101, obs, home_ml=-160, away_ml=+140)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            # Consulta principal: pred, game, home, away
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx == 2:
            # Consulta H2H snapshot
            res.scalar_one_or_none.return_value = snap
            return res
        elif call_idx == 3:
            # Consulta spread snapshot
            res.scalar_one_or_none.return_value = snap
            return res
        elif call_idx == 4:
            # Consulta total snapshot
            res.scalar_one_or_none.return_value = snap
            return res
        else:
            res.scalar_one_or_none.return_value = None
            res.all.return_value = []
            return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["sport"] == "NFL"
    assert result["total_picks"] == 1
    assert result["preview_picks"] == 1
    assert result["official_cohort_picks"] == 0

    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert pick["pick_side"] == "home"
    assert pick["pick_team_name"] == "Kansas City Chiefs"
    assert pick["momio"] == -160
    assert pick["ev_dolares"] > 0.0


@pytest.mark.asyncio
async def test_official_cohort_mode_picks_within_2h_and_fresh_odds():
    """
    Juego a 1 hora del kickoff con cuota observada hace 30 minutos (<=36h y <=2h kickoff).
    Predicción generada a 25 min del kickoff (dentro de la ventana de 2h).
    Registro confirmado en la cohorte prospectiva con attempt_status='evaluated'.
    Cumple los 5 criterios de elegibilidad de cohorte -> pick_status='official_cohort'.
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)   # 1h en el futuro (<= 2h)
    obs = now - timedelta(minutes=30)    # 30min de antigüedad (fresca)
    pred_dt = now - timedelta(minutes=25)  # Inferencia generada dentro de la ventana oficial de 2h

    game = _build_mock_game(102, kickoff)
    pred = _build_mock_prediction(102, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(202, "San Francisco 49ers", "SF")
    away_team = _build_mock_team(302, "Seattle Seahawks", "SEA")
    snap = _build_mock_snapshot(102, obs, home_ml=-180, away_ml=+155, home_fair=0.61, away_fair=0.39)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx == 2:
            res.scalar_one_or_none.return_value = snap
            return res
        elif call_idx == 3:
            res.scalar_one_or_none.return_value = snap
            return res
        elif call_idx == 4:
            res.scalar_one_or_none.return_value = snap
            return res
        else:
            res.scalar_one_or_none.return_value = None
            res.all.return_value = []
            return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    mock_attempt = _build_mock_cohort_attempt(
        102,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt,
        as_of_cutoff_utc=pred_dt,
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -180,
            "away_ml": 155,
            "home_fair_prob": 0.61,
            "away_fair_prob": 0.39,
            "captured_at_utc": obs.isoformat(),
            "is_stale": False,
        },
    )
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["sport"] == "NFL"
    assert result["total_picks"] == 1
    assert result["preview_picks"] == 0
    assert result["official_cohort_picks"] == 1

    pick = result["picks"][0]
    assert pick["pick_status"] == "official_cohort"
    assert "oficial confirmado" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_status_query_filtering_behavior():
    """
    Verifica que el parámetro `status` filtre correctamente:
    - status="preview" solo incluye picks en modo preview.
    - status="official_cohort" solo incluye picks oficiales confirmados.
    - status="all" incluye ambos.
    """
    now = datetime.now(timezone.utc)
    # Juego 1: Preview (kickoff en 48h, cuota de hace 40h)
    game1 = _build_mock_game(103, now + timedelta(hours=48))
    pred1 = _build_mock_prediction(103, home_prob=0.65, away_prob=0.35)
    home1 = _build_mock_team(203, "Dallas Cowboys", "DAL")
    away1 = _build_mock_team(303, "Philadelphia Eagles", "PHI")
    snap1 = _build_mock_snapshot(103, now - timedelta(hours=40), home_ml=-140, away_ml=+120)

    # Juego 2: Oficial (kickoff en 1h, cuota de hace 15min, inferencia a 10min)
    game2 = _build_mock_game(104, now + timedelta(hours=1))
    pred2 = _build_mock_prediction(104, home_prob=0.55, away_prob=0.45, pred_dt=now - timedelta(minutes=10))
    home2 = _build_mock_team(204, "Green Bay Packers", "GB")
    away2 = _build_mock_team(304, "Detroit Lions", "DET")
    snap2 = _build_mock_snapshot(104, now - timedelta(minutes=15), home_ml=-115, away_ml=-105, home_fair=0.52, away_fair=0.48)

    def make_db_mock():
        call_idx = 0

        async def mock_execute(stmt):
            nonlocal call_idx
            call_idx += 1
            res = MagicMock()
            if call_idx == 1:
                res.all.return_value = [(pred1, game1, home1, away1), (pred2, game2, home2, away2)]
                return res
            elif call_idx in (2, 3, 4):  # snap1 (h2h, spread, total)
                res.scalar_one_or_none.return_value = snap1
                return res
            elif call_idx in (5, 6, 7):  # snap2 (h2h, spread, total)
                res.scalar_one_or_none.return_value = snap2
                return res
            else:
                res.scalar_one_or_none.return_value = None
                res.all.return_value = []
                return res

        mdb = AsyncMock()
        mdb.execute.side_effect = mock_execute
        return mdb

    mock_attempt2 = _build_mock_cohort_attempt(
        104,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=now - timedelta(minutes=10),
        as_of_cutoff_utc=now - timedelta(minutes=10),
        home_win_probability=0.55,
        away_win_probability=0.45,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -115,
            "away_ml": -105,
            "home_fair_prob": 0.52,
            "away_fair_prob": 0.48,
            "captured_at_utc": (now - timedelta(minutes=15)).isoformat(),
            "is_stale": False,
        },
    )
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt2]):
        # 1. Filtro status="preview"
        res_prev = await get_today_picks(sport="NFL", status="preview", db=make_db_mock())
        assert res_prev["total_picks"] == 1
        assert res_prev["picks"][0]["pick_status"] == "preview"
        assert res_prev["picks"][0]["game_id"] == 103

        # 2. Filtro status="official_cohort"
        res_off = await get_today_picks(sport="NFL", status="official_cohort", db=make_db_mock())
        assert res_off["total_picks"] == 1
        assert res_off["picks"][0]["pick_status"] == "official_cohort"
        assert res_off["picks"][0]["game_id"] == 104

        # 3. Filtro status="all"
        res_all = await get_today_picks(sport="NFL", status="all", db=make_db_mock())
        assert res_all["total_picks"] == 2
        statuses = [p["pick_status"] for p in res_all["picks"]]
        assert "preview" in statuses
        assert "official_cohort" in statuses


@pytest.mark.asyncio
async def test_preview_picks_do_not_leak_into_cohort_registry():
    """
    Invariante crítico de seguridad: Los picks generados en Modo Preview para UI
    NUNCA deben registrarse como intentos oficiales en NFLProspectiveCohortService ni
    escribir en nfl_cohort_registry.jsonl.
    """
    now = datetime.now(timezone.utc)
    game = _build_mock_game(105, now + timedelta(days=2))
    pred = _build_mock_prediction(105, home_prob=0.60, away_prob=0.40)
    home_team = _build_mock_team(205, "Miami Dolphins", "MIA")
    away_team = _build_mock_team(305, "New York Jets", "NYJ")
    snap = _build_mock_snapshot(105, now - timedelta(hours=48), home_ml=-130, away_ml=+110)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        else:
            res.scalar_one_or_none.return_value = None
            res.all.return_value = []
            return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    with patch.object(NFLProspectiveCohortService, "record_attempt") as mock_record:
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

        assert result["total_picks"] == 1
        assert result["picks"][0]["pick_status"] == "preview"
        # Invariante comprobado: record_attempt nunca fue llamado
        mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_get_game_prediction_endpoint_exposes_pick_status():
    """
    get_game_prediction expone correctamente pick_status y status_reason para un partido específico.
    """
    now = datetime.now(timezone.utc)
    game_prev = _build_mock_game(106, now + timedelta(days=4))
    pred_prev = _build_mock_prediction(106, home_prob=0.62, away_prob=0.38)
    snap_prev = _build_mock_snapshot(106, now - timedelta(hours=24), home_ml=-145, away_ml=+125)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.scalar_one_or_none.return_value = pred_prev
            return res
        elif call_idx == 2:
            res.scalar_one_or_none.return_value = game_prev
            return res
        elif call_idx == 3:
            res.scalar_one_or_none.return_value = snap_prev
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    res = await get_game_prediction(game_id=106, db=mock_db)

    assert res["game_id"] == 106
    assert res["pick_status"] == "preview"
    assert "Early Lean" in res["status_reason"]
    assert "probabilidades" in res
    assert res["probabilidades"]["home_win"] == 0.62

    # Caso 2: Oficial Confirmado (ventana <= 2h, cuota fresca, registro en cohorte)
    game_off = _build_mock_game(106, now + timedelta(hours=1))
    pred_off = _build_mock_prediction(106, home_prob=0.72, away_prob=0.28, pred_dt=now - timedelta(minutes=15))
    snap_off = _build_mock_snapshot(106, now - timedelta(minutes=20), home_ml=-160, away_ml=+140, home_fair=0.60, away_fair=0.40)

    call_idx_off = 0

    async def mock_execute_off(stmt):
        nonlocal call_idx_off
        call_idx_off += 1
        res = MagicMock()
        if call_idx_off == 1:
            res.scalar_one_or_none.return_value = pred_off
            return res
        elif call_idx_off == 2:
            res.scalar_one_or_none.return_value = game_off
            return res
        elif call_idx_off == 3:
            res.scalar_one_or_none.return_value = snap_off
            return res
        return res

    mock_db_off = AsyncMock()
    mock_db_off.execute.side_effect = mock_execute_off

    mock_attempt_106 = _build_mock_cohort_attempt(
        106,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=now - timedelta(minutes=15),
        as_of_cutoff_utc=now - timedelta(minutes=15),
        home_win_probability=0.72,
        away_win_probability=0.28,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -160,
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": (now - timedelta(minutes=20)).isoformat(),
            "is_stale": False,
        },
    )
    with patch.object(NFLProspectiveCohortService, "get_registered_attempt", return_value=mock_attempt_106):
        res_off = await get_game_prediction(game_id=106, db=mock_db_off)

    assert res_off["game_id"] == 106
    assert res_off["pick_status"] == "official_cohort"
    assert "oficial confirmado" in res_off["status_reason"].lower()


@pytest.mark.asyncio
async def test_fail_closed_on_corrupt_odds():
    """
    Momios corruptos o imposibles (0, -50, strings no numéricos) no causan error 500
    y se descartan fail-closed sin emitir picks con EV fraudulento.
    """
    now = datetime.now(timezone.utc)
    game = _build_mock_game(107, now + timedelta(hours=10))
    pred = _build_mock_prediction(107, home_prob=0.60, away_prob=0.40)
    home_team = _build_mock_team(207, "Denver Broncos", "DEN")
    away_team = _build_mock_team(307, "Las Vegas Raiders", "LV")

    corrupt_cases = [
        {"home_ml": 0, "away_ml": +130},
        {"home_ml": -50, "away_ml": +130},
        {"home_ml": "invalid_string", "away_ml": +130},
        {"home_ml": -150, "away_ml": "bad_value"},
    ]

    for odds_case in corrupt_cases:
        snap_corrupt = _build_mock_snapshot(
            107,
            now - timedelta(hours=1),
            home_ml=odds_case["home_ml"],
            away_ml=odds_case["away_ml"],
        )

        call_idx = 0

        async def mock_execute(stmt):
            nonlocal call_idx
            call_idx += 1
            res = MagicMock()
            if call_idx == 1:
                res.all.return_value = [(pred, game, home_team, away_team)]
                return res
            elif call_idx in (2, 3, 4):
                res.scalar_one_or_none.return_value = snap_corrupt
                return res
            return res

        mock_db = AsyncMock()
        mock_db.execute.side_effect = mock_execute

        res = await get_today_picks(sport="NFL", status="all", db=mock_db)
        assert res["total_picks"] == 0
        assert res["excluded_missing_market_data"] >= 1


@pytest.mark.asyncio
async def test_missing_captured_at_timestamp_cannot_be_official_cohort():
    """
    Invariante crítico de no-síntesis:
    Un partido dentro de la ventana de 2h pero cuyo snapshot carece de captured_at verificado
    (captured_at is None) NUNCA debe promoverse a 'official_cohort' usando observed_at sintético.
    Debe fallar cerrado y permanecer como 'preview' ('Early Lean').
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)   # Dentro de la ventana pre-kickoff (<= 2h)
    obs = now - timedelta(minutes=20)    # Antigüedad fresca (20 min)
    pred_dt = now - timedelta(minutes=15)

    game = _build_mock_game(108, kickoff)
    pred = _build_mock_prediction(108, home_prob=0.72, away_prob=0.28, pred_dt=pred_dt)
    home_team = _build_mock_team(208, "Houston Texans", "HOU")
    away_team = _build_mock_team(308, "Indianapolis Colts", "IND")

    snap = _build_mock_snapshot(108, obs, home_ml=-200, away_ml=+170, home_fair=0.64, away_fair=0.36)
    # Timestamp de captura explícitamente ausente / no verificado
    snap.captured_at = None

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    mock_attempt = _build_mock_cohort_attempt(108, "evaluated")
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    # No puede ser oficial sin captured_at verificado
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "carece de captured_at_utc verificado" in pick["status_reason"]


@pytest.mark.asyncio
async def test_top_picks_endpoint_status_filtering():
    """
    Verifica que el endpoint /predictions/top admita filtrado por `status` (preview vs official_cohort).
    """
    now = datetime.now(timezone.utc)
    # Juego 1: Preview (72h pre-kickoff, cuota de hace 40h)
    game1 = _build_mock_game(109, now + timedelta(hours=72))
    pred1 = _build_mock_prediction(109, home_prob=0.75, away_prob=0.25)
    pred1.home_ev = 20.0
    home1 = _build_mock_team(209, "Chiefs", "KC")
    away1 = _build_mock_team(309, "Raiders", "LV")
    snap1 = _build_mock_snapshot(109, now - timedelta(hours=40), home_ml=-180, away_ml=+155)

    # Juego 2: Oficial (1h pre-kickoff, cuota fresca de hace 10min, inferencia a 10min)
    game2 = _build_mock_game(110, now + timedelta(hours=1))
    pred2 = _build_mock_prediction(110, home_prob=0.68, away_prob=0.32, pred_dt=now - timedelta(minutes=10))
    pred2.home_ev = 15.0
    home2 = _build_mock_team(210, "Bills", "BUF")
    away2 = _build_mock_team(310, "Dolphins", "MIA")
    snap2 = _build_mock_snapshot(110, now - timedelta(minutes=10), home_ml=-150, away_ml=+130, home_fair=0.58, away_fair=0.42)

    def make_db():
        call_idx = 0

        async def mock_execute(stmt):
            nonlocal call_idx
            call_idx += 1
            res = MagicMock()
            if call_idx == 1:
                res.all.return_value = [(pred1, game1, home1, away1), (pred2, game2, home2, away2)]
                return res
            elif call_idx in (2, 3, 4):
                res.scalar_one_or_none.return_value = snap1
                return res
            elif call_idx in (5, 6, 7):
                res.scalar_one_or_none.return_value = snap2
                return res
            return res

        mdb = AsyncMock()
        mdb.execute.side_effect = mock_execute
        return mdb

    mock_attempt110 = _build_mock_cohort_attempt(
        110,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=now - timedelta(minutes=10),
        as_of_cutoff_utc=now - timedelta(minutes=10),
        home_win_probability=0.68,
        away_win_probability=0.32,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -150,
            "away_ml": 130,
            "home_fair_prob": 0.58,
            "away_fair_prob": 0.42,
            "captured_at_utc": (now - timedelta(minutes=10)).isoformat(),
            "is_stale": False,
        },
    )
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt110]):
        # 1. Filtro preview en top picks
        top_prev = await get_top_picks(sport="NFL", status="preview", db=make_db())
        assert len(top_prev["top_picks"]) == 1
        assert top_prev["top_picks"][0]["pick_status"] == "preview"
        assert top_prev["top_picks"][0]["game_id"] == 109

        # 2. Filtro official_cohort en top picks
        top_off = await get_top_picks(sport="NFL", status="official_cohort", db=make_db())
        assert len(top_off["top_picks"]) == 1
        assert top_off["top_picks"][0]["pick_status"] == "official_cohort"
        assert top_off["top_picks"][0]["game_id"] == 110

        # 3. Filtro all en top picks
        top_all = await get_top_picks(sport="NFL", status="all", db=make_db())
        assert len(top_all["top_picks"]) == 2


@pytest.mark.asyncio
async def test_old_prediction_outside_2h_remains_preview_despite_fresh_odds():
    """
    Caso adversarial 1 (Auditoría Codex):
    Un partido a 1h del kickoff con cuota fresca (15m) pero con predicción as_of/predicted_at
    de hace 12h (fuera de la ventana pre-kickoff oficial <= 2h).
    Incluso si existiera un registro en cohorte o cuotas frescas, la inferencia no corresponde
    a la ventana final de 2h (sin inactivos finales) -> DEBE permanecer preview ('Early Lean').
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)
    obs = now - timedelta(minutes=15)
    pred_old_dt = now - timedelta(hours=12)

    game = _build_mock_game(111, kickoff)
    pred = _build_mock_prediction(111, home_prob=0.67, away_prob=0.33, pred_dt=pred_old_dt)
    home_team = _build_mock_team(211, "Chiefs", "KC")
    away_team = _build_mock_team(311, "Broncos", "DEN")
    snap = _build_mock_snapshot(111, obs, home_ml=-160, away_ml=+140, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    mock_attempt = _build_mock_cohort_attempt(111, "evaluated")
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    assert result["official_cohort_picks"] == 0
    assert result["preview_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "confirmación oficial requiere inferencia a ≤ 2h" in pick["status_reason"]


@pytest.mark.asyncio
async def test_unregistered_attempt_remains_preview_despite_timing():
    """
    Caso adversarial 2 (Auditoría Codex):
    Partido a 1h del kickoff, predicción generada a 15 min, cuotas frescas verificadas.
    Cumple ventana <= 2h, pero NO existe evidencia de registro en nfl_cohort_registry.jsonl.
    El badge 'Pick Oficial Confirmado' no debe emitirse sin inscripción en el log append-only.
    Debe permanecer preview ('Early Lean: Elegible en ventana pre-kickoff pero pendiente de registro').
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)
    obs = now - timedelta(minutes=20)
    pred_dt = now - timedelta(minutes=15)

    game = _build_mock_game(112, kickoff)
    pred = _build_mock_prediction(112, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(212, "Eagles", "PHI")
    away_team = _build_mock_team(312, "Giants", "NYG")
    snap = _build_mock_snapshot(112, obs, home_ml=-175, away_ml=+150, home_fair=0.61, away_fair=0.39)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    # Sin registros en la cohorte prospectiva (intento no registrado)
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    assert result["official_cohort_picks"] == 0
    assert result["preview_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "pendiente de registro en cohorte prospectiva" in pick["status_reason"]


@pytest.mark.asyncio
async def test_missing_fair_probabilities_cannot_be_official_cohort():
    """
    Caso adversarial 3 (Auditoría Codex):
    Partido dentro de la ventana de 2h con cuota captured_at verificado,
    pero snapshot carece de ambas probabilidades justas (home_fair_prob=None, away_fair_prob=None).
    check_cohort_eligibility debe fallar cerrado y el pick debe clasificarse como preview ('Early Lean').
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)
    obs = now - timedelta(minutes=20)
    pred_dt = now - timedelta(minutes=15)

    game = _build_mock_game(113, kickoff)
    pred = _build_mock_prediction(113, home_prob=0.65, away_prob=0.35, pred_dt=pred_dt)
    home_team = _build_mock_team(213, "Ravens", "BAL")
    away_team = _build_mock_team(313, "Steelers", "PIT")
    snap = _build_mock_snapshot(113, obs, home_ml=-150, away_ml=+130, home_fair=None, away_fair=None)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    mock_attempt = _build_mock_cohort_attempt(113, "evaluated")
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    assert result["official_cohort_picks"] == 0
    assert result["preview_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Faltan probabilidades justas" in pick["status_reason"]


@pytest.mark.asyncio
async def test_uncorrelated_old_registered_attempt_remains_preview():
    """
    Caso adversarial (Auditoría Codex):
    Partido a 1h del kickoff con cuota fresca (15m) e inferencia reciente (10m pre-kickoff).
    Sin embargo, el registro append-only de cohorte tiene un attempt_timestamp_utc de hace 10 horas
    (por ejemplo, una inferencia obsoleta de la mañana).
    El pick NO debe promoverse a 'official_cohort'; debe permanecer en 'preview' ('Early Lean')
    con motivo explícito de timestamp descorrelacionado.
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)
    obs = now - timedelta(minutes=15)
    pred_dt = now - timedelta(minutes=10)
    old_attempt_dt = now - timedelta(hours=10)

    game = _build_mock_game(114, kickoff)
    pred = _build_mock_prediction(114, home_prob=0.68, away_prob=0.32, pred_dt=pred_dt)
    home_team = _build_mock_team(214, "Detroit Lions", "DET")
    away_team = _build_mock_team(314, "Minnesota Vikings", "MIN")
    snap = _build_mock_snapshot(114, obs, home_ml=-155, away_ml=+135, home_fair=0.59, away_fair=0.41)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    mock_attempt_old = _build_mock_cohort_attempt(
        114,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=old_attempt_dt.isoformat(),
        as_of_cutoff_utc=old_attempt_dt.isoformat(),
        home_win_probability=0.68,
        away_win_probability=0.32,
    )
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt_old]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    assert result["official_cohort_picks"] == 0
    assert result["preview_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "descorrelacionado" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_discordant_variant_tag_registered_attempt_remains_preview():
    """
    Caso adversarial (Auditoría Codex):
    Partido en ventana de 2h con cuota fresca e inferencia sincronizada.
    Sin embargo, el intento en el registro de cohorte pertenece a otra variante no oficial
    (ej. 'spread_dual_penalty' en vez de DEFAULT_VARIANT_TAG).
    El pick DEBE permanecer en 'preview' con motivo explícito de variante discordante.
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)
    obs = now - timedelta(minutes=15)
    pred_dt = now - timedelta(minutes=10)

    game = _build_mock_game(115, kickoff)
    pred = _build_mock_prediction(115, home_prob=0.68, away_prob=0.32, pred_dt=pred_dt)
    home_team = _build_mock_team(215, "Seattle Seahawks", "SEA")
    away_team = _build_mock_team(315, "Arizona Cardinals", "ARI")
    snap = _build_mock_snapshot(115, obs, home_ml=-150, away_ml=+130, home_fair=0.58, away_fair=0.42)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    mock_attempt_bad_variant = _build_mock_cohort_attempt(
        115,
        "evaluated",
        variant_tag="spread_dual_penalty",  # Variante discordante
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.68,
        away_win_probability=0.32,
    )
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt_bad_variant]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    assert result["official_cohort_picks"] == 0
    assert result["preview_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "discordante" in pick["status_reason"].lower() or "no coincide" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_discordant_probabilities_registered_attempt_remains_preview():
    """
    Caso adversarial (Auditoría Codex):
    Partido en ventana de 2h con cuota fresca e inferencia en timestamp concordante,
    pero las probabilidades registradas en el log append-only difieren de la predicción servida
    (ej. registro tiene 0.52/0.48 pero la predicción servida tiene 0.70/0.30).
    El pick DEBE permanecer en 'preview' con motivo explícito de probabilidades discordantes.
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)
    obs = now - timedelta(minutes=15)
    pred_dt = now - timedelta(minutes=10)

    game = _build_mock_game(116, kickoff)
    pred = _build_mock_prediction(116, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(216, "Dallas Cowboys", "DAL")
    away_team = _build_mock_team(316, "Washington Commanders", "WAS")
    snap = _build_mock_snapshot(116, obs, home_ml=-170, away_ml=+145, home_fair=0.61, away_fair=0.39)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    mock_attempt_bad_prob = _build_mock_cohort_attempt(
        116,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.52,  # Discordante con 0.70
        away_win_probability=0.48,  # Discordante con 0.30
    )
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt_bad_prob]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    assert result["official_cohort_picks"] == 0
    assert result["preview_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "discordante" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_discordant_market_odds_registered_attempt_remains_preview():
    """
    Caso adversarial (Auditoría Codex):
    Partido en ventana de 2h con cuotas y timestamp concordantes,
    pero las probabilidades justas registradas en market_odds del intento de cohorte
    difieren significativamente de las cuotas del snapshot actual servido.
    El pick DEBE permanecer en 'preview'.
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)
    obs = now - timedelta(minutes=15)
    pred_dt = now - timedelta(minutes=10)

    game = _build_mock_game(117, kickoff)
    pred = _build_mock_prediction(117, home_prob=0.65, away_prob=0.35, pred_dt=pred_dt)
    home_team = _build_mock_team(217, "Baltimore Ravens", "BAL")
    away_team = _build_mock_team(317, "Cleveland Browns", "CLE")
    snap = _build_mock_snapshot(117, obs, home_ml=-155, away_ml=+135, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    mock_attempt_bad_odds = _build_mock_cohort_attempt(
        117,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.65,
        away_win_probability=0.35,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -110,
            "away_ml": -110,
            "home_fair_prob": 0.45,  # Discordante con 0.60
            "away_fair_prob": 0.55,  # Discordante con 0.40
            "captured_at_utc": obs.isoformat(),
            "is_stale": False,
        },
    )
    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[mock_attempt_bad_odds]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    assert result["official_cohort_picks"] == 0
    assert result["preview_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "discordante" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_exact_100_percent_correlated_attempt_promotes_to_official_cohort():
    """
    Caso positivo estricto (Auditoría Codex):
    Inscripción en nfl_cohort_registry.jsonl con correspondencia 100% exacta:
    - variant_tag == DEFAULT_VARIANT_TAG ("baseline_logistic_c672f6a3")
    - model_version == "nfl_temporal_v1"
    - attempt_timestamp_utc y as_of_cutoff_utc coincidentes con pred_ts
    - home_win_probability y away_win_probability exactamente coincidentes
    - market_odds coherente con el snapshot servido
    - ventana pre-kickoff <= 2h y cuota fresca
    Certifica que TANTO get_today_picks COMO get_game_prediction promueven el pick
    a 'official_cohort' con el badge 'Pick oficial confirmado para cohorte prospectiva'.
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)
    obs = now - timedelta(minutes=20)
    pred_dt = now - timedelta(minutes=15)

    game = _build_mock_game(118, kickoff)
    pred = _build_mock_prediction(118, home_prob=0.68, away_prob=0.32, pred_dt=pred_dt)
    home_team = _build_mock_team(218, "Pittsburgh Steelers", "PIT")
    away_team = _build_mock_team(318, "Cincinnati Bengals", "CIN")
    snap = _build_mock_snapshot(118, obs, home_ml=-160, away_ml=+140, home_fair=0.60, away_fair=0.40)

    # 1. Verificación en get_today_picks
    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    correlated_attempt = _build_mock_cohort_attempt(
        118,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.68,
        away_win_probability=0.32,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -160,
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": obs.isoformat(),
            "is_stale": False,
        },
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[correlated_attempt]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["sport"] == "NFL"
    assert result["total_picks"] == 1
    assert result["preview_picks"] == 0
    assert result["official_cohort_picks"] == 1

    pick = result["picks"][0]
    assert pick["pick_status"] == "official_cohort"
    assert "oficial confirmado" in pick["status_reason"].lower()

    # 2. Verificación en get_game_prediction
    call_idx_g = 0

    async def mock_execute_game(stmt):
        nonlocal call_idx_g
        call_idx_g += 1
        res = MagicMock()
        if call_idx_g == 1:
            res.scalar_one_or_none.return_value = pred
            return res
        elif call_idx_g == 2:
            res.scalar_one_or_none.return_value = game
            return res
        elif call_idx_g == 3:
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db_game = AsyncMock()
    mock_db_game.execute.side_effect = mock_execute_game

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[correlated_attempt]):
        res_game = await get_game_prediction(game_id=118, db=mock_db_game)

    assert res_game["game_id"] == 118
    assert res_game["pick_status"] == "official_cohort"
    assert "oficial confirmado" in res_game["status_reason"].lower()


@pytest.mark.asyncio
async def test_duplicate_registered_attempts_selection_and_settlement():
    """
    Caso adversarial de duplicados y liquidación:
    Para un mismo partido existen dos entradas en el historial:
    - Intento 1: intento previo abstención por incertidumbre ('abstained').
    - Intento 2: intento posterior evaluado y liquidado ('evaluated', is_settled=True).
    find_correlated_attempt debe evaluar los candidatos y seleccionar el intento evaluado
    que correlaciona con la predicción actual, promoviéndolo a 'official_cohort'.
    """
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=1)
    obs = now - timedelta(minutes=20)
    pred_dt = now - timedelta(minutes=15)

    game = _build_mock_game(119, kickoff)
    pred = _build_mock_prediction(119, home_prob=0.71, away_prob=0.29, pred_dt=pred_dt)
    home_team = _build_mock_team(219, "Buffalo Bills", "BUF")
    away_team = _build_mock_team(319, "Miami Dolphins", "MIA")
    snap = _build_mock_snapshot(119, obs, home_ml=-165, away_ml=+145, home_fair=0.61, away_fair=0.39)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    # Intento 1: preliminar marcado como abstención
    rec_abstained = _build_mock_cohort_attempt(
        119,
        "abstained",
        attempt_timestamp_utc=(now - timedelta(hours=8)).isoformat(),
    )
    rec_abstained.abstention_reason = "qb_uncertainty"

    # Intento 2: evaluado y liquidado, exactamente correlacionado con pred
    rec_evaluated = _build_mock_cohort_attempt(
        119,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.71,
        away_win_probability=0.29,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -165,
            "away_ml": 145,
            "home_fair_prob": 0.61,
            "away_fair_prob": 0.39,
            "captured_at_utc": obs.isoformat(),
            "is_stale": False,
        },
    )
    rec_evaluated.is_settled = True
    rec_evaluated.home_score = 27
    rec_evaluated.away_score = 24
    rec_evaluated.home_win = 1

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec_abstained, rec_evaluated]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    assert result["official_cohort_picks"] == 1
    assert result["preview_picks"] == 0
    pick = result["picks"][0]
    assert pick["pick_status"] == "official_cohort"
    assert "oficial confirmado" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_missing_model_version_in_registered_attempt_remains_preview():
    """
    Un registro de intento sin model_version no puede ser promovido a official_cohort.
    """
    now = datetime.now(timezone.utc)
    pred_dt = now - timedelta(minutes=10)
    obs = now - timedelta(minutes=15)
    ko = now + timedelta(hours=1)

    game = _build_mock_game(120, ko)
    pred = _build_mock_prediction(120, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(220, "Ravens", "BAL")
    away_team = _build_mock_team(320, "Steelers", "PIT")
    snap = _build_mock_snapshot(120, obs, home_ml=-160, away_ml=140, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    rec = _build_mock_cohort_attempt(
        120,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version=None,
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -160,
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": obs.isoformat(),
        },
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "carece de model_version" in pick["status_reason"]


@pytest.mark.asyncio
async def test_corrupt_or_missing_cutoff_in_registered_attempt_remains_preview():
    """
    Un registro de intento con as_of_cutoff_utc ausente o inválido no puede ser promovido.
    """
    now = datetime.now(timezone.utc)
    pred_dt = now - timedelta(minutes=10)
    obs = now - timedelta(minutes=15)
    ko = now + timedelta(hours=1)

    game = _build_mock_game(121, ko)
    pred = _build_mock_prediction(121, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(221, "Packers", "GB")
    away_team = _build_mock_team(321, "Bears", "CHI")
    snap = _build_mock_snapshot(121, obs, home_ml=-160, away_ml=140, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    rec = _build_mock_cohort_attempt(
        121,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=None,  # Ausente
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -160,
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": obs.isoformat(),
        },
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "carece de as_of_cutoff_utc" in pick["status_reason"]


@pytest.mark.asyncio
async def test_missing_market_odds_in_registered_attempt_remains_preview():
    """
    Un registro de intento sin snapshot de mercado (market_odds=None) no puede ser promovido.
    """
    now = datetime.now(timezone.utc)
    pred_dt = now - timedelta(minutes=10)
    obs = now - timedelta(minutes=15)
    ko = now + timedelta(hours=1)

    game = _build_mock_game(122, ko)
    pred = _build_mock_prediction(122, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(222, "Eagles", "PHI")
    away_team = _build_mock_team(322, "Giants", "NYG")
    snap = _build_mock_snapshot(122, obs, home_ml=-160, away_ml=140, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    rec = _build_mock_cohort_attempt(
        122,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds=None,  # Ausente
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "carece de snapshot de cuotas de mercado" in pick["status_reason"]


@pytest.mark.asyncio
async def test_discordant_bookmaker_in_registered_attempt_remains_preview():
    """
    Un registro de intento con un bookmaker discordante ("fanduel" vs "draftkings") no puede ser promovido.
    """
    now = datetime.now(timezone.utc)
    pred_dt = now - timedelta(minutes=10)
    obs = now - timedelta(minutes=15)
    ko = now + timedelta(hours=1)

    game = _build_mock_game(123, ko)
    pred = _build_mock_prediction(123, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(223, "Lions", "DET")
    away_team = _build_mock_team(323, "Vikings", "MIN")
    snap = _build_mock_snapshot(123, obs, home_ml=-160, away_ml=140, home_fair=0.60, away_fair=0.40, bookmaker="draftkings")

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    rec = _build_mock_cohort_attempt(
        123,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds={
            "bookmaker": "fanduel",  # Discordante
            "home_ml": -160,
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": obs.isoformat(),
            "is_stale": False,
        },
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "bookmaker de mercado discordante" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_discordant_moneylines_in_registered_attempt_remains_preview():
    """
    Un registro de intento con moneylines discordantes (-140 vs -160) no puede ser promovido.
    """
    now = datetime.now(timezone.utc)
    pred_dt = now - timedelta(minutes=10)
    obs = now - timedelta(minutes=15)
    ko = now + timedelta(hours=1)

    game = _build_mock_game(124, ko)
    pred = _build_mock_prediction(124, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(224, "Texans", "HOU")
    away_team = _build_mock_team(324, "Colts", "IND")
    snap = _build_mock_snapshot(124, obs, home_ml=-160, away_ml=140, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    rec = _build_mock_cohort_attempt(
        124,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -140,  # Discordante con snap (-160)
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": obs.isoformat(),
            "is_stale": False,
        },
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "moneyline home de mercado discordante" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_stale_market_odds_in_registered_attempt_remains_preview():
    """
    Un registro de intento con snapshot marcado como is_stale=True no puede ser promovido.
    """
    now = datetime.now(timezone.utc)
    pred_dt = now - timedelta(minutes=10)
    obs = now - timedelta(minutes=15)
    ko = now + timedelta(hours=1)

    game = _build_mock_game(125, ko)
    pred = _build_mock_prediction(125, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(225, "Bengals", "CIN")
    away_team = _build_mock_team(325, "Browns", "CLE")
    snap = _build_mock_snapshot(125, obs, home_ml=-160, away_ml=140, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    rec = _build_mock_cohort_attempt(
        125,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -160,
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": obs.isoformat(),
            "is_stale": True,  # Obsoleto
        },
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "obsoleto" in pick["status_reason"]


@pytest.mark.asyncio
async def test_fractional_moneyline_in_registered_attempt_remains_preview():
    """
    Un registro de intento con moneyline fraccionario (-160.4) no debe redondearse
    para coincidir con -160; debe fallar cerrado y permanecer como preview.
    """
    now = datetime.now(timezone.utc)
    pred_dt = now - timedelta(minutes=10)
    obs = now - timedelta(minutes=15)
    ko = now + timedelta(hours=1)

    game = _build_mock_game(126, ko)
    pred = _build_mock_prediction(126, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(226, "Bengals", "CIN")
    away_team = _build_mock_team(326, "Browns", "CLE")
    snap = _build_mock_snapshot(126, obs, home_ml=-160, away_ml=140, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    rec = _build_mock_cohort_attempt(
        126,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -160.4,  # Fraccionario, NO entero (no debe redondearse a -160)
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": obs.isoformat(),
            "is_stale": False,
        },
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "fraccionario no entero" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_missing_is_stale_in_registered_attempt_remains_preview():
    """
    Un registro de intento sin el campo is_stale debe fallar cerrado a preview.
    """
    now = datetime.now(timezone.utc)
    pred_dt = now - timedelta(minutes=10)
    obs = now - timedelta(minutes=15)
    ko = now + timedelta(hours=1)

    game = _build_mock_game(127, ko)
    pred = _build_mock_prediction(127, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(227, "Bengals", "CIN")
    away_team = _build_mock_team(327, "Browns", "CLE")
    snap = _build_mock_snapshot(127, obs, home_ml=-160, away_ml=140, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    rec_missing = _build_mock_cohort_attempt(
        127,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -160,
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": obs.isoformat(),
            # is_stale ausente
        },
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec_missing]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "carece del campo is_stale" in pick["status_reason"].lower()


@pytest.mark.asyncio
async def test_invalid_type_is_stale_in_registered_attempt_remains_preview():
    """
    Un registro de intento con is_stale de tipo/valor inválido ('false' string) debe fallar cerrado a preview.
    """
    now = datetime.now(timezone.utc)
    pred_dt = now - timedelta(minutes=10)
    obs = now - timedelta(minutes=15)
    ko = now + timedelta(hours=1)

    game = _build_mock_game(128, ko)
    pred = _build_mock_prediction(128, home_prob=0.70, away_prob=0.30, pred_dt=pred_dt)
    home_team = _build_mock_team(228, "Bengals", "CIN")
    away_team = _build_mock_team(328, "Browns", "CLE")
    snap = _build_mock_snapshot(128, obs, home_ml=-160, away_ml=140, home_fair=0.60, away_fair=0.40)

    call_idx = 0

    async def mock_execute(stmt):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.all.return_value = [(pred, game, home_team, away_team)]
            return res
        elif call_idx in (2, 3, 4):
            res.scalar_one_or_none.return_value = snap
            return res
        return res

    mock_db = AsyncMock()
    mock_db.execute.side_effect = mock_execute

    rec_invalid = _build_mock_cohort_attempt(
        128,
        "evaluated",
        variant_tag=DEFAULT_VARIANT_TAG,
        model_version="nfl_temporal_v1",
        attempt_timestamp_utc=pred_dt.isoformat(),
        as_of_cutoff_utc=pred_dt.isoformat(),
        home_win_probability=0.70,
        away_win_probability=0.30,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -160,
            "away_ml": 140,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": obs.isoformat(),
            "is_stale": "false",  # String en vez de booleano
        },
    )

    with patch.object(NFLProspectiveCohortService, "load_all_records", return_value=[rec_invalid]):
        result = await get_today_picks(sport="NFL", status="all", db=mock_db)

    assert result["total_picks"] == 1
    pick = result["picks"][0]
    assert pick["pick_status"] == "preview"
    assert "Early Lean" in pick["status_reason"]
    assert "is_stale inválido" in pick["status_reason"].lower()




