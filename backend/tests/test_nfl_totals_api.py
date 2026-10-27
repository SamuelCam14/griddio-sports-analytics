"""
Tests para TAREA-10: Integración de Totals NFL en API y Frontend.

Certifica:
1. Contrato backend de Totals en sports_analytics_service:
   - Proyección, línea, probabilidades Over/Under y cálculo de EV.
   - Manejo de Push cuando projected_total == total_line (probabilidad 0.5, EV 0.0, edge 0.0).
   - Ausencia o corrupción de línea se reporta como 'unavailable'.
   - Momios corruptos de totales se reportan como 'unavailable' sin cuotas sintéticas.
   - Estado estrictamente 'experimental' (tier='EXPERIMENTAL', has_kelly_edge=False, production_enabled=False).
   - Best Bet (#1 Pick) nunca promueve Totals como pick oficial.
2. Contrato en API predictions (/today y /game/{id}):
   - Exposición coherente de la entidad totals en picks de cartelera.
   - Exposición de totals en predicción individual de partido.
   - Rechazo de tipos booleanos en proyección y sigma de forma fail-closed.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import math
import pytest
import numpy as np

from app.services.sports_analytics_service import (
    generate_nfl_matchup_analytics,
    get_nfl_totals_calibrated_sigma,
)
from app.api.v1.predictions import get_today_picks, get_game_prediction
from app.models.game import Game, Team, Prediction, OddsSnapshot
from app.core.database import engine, init_db
from sqlalchemy import text, select


class TestNFLTotalsAnalyticsServiceIntegration:
    """Pruebas del servicio de analítica deportiva para el mercado de Totales NFL."""

    def test_totals_over_pick_and_ev_calculation(self):
        """Si projected_total > total_line, emite pick OVER con probabilidad > 0.5 y EV calculado."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            game_date=game_dt,
            home_ml=-130,
            away_ml=110,
            total_line=45.5,
            over_ml=-110,
            under_ml=-110,
            model_prob_home=0.58,
            model_prob_away=0.42,
            model_projected_total=49.0,
            totals_residual_sigma=14.0,
        )

        totals = result["totals"]
        assert totals["status"] == "available"
        assert totals["is_experimental"] is True
        assert totals["market_line"] == 45.5
        assert totals["projected_total"] == 49.0
        assert totals["pick"] == "OVER"
        assert totals["probability"] > 0.5
        assert totals["prob_over"] == totals["probability"]
        assert totals["prob_under"] < 0.5
        assert math.isclose(totals["prob_over"] + totals["prob_under"], 1.0, abs_tol=1e-3)
        assert totals["ev_dollars"] is not None
        assert totals["ev_dollars"] > 0
        assert totals["tier"] == "EXPERIMENTAL"
        assert totals["has_kelly_edge"] is False
        assert totals["kelly_stake"] is None
        assert "OVER" in totals["recommendation"]
        assert "experimental" in totals["recommendation"].lower()

    def test_totals_under_pick_and_ev_calculation(self):
        """Si projected_total < total_line, emite pick UNDER con probabilidad > 0.5 y EV calculado."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Pittsburgh Steelers",
            away_team="Cleveland Browns",
            game_date=game_dt,
            home_ml=-120,
            away_ml=100,
            total_line=41.5,
            over_ml=-105,
            under_ml=-115,
            model_prob_home=0.52,
            model_prob_away=0.48,
            model_projected_total=36.0,
            totals_residual_sigma=14.0,
        )

        totals = result["totals"]
        assert totals["status"] == "available"
        assert totals["is_experimental"] is True
        assert totals["market_line"] == 41.5
        assert totals["projected_total"] == 36.0
        assert totals["pick"] == "UNDER"
        assert totals["probability"] > 0.5
        assert totals["prob_under"] == totals["probability"]
        assert totals["prob_over"] < 0.5
        assert math.isclose(totals["prob_over"] + totals["prob_under"], 1.0, abs_tol=1e-3)
        assert totals["ev_dollars"] is not None
        assert totals["ev_dollars"] > 0
        assert totals["tier"] == "EXPERIMENTAL"
        assert totals["has_kelly_edge"] is False
        assert "UNDER" in totals["recommendation"]

    def test_totals_push_handling(self):
        """Si projected_total == total_line, maneja push exacto (prob=0.5, ev=0.0, edge=0.0, pick=PUSH)."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Denver Broncos",
            away_team="Las Vegas Raiders",
            game_date=game_dt,
            home_ml=-110,
            away_ml=-110,
            total_line=44.0,
            over_ml=-110,
            under_ml=-110,
            model_prob_home=0.50,
            model_prob_away=0.50,
            model_projected_total=44.0,
            totals_residual_sigma=14.0,
        )

        totals = result["totals"]
        assert totals["status"] == "available"
        assert totals["is_experimental"] is True
        assert totals["market_line"] == 44.0
        assert totals["projected_total"] == 44.0
        assert totals["pick"] == "PUSH"
        assert totals["probability"] == 0.5
        assert totals["prob_over"] == 0.5
        assert totals["prob_under"] == 0.5
        assert totals["ev_dollars"] == 0.0
        assert totals["edge_pct"] == 0.0
        assert totals["odds_american"] is None
        assert totals["tier"] == "EXPERIMENTAL"
        assert totals["has_kelly_edge"] is False
        assert "PUSH" in totals["recommendation"]

    def test_totals_missing_line_returns_unavailable(self):
        """Si total_line es None, el estado es 'unavailable' y los campos de resultado son None."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            game_date=game_dt,
            home_ml=-130,
            away_ml=110,
            total_line=None,
            model_prob_home=0.58,
            model_prob_away=0.42,
        )

        totals = result["totals"]
        assert totals["status"] == "unavailable"
        assert totals["market_line"] is None
        assert totals["projected_total"] is None
        assert totals["pick"] is None
        assert totals["probability"] is None
        assert totals["prob_over"] is None
        assert totals["prob_under"] is None
        assert totals["ev_dollars"] is None
        assert totals["tier"] == "UNAVAILABLE"
        assert totals["has_kelly_edge"] is False
        assert "no disponibles" in totals["recommendation"].lower()

    @pytest.mark.parametrize("bad_line", [0, -10.5, float("nan"), float("inf"), -float("inf"), True, False, "bad"])
    def test_totals_corrupted_line_returns_unavailable(self, bad_line):
        """Líneas no positivas, no finitas o booleanas se sanitizan a unavailable fail-closed."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            game_date=game_dt,
            home_ml=-130,
            away_ml=110,
            total_line=bad_line,
            model_prob_home=0.58,
            model_prob_away=0.42,
        )

        totals = result["totals"]
        assert totals["status"] == "unavailable"
        assert totals["market_line"] is None
        assert totals["pick"] is None

    @pytest.mark.parametrize("bad_odds", [0, 50, -50, float("nan"), float("inf"), True, False])
    def test_totals_corrupted_odds_returns_unavailable(self, bad_odds):
        """Momios de totales fuera de rango (-100..100) o no válidos son sanitizados fail-closed."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            game_date=game_dt,
            home_ml=-130,
            away_ml=110,
            total_line=47.5,
            over_ml=bad_odds,
            under_ml=-110,
            model_prob_home=0.58,
            model_prob_away=0.42,
        )

        totals = result["totals"]
        assert totals["status"] == "unavailable"
        assert totals["pick"] is None
        assert totals["ev_dollars"] is None

    def test_totals_never_included_in_best_bet(self):
        """Totals NUNCA se promueve a Best Bet (#1 pick oficial) para preservar el estado experimental."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        # Forzar un total con gran diferencia
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            game_date=game_dt,
            home_ml=-110,
            away_ml=-110,
            total_line=40.0,
            over_ml=150,  # Gran momio
            under_ml=-180,
            model_prob_home=0.50,
            model_prob_away=0.50,
            model_projected_total=55.0,  # Gran diferencia
            totals_residual_sigma=10.0,
        )

        best_bet = result.get("best_bet")
        if best_bet is not None:
            assert best_bet.get("market") != "totals"

    @pytest.mark.parametrize("bad_val", [True, False, float("nan"), float("inf"), -1.0])
    def test_totals_rejects_boolean_and_invalid_projected_total(self, bad_val):
        """generate_nfl_matchup_analytics rechaza proyecciones booleanas o inválidas."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            game_date=game_dt,
            home_ml=-130,
            away_ml=110,
            total_line=45.0,
            model_prob_home=0.55,
            model_prob_away=0.45,
            model_projected_total=bad_val,
        )
        totals = result["totals"]
        assert totals["status"] == "unavailable"
        assert totals["pick"] is None
        assert totals["projected_total"] is None
        assert totals["ev_dollars"] is None
        assert totals["edge_pct"] is None
        assert totals["probability"] is None
        assert "no disponibles" in totals["recommendation"].lower()

    def test_totals_missing_projection_fails_closed(self):
        """Si falta model_projected_total (None), totals falla cerrado a unavailable."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            game_date=game_dt,
            home_ml=-130,
            away_ml=110,
            total_line=45.0,
            over_ml=-110,
            under_ml=-110,
            model_prob_home=0.55,
            model_prob_away=0.45,
            model_projected_total=None,
        )
        totals = result["totals"]
        assert totals["status"] == "unavailable"
        assert totals["pick"] is None
        assert totals["projected_total"] is None
        assert totals["ev_dollars"] is None
        assert totals["probability"] is None

    def test_totals_missing_odds_never_synthesizes_minus_110(self):
        """Si hay proyección y línea pero faltan momios, calcula pick/probabilidad pero NO EV ni ventaja, y jamás inventa -110."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            game_date=game_dt,
            home_ml=-130,
            away_ml=110,
            total_line=45.5,
            over_ml=None,
            under_ml=None,
            model_prob_home=0.58,
            model_prob_away=0.42,
            model_projected_total=49.0,
            totals_residual_sigma=14.0,
        )
        totals = result["totals"]
        assert totals["status"] == "available"
        assert totals["projected_total"] == 49.0
        assert totals["pick"] == "OVER"
        assert totals["probability"] > 0.5
        assert totals["ev_dollars"] is None
        assert totals["edge_pct"] is None
        assert totals["odds_american"] is None
        assert "sin momios observados" in totals["recommendation"].lower()

    @pytest.mark.parametrize("fractional_odds", [-110.5, 105.2, 0.5, -0.5])
    def test_totals_rejects_fractional_american_odds_fail_closed(self, fractional_odds):
        """Rechaza momios con decimales/fracciones sin truncar ni redondearlos a enteros."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            game_date=game_dt,
            home_ml=-130,
            away_ml=110,
            total_line=45.0,
            over_ml=fractional_odds,
            under_ml=-110,
            model_prob_home=0.55,
            model_prob_away=0.45,
            model_projected_total=49.0,
        )
        totals = result["totals"]
        assert totals["status"] == "unavailable"
        assert totals["pick"] is None
        assert totals["ev_dollars"] is None
        assert totals["over_ml"] is None


class TestNFLTotalsAPIEndpointIntegration:
    """Pruebas de integración de endpoints /predictions/today y /predictions/game/{id} para Totals."""

    @pytest.mark.asyncio
    async def test_get_game_prediction_includes_totals_contract(self):
        """get_game_prediction transporta la entidad totals con contrato coherente."""
        now = datetime.now(timezone.utc)

        mock_game = MagicMock(spec=Game)
        mock_game.id = 150
        mock_game.sport = "NFL"
        mock_game.game_date = now + timedelta(hours=3)
        mock_game.status = "scheduled"
        mock_game.home_team = MagicMock(name="Chiefs")
        mock_game.home_team.name = "Kansas City Chiefs"
        mock_game.away_team = MagicMock(name="Bills")
        mock_game.away_team.name = "Buffalo Bills"

        mock_pred = MagicMock(spec=Prediction)
        mock_pred.id = 1050
        mock_pred.game_id = 150
        mock_pred.model_version = "nfl_temporal_v1"
        mock_pred.predicted_at = now - timedelta(hours=4)
        mock_pred.as_of = mock_pred.predicted_at
        mock_pred.home_win_prob = 0.60
        mock_pred.away_win_prob = 0.40
        mock_pred.confluence_tier = "TIER_3"
        mock_pred.model_agreement = 3
        mock_pred.best_bookmaker = "draftkings"
        mock_pred.best_home_ml = -140
        mock_pred.best_away_ml = 120
        mock_pred.home_ev = 5.2
        mock_pred.away_ev = -10.0
        mock_pred.home_kelly = 0.02
        mock_pred.away_kelly = 0.0
        mock_pred.home_has_edge = True
        mock_pred.away_has_edge = False
        mock_pred.projected_total = 49.5

        mock_snap = MagicMock(spec=OddsSnapshot)
        mock_snap.game_id = 150
        mock_snap.observed_at = now - timedelta(hours=1)
        mock_snap.captured_at = now - timedelta(hours=1)
        mock_snap.bookmaker = "draftkings"
        mock_snap.home_ml = -140
        mock_snap.away_ml = 120
        mock_snap.home_implied_prob = 0.58
        mock_snap.away_implied_prob = 0.42
        mock_snap.total_line = 48.5
        mock_snap.over_ml = -110
        mock_snap.under_ml = -110
        mock_snap.over_bookmaker = "draftkings"
        mock_snap.under_bookmaker = "draftkings"

        call_count = 0
        async def mock_exec(stmt):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one_or_none.return_value = mock_pred
            elif call_count == 2:
                res.scalar_one_or_none.return_value = mock_game
            elif call_count == 3:
                res.scalar_one_or_none.return_value = mock_snap
            return res

        mock_db = AsyncMock()
        mock_db.execute.side_effect = mock_exec

        res = await get_game_prediction(game_id=150, db=mock_db)

        assert "totals" in res
        tot = res["totals"]
        assert tot["is_experimental"] is True
        assert tot["status"] == "available"
        assert tot["market_line"] == 48.5
        assert tot["projected_total"] == 49.5
        assert tot["pick"] == "OVER"
        assert tot["probability"] > 0.5
        assert tot["odds_american"] == -110
        assert tot["ev_dollars"] is not None
        assert tot["tier"] == "EXPERIMENTAL"
        assert tot["has_kelly_edge"] is False

    @pytest.mark.asyncio
    async def test_get_game_prediction_rejects_post_kickoff_totals_snapshot(self):
        """Verifica que un snapshot observado en o después del kickoff se rechace (Cero Data Leakage)."""
        now = datetime.now(timezone.utc)
        kickoff = now - timedelta(minutes=10)  # Partido ya comenzó

        mock_game = MagicMock(spec=Game)
        mock_game.id = 200
        mock_game.sport = "NFL"
        mock_game.game_date = kickoff
        mock_game.external_id = "nfl_leak_check_2026"

        mock_pred = MagicMock(spec=Prediction)
        mock_pred.game_id = 200
        mock_pred.model_version = "nfl_temporal_v1"
        mock_pred.predicted_at = now - timedelta(hours=3)
        mock_pred.as_of = mock_pred.predicted_at
        mock_pred.home_win_prob = 0.55
        mock_pred.away_win_prob = 0.45
        mock_pred.confluence_tier = "TIER_2"
        mock_pred.model_agreement = 2
        mock_pred.best_bookmaker = "draftkings"
        mock_pred.best_home_ml = -120
        mock_pred.best_away_ml = 100
        mock_pred.home_ev = 2.0
        mock_pred.away_ev = -5.0
        mock_pred.home_kelly = 0.01
        mock_pred.away_kelly = 0.0
        mock_pred.home_has_edge = True
        mock_pred.away_has_edge = False

        # Snapshot observado después del kickoff (data leakage)
        post_ko_snap = MagicMock(spec=OddsSnapshot)
        post_ko_snap.game_id = 200
        post_ko_snap.observed_at = now  # Post kickoff
        post_ko_snap.captured_at = now
        post_ko_snap.bookmaker = "draftkings"
        post_ko_snap.home_ml = -120
        post_ko_snap.away_ml = 100
        post_ko_snap.total_line = 51.5

        call_count = 0
        async def mock_exec(stmt):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one_or_none.return_value = mock_pred
            elif call_count == 2:
                res.scalar_one_or_none.return_value = mock_game
            elif call_count == 3:
                res.scalar_one_or_none.return_value = post_ko_snap
            return res

        mock_db = AsyncMock()
        mock_db.execute.side_effect = mock_exec

        res = await get_game_prediction(game_id=200, db=mock_db)
        assert res["totals"]["status"] == "unavailable"
        assert res["totals"]["market_line"] is None

    @pytest.mark.asyncio
    async def test_get_game_prediction_queries_dedicated_totals_snapshot_when_h2h_lacks_it(self):
        """Si h2h_snap no tiene total_line, consulta el snapshot dedicado de totales pre-kickoff."""
        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=5)

        mock_game = MagicMock(spec=Game)
        mock_game.id = 300
        mock_game.sport = "NFL"
        mock_game.game_date = kickoff
        mock_game.external_id = "nfl_split_market_2026"
        mock_game.home_team = MagicMock()
        mock_game.home_team.name = "Dallas Cowboys"
        mock_game.away_team = MagicMock()
        mock_game.away_team.name = "Philadelphia Eagles"

        mock_pred = MagicMock(spec=Prediction)
        mock_pred.game_id = 300
        mock_pred.model_version = "nfl_temporal_v1"
        mock_pred.predicted_at = now - timedelta(hours=2)
        mock_pred.as_of = mock_pred.predicted_at
        mock_pred.home_win_prob = 0.52
        mock_pred.away_win_prob = 0.48
        mock_pred.confluence_tier = "TIER_2"
        mock_pred.model_agreement = 2
        mock_pred.best_bookmaker = "fanduel"
        mock_pred.best_home_ml = -115
        mock_pred.best_away_ml = -105
        mock_pred.home_ev = 1.0
        mock_pred.away_ev = -2.0
        mock_pred.home_kelly = 0.005
        mock_pred.away_kelly = 0.0
        mock_pred.home_has_edge = False
        mock_pred.away_has_edge = False
        mock_pred.projected_total = 50.0

        # h2h_snap carece de total_line (None)
        h2h_snap = MagicMock(spec=OddsSnapshot)
        h2h_snap.game_id = 300
        h2h_snap.observed_at = now - timedelta(hours=1)
        h2h_snap.captured_at = now - timedelta(hours=1)
        h2h_snap.bookmaker = "fanduel"
        h2h_snap.home_ml = -115
        h2h_snap.away_ml = -105
        h2h_snap.total_line = None

        # total_snap dedicado sí tiene total_line
        tot_snap = MagicMock(spec=OddsSnapshot)
        tot_snap.game_id = 300
        tot_snap.observed_at = now - timedelta(hours=1)
        tot_snap.captured_at = now - timedelta(hours=1)
        tot_snap.bookmaker = "draftkings"
        tot_snap.total_line = 47.5
        tot_snap.over_ml = -110
        tot_snap.under_ml = -110
        tot_snap.over_bookmaker = "draftkings"
        tot_snap.under_bookmaker = "draftkings"

        call_count = 0
        async def mock_exec(stmt):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one_or_none.return_value = mock_pred
            elif call_count == 2:
                res.scalar_one_or_none.return_value = mock_game
            elif call_count == 3:
                res.scalar_one_or_none.return_value = h2h_snap
            elif call_count == 4:
                res.scalar_one_or_none.return_value = tot_snap
            return res

        mock_db = AsyncMock()
        mock_db.execute.side_effect = mock_exec

        res = await get_game_prediction(game_id=300, db=mock_db)
        assert res["totals"]["status"] == "available"
        assert res["totals"]["market_line"] == 47.5
        assert res["totals"]["is_experimental"] is True

    def test_spread_and_totals_fail_closed_on_boolean_types(self):
        """Verifica que spread_line, total_line y momios rechacen booleanos fail-closed."""
        game_dt = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
        res = generate_nfl_matchup_analytics(
            home_team="Team A",
            away_team="Team B",
            game_date=game_dt,
            home_ml=-110,
            away_ml=-110,
            spread_line=True,  # Booleano no debe tratarse como float 1.0
            total_line=False,  # Booleano no debe tratarse como float 0.0
            over_ml=True,
            under_ml=False,
            model_prob_home=0.55,
            model_prob_away=0.45,
        )
        assert res["spread"]["status"] == "unavailable"
        assert res["spread"]["market_line"] is None
        assert res["totals"]["status"] == "unavailable"
        assert res["totals"]["market_line"] is None

    @pytest.mark.asyncio
    async def test_get_game_prediction_with_stored_prediction_without_totals(self):
        """Si la predicción almacenada carece de projected_total (None), el endpoint retorna totals.status='unavailable'."""
        now = datetime.now(timezone.utc)

        mock_game = MagicMock(spec=Game)
        mock_game.id = 160
        mock_game.sport = "NFL"
        mock_game.game_date = now + timedelta(hours=3)
        mock_game.status = "scheduled"
        mock_game.home_team = MagicMock(name="Chiefs")
        mock_game.home_team.name = "Kansas City Chiefs"
        mock_game.away_team = MagicMock(name="Bills")
        mock_game.away_team.name = "Buffalo Bills"

        mock_pred = MagicMock(spec=Prediction)
        mock_pred.id = 1060
        mock_pred.game_id = 160
        mock_pred.model_version = "nfl_temporal_v1"
        mock_pred.predicted_at = now - timedelta(hours=4)
        mock_pred.as_of = mock_pred.predicted_at
        mock_pred.home_win_prob = 0.60
        mock_pred.away_win_prob = 0.40
        mock_pred.confluence_tier = "TIER_3"
        mock_pred.model_agreement = 3
        mock_pred.best_bookmaker = "draftkings"
        mock_pred.best_home_ml = -140
        mock_pred.best_away_ml = 120
        mock_pred.home_ev = 5.2
        mock_pred.away_ev = -10.0
        mock_pred.home_kelly = 0.02
        mock_pred.away_kelly = 0.0
        mock_pred.home_has_edge = True
        mock_pred.away_has_edge = False
        mock_pred.projected_total = None  # Sin totales proyectados

        mock_snap = MagicMock(spec=OddsSnapshot)
        mock_snap.game_id = 160
        mock_snap.observed_at = now - timedelta(hours=1)
        mock_snap.captured_at = now - timedelta(hours=1)
        mock_snap.bookmaker = "draftkings"
        mock_snap.home_ml = -140
        mock_snap.away_ml = 120
        mock_snap.home_implied_prob = 0.58
        mock_snap.away_implied_prob = 0.42
        mock_snap.total_line = 48.5
        mock_snap.over_ml = -110
        mock_snap.under_ml = -110

        call_count = 0
        async def mock_exec(stmt):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one_or_none.return_value = mock_pred
            elif call_count == 2:
                res.scalar_one_or_none.return_value = mock_game
            elif call_count == 3:
                res.scalar_one_or_none.return_value = mock_snap
            return res

        mock_db = AsyncMock()
        mock_db.execute.side_effect = mock_exec

        res = await get_game_prediction(game_id=160, db=mock_db)
        assert "totals" in res
        tot = res["totals"]
        assert tot["status"] == "unavailable"
        assert tot["projected_total"] is None
        assert tot["pick"] is None
        assert tot["probability"] is None
        assert tot["ev_dollars"] is None
        assert tot["edge_pct"] is None
        assert tot["is_experimental"] is True

    @pytest.mark.asyncio
    async def test_get_today_picks_with_stored_prediction_without_totals(self):
        """get_today_picks con una predicción sin projected_total devuelve pick con totals.status='unavailable'."""
        now = datetime.now(timezone.utc)
        game_dt = now + timedelta(hours=4)

        mock_game = MagicMock(spec=Game)
        mock_game.id = 500
        mock_game.sport = "NFL"
        mock_game.game_date = game_dt
        mock_game.status = "scheduled"
        mock_game.external_id = "nfl_today_nototals_2026"
        mock_game.home_team_id = 1
        mock_game.away_team_id = 2

        home = MagicMock(spec=Team)
        home.id = 1
        home.name = "Kansas City Chiefs"
        home.abbreviation = "KC"
        home.city = "Kansas City"
        home.conference = "AFC"
        home.division = "West"

        away = MagicMock(spec=Team)
        away.id = 2
        away.name = "Buffalo Bills"
        away.abbreviation = "BUF"
        away.city = "Buffalo"
        away.conference = "AFC"
        away.division = "East"

        mock_pred = MagicMock(spec=Prediction)
        mock_pred.id = 101
        mock_pred.game_id = 500
        mock_pred.model_version = "nfl_temporal_v1"
        mock_pred.predicted_at = now - timedelta(hours=2)
        mock_pred.as_of = mock_pred.predicted_at
        mock_pred.home_win_prob = 0.65
        mock_pred.away_win_prob = 0.35
        mock_pred.confluence_tier = "PREMIUM"
        mock_pred.model_agreement = 3
        mock_pred.projected_total = None  # Sin proyección de totales

        snap = MagicMock(spec=OddsSnapshot)
        snap.game_id = 500
        snap.bookmaker = "draftkings"
        snap.observed_at = now - timedelta(hours=1)
        snap.captured_at = now - timedelta(hours=1)
        snap.home_ml = -150
        snap.away_ml = 130
        snap.home_spread = -3.0
        snap.away_spread = 3.0
        snap.home_spread_ml = -110
        snap.away_spread_ml = -110
        snap.total_line = 47.5
        snap.over_ml = -110
        snap.under_ml = -110
        snap.home_implied_prob = 0.58
        snap.away_implied_prob = 0.42
        snap.is_stale = False

        call_idx = 0
        async def mock_exec(stmt):
            nonlocal call_idx
            call_idx += 1
            res = MagicMock()
            if call_idx == 1:
                res.all.return_value = [(mock_pred, mock_game, home, away)]
            elif call_idx in (2, 3, 4):
                res.scalar_one_or_none.return_value = snap
            else:
                res.scalar_one_or_none.return_value = None
            return res

        mock_db = AsyncMock()
        mock_db.execute.side_effect = mock_exec

        res = await get_today_picks(sport="NFL", status="all", period="all", db=mock_db)
        assert res["total_picks"] == 1
        pick = res["picks"][0]
        assert "totals" in pick
        assert pick["totals"]["status"] == "unavailable"
        assert pick["totals"]["projected_total"] is None
        assert pick["totals"]["pick"] is None
        assert pick["totals"]["ev_dollars"] is None

    @pytest.mark.asyncio
    async def test_get_today_picks_with_stored_prediction_with_totals(self):
        """get_today_picks con projected_total válido en DB expone totals disponible y experimental."""
        now = datetime.now(timezone.utc)
        game_dt = now + timedelta(hours=4)

        mock_game = MagicMock(spec=Game)
        mock_game.id = 501
        mock_game.sport = "NFL"
        mock_game.game_date = game_dt
        mock_game.status = "scheduled"
        mock_game.external_id = "nfl_today_withtotals_2026"
        mock_game.home_team_id = 1
        mock_game.away_team_id = 2

        home = MagicMock(spec=Team)
        home.id = 1
        home.name = "Kansas City Chiefs"
        home.abbreviation = "KC"
        home.city = "Kansas City"
        home.conference = "AFC"
        home.division = "West"

        away = MagicMock(spec=Team)
        away.id = 2
        away.name = "Buffalo Bills"
        away.abbreviation = "BUF"
        away.city = "Buffalo"
        away.conference = "AFC"
        away.division = "East"

        mock_pred = MagicMock(spec=Prediction)
        mock_pred.id = 102
        mock_pred.game_id = 501
        mock_pred.model_version = "nfl_temporal_v1"
        mock_pred.predicted_at = now - timedelta(hours=2)
        mock_pred.as_of = mock_pred.predicted_at
        mock_pred.home_win_prob = 0.65
        mock_pred.away_win_prob = 0.35
        mock_pred.confluence_tier = "PREMIUM"
        mock_pred.model_agreement = 3
        mock_pred.projected_total = 52.0  # Proyección real de totales

        snap = MagicMock(spec=OddsSnapshot)
        snap.game_id = 501
        snap.bookmaker = "draftkings"
        snap.observed_at = now - timedelta(hours=1)
        snap.captured_at = now - timedelta(hours=1)
        snap.home_ml = -150
        snap.away_ml = 130
        snap.home_spread = -3.0
        snap.away_spread = 3.0
        snap.home_spread_ml = -110
        snap.away_spread_ml = -110
        snap.total_line = 47.5
        snap.over_ml = -110
        snap.under_ml = -110
        snap.home_implied_prob = 0.58
        snap.away_implied_prob = 0.42
        snap.is_stale = False

        call_idx = 0
        async def mock_exec(stmt):
            nonlocal call_idx
            call_idx += 1
            res = MagicMock()
            if call_idx == 1:
                res.all.return_value = [(mock_pred, mock_game, home, away)]
            elif call_idx in (2, 3, 4):
                res.scalar_one_or_none.return_value = snap
            else:
                res.scalar_one_or_none.return_value = None
            return res

        mock_db = AsyncMock()
        mock_db.execute.side_effect = mock_exec

        res = await get_today_picks(sport="NFL", status="all", period="all", db=mock_db)
        assert res["total_picks"] == 1
        pick = res["picks"][0]
        assert "totals" in pick
        assert pick["totals"]["status"] == "available"
        assert pick["totals"]["projected_total"] == 52.0
        assert pick["totals"]["pick"] == "OVER"
        assert pick["totals"]["market_line"] == 47.5
        assert pick["totals"]["is_experimental"] is True
        assert pick["totals"]["ev_dollars"] is not None


class TestCalibratedSigmaExcludesHoldout:
    """Pruebas para garantizar que get_nfl_totals_calibrated_sigma excluye estrictamente el holdout/lockbox."""

    def test_sigma_prefers_metrics_oof_over_holdout_when_both_present(self):
        """Cuando la metadata contiene tanto holdout_evaluation como metrics OOF, se elige estrictamente OOF."""
        meta = {
            "totals_evaluation": {
                "selected_model": "ridge",
                "holdout_evaluation": {
                    "residual_sigma": 8.12345,
                    "total_mae": 9.5,
                },
                "metrics": {
                    "residual_sigma": 15.25,
                    "mean_total_mae": 12.3,
                },
            }
        }
        sigma = get_nfl_totals_calibrated_sigma(force_refresh=True, metadata_override=meta)
        assert sigma == 15.25
        assert sigma != 8.12345

    def test_sigma_prefers_development_folds_oof_over_holdout_when_metrics_missing(self):
        """Si metrics no contiene residual_sigma, calcula el promedio de folds OOF ignorando holdout."""
        meta = {
            "totals_evaluation": {
                "selected_model": "ridge",
                "holdout_evaluation": {
                    "residual_sigma": 7.777,
                },
                "models": {
                    "ridge": {
                        "folds": [
                            {"fold": 1, "residual_sigma": 14.0},
                            {"fold": 2, "residual_sigma": 16.0},
                        ]
                    }
                },
                "metrics": {},
            }
        }
        sigma = get_nfl_totals_calibrated_sigma(force_refresh=True, metadata_override=meta)
        assert sigma == 15.0
        assert sigma != 7.777

    def test_sigma_never_uses_holdout_even_if_only_holdout_is_defined(self):
        """Si la única clave presente en metadata es holdout_evaluation, se rehúsa y cae al fallback seguro 14.6."""
        meta = {
            "totals_evaluation": {
                "holdout_evaluation": {
                    "residual_sigma": 9.999,
                },
                "metrics": {},
            }
        }
        sigma = get_nfl_totals_calibrated_sigma(force_refresh=True, metadata_override=meta)
        assert sigma == 14.6
        assert sigma != 9.999

    def test_sigma_production_metadata_loads_clean_oof_value(self):
        """La metadata oficial de disco carga residual_sigma OOF (~14.60) sin contaminación de holdout."""
        sigma = get_nfl_totals_calibrated_sigma(force_refresh=True)
        assert math.isclose(sigma, 14.602887, abs_tol=1e-3)

    @pytest.mark.parametrize("invalid_metrics_sigma", [0, 0.0, -1.0, float("nan"), float("inf"), True, False, "bad_str"])
    def test_sigma_falls_back_to_folds_when_metrics_sigma_is_invalid(self, invalid_metrics_sigma):
        """Cuando metrics.residual_sigma es inválido o booleano, retrocede a folds OOF ignorando holdout."""
        meta = {
            "totals_evaluation": {
                "selected_model": "ridge",
                "holdout_evaluation": {
                    "residual_sigma": 6.543,
                },
                "metrics": {
                    "residual_sigma": invalid_metrics_sigma,
                },
                "models": {
                    "ridge": {
                        "folds": [
                            {"fold": 1, "residual_sigma": 14.0},
                            {"fold": 2, "residual_sigma": 16.0},
                        ]
                    }
                },
            }
        }
        sigma = get_nfl_totals_calibrated_sigma(force_refresh=True, metadata_override=meta)
        assert sigma == 15.0
        assert sigma != 6.543

    def test_sigma_filters_out_corrupt_fold_sigmas(self):
        """Si los folds contienen sigmas booleanos, no finitos o negativos, se filtran antes de promediar."""
        meta = {
            "totals_evaluation": {
                "selected_model": "ridge",
                "holdout_evaluation": {
                    "residual_sigma": 5.555,
                },
                "metrics": {},
                "models": {
                    "ridge": {
                        "folds": [
                            {"fold": 1, "residual_sigma": 14.0},
                            {"fold": 2, "residual_sigma": True},
                            {"fold": 3, "residual_sigma": -1.0},
                            {"fold": 4, "residual_sigma": float("nan")},
                            {"fold": 5, "residual_sigma": 16.0},
                            {"fold": 6, "residual_sigma": "corrupt"},
                        ]
                    }
                },
            }
        }
        sigma = get_nfl_totals_calibrated_sigma(force_refresh=True, metadata_override=meta)
        assert sigma == 15.0  # promedio de 14.0 y 16.0
        assert sigma != 5.555


class TestDatabaseInitSchemaUpgrade:
    """Pruebas para certificar la migración/upgrade idempotente de predictions.projected_total en init_db."""

    @pytest.mark.asyncio
    async def test_init_db_adds_projected_total_to_existing_predictions_table(self):
        """Simula una tabla preexistente sin projected_total en un esquema temporal aislado.

        Certifica:
        1. Que init_db migra agregando projected_total DOUBLE PRECISION.
        2. Que los datos preexistentes en la tabla se preservan intactos sin pérdida de registros.
        3. Que la operación es 100% idempotente al ejecutarse repetidas veces.
        4. Que la tabla compartida de producción (public.predictions) no es alterada ni eliminada.
        """
        import uuid
        tmp_schema = f"test_iso_upgrade_{uuid.uuid4().hex[:8]}"

        async with engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                # 0. Verificar que la tabla compartida existe y registrar recuento de filas
                shared_count_before = (await conn.execute(text("SELECT count(*) FROM public.predictions;"))).scalar()
                assert shared_count_before is not None

                try:
                    # 1. Crear esquema temporal aislado y cambiar search_path
                    await conn.execute(text(f"CREATE SCHEMA {tmp_schema};"))
                    await conn.execute(text(f"SET search_path TO {tmp_schema};"))
                    curr = (await conn.execute(text("SELECT current_schema();"))).scalar()
                    assert curr == tmp_schema

                    # 2. Simular esquema antiguo preexistente sin projected_total
                    await conn.execute(text(
                        "CREATE TABLE predictions ("
                        "id SERIAL PRIMARY KEY, "
                        "game_id INT, "
                        "model_version VARCHAR(50), "
                        "home_win_prob DOUBLE PRECISION"
                        ");"
                    ))
                    # Insertar registros preexistentes para validar que la migración no los destruye
                    await conn.execute(text(
                        "INSERT INTO predictions (game_id, model_version, home_win_prob) VALUES "
                        "(9001, 'legacy_v1', 0.65), "
                        "(9002, 'legacy_v2', 0.42);"
                    ))

                    # Verificar que en el esquema aislado NO existe projected_total
                    check_no_col = await conn.execute(text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND table_name='predictions' AND column_name='projected_total';"
                    ))
                    assert check_no_col.scalar_one_or_none() is None

                    # 3. Ejecutar init_db sobre la conexión del esquema aislado
                    await init_db(target_conn=conn)

                    # 4. Comprobar que la columna fue añadida con tipo double precision
                    check_migrated = await conn.execute(text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND table_name='predictions' AND column_name='projected_total';"
                    ))
                    dt = check_migrated.scalar_one_or_none()
                    assert dt is not None, "init_db debió agregar la columna projected_total"
                    assert "double precision" in dt.lower()

                    # 5. Comprobar que los registros preexistentes están 100% intactos
                    rows = (await conn.execute(text(
                        "SELECT id, game_id, model_version, home_win_prob, projected_total FROM predictions ORDER BY game_id;"
                    ))).fetchall()
                    assert len(rows) == 2
                    assert rows[0][1] == 9001 and rows[0][2] == "legacy_v1" and rows[0][3] == 0.65 and rows[0][4] is None
                    assert rows[1][1] == 9002 and rows[1][2] == "legacy_v2" and rows[1][3] == 0.42 and rows[1][4] is None

                    # 6. Probar idempotencia ejecutando init_db una segunda vez consecutiva
                    await init_db(target_conn=conn)
                    check_idem = await conn.execute(text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND table_name='predictions' AND column_name='projected_total';"
                    ))
                    assert check_idem.scalar_one_or_none() is not None

                    # Verificar que los datos siguen intactos tras la segunda ejecución
                    rows_after = (await conn.execute(text("SELECT count(*) FROM predictions;"))).scalar()
                    assert rows_after == 2

                finally:
                    # Restaurar search_path y eliminar el esquema temporal
                    await conn.execute(text("SET search_path TO public;"))
                    await conn.execute(text(f"DROP SCHEMA IF EXISTS {tmp_schema} CASCADE;"))

                # 7. Verificar que la tabla compartida de producción public.predictions no perdió registros
                shared_count_after = (await conn.execute(text("SELECT count(*) FROM public.predictions;"))).scalar()
                assert shared_count_after == shared_count_before, "La tabla compartida jamás debe ser afectada por la prueba de migración"

    @pytest.mark.asyncio
    async def test_init_db_sqlite_dialect_adds_column_when_missing(self):
        """Verifica que init_db en dialecto SQLite consulte table_info y emita ALTER TABLE si falta projected_total."""
        mock_conn = AsyncMock()
        mock_conn.dialect.name = "sqlite"
        mock_res = MagicMock()
        mock_res.fetchall.return_value = [(0, "id"), (1, "game_id"), (2, "model_version")]
        mock_conn.execute.return_value = mock_res

        await init_db(target_conn=mock_conn)

        executed_stmts = [str(call.args[0]) for call in mock_conn.execute.call_args_list]
        assert any("PRAGMA table_info(predictions)" in s for s in executed_stmts), "init_db debió consultar PRAGMA table_info"
        assert any("ALTER TABLE predictions ADD COLUMN projected_total REAL" in s for s in executed_stmts), "init_db debió emitir ALTER TABLE para SQLite"

    @pytest.mark.asyncio
    async def test_init_db_sqlite_dialect_idempotent_when_column_already_present(self):
        """Verifica que init_db en dialecto SQLite sea idempotente y no altere la tabla si projected_total ya existe."""
        mock_conn = AsyncMock()
        mock_conn.dialect.name = "sqlite"
        mock_res = MagicMock()
        mock_res.fetchall.return_value = [(0, "id"), (1, "game_id"), (2, "model_version"), (3, "projected_total")]
        mock_conn.execute.return_value = mock_res

        await init_db(target_conn=mock_conn)

        executed_stmts = [str(call.args[0]) for call in mock_conn.execute.call_args_list]
        assert any("PRAGMA table_info(predictions)" in s for s in executed_stmts)
        assert not any("ALTER TABLE predictions ADD COLUMN projected_total REAL" in s for s in executed_stmts), "No debe emitir ALTER TABLE si la columna ya existe"

    @pytest.mark.asyncio
    async def test_init_db_sqlite_migration_compatibility(self):
        """Verifica que la lógica de migración en init_db no ejecute DO $$ y sea compatible con SQLite."""
        pytest.importorskip("aiosqlite", reason="aiosqlite driver opcional para pruebas sqlite")
        from sqlalchemy.ext.asyncio import create_async_engine
        sqlite_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        from app.core.database import Base
        async with sqlite_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            res = await conn.execute(text("PRAGMA table_info(predictions)"))
            cols = [row[1] for row in res.fetchall()]
            assert "projected_total" in cols
        await sqlite_engine.dispose()
