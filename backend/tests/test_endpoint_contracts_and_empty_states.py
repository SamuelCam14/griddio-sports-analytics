"""Suite de pruebas exhaustiva para auditoría de contratos, estados vacíos,
parámetros defensivos y procedencia de datos en los endpoints FastAPI y servicios directos:
- app/api/v1/predictions.py
- app/api/v1/games.py
- app/api/v1/odds.py
- app/api/v1/parlay.py
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.game import Game, Team, Prediction, OddsSnapshot
from app.api.v1.predictions import (
    get_today_picks,
    get_top_picks,
    get_game_prediction,
    get_model_info,
    simulate_matchup,
    SimulationRequest,
    get_experimental_predictions,
)
from app.api.v1.games import (
    get_standings,
    get_upcoming_games,
    get_external_game_prediction,
    get_game_context,
)
from app.api.v1.odds import (
    get_nfl_odds,
    get_nba_odds,
    get_remaining_requests,
    sync_live_data,
)
from app.api.v1.parlay import (
    get_recommended_parlays,
    evaluate_parlay,
    EvaluateParlayRequest,
    CustomLeg,
)


# ==============================================================================
# 1. Auditoría de Endpoints de Predicciones (/api/v1/predictions)
# ==============================================================================

class TestPredictionsEndpointContracts:
    """Audita esquemas de respuesta, filtros, estados vacíos y manejo defensivo de parámetros."""

    @pytest.mark.asyncio
    async def test_get_today_picks_empty_db_contract(self):
        """Si la sesión de base de datos es None, retorna el esquema canónico vacío con total_picks=0."""
        res = await get_today_picks(sport="NFL", db=None)
        assert res["sport"] == "NFL"
        assert res["total_picks"] == 0
        assert res["picks"] == []
        assert res["predictions_enabled"] is True
        assert "as_of" in res
        assert res["confianza"] == 0
        assert res["no_pick"] == 0

    @pytest.mark.asyncio
    async def test_get_today_picks_disabled_sport_contract(self):
        """Para deportes con producción deshabilitada (e.g. NBA), retorna predictions_enabled=False."""
        async with AsyncSessionLocal() as session:
            res = await get_today_picks(sport="NBA", db=session)
            assert res["sport"] == "NBA"
            assert res["predictions_enabled"] is False
            assert res["total_picks"] == 0
            assert res["picks"] == []
            assert "reason" in res

    @pytest.mark.asyncio
    async def test_get_today_picks_query_object_defensive_unpacking(self):
        """Invocación directa en Python sin argumentos de keyword no debe fallar por objetos Query."""
        async with AsyncSessionLocal() as session:
            # get_today_picks usa defaults de Query(...) para sport, tier, period
            res = await get_today_picks(db=session)
            assert isinstance(res, dict)
            assert "total_picks" in res
            assert "predictions_enabled" in res
            assert isinstance(res["picks"], list)

    @pytest.mark.asyncio
    async def test_get_today_picks_tier_and_period_filters(self):
        """Los filtros por tier ('CONFIANZA', 'NO PICK', 'all') y period ('auto', 'week', 'all') son reconocidos."""
        async with AsyncSessionLocal() as session:
            res_all = await get_today_picks(sport="NFL", tier="all", period="all", db=session)
            assert isinstance(res_all["picks"], list)

            res_conf = await get_today_picks(sport="NFL", tier="CONFIANZA", period="all", db=session)
            for pick in res_conf["picks"]:
                assert pick["tier"] == "CONFIANZA"

            res_nopick = await get_today_picks(sport="NFL", tier="NO PICK", period="all", db=session)
            for pick in res_nopick["picks"]:
                assert pick["tier"] == "NO PICK"

    @pytest.mark.asyncio
    async def test_get_top_picks_defensive_unpacking_and_filtering(self):
        """get_top_picks no crashea en Python directo y solo retorna picks con ventaja y tier elegible."""
        async with AsyncSessionLocal() as session:
            res = await get_top_picks(db=session)
            assert "sport" in res
            assert "top_picks" in res
            assert "mensaje" in res
            assert isinstance(res["top_picks"], list)

            # Para cada pick en top_picks, certificar que tiene_ventaja sea True y tier sea válido
            for pick in res["top_picks"]:
                assert pick["tiene_ventaja"] is True
                assert pick["tier"] in ("CONFIANZA", "PREMIUM", "VALUE")

    @pytest.mark.asyncio
    async def test_get_top_picks_limit_bounds(self):
        """El límite de top_picks restringe adecuadamente el número de elementos devueltos."""
        async with AsyncSessionLocal() as session:
            res_limit1 = await get_top_picks(sport="NFL", limit=1, db=session)
            assert len(res_limit1["top_picks"]) <= 1

    @pytest.mark.asyncio
    async def test_get_game_prediction_not_found(self):
        """get_game_prediction eleva 404 para un game_id inexistente."""
        async with AsyncSessionLocal() as session:
            with pytest.raises(HTTPException) as exc_info:
                await get_game_prediction(game_id=999999999, db=session)
            assert exc_info.value.status_code == 404
            assert "No hay predicción para game_id" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_game_prediction_db_none(self):
        """get_game_prediction eleva 503 si db es None."""
        with pytest.raises(HTTPException) as exc_info:
            await get_game_prediction(game_id=1, db=None)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_get_model_info_contract(self):
        """get_model_info expone el estado de validación temporal sin afirmaciones falsas de producción."""
        res = await get_model_info()
        assert res["status"] == "experimental"
        assert res["predictions_enabled"] is False
        assert "NFL" in res["models"]
        assert "NBA" in res["models"]
        assert res["models"]["NFL"]["production_enabled"] is True
        assert res["models"]["NBA"]["production_enabled"] is False

    @pytest.mark.asyncio
    async def test_simulate_matchup_fails_closed(self):
        """simulate_matchup eleva estrictamente 503 fail-closed para inputs arbitrarios."""
        req = SimulationRequest(home_team_name="Chiefs", visitor_team_name="Ravens")
        with pytest.raises(HTTPException) as exc_info:
            await simulate_matchup(req)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_get_experimental_predictions_unsupported_sport(self):
        """Deporte no soportado en experimental predictions eleva 404."""
        async with AsyncSessionLocal() as session:
            with pytest.raises(HTTPException) as exc_info:
                await get_experimental_predictions(sport="baseball", db=session)
            assert exc_info.value.status_code == 404


# ==============================================================================
# 2. Auditoría de Endpoints de Juegos (/api/v1/games)
# ==============================================================================

class TestGamesEndpointContracts:
    """Audita esquemas de standings, upcoming, external prediction y context."""

    @pytest.mark.asyncio
    async def test_get_standings_unsupported_sport_raises_400(self):
        """Deportes inválidos en standings deben ser rechazados con 400 Bad Request."""
        async with AsyncSessionLocal() as session:
            with pytest.raises(HTTPException) as exc_info:
                await get_standings(sport="SOCCER", db=session)
            assert exc_info.value.status_code == 400
            assert "Deporte no soportado" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_standings_defensive_query_unpacking(self):
        """Llamar a get_standings con el valor default de Query no crashea."""
        async with AsyncSessionLocal() as session:
            res = await get_standings(db=session)
            assert res["sport"] == "NFL"
            assert res["status"] in ("available", "partial", "unavailable")
            assert isinstance(res["standings"], list)

    @pytest.mark.asyncio
    async def test_get_upcoming_games_defensive_query_unpacking(self):
        """Llamar a get_upcoming_games sin kwargs no crashea por timedelta(days=Query)."""
        async with AsyncSessionLocal() as session:
            res = await get_upcoming_games(db=session)
            assert res["sport"] == "NBA"
            assert res["range_days"] == 7
            assert isinstance(res["games"], list)
            assert res["total"] == len(res["games"])

    @pytest.mark.asyncio
    async def test_get_upcoming_games_nfl_and_nba_custom_days(self):
        """Consulta con parámetros explícitos de days y sport."""
        async with AsyncSessionLocal() as session:
            res_nfl = await get_upcoming_games(days=14, sport="NFL", db=session)
            assert res_nfl["sport"] == "NFL"
            assert res_nfl["range_days"] == 14
            for g in res_nfl["games"]:
                assert g["sport"] == "NFL"
                assert g["status"] == "scheduled"

    @pytest.mark.asyncio
    async def test_get_external_game_prediction_not_found(self):
        """get_external_game_prediction eleva 404 si el juego no existe."""
        async with AsyncSessionLocal() as session:
            with pytest.raises(HTTPException) as exc_info:
                await get_external_game_prediction(game_id=999999999, db=session)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_game_context_not_found(self):
        """get_game_context eleva 404 si el juego no existe."""
        async with AsyncSessionLocal() as session:
            with pytest.raises(HTTPException) as exc_info:
                await get_game_context(game_id=999999999, db=session)
            assert exc_info.value.status_code == 404


# ==============================================================================
# 3. Auditoría de Endpoints de Cuotas (/api/v1/odds)
# ==============================================================================

class TestOddsEndpointContracts:
    """Audita persistencia DB, cuota ledger local, y rechazo de ingesta fuera de alcance."""

    @pytest.mark.asyncio
    async def test_get_nfl_odds_schema(self):
        """GET /api/v1/odds/nfl retorna el esquema con juegos observados o estado sin eventos."""
        async with AsyncSessionLocal() as session:
            res = await get_nfl_odds(db=session)
            assert res["sport"] == "NFL"
            assert res["status"] in ("available", "no_upcoming_events")
            assert isinstance(res["games"], list)
            assert res["total_games"] == len(res["games"])

    @pytest.mark.asyncio
    async def test_get_nba_odds_schema(self):
        """GET /api/v1/odds/nba retorna el esquema con estado offseason o juegos disponibles."""
        async with AsyncSessionLocal() as session:
            res = await get_nba_odds(db=session)
            assert res["sport"] == "NBA"
            assert res["status"] in ("available", "offseason")
            assert isinstance(res["games"], list)
            assert res["total_games"] == len(res["games"])

    @pytest.mark.asyncio
    async def test_get_remaining_requests_no_external_call(self):
        """GET /api/v1/odds/remaining consulta el ledger local sin consumir cuotas de The Odds API."""
        res = await get_remaining_requests()
        assert res["status"] == "ok"
        assert "requests_remaining" in res
        assert "requests_used" in res
        assert "total_remaining_credits" in res
        assert isinstance(res["requests_remaining"], int)

    @pytest.mark.asyncio
    async def test_sync_live_data_rejects_non_nfl(self):
        """POST /api/v1/odds/sync rechaza cualquier deporte que no sea NFL con 400 Bad Request."""
        async with AsyncSessionLocal() as session:
            with pytest.raises(HTTPException) as exc_info:
                await sync_live_data(sport="NBA", db=session)
            assert exc_info.value.status_code == 400
            assert "exclusivamente a NFL" in exc_info.value.detail


# ==============================================================================
# 4. Auditoría de Endpoints de Parlay Builder (/api/v1/parlay)
# ==============================================================================

class TestParlayEndpointContracts:
    """Audita restricción de deporte, rechazo de correlación, ausencia de cuotas sintéticas y Kelly."""

    @pytest.mark.asyncio
    async def test_get_recommended_parlays_rejects_non_nfl(self):
        """get_recommended_parlays rechaza NBA con estado unavailable."""
        async with AsyncSessionLocal() as session:
            res = await get_recommended_parlays(sport="NBA", db=session)
            assert res["status"] == "unavailable"
            assert res["sport"] == "NBA"
            assert res["total"] == 0
            assert res["parlays"] == []
            assert "restringido exclusivamente a NFL" in res["reason"]

    @pytest.mark.asyncio
    async def test_get_recommended_parlays_defensive_unpacking(self):
        """Invocación directa con defaults de Query no crashea en Python."""
        async with AsyncSessionLocal() as session:
            res = await get_recommended_parlays(db=session)
            assert res["sport"] == "NFL"
            assert "total" in res
            assert isinstance(res["parlays"], list)

    @pytest.mark.asyncio
    async def test_get_recommended_parlays_zero_synthetic_fallbacks(self):
        """Si se devuelven parlays recomendados, ninguna pata debe contener bookmaker hardcodeado o timestamps inventados."""
        async with AsyncSessionLocal() as session:
            res = await get_recommended_parlays(sport="NFL", db=session)
            for p in res.get("parlays", []):
                for leg in p.get("legs_evaluadas", []):
                    assert leg.get("source") in ("real", "manual")
                    if leg.get("source") == "real":
                        assert leg.get("observed_at") is not None
                        assert leg.get("bookmaker") is not None

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_fails_closed_when_snapshot_absent(self, monkeypatch):
        """
        Invariante estricto: ante ausencia de OddsSnapshot verificado, generate_recommended_parlays
        omite las patas fail-closed y retorna lista vacía.
        Prohibido usar Prediction.as_of, predicted_at, best_bookmaker o best_home_ml como sustitutos.
        """
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )

        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.best_home_ml = -150
        pred1.best_away_ml = 130
        pred1.best_bookmaker = "draftkings"
        pred1.as_of = datetime.now(timezone.utc) - timedelta(hours=2)
        pred1.predicted_at = datetime.now(timezone.utc) - timedelta(hours=2)
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 1
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = datetime.now(timezone.utc) + timedelta(days=2)

        team1 = MagicMock(spec=Team)
        team1.name = "Kansas City Chiefs"
        team1.abbreviation = "KC"

        pred2 = MagicMock(spec=Prediction)
        pred2.home_win_prob = 0.72
        pred2.away_win_prob = 0.28
        pred2.best_home_ml = -140
        pred2.best_away_ml = 120
        pred2.best_bookmaker = "fanduel"
        pred2.as_of = datetime.now(timezone.utc) - timedelta(hours=2)
        pred2.predicted_at = datetime.now(timezone.utc) - timedelta(hours=2)
        pred2.confluence_tier = "PREMIUM"
        pred2.model_version = "nfl_temporal_v1"

        game2 = MagicMock(spec=Game)
        game2.id = 2
        game2.sport = "NFL"
        game2.status = "scheduled"
        game2.game_date = datetime.now(timezone.utc) + timedelta(days=2)

        team2 = MagicMock(spec=Team)
        team2.name = "Buffalo Bills"
        team2.abbreviation = "BUF"

        # mock_db que devuelve rows para la consulta de Prediction, pero None para OddsSnapshot
        mock_db = AsyncMock()
        first_call = True

        async def mock_execute(stmt):
            nonlocal first_call
            mock_res = MagicMock()
            if first_call:
                first_call = False
                mock_res.all.return_value = [(pred1, game1, team1), (pred2, game2, team2)]
                return mock_res
            # Para consultas posteriores de OddsSnapshot, retornar None (ausencia de snapshot)
            mock_res.scalar_one_or_none.return_value = None
            return mock_res

        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        # Debe fallar cerrado: 0 candidatos, 0 parlays
        assert parlays == []

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_uses_exact_snapshot_and_verifies_coherence(self, monkeypatch):
        """
        Verifica coherencia estricta entre el precio, bookmaker y observed_at del snapshot usado
        y la pata evaluada en el parlay, ignorando momios o timestamps divergentes en Prediction.
        """
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )

        now = datetime.now(timezone.utc)
        snap1_obs = now - timedelta(hours=1)
        snap2_obs = now - timedelta(hours=2)

        # Predicciones con valores divergentes en best_home_ml y as_of
        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.best_home_ml = -999  # Incoherente en pred
        pred1.best_away_ml = 999
        pred1.best_bookmaker = "synthetic_bm"
        pred1.as_of = now - timedelta(hours=5)
        pred1.predicted_at = now - timedelta(hours=5)
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 101
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = now + timedelta(days=1)

        team1 = MagicMock(spec=Team)
        team1.name = "Kansas City Chiefs"
        team1.abbreviation = "KC"

        pred2 = MagicMock(spec=Prediction)
        pred2.home_win_prob = 0.72
        pred2.away_win_prob = 0.28
        pred2.best_home_ml = -888  # Incoherente en pred
        pred2.best_away_ml = 888
        pred2.best_bookmaker = "synthetic_bm"
        pred2.as_of = now - timedelta(hours=5)
        pred2.predicted_at = now - timedelta(hours=5)
        pred2.confluence_tier = "PREMIUM"
        pred2.model_version = "nfl_temporal_v1"

        game2 = MagicMock(spec=Game)
        game2.id = 102
        game2.sport = "NFL"
        game2.status = "scheduled"
        game2.game_date = now + timedelta(days=1)

        team2 = MagicMock(spec=Team)
        team2.name = "Philadelphia Eagles"
        team2.abbreviation = "PHI"

        # Snapshots reales/verificados
        snap1 = MagicMock(spec=OddsSnapshot)
        snap1.home_ml = -140
        snap1.away_ml = 120
        snap1.home_bookmaker = "draftkings"
        snap1.away_bookmaker = "fanduel"
        snap1.bookmaker = "draftkings/fanduel"
        snap1.observed_at = snap1_obs

        snap2 = MagicMock(spec=OddsSnapshot)
        snap2.home_ml = -130
        snap2.away_ml = 110
        snap2.home_bookmaker = "betmgm"
        snap2.away_bookmaker = "caesars"
        snap2.bookmaker = "betmgm/caesars"
        snap2.observed_at = snap2_obs

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_res = MagicMock()
            if call_count == 1:
                mock_res.all.return_value = [(pred1, game1, team1), (pred2, game2, team2)]
                return mock_res
            elif call_count == 2:
                mock_res.scalar_one_or_none.return_value = snap1
                return mock_res
            else:
                mock_res.scalar_one_or_none.return_value = snap2
                return mock_res

        mock_db = AsyncMock()
        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        assert len(parlays) > 0
        p = parlays[0]
        assert p["viable"] is True

        legs = p["legs_evaluadas"]
        assert len(legs) == 2

        leg1 = next(l for l in legs if l["game_id"] == 101)
        leg2 = next(l for l in legs if l["game_id"] == 102)

        # Coherencia 1: Precio proviene exactamente del snapshot (-140, no -999)
        assert leg1["odds_american"] == -140
        # Coherencia 2: Casa proviene exactamente del snapshot (draftkings, no synthetic_bm)
        assert leg1["bookmaker"] == "draftkings"
        # Coherencia 3: Timestamp proviene exactamente del snapshot (snap1_obs, no pred1.as_of)
        assert leg1["observed_at"] == snap1_obs.isoformat()
        assert leg1["observed_at"] != pred1.as_of.isoformat()

        assert leg2["odds_american"] == -130
        assert leg2["bookmaker"] == "betmgm"
        assert leg2["observed_at"] == snap2_obs.isoformat()
        assert leg2["observed_at"] != pred2.as_of.isoformat()

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_omits_stale_and_post_kickoff_snapshots(self, monkeypatch):
        """Snapshots obsoletos o con observed_at posterior al kickoff son excluidos fail-closed."""
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )
        now = datetime.now(timezone.utc)

        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 1
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = now - timedelta(hours=1)  # Kickoff ya ocurrió

        team1 = MagicMock(spec=Team)
        team1.name = "Team A"

        # Snapshot post-kickoff
        snap1 = MagicMock(spec=OddsSnapshot)
        snap1.home_ml = -140
        snap1.away_ml = 120
        snap1.home_bookmaker = "draftkings"
        snap1.bookmaker = "draftkings"
        snap1.observed_at = now  # Post-kickoff!

        mock_db = AsyncMock()
        c = 0
        async def mock_execute(stmt):
            nonlocal c
            c += 1
            m = MagicMock()
            if c == 1:
                m.all.return_value = [(pred1, game1, team1)]
            else:
                m.scalar_one_or_none.return_value = snap1
            return m
        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        assert parlays == []

    @pytest.mark.asyncio
    async def test_evaluate_parlay_insufficient_legs_rejected(self):
        """Petición con menos de 2 patas es rechazada en Pydantic y en la lógica interna."""
        # 1. Pydantic min_length=2 rechaza el request
        with pytest.raises(Exception):
            EvaluateParlayRequest(
                legs=[
                    CustomLeg(game_id="1", side="home", prob_model=0.70, odds_american=-150, source="manual"),
                ],
                bankroll=1000.0,
                kelly_fraction=0.25,
            )

        # 2. evaluate_custom_parlay rechaza directamente cualquier lista < 2 legs
        from app.services.parlay_service import evaluate_custom_parlay
        single_leg = CustomLeg(game_id="1", side="home", prob_model=0.70, odds_american=-150, source="manual").model_dump()
        res = evaluate_custom_parlay(legs=[single_leg])
        assert res["viable"] is False
        assert "al menos 2 eventos" in res["razon"]

    @pytest.mark.asyncio
    async def test_evaluate_parlay_correlated_same_game_rejected(self):
        """Combinar patas del mismo partido es rechazado fail-closed."""
        async with AsyncSessionLocal() as session:
            req = EvaluateParlayRequest(
                legs=[
                    CustomLeg(game_id="game_100", side="home", prob_model=0.65, odds_american=-120, source="manual"),
                    CustomLeg(game_id="game_100", side="away", prob_model=0.35, odds_american=+100, source="manual"),
                ],
                bankroll=1000.0,
                kelly_fraction=0.25,
            )
            res = await evaluate_parlay(req, db=session)
            assert res["viable"] is False
            assert res["has_correlated_legs"] is True
            assert "No se permite combinar selecciones del mismo evento" in res["razon"]

    @pytest.mark.asyncio
    async def test_evaluate_parlay_independent_manual_legs_success(self):
        """Combinar patas independientes válidas calcula probabilidad conjunta, momio combinado y Kelly."""
        async with AsyncSessionLocal() as session:
            req = EvaluateParlayRequest(
                legs=[
                    CustomLeg(game_id="game_101", side="home", prob_model=0.70, odds_american=-150, source="manual"),
                    CustomLeg(game_id="game_102", side="home", prob_model=0.65, odds_american=-110, source="manual"),
                ],
                bankroll=1000.0,
                kelly_fraction=0.25,
            )
            res = await evaluate_parlay(req, db=session)
            assert res["viable"] is True
            assert res["has_correlated_legs"] is False
            assert res["n_legs"] == 2
            assert abs(res["prob_combinada"] - (0.70 * 0.65)) < 1e-4
            assert res["momio_decimal"] is not None
            assert res["momio_decimal"] > 1.0
            assert res["ev_dolares"] is not None
            assert res["kelly_fraccion"] is not None
            assert res["kelly_monto"] is not None

    def test_parlay_service_has_zero_unittest_mock_imports(self):
        """El servicio de producción parlay_service.py NO debe importar unittest.mock bajo ninguna circunstancia."""
        import inspect
        from app.services import parlay_service
        source = inspect.getsource(parlay_service)
        assert "unittest.mock" not in source
        assert "MagicMock" not in source
        assert "Mock" not in source

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_rejects_mock_or_unparseable_observed_at(self, monkeypatch):
        """Un snapshot con observed_at no parseable o tipo anómalo (int, object) es rechazado fail-closed."""
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )
        now = datetime.now(timezone.utc)

        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 1
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = now + timedelta(days=2)

        team1 = MagicMock(spec=Team)
        team1.name = "Team A"

        # Snapshot con observed_at de tipo entero o corrupto
        snap1 = MagicMock(spec=OddsSnapshot)
        snap1.home_ml = -140
        snap1.away_ml = 120
        snap1.home_bookmaker = "draftkings"
        snap1.bookmaker = "draftkings"
        snap1.observed_at = 1700000000  # Int, no datetime ni str ISO

        mock_db = AsyncMock()
        c = 0
        async def mock_execute(stmt):
            nonlocal c
            c += 1
            m = MagicMock()
            if c == 1:
                m.all.return_value = [(pred1, game1, team1)]
            else:
                m.scalar_one_or_none.return_value = snap1
            return m
        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        assert parlays == []

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_rejects_whitespace_or_empty_bookmaker(self, monkeypatch):
        """Un snapshot con momios válidos pero bookmaker de solo espacios en blanco se descarta fail-closed."""
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )
        now = datetime.now(timezone.utc)

        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 1
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = now + timedelta(days=2)

        team1 = MagicMock(spec=Team)
        team1.name = "Team A"

        snap1 = MagicMock(spec=OddsSnapshot)
        snap1.home_ml = -140
        snap1.away_ml = 120
        snap1.home_bookmaker = "   "  # Solo espacios
        snap1.bookmaker = "  "
        snap1.observed_at = now - timedelta(hours=1)

        mock_db = AsyncMock()
        c = 0
        async def mock_execute(stmt):
            nonlocal c
            c += 1
            m = MagicMock()
            if c == 1:
                m.all.return_value = [(pred1, game1, team1)]
            else:
                m.scalar_one_or_none.return_value = snap1
            return m
        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        assert parlays == []

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_rejects_composite_bookmaker_when_side_missing(self, monkeypatch):
        """Si falta home_bookmaker y bookmaker es compuesto ('dk/fd'), no se atribuye falsamente a la pata."""
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )
        now = datetime.now(timezone.utc)

        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 1
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = now + timedelta(days=2)

        team1 = MagicMock(spec=Team)
        team1.name = "Team A"

        snap1 = MagicMock(spec=OddsSnapshot)
        snap1.home_ml = -140
        snap1.away_ml = 120
        snap1.home_bookmaker = None
        snap1.away_bookmaker = None
        snap1.bookmaker = "draftkings/fanduel"  # Compuesto, no una sola casa
        snap1.observed_at = now - timedelta(hours=1)

        mock_db = AsyncMock()
        c = 0
        async def mock_execute(stmt):
            nonlocal c
            c += 1
            m = MagicMock()
            if c == 1:
                m.all.return_value = [(pred1, game1, team1)]
            else:
                m.scalar_one_or_none.return_value = snap1
            return m
        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        assert parlays == []

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_rejects_impossible_odds_in_snapshot(self, monkeypatch):
        """Momios imposibles (-50 o 0) en OddsSnapshot son rechazados fail-closed."""
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )
        now = datetime.now(timezone.utc)

        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 1
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = now + timedelta(days=2)

        team1 = MagicMock(spec=Team)
        team1.name = "Team A"

        snap1 = MagicMock(spec=OddsSnapshot)
        snap1.home_ml = 50  # Momio americano imposible (-100 < x < 100)
        snap1.away_ml = 120
        snap1.home_bookmaker = "draftkings"
        snap1.bookmaker = "draftkings"
        snap1.observed_at = now - timedelta(hours=1)

        mock_db = AsyncMock()
        c = 0
        async def mock_execute(stmt):
            nonlocal c
            c += 1
            m = MagicMock()
            if c == 1:
                m.all.return_value = [(pred1, game1, team1)]
            else:
                m.scalar_one_or_none.return_value = snap1
            return m
        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        assert parlays == []

    def test_is_snapshot_usable_strict_timezone_awareness(self):
        """Verifica que _is_snapshot_usable rechace fail-closed cualquier timestamp naive tanto en datetime como en str ISO."""
        from app.services.parlay_service import _is_snapshot_usable

        class MockSnap:
            def __init__(self, obs):
                self.observed_at = obs

        now_utc = datetime.now(timezone.utc)
        future_k = now_utc + timedelta(days=2)

        # 1. Datetime naive (sin tzinfo) -> RECHAZADO fail-closed (False)
        naive_dt = datetime(2026, 10, 26, 12, 0)
        assert _is_snapshot_usable(MockSnap(naive_dt), future_k, 24.0, now_utc) is False

        # 2. String ISO naive (sin delimitador ni offset de zona horaria) -> RECHAZADO (False)
        naive_str = "2026-10-26T12:00:00"
        assert _is_snapshot_usable(MockSnap(naive_str), future_k, 24.0, now_utc) is False

        # 3. String ISO naive con microsegundos -> RECHAZADO (False)
        naive_str_ms = "2026-10-26T12:00:00.123456"
        assert _is_snapshot_usable(MockSnap(naive_str_ms), future_k, 24.0, now_utc) is False

        # 4. Kickoff naive con observed_at aware -> RECHAZADO (False)
        aware_dt = now_utc - timedelta(hours=1)
        naive_k = datetime(2026, 10, 28, 12, 0)
        assert _is_snapshot_usable(MockSnap(aware_dt), naive_k, 24.0, now_utc) is False

        # 5. Datetime aware en UTC -> ACEPTADO (True)
        assert _is_snapshot_usable(MockSnap(aware_dt), future_k, 24.0, now_utc) is True

        # 6. String ISO aware con sufijo 'Z' -> ACEPTADO (True)
        aware_iso_z = (now_utc - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert _is_snapshot_usable(MockSnap(aware_iso_z), future_k, 24.0, now_utc) is True

        # 7. String ISO aware con offset explícito ('+00:00' o '-05:00') -> ACEPTADO (True)
        aware_iso_offset = (now_utc - timedelta(hours=1)).isoformat()
        assert _is_snapshot_usable(MockSnap(aware_iso_offset), future_k, 24.0, now_utc) is True

        # 8. Datetime aware con zona horaria no-UTC (e.g. UTC-5) -> ACEPTADO (True)
        tz_est = timezone(timedelta(hours=-5))
        aware_dt_est = (now_utc - timedelta(hours=1)).astimezone(tz_est)
        assert _is_snapshot_usable(MockSnap(aware_dt_est), future_k, 24.0, now_utc) is True

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_rejects_naive_datetime_observed_at(self, monkeypatch):
        """Un snapshot con datetime naive en observed_at omite la pata fail-closed sin asumir UTC."""
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )
        now = datetime.now(timezone.utc)

        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 1
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = now + timedelta(days=2)

        team1 = MagicMock(spec=Team)
        team1.name = "Team A"

        snap1 = MagicMock(spec=OddsSnapshot)
        snap1.home_ml = -140
        snap1.away_ml = 120
        snap1.home_bookmaker = "draftkings"
        snap1.bookmaker = "draftkings"
        # Datetime naive (sin tzinfo)
        snap1.observed_at = datetime(2026, 10, 26, 12, 0)

        mock_db = AsyncMock()
        c = 0
        async def mock_execute(stmt):
            nonlocal c
            c += 1
            m = MagicMock()
            if c == 1:
                m.all.return_value = [(pred1, game1, team1)]
            else:
                m.scalar_one_or_none.return_value = snap1
            return m
        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        assert parlays == []

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_rejects_naive_iso_string_observed_at(self, monkeypatch):
        """Un snapshot con string ISO naive en observed_at omite la pata fail-closed sin asumir UTC."""
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )
        now = datetime.now(timezone.utc)

        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 1
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = now + timedelta(days=2)

        team1 = MagicMock(spec=Team)
        team1.name = "Team A"

        snap1 = MagicMock(spec=OddsSnapshot)
        snap1.home_ml = -140
        snap1.away_ml = 120
        snap1.home_bookmaker = "draftkings"
        snap1.bookmaker = "draftkings"
        # String ISO naive sin offset
        snap1.observed_at = "2026-10-26T12:00:00"

        mock_db = AsyncMock()
        c = 0
        async def mock_execute(stmt):
            nonlocal c
            c += 1
            m = MagicMock()
            if c == 1:
                m.all.return_value = [(pred1, game1, team1)]
            else:
                m.scalar_one_or_none.return_value = snap1
            return m
        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        assert parlays == []

    @pytest.mark.asyncio
    async def test_generate_recommended_parlays_preserves_valid_timezone_aware_offsets(self, monkeypatch):
        """Snapshots con offsets timezone-aware válidos (UTC y no-UTC) son aceptados y preservan información de zona."""
        from app.services.parlay_service import generate_recommended_parlays
        monkeypatch.setattr(
            "app.services.parlay_service.get_approved_model_version",
            lambda sport: "nfl_temporal_v1" if sport == "NFL" else None,
        )
        now = datetime.now(timezone.utc)
        tz_cst = timezone(timedelta(hours=-6))

        # Snapshots con offsets aware válidos
        snap1_obs = now - timedelta(hours=1)
        snap2_obs = (now - timedelta(hours=2)).astimezone(tz_cst)

        pred1 = MagicMock(spec=Prediction)
        pred1.home_win_prob = 0.75
        pred1.away_win_prob = 0.25
        pred1.confluence_tier = "PREMIUM"
        pred1.model_version = "nfl_temporal_v1"

        game1 = MagicMock(spec=Game)
        game1.id = 101
        game1.sport = "NFL"
        game1.status = "scheduled"
        game1.game_date = now + timedelta(days=1)

        team1 = MagicMock(spec=Team)
        team1.name = "Kansas City Chiefs"
        team1.abbreviation = "KC"

        pred2 = MagicMock(spec=Prediction)
        pred2.home_win_prob = 0.72
        pred2.away_win_prob = 0.28
        pred2.confluence_tier = "PREMIUM"
        pred2.model_version = "nfl_temporal_v1"

        game2 = MagicMock(spec=Game)
        game2.id = 102
        game2.sport = "NFL"
        game2.status = "scheduled"
        game2.game_date = now + timedelta(days=1)

        team2 = MagicMock(spec=Team)
        team2.name = "Philadelphia Eagles"
        team2.abbreviation = "PHI"

        snap1 = MagicMock(spec=OddsSnapshot)
        snap1.home_ml = -140
        snap1.away_ml = 120
        snap1.home_bookmaker = "draftkings"
        snap1.bookmaker = "draftkings"
        snap1.observed_at = snap1_obs

        snap2 = MagicMock(spec=OddsSnapshot)
        snap2.home_ml = -130
        snap2.away_ml = 110
        snap2.home_bookmaker = "fanduel"
        snap2.bookmaker = "fanduel"
        snap2.observed_at = snap2_obs

        call_count = 0
        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_res = MagicMock()
            if call_count == 1:
                mock_res.all.return_value = [(pred1, game1, team1), (pred2, game2, team2)]
            elif call_count == 2:
                mock_res.scalar_one_or_none.return_value = snap1
            else:
                mock_res.scalar_one_or_none.return_value = snap2
            return mock_res

        mock_db = AsyncMock()
        mock_db.execute.side_effect = mock_execute

        parlays = await generate_recommended_parlays(db=mock_db, sport="NFL")
        assert len(parlays) > 0
        p = parlays[0]
        assert p["viable"] is True

        legs = p["legs_evaluadas"]
        assert len(legs) == 2

        leg1 = next(l for l in legs if l["game_id"] == 101)
        leg2 = next(l for l in legs if l["game_id"] == 102)

        assert leg1["observed_at"] == snap1_obs.isoformat()
        assert leg2["observed_at"] == snap2_obs.isoformat()
        # Verificar que el offset de -06:00 esté presente y preservado
        assert "-06:00" in leg2["observed_at"]
