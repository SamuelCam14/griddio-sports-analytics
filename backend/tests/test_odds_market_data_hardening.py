"""
Pruebas focalizadas de endurecimiento para la capa de datos de cuotas, spreads y totales (TAREA-2).

Verifica:
1. Odds converter: protección contra momio 0, rangos inválidos (-100 < odds < 100), NaNs, y devig con denominadores degenerados.
2. Odds service helpers:
   - _american_to_decimal, _implied_probability, _remove_vig con edge cases.
   - _parse_odds_response: rechazo de puntos faltantes o asimétricos en spreads, totales no positivos o desiguales,
     momios inválidos (0 o entre -100 y 100), y line shopping estricto sobre el punto de spread consenso.
3. NFLMarketPricingService:
   - Rechazo de cuotas post as_of (fetched_dt > as_of o source_dt > as_of).
   - Rechazo de odds_age negativa o mapping_confidence NaN.
   - Rechazo de momios americanos inválidos en bookmakers (0, 50, -50).
   - Preservación del punto de línea en PriceRecord.
4. SportsAnalyticsService:
   - Preservación de momios observados de spread y total (no forzar -110 sintético).
   - Validación de simetría y finitud en líneas de spread y totales.
5. ParlayService:
   - Preservación de 'point' en legs resueltas de spreads y totals.
"""
from datetime import datetime, timezone, timedelta
import math
import pytest

from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.game import Game, Team, OddsSnapshot, Prediction, ProviderGameCrosswalk
from app.services.live_ingestion_service import sync_real_games
from app.services.odds_service import fetch_odds_from_db
from app.services.math.odds_converter import (
    american_to_decimal,
    decimal_to_american,
    american_to_implied_prob,
    remove_vig,
)
from app.services.odds_service import (
    _american_to_decimal,
    _implied_probability,
    _remove_vig,
    _parse_odds_response,
)
from app.services.nfl_market_pricing_service import (
    NFLMarketPricingService,
    PriceRecord,
)
from app.services.sports_analytics_service import generate_nfl_matchup_analytics
from app.services.parlay_service import evaluate_custom_parlay


# ============================================================================
# 1. Math: Odds Converter Hardening
# ============================================================================

class TestOddsConverterHardening:
    def test_american_to_decimal_invalid_inputs(self):
        # Entradas inválidas deben retornar la identidad neutral 1.0 de forma segura sin excepciones
        assert american_to_decimal(None) == 1.0
        assert american_to_decimal(0) == 1.0
        assert american_to_decimal(float("nan")) == 1.0
        assert american_to_decimal(float("inf")) == 1.0
        # En apuestas americanas, momios entre -100 y +100 son imposibles
        assert american_to_decimal(50) == 1.0
        assert american_to_decimal(-50) == 1.0
        assert american_to_decimal(99) == 1.0
        assert american_to_decimal(-99) == 1.0

    def test_american_to_decimal_valid_boundaries(self):
        assert pytest.approx(american_to_decimal(100), 0.001) == 2.00
        assert pytest.approx(american_to_decimal(-100), 0.001) == 2.00
        assert pytest.approx(american_to_decimal(-110), 0.001) == 1.9091
        assert pytest.approx(american_to_decimal(110), 0.001) == 2.10

    def test_decimal_to_american_invalid_inputs(self):
        # Entradas inválidas o <= 1.0 deben retornar 0
        assert decimal_to_american(None) == 0
        assert decimal_to_american(0.0) == 0
        assert decimal_to_american(1.0) == 0
        assert decimal_to_american(0.8) == 0
        assert decimal_to_american(float("nan")) == 0

    def test_american_to_implied_prob_invalid(self):
        # Entradas inválidas deben retornar 0.5 neutral sin causar ZeroDivisionError
        assert american_to_implied_prob(None) == 0.5
        assert american_to_implied_prob(0) == 0.5
        assert american_to_implied_prob(-50) == 0.5
        assert american_to_implied_prob(float("nan")) == 0.5

    def test_remove_vig_degenerate_and_nan(self):
        fair_h, fair_a, vig = remove_vig(0.0, 0.0)
        assert fair_h == 0.5 and fair_a == 0.5 and vig == 0.0

        fair_h, fair_a, vig = remove_vig(float("nan"), 0.5)
        assert fair_h == 0.5 and fair_a == 0.5 and vig == 0.0

        fair_h, fair_a, vig = remove_vig(-0.2, 0.0)
        assert fair_h == 0.5 and fair_a == 0.5 and vig == 0.0

    def test_remove_vig_rejects_negative_or_greater_than_one_probabilities(self):
        # Codex point 2: remove_vig(-0.2, 0.7) debe fallar cerrado aunque la suma sea positiva
        fair_h, fair_a, vig = remove_vig(-0.2, 0.7)
        assert fair_h == 0.5 and fair_a == 0.5 and vig == 0.0

        fair_h, fair_a, vig = remove_vig(0.7, -0.2)
        assert fair_h == 0.5 and fair_a == 0.5 and vig == 0.0

        fair_h, fair_a, vig = remove_vig(1.2, 0.4)
        assert fair_h == 0.5 and fair_a == 0.5 and vig == 0.0

        fair_h, fair_a, vig = remove_vig(0.0, 0.5)
        assert fair_h == 0.5 and fair_a == 0.5 and vig == 0.0


# ============================================================================
# 2. OddsService Parsing & Mathematical Helpers Hardening
# ============================================================================

class TestOddsServiceHelpersHardening:
    def test_internal_helpers_with_zero_and_invalid_odds(self):
        assert _american_to_decimal(0) == 1.0
        assert _american_to_decimal(50) == 1.0
        assert _implied_probability(0) is None
        assert _implied_probability(-50) is None
        assert _remove_vig(0.0, 0.0) == (None, None)
        assert _remove_vig(0.5, 0.5) == (0.5, 0.5)

    def test_internal_remove_vig_rejects_out_of_bounds_probabilities(self):
        # Codex point 2: _remove_vig(-0.2, 0.7) debe fallar cerrado a (None, None)
        assert _remove_vig(-0.2, 0.7) == (None, None)
        assert _remove_vig(0.7, -0.2) == (None, None)
        assert _remove_vig(1.2, 0.5) == (None, None)
        assert _remove_vig(0.0, 0.5) == (None, None)

    def test_parse_odds_response_spread_symmetry_validation(self):
        """Spreads must have opposite points (+X and -X). Asymmetric points must be dropped."""
        mock_raw = [
            {
                "id": "game_1",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "Kansas City Chiefs",
                "away_team": "Denver Broncos",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Kansas City Chiefs", "price": -110, "point": -3.5},
                                    # Corrupted feed: Broncos also -3.5 instead of +3.5!
                                    {"name": "Denver Broncos", "price": -110, "point": -3.5},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        parsed = _parse_odds_response(mock_raw)
        assert len(parsed) == 1
        # Bad spread should be discarded fail-closed
        assert parsed[0]["home_spread"] is None
        assert parsed[0]["away_spread"] is None

    def test_parse_odds_response_spread_line_shopping_consensus(self):
        """Line shopping must not compare prices across disparate points (e.g. -7.5 vs -3.5)."""
        mock_raw = [
            {
                "id": "game_2",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "Buffalo Bills",
                "away_team": "Miami Dolphins",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Buffalo Bills", "price": -110, "point": -3.5},
                                    {"name": "Miami Dolphins", "price": -110, "point": 3.5},
                                ],
                            }
                        ],
                    },
                    {
                        "key": "fanduel",
                        "title": "FanDuel",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Buffalo Bills", "price": -105, "point": -3.5},
                                    {"name": "Miami Dolphins", "price": -115, "point": 3.5},
                                ],
                            }
                        ],
                    },
                    {
                        "key": "betmgm",
                        "title": "BetMGM",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    # Very high price (+200) but on a completely different line (-7.5)
                                    {"name": "Buffalo Bills", "price": 200, "point": -7.5},
                                    {"name": "Miami Dolphins", "price": -240, "point": 7.5},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        parsed = _parse_odds_response(mock_raw)
        assert len(parsed) == 1
        # The consensus point is -3.5
        assert parsed[0]["home_spread"] == -3.5
        # The best price on -3.5 is -105 (from FanDuel), NOT +200 (from BetMGM outlier)
        assert parsed[0]["home_spread_ml"] == -105
        # Line shopping por lado: en el punto consenso, DraftKings ofrece -110 para Dolphins (+3.5),
        # que es mejor que el -115 de FanDuel. No se copia ciegamente del bookmaker de home.
        assert parsed[0]["away_spread_ml"] == -110
        assert parsed[0]["spread_home_bookmaker"] == "fanduel"
        assert parsed[0]["spread_away_bookmaker"] == "draftkings"

    def test_parse_odds_response_line_shopping_by_side_and_provenance(self):
        """Line shopping independently selects the best price per side and preserves side provenance."""
        mock_raw = [
            {
                "id": "game_ls_side",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "Chiefs",
                "away_team": "Broncos",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Chiefs", "price": -160},
                                    {"name": "Broncos", "price": 145},
                                ],
                            },
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Chiefs", "price": -115, "point": -3.5},
                                    {"name": "Broncos", "price": -105, "point": 3.5},
                                ],
                            },
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": -115, "point": 47.5},
                                    {"name": "Under", "price": -105, "point": 47.5},
                                ],
                            },
                        ],
                    },
                    {
                        "key": "fanduel",
                        "title": "FanDuel",
                        "last_update": "2026-10-26T12:05:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Chiefs", "price": -150},
                                    {"name": "Broncos", "price": 135},
                                ],
                            },
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Chiefs", "price": -105, "point": -3.5},
                                    {"name": "Broncos", "price": -115, "point": 3.5},
                                ],
                            },
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": -105, "point": 47.5},
                                    {"name": "Under", "price": -115, "point": 47.5},
                                ],
                            },
                        ],
                    },
                ],
            }
        ]
        parsed = _parse_odds_response(mock_raw)
        assert len(parsed) == 1
        item = parsed[0]
        # H2H: FanDuel has better Chiefs (-150 > -160), DraftKings has better Broncos (+145 > +135)
        assert item["home_ml"] == -150
        assert item["away_ml"] == 145
        assert item["ml_home_bookmaker"] == "fanduel"
        assert item["ml_away_bookmaker"] == "draftkings"

        # Spreads: FanDuel has better Chiefs (-105 > -115), DraftKings has better Broncos (-105 > -115)
        assert item["home_spread"] == -3.5
        assert item["away_spread"] == 3.5
        assert item["home_spread_ml"] == -105
        assert item["away_spread_ml"] == -105
        assert item["spread_home_bookmaker"] == "fanduel"
        assert item["spread_away_bookmaker"] == "draftkings"

        # Totals: FanDuel has better Over (-105 > -115), DraftKings has better Under (-105 > -115)
        assert item["total_line"] == 47.5
        assert item["over_ml"] == -105
        assert item["under_ml"] == -105
        assert item["total_over_bookmaker"] == "fanduel"
        assert item["total_under_bookmaker"] == "draftkings"

    def test_parse_odds_response_totals_validation(self):
        """Totals must be positive and both sides must agree on the line."""
        mock_raw = [
            {
                "id": "game_3",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "Detroit Lions",
                "away_team": "Green Bay Packers",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": -110, "point": -45.0},
                                    {"name": "Under", "price": -110, "point": -45.0},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        parsed = _parse_odds_response(mock_raw)
        assert parsed[0]["total_line"] is None
        assert parsed[0]["over_ml"] is None

    def test_parse_odds_response_rejects_zero_or_out_of_range_odds(self):
        # 1. Momio 0 debe ser rechazado
        mock_raw_zero = [
            {
                "id": "game_zero",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "Dallas Cowboys",
                "away_team": "Philadelphia Eagles",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Dallas Cowboys", "price": 0},
                                    {"name": "Philadelphia Eagles", "price": 0},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        parsed_zero = _parse_odds_response(mock_raw_zero)
        assert parsed_zero[0]["home_ml"] is None
        assert parsed_zero[0]["away_ml"] is None

        # 2. Momios en intervalo imposible (-100 < odds < 100), ej. 50 y -50, deben ser rechazados fail-closed
        mock_raw_out_of_range = [
            {
                "id": "game_out_of_range",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "Dallas Cowboys",
                "away_team": "Philadelphia Eagles",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Dallas Cowboys", "price": 50},
                                    {"name": "Philadelphia Eagles", "price": -50},
                                ],
                            },
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Dallas Cowboys", "price": 50, "point": -3.5},
                                    {"name": "Philadelphia Eagles", "price": -50, "point": 3.5},
                                ],
                            },
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 50, "point": 48.5},
                                    {"name": "Under", "price": -50, "point": 48.5},
                                ],
                            },
                        ],
                    }
                ],
            }
        ]
        parsed_oor = _parse_odds_response(mock_raw_out_of_range)
        assert parsed_oor[0]["home_ml"] is None
        assert parsed_oor[0]["away_ml"] is None
        assert parsed_oor[0]["home_spread"] is None
        assert parsed_oor[0]["total_line"] is None

    def test_parse_odds_response_spread_consensus_when_outlier_is_first(self):
        """Si un bookmaker outlier (-7.5) viene primero en el JSON, el consenso (-3.5) debe prevalecer."""
        mock_raw = [
            {
                "id": "game_outlier_first",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "Buffalo Bills",
                "away_team": "Miami Dolphins",
                "bookmakers": [
                    {
                        "key": "betmgm",  # Outlier primero en el feed
                        "title": "BetMGM",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Buffalo Bills", "price": 200, "point": -7.5},
                                    {"name": "Miami Dolphins", "price": -240, "point": 7.5},
                                ],
                            }
                        ],
                    },
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Buffalo Bills", "price": -110, "point": -3.5},
                                    {"name": "Miami Dolphins", "price": -110, "point": 3.5},
                                ],
                            }
                        ],
                    },
                    {
                        "key": "fanduel",
                        "title": "FanDuel",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Buffalo Bills", "price": -105, "point": -3.5},
                                    {"name": "Miami Dolphins", "price": -115, "point": 3.5},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        parsed = _parse_odds_response(mock_raw)
        assert len(parsed) == 1
        # La línea consenso es -3.5 (DraftKings y FanDuel coinciden), no el outlier -7.5
        assert parsed[0]["home_spread"] == -3.5
        # El mejor precio sobre -3.5 es FanDuel (-105) para casa y DraftKings (-110) para visita
        assert parsed[0]["home_spread_ml"] == -105
        assert parsed[0]["away_spread_ml"] == -110
        assert parsed[0]["spread_home_bookmaker"] == "fanduel"
        assert parsed[0]["spread_away_bookmaker"] == "draftkings"
        assert parsed[0]["spread_bookmaker"] == "fanduel/draftkings"

    def test_parse_odds_response_totals_case_insensitivity(self):
        """Totales con 'over' / 'under' en minúsculas deben parsearse correctamente."""
        mock_raw = [
            {
                "id": "game_lower_totals",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "KC",
                "away_team": "DEN",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "over", "price": -110, "point": 45.5},
                                    {"name": "under", "price": -110, "point": 45.5},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        parsed = _parse_odds_response(mock_raw)
        assert parsed[0]["total_line"] == 45.5
        assert parsed[0]["over_ml"] == -110
        assert parsed[0]["under_ml"] == -110


# ============================================================================
# 3. NFLMarketPricingService Hardening
# ============================================================================

class TestNFLMarketPricingServiceHardening:
    @pytest.fixture
    def pricing_service(self):
        return NFLMarketPricingService()

    @pytest.fixture
    def base_price_record(self):
        return PriceRecord(
            event_id="nfl_2026_test",
            sportsbook="draftkings",
            market_key="h2h",
            home_team="Kansas City Chiefs",
            away_team="Denver Broncos",
            home_odds_american=-150,
            away_odds_american=130,
            source_timestamp_utc="2026-10-20T11:00:00Z",
            fetched_timestamp_utc="2026-10-20T11:05:00Z",
            decision_cutoff_utc="2026-10-20T12:00:00Z",
            market_status="open",
            mapping_confidence=1.0,
        )

    def test_evaluate_game_pricing_rejects_odds_post_as_of(self, pricing_service, base_price_record):
        """Odds fetched after as_of are a temporal leak and must be rejected fail-closed."""
        # as_of is 10:00:00Z, but odds were fetched at 11:05:00Z
        as_of = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
        res = pricing_service.evaluate_game_pricing(0.65, base_price_record, as_of=as_of)
        assert res["status"] == "rejected"
        assert res["reason"] == "odds_post_as_of"

    def test_evaluate_game_pricing_rejects_negative_age(self, pricing_service):
        as_of = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
        record = PriceRecord(
            event_id="nfl_2026_future",
            sportsbook="draftkings",
            market_key="h2h",
            home_team="KC",
            away_team="DEN",
            home_odds_american=-150,
            away_odds_american=130,
            source_timestamp_utc="2026-10-20T11:00:00Z",
            fetched_timestamp_utc="2026-10-20T13:00:00Z",  # fetched in future relative to as_of
            decision_cutoff_utc="2026-10-20T14:00:00Z",
            market_status="open",
            mapping_confidence=1.0,
        )
        res = pricing_service.evaluate_game_pricing(0.65, record, as_of=as_of)
        assert res["status"] == "rejected"
        assert res["reason"] in ("odds_post_as_of", "odds_future_negative_age")

    def test_evaluate_game_pricing_rejects_nan_mapping_confidence(self, pricing_service):
        record = PriceRecord(
            event_id="nfl_2026_nan_conf",
            sportsbook="draftkings",
            market_key="h2h",
            home_team="KC",
            away_team="DEN",
            home_odds_american=-150,
            away_odds_american=130,
            source_timestamp_utc="2026-10-20T11:00:00Z",
            fetched_timestamp_utc="2026-10-20T11:05:00Z",
            decision_cutoff_utc="2026-10-20T12:00:00Z",
            market_status="open",
            mapping_confidence=float("nan"),
        )
        res = pricing_service.evaluate_game_pricing(0.65, record)
        assert res["status"] == "rejected"
        assert res["reason"] == "low_mapping_confidence"

    def test_evaluate_game_pricing_rejects_invalid_american_odds(self, pricing_service):
        """American odds in invalid range (e.g. 50 or -50) must be rejected."""
        record = PriceRecord(
            event_id="nfl_2026_bad_odds",
            sportsbook="draftkings",
            market_key="h2h",
            home_team="KC",
            away_team="DEN",
            home_odds_american=50,  # Invalid
            away_odds_american=130,
            source_timestamp_utc="2026-10-20T11:00:00Z",
            fetched_timestamp_utc="2026-10-20T11:05:00Z",
            decision_cutoff_utc="2026-10-20T12:00:00Z",
            market_status="open",
            mapping_confidence=1.0,
        )
        res = pricing_service.evaluate_game_pricing(0.65, record)
        assert res["status"] == "rejected"
        assert res["reason"] == "invalid_odds_decimal"

    def test_evaluate_game_pricing_preserves_line_in_price_record(self):
        """PriceRecord preserves optional line for spreads and totals."""
        record = PriceRecord(
            event_id="nfl_2026_spr",
            sportsbook="draftkings",
            market_key="spreads",
            home_team="KC",
            away_team="DEN",
            home_odds_american=-110,
            away_odds_american=-110,
            source_timestamp_utc="2026-10-20T11:00:00Z",
            fetched_timestamp_utc="2026-10-20T11:05:00Z",
            decision_cutoff_utc="2026-10-20T12:00:00Z",
            market_status="open",
            mapping_confidence=1.0,
            line=-3.5,
        )
        assert record.line == -3.5

    def test_evaluate_game_pricing_rejects_missing_or_invalid_line_for_spreads_and_totals(self, pricing_service):
        rec_spr = PriceRecord(
            event_id="t1", sportsbook="draftkings", market_key="spreads",
            home_team="KC", away_team="DEN", home_odds_american=-110, away_odds_american=-110,
            source_timestamp_utc="2026-10-20T11:00:00Z", fetched_timestamp_utc="2026-10-20T11:05:00Z",
            decision_cutoff_utc="2026-10-20T12:00:00Z", line=None
        )
        res_spr = pricing_service.evaluate_game_pricing(0.55, rec_spr)
        assert res_spr["status"] == "rejected"
        assert res_spr["reason"] == "invalid_spread_line"

        rec_tot = PriceRecord(
            event_id="t2", sportsbook="draftkings", market_key="totals",
            home_team="KC", away_team="DEN", home_odds_american=-110, away_odds_american=-110,
            source_timestamp_utc="2026-10-20T11:00:00Z", fetched_timestamp_utc="2026-10-20T11:05:00Z",
            decision_cutoff_utc="2026-10-20T12:00:00Z", line=-5.0
        )
        res_tot = pricing_service.evaluate_game_pricing(0.55, rec_tot)
        assert res_tot["status"] == "rejected"
        assert res_tot["reason"] == "invalid_total_line"


# ============================================================================
# 4. SportsAnalyticsService Preservation of Observed Prices
# ============================================================================

class TestSportsAnalyticsServiceHardening:
    def test_analytics_preserves_observed_spread_odds(self):
        """Observed spread odds (-105 / -115) must be preserved in result rather than hardcoding -110."""
        game_dt = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Denver Broncos",
            game_date=game_dt,
            home_ml=-150,
            away_ml=130,
            spread_line=-3.5,
            total_line=47.5,
            home_spread_ml=-105,
            away_spread_ml=-115,
            over_ml=-108,
            under_ml=-112,
            model_prob_home=0.65,
            model_prob_away=0.35,
            model_projected_total=48.0,
        )

        assert result["spread"]["status"] == "available"
        assert result["spread"]["home_spread_ml"] == -105
        assert result["spread"]["away_spread_ml"] == -115

        assert result["totals"]["status"] == "available"
        assert result["totals"]["over_ml"] == -108
        assert result["totals"]["under_ml"] == -112

    def test_analytics_devigs_spread_and_totals_lines(self):
        game_dt = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Denver Broncos",
            game_date=game_dt,
            home_ml=-150,
            away_ml=130,
            spread_line=-3.5,
            total_line=47.5,
            home_spread_ml=-105,
            away_spread_ml=-115,
            over_ml=-105,
            under_ml=-115,
            model_prob_home=0.60,
            model_prob_away=0.40,
        )
        # De-vigged spread cover probability: -105 vs -115 fair home cover is ~0.4891
        assert result["spread"]["market_prob_home_cover_no_vig"] is not None
        assert pytest.approx(result["spread"]["market_prob_home_cover_no_vig"], 0.01) == 0.4891
        assert result["spread"]["market_vig"] is not None
        assert result["spread"]["market_vig"] > 0

        # Totals vig should also be calculated
        assert result["totals"]["market_prob_over_no_vig"] is not None
        assert pytest.approx(result["totals"]["market_prob_over_no_vig"], 0.01) == 0.4891
        assert result["totals"]["market_vig"] is not None

    def test_analytics_sanitizes_out_of_range_spread_and_totals_odds(self):
        """Momios de spread o total en rango -100..100 o 0 deben ser sanitizados a None."""
        game_dt = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="KC",
            away_team="DEN",
            game_date=game_dt,
            home_ml=-150,
            away_ml=130,
            spread_line=-3.5,
            total_line=45.0,
            home_spread_ml=50,   # Inválido
            away_spread_ml=-50,  # Inválido
            over_ml=0,           # Inválido
            under_ml=80,         # Inválido
            model_prob_home=0.60,
            model_prob_away=0.40,
        )
        assert result["spread"]["home_spread_ml"] is None
        assert result["spread"]["away_spread_ml"] is None
        assert result["spread"]["status"] == "unavailable"
        assert result["spread"]["pick"] is None
        assert result["spread"]["ev_dollars"] is None
        assert result["totals"]["over_ml"] is None
        assert result["totals"]["under_ml"] is None
        assert result["totals"]["status"] == "unavailable"

    def test_analytics_spread_abstains_on_explicit_corrupt_odds_without_synthetic_minus_110(self):
        """Codex Point 3: Momios de spread explícitos inválidos no deben reemplazarse por -110 ni generar EV/Kelly."""
        game_dt = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
        # Caso 1: Precio de spread ausente (None) -> usa fallback estándar -110 para pricing
        res_absent = generate_nfl_matchup_analytics(
            home_team="KC",
            away_team="DEN",
            game_date=game_dt,
            home_ml=-200,
            away_ml=170,
            spread_line=-3.5,
            home_spread_ml=None,
            away_spread_ml=None,
            model_prob_home=0.65,
            model_prob_away=0.35,
        )
        assert res_absent["spread"]["status"] == "available"
        assert res_absent["spread"]["ev_dollars"] is not None

        # Caso 2: Precio de spread explícitamente corrupto (50) -> abstención total, no -110
        res_corrupt = generate_nfl_matchup_analytics(
            home_team="KC",
            away_team="DEN",
            game_date=game_dt,
            home_ml=-200,
            away_ml=170,
            spread_line=-3.5,
            home_spread_ml=50,  # Inválido explícito
            away_spread_ml=-110,
            model_prob_home=0.65,
            model_prob_away=0.35,
        )
        assert res_corrupt["spread"]["status"] == "unavailable"
        assert res_corrupt["spread"]["pick"] is None
        assert res_corrupt["spread"]["ev_dollars"] is None
        assert res_corrupt["spread"]["tier"] == "UNAVAILABLE"
        assert res_corrupt["spread"]["has_kelly_edge"] is False
        assert res_corrupt["spread"]["home_spread_ml"] is None
        if res_corrupt["best_bet"]:
            assert res_corrupt["best_bet"]["market"] != "spread"

    def test_analytics_rejects_negative_or_nan_total(self):
        """Negative total line or NaN total line must be sanitized to unavailable."""
        game_dt = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Denver Broncos",
            game_date=game_dt,
            home_ml=-150,
            away_ml=130,
            total_line=-40.0,
        )
        assert result["totals"]["status"] == "unavailable"
        assert result["totals"]["market_line"] is None

    def test_analytics_rejects_nan_spread(self):
        game_dt = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
        result = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Denver Broncos",
            game_date=game_dt,
            home_ml=-150,
            away_ml=130,
            spread_line=float("nan"),
        )
        assert result["spread"]["status"] == "unavailable"
        assert result["spread"]["market_line"] is None


# ============================================================================
# 5. Parlay Service Hardening
# ============================================================================

class TestParlayServicePointPreservation:
    def test_evaluate_custom_parlay_preserves_point_attribute(self):
        res = evaluate_custom_parlay(
            legs=[
                {
                    "team_name": "Kansas City Chiefs",
                    "matchup": "DEN @ KC",
                    "market": "spreads",
                    "side": "home",
                    "point": -3.5,
                    "prob_model": 0.58,
                    "odds_american": -105,
                },
                {
                    "team_name": "Detroit Lions",
                    "matchup": "GB @ DET",
                    "market": "h2h",
                    "side": "home",
                    "prob_model": 0.62,
                    "odds_american": -140,
                },
            ]
        )
        evaluated_legs = res["legs_evaluadas"]
        assert len(evaluated_legs) == 2
        spread_leg = evaluated_legs[0]
        assert spread_leg["point"] == -3.5

    def test_evaluate_custom_parlay_fails_closed_on_zero_or_out_of_range_odds(self):
        res_zero = evaluate_custom_parlay(
            legs=[
                {
                    "team_name": "Kansas City Chiefs",
                    "prob_model": 0.70,
                    "odds_american": 0,  # Zero odds
                },
                {
                    "team_name": "Detroit Lions",
                    "prob_model": 0.65,
                    "odds_american": -110,
                },
            ]
        )
        assert res_zero["viable"] is False
        assert res_zero["has_sufficient_model_data"] is False
        assert res_zero["ev_dolares"] is None
        assert res_zero["legs_evaluadas"][0]["odds_american"] is None

        res_oor = evaluate_custom_parlay(
            legs=[
                {
                    "team_name": "Kansas City Chiefs",
                    "prob_model": 0.70,
                    "odds_american": 50,  # Out of range (-100..100)
                },
                {
                    "team_name": "Detroit Lions",
                    "prob_model": 0.65,
                    "odds_american": -110,
                },
            ]
        )
        assert res_oor["viable"] is False
        assert res_oor["has_sufficient_model_data"] is False
        assert res_oor["ev_dolares"] is None
        assert res_oor["legs_evaluadas"][0]["odds_american"] is None


# ============================================================================
# 6. Fetch Odds From DB Hardening
# ============================================================================

from unittest.mock import AsyncMock, MagicMock
from app.services.odds_service import fetch_odds_from_db

class TestFetchOddsFromDbHardening:
    @pytest.mark.asyncio
    async def test_fetch_odds_from_db_discards_corrupted_spreads_and_totals(self):
        mock_game = MagicMock()
        mock_game.external_id = "test_g1"
        mock_game.game_date = datetime(2026, 12, 1, 18, 0, tzinfo=timezone.utc)
        mock_game.home_team.name = "Kansas City Chiefs"
        mock_game.away_team.name = "Denver Broncos"

        now = datetime.now(timezone.utc)

        # Snapshot con spread asimétrico (-3.5 / -3.5)
        snap_bad_spread = MagicMock()
        snap_bad_spread.market = "spreads"
        snap_bad_spread.home_spread = -3.5
        snap_bad_spread.away_spread = -3.5  # Asimétrico!
        snap_bad_spread.home_spread_ml = -110
        snap_bad_spread.away_spread_ml = -110
        snap_bad_spread.total_line = None
        snap_bad_spread.home_ml = None
        snap_bad_spread.away_ml = None
        snap_bad_spread.bookmaker = "draftkings"
        snap_bad_spread.observed_at = now - timedelta(hours=1)
        snap_bad_spread.captured_at = now - timedelta(hours=1)

        # Snapshot con total negativo (-45.0)
        snap_bad_total = MagicMock()
        snap_bad_total.market = "totals"
        snap_bad_total.total_line = -45.0  # Negativo!
        snap_bad_total.over_ml = -110
        snap_bad_total.under_ml = -110
        snap_bad_total.home_spread = None
        snap_bad_total.away_spread = None
        snap_bad_total.home_ml = None
        snap_bad_total.away_ml = None
        snap_bad_total.bookmaker = "draftkings"
        snap_bad_total.observed_at = now - timedelta(hours=1)
        snap_bad_total.captured_at = now - timedelta(hours=1)

        # Snapshot con ML fuera de rango (50)
        snap_bad_h2h = MagicMock()
        snap_bad_h2h.market = "h2h"
        snap_bad_h2h.home_ml = 50  # Inválido!
        snap_bad_h2h.away_ml = -50
        snap_bad_h2h.home_implied_prob = 0.5
        snap_bad_h2h.away_implied_prob = 0.5
        snap_bad_h2h.home_spread = None
        snap_bad_h2h.away_spread = None
        snap_bad_h2h.total_line = None
        snap_bad_h2h.bookmaker = "draftkings"
        snap_bad_h2h.observed_at = now - timedelta(hours=1)
        snap_bad_h2h.captured_at = now - timedelta(hours=1)

        mock_game.odds_snapshots = [snap_bad_spread, snap_bad_total, snap_bad_h2h]

        mock_session = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = [mock_game]
        mock_session.execute.return_value = mock_res

        data = await fetch_odds_from_db(sport="NFL", session=mock_session)
        assert len(data) == 1
        item = data[0]
        # Spread asimétrico debe descartarse fail-closed
        assert item["home_spread"] is None
        assert item["away_spread"] is None
        # Total <= 0 debe descartarse fail-closed
        assert item["total_line"] is None
        # ML fuera de rango debe descartarse fail-closed
        assert item["home_ml"] is None
        assert item["away_ml"] is None
        assert item["is_stale"] is True


# ============================================================================
# 7. Sync Real Games Hardening
# ============================================================================

from app.services.live_ingestion_service import sync_real_games
from app.models.game import Team, OddsSnapshot

class TestSyncRealGamesHardening:
    @pytest.mark.asyncio
    async def test_sync_real_games_rejects_corrupted_odds_across_markets(self, monkeypatch):
        """Codex Point 1: sync_real_games no debe persistir cuotas no finitas, cero o en (-100, 100)."""
        now = datetime.now(timezone.utc)
        commence_str = (now + timedelta(days=2)).isoformat()
        observed_str = (now - timedelta(hours=1)).isoformat()

        # Odds item con ML corrupto (50), spread con momio 0, y total con momio -50
        mock_odds = [
            {
                "external_id": "game_test_corrupt",
                "home_team": "Kansas City Chiefs",
                "away_team": "Denver Broncos",
                "commence_time": commence_str,
                "best_bookmaker": "draftkings",
                "spread_bookmaker": "draftkings",
                "total_bookmaker": "draftkings",
                "home_ml": 50,       # Inválido en (-100, 100)
                "away_ml": -150,
                "ml_last_update": observed_str,
                "home_spread": -3.5,
                "away_spread": 3.5,
                "home_spread_ml": 0,  # Inválido (cero)
                "away_spread_ml": -110,
                "spread_last_update": observed_str,
                "total_line": 45.5,
                "over_ml": -50,      # Inválido en (-100, 100)
                "under_ml": -110,
                "totals_last_update": observed_str,
            }
        ]

        async def mock_fetch_odds(sport, **kwargs):
            return mock_odds

        monkeypatch.setattr("app.services.live_ingestion_service.fetch_odds", mock_fetch_odds)
        monkeypatch.setattr("app.services.espn_service.sync_espn_scores", AsyncMock(return_value={"updated": 0}))

        mock_team_h = MagicMock(spec=Team)
        mock_team_h.id = 1
        mock_team_h.name = "Kansas City Chiefs"
        mock_team_h.abbreviation = "KC"
        mock_team_h.sport = "NFL"

        mock_team_a = MagicMock(spec=Team)
        mock_team_a.id = 2
        mock_team_a.name = "Denver Broncos"
        mock_team_a.abbreviation = "DEN"
        mock_team_a.sport = "NFL"

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        async def mock_execute(stmt, *args, **kwargs):
            res = MagicMock()
            str_stmt = str(stmt).lower()
            if "teams" in str_stmt:
                res.scalars.return_value.all.return_value = [mock_team_h, mock_team_a]
            else:
                res.scalars.return_value.all.return_value = []
            res.scalar_one_or_none.return_value = None
            res.rowcount = 1
            return res

        mock_db.execute.side_effect = mock_execute

        res = await sync_real_games(sport="NFL", db=mock_db)
        assert res.get("status") != "error"

        # Ningún snapshot corrupto (h2h con 50, spread con 0, total con -50) debe persistirse
        # Si la consulta usa add_snapshot_idempotent (pg_insert), track execute calls:
        # Los execute de pg_insert para OddsSnapshot no deben haberse llamado
        for call_args in mock_db.execute.call_args_list:
            stmt = call_args[0][0]
            # Si es un insert en odds_snapshots, verificar que no fue ejecutado
            if hasattr(stmt, "table") and getattr(stmt.table, "name", "") == "odds_snapshots":
                pytest.fail("No debe ejecutarse INSERT de OddsSnapshot para cuotas con momios corruptos")


# ============================================================================
# 8. De-vig Coherente (Benchmark de Casa Única vs Momios Comprables por Lado)
# ============================================================================

class TestDevigBenchmarkCoherence:
    def test_parse_odds_response_devig_benchmark_does_not_use_mixed_pair(self):
        """
        Codex Point 2: Las probabilidades implícitas no-vig (benchmark) deben
        calcularse desde una pareja H2H coherente de una misma casa, NO normalizando
        el par sintético mezclado de dos casas distintas.
        """
        mock_raw = [
            {
                "id": "game_devig_test",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "Chiefs",
                "away_team": "Broncos",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Chiefs", "price": -105},  # Mejor home ML
                                    {"name": "Broncos", "price": -115}, # Peor away ML
                                ],
                            }
                        ],
                    },
                    {
                        "key": "fanduel",
                        "title": "FanDuel",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Chiefs", "price": -120}, # Peor home ML
                                    {"name": "Broncos", "price": 105},  # Mejor away ML
                                ],
                            }
                        ],
                    },
                ],
            }
        ]

        parsed = _parse_odds_response(mock_raw)
        assert len(parsed) == 1
        item = parsed[0]

        # 1. Momios comprables óptimos por lado (Line Shopping para EV)
        assert item["home_ml"] == -105
        assert item["away_ml"] == 105
        assert item["ml_home_bookmaker"] == "draftkings"
        assert item["ml_away_bookmaker"] == "fanduel"
        assert item["best_bookmaker"] == "draftkings/fanduel"

        # 2. Benchmark de-vig coherente:
        # DraftKings es la casa de mayor rango (priority_idx 0 vs 1).
        # Su pareja coherente es (-105, -115).
        # Implied raw: 105/205 = 0.512195, 115/215 = 0.534884. Sum = 1.047079.
        # De-vigged: 0.512195 / 1.047079 = 0.489166 -> 0.4892.
        assert item["h2h_benchmark_bookmaker"] == "draftkings"
        assert item["home_implied_prob"] == 0.4892
        assert item["away_implied_prob"] == 0.5108

        # CRÍTICO: Si se hubiera usado la pareja mezclada sintética (-105, +105):
        # 105/205 = 0.5122, 100/205 = 0.4878 (sum = 1.0000).
        # Comprobar taxativamente que el benchmark NO usa esa pareja mezclada:
        assert item["home_implied_prob"] != 0.5122
        assert item["away_implied_prob"] != 0.4878

    def test_parse_odds_response_devig_benchmark_tie_breaker_by_vig_and_timestamp(self):
        """Desempate cuando dos casas de igual prioridad o casas alternativas se evalúan."""
        mock_raw = [
            {
                "id": "game_tie_test",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-11-01T18:00:00Z",
                "home_team": "Bills",
                "away_team": "Dolphins",
                "bookmakers": [
                    {
                        "key": "betmgm",  # priority_idx = 2
                        "title": "BetMGM",
                        "last_update": "2026-10-26T11:00:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Bills", "price": -110},
                                    {"name": "Dolphins", "price": -110},
                                ],
                            }
                        ],
                    },
                    {
                        "key": "caesars",  # priority_idx = 3
                        "title": "Caesars",
                        "last_update": "2026-10-26T12:00:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Bills", "price": -105},
                                    {"name": "Dolphins", "price": -105},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        parsed = _parse_odds_response(mock_raw)
        assert len(parsed) == 1
        item = parsed[0]
        # BetMGM tiene mayor prioridad en PRIORITY_BOOKMAKERS que Caesars (idx 2 vs 3)
        assert item["h2h_benchmark_bookmaker"] == "betmgm"
        assert item["home_implied_prob"] == 0.5
        assert item["away_implied_prob"] == 0.5


# ============================================================================
# 9. Integración: Persistencia y Lectura de Procedencia por Lado de Dos Casas
# ============================================================================

class TestSideProvenancePersistenceAndDbFallbackIntegration:
    @pytest.mark.asyncio
    async def test_sync_real_games_persists_two_bookmakers_payload(self, monkeypatch):
        """
        Codex Point 1: sync_real_games debe persistir cuotas de lados procedentes
        de casas distintas con home_bookmaker y away_bookmaker exactos, evitando
        atribuir falsamente el lado visitante/under a la casa local.
        """
        now = datetime.now(timezone.utc)
        commence_str = (now + timedelta(days=2)).isoformat()
        observed_str = (now - timedelta(hours=1)).isoformat()
        benchmark_str = (now - timedelta(hours=1, minutes=15)).isoformat()

        # Payload con dos casas diferentes por lado y procedencia de benchmark
        mock_odds = [
            {
                "external_id": "game_two_books",
                "home_team": "Kansas City Chiefs",
                "away_team": "Denver Broncos",
                "commence_time": commence_str,
                "best_bookmaker": "draftkings/fanduel",
                "ml_home_bookmaker": "draftkings",
                "ml_away_bookmaker": "fanduel",
                "h2h_benchmark_bookmaker": "draftkings",
                "benchmark_bookmaker": "draftkings",
                "h2h_benchmark_last_update": benchmark_str,
                "benchmark_last_update": benchmark_str,
                "spread_bookmaker": "draftkings/fanduel",
                "spread_home_bookmaker": "draftkings",
                "spread_away_bookmaker": "fanduel",
                "total_bookmaker": "draftkings/fanduel",
                "total_over_bookmaker": "draftkings",
                "total_under_bookmaker": "fanduel",
                "home_ml": -105,
                "away_ml": 105,
                "home_implied_prob": 0.4892,
                "away_implied_prob": 0.5108,
                "ml_last_update": observed_str,
                "home_spread": -3.5,
                "away_spread": 3.5,
                "home_spread_ml": -105,
                "away_spread_ml": -105,
                "spread_last_update": observed_str,
                "total_line": 47.5,
                "over_ml": -105,
                "under_ml": -105,
                "totals_last_update": observed_str,
            }
        ]

        async def mock_fetch_odds(sport, **kwargs):
            return mock_odds

        monkeypatch.setattr("app.services.live_ingestion_service.fetch_odds", mock_fetch_odds)
        monkeypatch.setattr("app.services.espn_service.sync_espn_scores", AsyncMock(return_value={"updated": 0}))

        mock_team_h = MagicMock(spec=Team)
        mock_team_h.id = 1
        mock_team_h.name = "Kansas City Chiefs"
        mock_team_h.abbreviation = "KC"
        mock_team_h.sport = "NFL"

        mock_team_a = MagicMock(spec=Team)
        mock_team_a.id = 2
        mock_team_a.name = "Denver Broncos"
        mock_team_a.abbreviation = "DEN"
        mock_team_a.sport = "NFL"

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        saved_inserts = []

        async def mock_execute(stmt, *args, **kwargs):
            res = MagicMock()
            str_stmt = str(stmt).lower()
            if "teams" in str_stmt:
                res.scalars.return_value.all.return_value = [mock_team_h, mock_team_a]
            else:
                res.scalars.return_value.all.return_value = []
            res.scalar_one_or_none.return_value = None
            res.rowcount = 1

            # Rastrear inserciones en odds_snapshots
            if hasattr(stmt, "table") and getattr(stmt.table, "name", "") == "odds_snapshots":
                val_params = {
                    getattr(col, "name", str(col)): getattr(val, "value", val)
                    for col, val in getattr(stmt, "_values", {}).items()
                }
                saved_inserts.append(val_params)

            return res

        mock_db.execute.side_effect = mock_execute

        res = await sync_real_games(sport="NFL", db=mock_db)
        assert res.get("status") != "error"
        assert len(saved_inserts) == 3  # H2H, Spread, Total

        # Verificar procedencia por lado en inserciones
        markets_seen = {}
        for ins in saved_inserts:
            mkt = ins.get("market")
            markets_seen[mkt] = ins

        # H2H
        h2h_ins = markets_seen["h2h"]
        assert h2h_ins["bookmaker"] == "draftkings/fanduel"
        assert h2h_ins["home_bookmaker"] == "draftkings"
        assert h2h_ins["away_bookmaker"] == "fanduel"
        assert h2h_ins["h2h_benchmark_bookmaker"] == "draftkings"
        assert h2h_ins["benchmark_bookmaker"] == "draftkings"
        assert h2h_ins["benchmark_observed_at"] is not None

        # Spreads
        spread_ins = markets_seen["spreads"]
        assert spread_ins["bookmaker"] == "draftkings/fanduel"
        assert spread_ins["home_bookmaker"] == "draftkings"
        assert spread_ins["away_bookmaker"] == "fanduel"

        # Totals
        total_ins = markets_seen["totals"]
        assert total_ins["bookmaker"] == "draftkings/fanduel"
        assert total_ins["over_bookmaker"] == "draftkings"
        assert total_ins["under_bookmaker"] == "fanduel"

    @pytest.mark.asyncio
    async def test_fetch_odds_from_db_returns_truthful_side_provenance(self):
        """
        Codex Point 1: fetch_odds_from_db debe retornar la procedencia por lado
        sin atribuir falsamente el lado visitante a la casa del lado local.
        """
        mock_game = MagicMock()
        mock_game.external_id = "test_game_multi_book"
        mock_game.game_date = datetime(2026, 12, 1, 18, 0, tzinfo=timezone.utc)
        mock_game.home_team.name = "Kansas City Chiefs"
        mock_game.away_team.name = "Denver Broncos"

        now = datetime.now(timezone.utc)

        # Snapshot H2H con procedencia por lado y benchmark
        snap_h2h = MagicMock()
        snap_h2h.market = "h2h"
        snap_h2h.bookmaker = "draftkings/fanduel"
        snap_h2h.home_bookmaker = "draftkings"
        snap_h2h.away_bookmaker = "fanduel"
        snap_h2h.h2h_benchmark_bookmaker = "draftkings"
        snap_h2h.benchmark_bookmaker = "draftkings"
        snap_h2h.home_ml = -105
        snap_h2h.away_ml = 105
        snap_h2h.home_implied_prob = 0.4892
        snap_h2h.away_implied_prob = 0.5108
        snap_h2h.home_spread = None
        snap_h2h.away_spread = None
        snap_h2h.total_line = None
        snap_h2h.observed_at = now - timedelta(hours=1)
        snap_h2h.captured_at = now - timedelta(hours=1)
        snap_h2h.benchmark_observed_at = now - timedelta(hours=1, minutes=15)

        # Snapshot Spread con procedencia por lado
        snap_spread = MagicMock()
        snap_spread.market = "spreads"
        snap_spread.bookmaker = "draftkings/fanduel"
        snap_spread.home_bookmaker = "draftkings"
        snap_spread.away_bookmaker = "fanduel"
        snap_spread.home_spread = -3.5
        snap_spread.away_spread = 3.5
        snap_spread.home_spread_ml = -105
        snap_spread.away_spread_ml = -105
        snap_spread.home_ml = None
        snap_spread.away_ml = None
        snap_spread.total_line = None
        snap_spread.observed_at = now - timedelta(hours=1)
        snap_spread.captured_at = now - timedelta(hours=1)

        # Snapshot Totales con procedencia por lado
        snap_total = MagicMock()
        snap_total.market = "totals"
        snap_total.bookmaker = "draftkings/fanduel"
        snap_total.over_bookmaker = "draftkings"
        snap_total.under_bookmaker = "fanduel"
        snap_total.home_bookmaker = "draftkings"
        snap_total.away_bookmaker = "fanduel"
        snap_total.total_line = 47.5
        snap_total.over_ml = -105
        snap_total.under_ml = -105
        snap_total.home_ml = None
        snap_total.away_ml = None
        snap_total.home_spread = None
        snap_total.away_spread = None
        snap_total.observed_at = now - timedelta(hours=1)
        snap_total.captured_at = now - timedelta(hours=1)

        mock_game.odds_snapshots = [snap_h2h, snap_spread, snap_total]

        mock_session = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = [mock_game]
        mock_session.execute.return_value = mock_res

        data = await fetch_odds_from_db(sport="NFL", session=mock_session)
        assert len(data) == 1
        item = data[0]

        # Comprobar atribución truthful en el fallback
        assert item["spread_home_bookmaker"] == "draftkings"
        assert item["spread_away_bookmaker"] == "fanduel"
        assert item["spread_bookmaker"] == "draftkings/fanduel"
        assert item["spread_bookmaker"] != "draftkings"

        assert item["ml_home_bookmaker"] == "draftkings"
        assert item["ml_away_bookmaker"] == "fanduel"
        assert item["h2h_benchmark_bookmaker"] == "draftkings"
        assert item["benchmark_bookmaker"] == "draftkings"
        assert item["benchmark_observed_at"] is not None
        assert item["best_bookmaker"] == "draftkings/fanduel"

        assert item["total_over_bookmaker"] == "draftkings"
        assert item["total_under_bookmaker"] == "fanduel"
        assert item["total_bookmaker"] == "draftkings/fanduel"

        # Markets provenance
        prov = item["markets_provenance"]
        assert prov["spread"]["home_bookmaker"] == "draftkings"
        assert prov["spread"]["away_bookmaker"] == "fanduel"
        assert prov["spread"]["bookmaker"] == "draftkings/fanduel"

        assert prov["moneyline"]["home_bookmaker"] == "draftkings"
        assert prov["moneyline"]["away_bookmaker"] == "fanduel"
        assert prov["moneyline"]["h2h_benchmark_bookmaker"] == "draftkings"
        assert prov["moneyline"]["benchmark_bookmaker"] == "draftkings"

        assert prov["total"]["over_bookmaker"] == "draftkings"
        assert prov["total"]["under_bookmaker"] == "fanduel"

    def test_analytics_and_predictions_truthful_attribution(self):
        """Verifica que el análisis del enfrentamiento asigna bookmaker fiel por mercado y selección."""
        game_dt = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
        res = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Denver Broncos",
            game_date=game_dt,
            home_ml=-150,
            away_ml=130,
            spread_line=-3.5,
            total_line=47.5,
            home_spread_ml=-105,
            away_spread_ml=-105,
            over_ml=-105,
            under_ml=-105,
            bookmaker="draftkings",
            home_bookmaker="draftkings",
            away_bookmaker="fanduel",
            h2h_benchmark_bookmaker="draftkings",
            benchmark_bookmaker="draftkings",
            benchmark_observed_at=game_dt - timedelta(hours=3),
            spread_home_bookmaker="draftkings",
            spread_away_bookmaker="fanduel",
            total_over_bookmaker="draftkings",
            total_under_bookmaker="fanduel",
            market_prob_home_no_vig=0.58,
            market_prob_away_no_vig=0.42,
            model_prob_home=0.65,
            model_prob_away=0.35,
        )

        assert res["moneyline"]["home_bookmaker"] == "draftkings"
        assert res["moneyline"]["away_bookmaker"] == "fanduel"
        assert res["moneyline"]["h2h_benchmark_bookmaker"] == "draftkings"
        assert res["h2h_benchmark_bookmaker"] == "draftkings"
        assert res["spread"]["home_bookmaker"] == "draftkings"
        assert res["spread"]["away_bookmaker"] == "fanduel"
        assert res["totals"]["over_bookmaker"] == "draftkings"
        assert res["totals"]["under_bookmaker"] == "fanduel"

    @pytest.mark.asyncio
    async def test_e2e_side_provenance_and_benchmark_db_persistence_and_api_contract(self, monkeypatch):
        """
        Codex Point 1 & 2: Prueba de integración de extremo a extremo que:
        1. Inserta cuotas reales con procedencia por lado y procedencia del benchmark
           a través de sync_real_games en PostgreSQL.
        2. Verifica directamente en DB que se persistan las 4 columnas de lado más benchmark_bookmaker
           y benchmark_observed_at.
        3. Recupera ese MISMO registro mediante fetch_odds_from_db y valida que se expongan fielmente
           las 6 procedencias por lado más el benchmark no-vig y su timestamp.
        4. Inserta una predicción con modelo aprobado y consume /api/v1/predictions/today y /api/v1/odds/nfl,
           validando el contrato serializado de la API con atribución fiel de cada mercado y lado.
        """
        now = datetime.now(timezone.utc)
        test_ext_id = f"test_e2e_two_books_{int(now.timestamp())}"
        commence_dt = now + timedelta(days=2)
        commence_str = commence_dt.isoformat()
        observed_dt = now - timedelta(hours=1)
        observed_str = observed_dt.isoformat()
        benchmark_dt = now - timedelta(hours=1, minutes=15)
        benchmark_str = benchmark_dt.isoformat()

        mock_odds = [
            {
                "external_id": test_ext_id,
                "home_team": "Kansas City Chiefs",
                "away_team": "Denver Broncos",
                "commence_time": commence_str,
                "best_bookmaker": "draftkings/fanduel",
                "ml_home_bookmaker": "draftkings",
                "ml_away_bookmaker": "fanduel",
                "h2h_benchmark_bookmaker": "draftkings",
                "benchmark_bookmaker": "draftkings",
                "h2h_benchmark_last_update": benchmark_str,
                "benchmark_last_update": benchmark_str,
                "spread_bookmaker": "draftkings/fanduel",
                "spread_home_bookmaker": "draftkings",
                "spread_away_bookmaker": "fanduel",
                "total_bookmaker": "draftkings/fanduel",
                "total_over_bookmaker": "draftkings",
                "total_under_bookmaker": "fanduel",
                "home_ml": -105,
                "away_ml": 105,
                "home_implied_prob": 0.4892,
                "away_implied_prob": 0.5108,
                "ml_last_update": observed_str,
                "home_spread": -3.5,
                "away_spread": 3.5,
                "home_spread_ml": -105,
                "away_spread_ml": -105,
                "spread_last_update": observed_str,
                "total_line": 47.5,
                "over_ml": -105,
                "under_ml": -105,
                "totals_last_update": observed_str,
            }
        ]

        async def mock_fetch_odds(sport, **kwargs):
            return mock_odds

        monkeypatch.setattr("app.services.live_ingestion_service.fetch_odds", mock_fetch_odds)
        monkeypatch.setattr("app.services.espn_service.sync_espn_scores", AsyncMock(return_value={"updated": 0}))

        created_game_id = None
        try:
            # 1. Ejecutar sync_real_games contra la base de datos real
            async with AsyncSessionLocal() as session:
                sync_result = await sync_real_games(sport="NFL", db=session)
                assert sync_result.get("status") in ("success", "partial"), f"sync failed: {sync_result}"
                assert sync_result.get("games_synced", 0) >= 1

                # Localizar el Game insertado
                g_stmt = select(Game).where(Game.external_id == test_ext_id)
                g_res = await session.execute(g_stmt)
                created_game = g_res.scalar_one_or_none()
                assert created_game is not None, "El juego no fue creado en PostgreSQL"
                created_game_id = created_game.id

                # 2. Verificar directamente en la tabla odds_snapshots de PostgreSQL
                snaps_stmt = select(OddsSnapshot).where(OddsSnapshot.game_id == created_game_id)
                snaps_res = await session.execute(snaps_stmt)
                db_snaps = snaps_res.scalars().all()
                assert len(db_snaps) == 3, f"Se esperaban 3 snapshots persistidos, encontrados {len(db_snaps)}"

                snaps_by_mkt = {s.market: s for s in db_snaps}

                # H2H / Moneyline
                h2h_snap = snaps_by_mkt["h2h"]
                assert h2h_snap.bookmaker == "draftkings/fanduel"
                assert h2h_snap.home_bookmaker == "draftkings"
                assert h2h_snap.away_bookmaker == "fanduel"
                assert h2h_snap.h2h_benchmark_bookmaker == "draftkings"
                assert h2h_snap.benchmark_bookmaker == "draftkings"
                assert h2h_snap.benchmark_observed_at is not None
                assert h2h_snap.home_ml == -105
                assert h2h_snap.away_ml == 105
                assert pytest.approx(h2h_snap.home_implied_prob, 0.001) == 0.4892
                assert pytest.approx(h2h_snap.away_implied_prob, 0.001) == 0.5108

                # Spreads
                spr_snap = snaps_by_mkt["spreads"]
                assert spr_snap.bookmaker == "draftkings/fanduel"
                assert spr_snap.home_bookmaker == "draftkings"
                assert spr_snap.away_bookmaker == "fanduel"
                assert spr_snap.home_spread == -3.5
                assert spr_snap.away_spread == 3.5

                # Totals
                tot_snap = snaps_by_mkt["totals"]
                assert tot_snap.bookmaker == "draftkings/fanduel"
                assert tot_snap.over_bookmaker == "draftkings"
                assert tot_snap.under_bookmaker == "fanduel"
                assert tot_snap.total_line == 47.5

                # 3. Recuperar el MISMO registro a través de fetch_odds_from_db
                fallback_data = await fetch_odds_from_db(sport="NFL", session=session)
                game_odds = next((o for o in fallback_data if o["external_id"] == test_ext_id), None)
                assert game_odds is not None, "El juego no fue recuperado por fetch_odds_from_db"

                # Validar las 6 procedencias por lado en el fallback
                assert game_odds["ml_home_bookmaker"] == "draftkings"
                assert game_odds["ml_away_bookmaker"] == "fanduel"
                assert game_odds["spread_home_bookmaker"] == "draftkings"
                assert game_odds["spread_away_bookmaker"] == "fanduel"
                assert game_odds["total_over_bookmaker"] == "draftkings"
                assert game_odds["total_under_bookmaker"] == "fanduel"

                # Validar procedencia del benchmark no-vig
                assert game_odds["h2h_benchmark_bookmaker"] == "draftkings"
                assert game_odds["benchmark_bookmaker"] == "draftkings"
                assert game_odds["benchmark_observed_at"] is not None

                # Validar composite bookmaker tags
                assert game_odds["best_bookmaker"] == "draftkings/fanduel"
                assert game_odds["spread_bookmaker"] == "draftkings/fanduel"
                assert game_odds["total_bookmaker"] == "draftkings/fanduel"

                # Validar markets_provenance anidado
                prov = game_odds["markets_provenance"]
                assert prov["moneyline"]["home_bookmaker"] == "draftkings"
                assert prov["moneyline"]["away_bookmaker"] == "fanduel"
                assert prov["moneyline"]["h2h_benchmark_bookmaker"] == "draftkings"
                assert prov["moneyline"]["benchmark_bookmaker"] == "draftkings"
                assert prov["spread"]["home_bookmaker"] == "draftkings"
                assert prov["spread"]["away_bookmaker"] == "fanduel"
                assert prov["total"]["over_bookmaker"] == "draftkings"
                assert prov["total"]["under_bookmaker"] == "fanduel"

                # 4. Insertar una Prediction para este juego y verificar contrato HTTP de API
                pred = Prediction(
                    game_id=created_game_id,
                    model_version="nfl_temporal_v1",
                    predicted_at=now - timedelta(hours=2),
                    as_of=now - timedelta(hours=2),
                    dataset_sha256="test_dataset_sha256",
                    artifact_sha256="test_artifact_sha256",
                    inference_input_sha256=f"test_inf_input_{int(now.timestamp())}",
                    home_win_prob=0.65,
                    away_win_prob=0.35,
                    best_bookmaker="draftkings/fanduel",
                    best_home_ml=-105,
                    best_away_ml=105,
                    home_ev=5.2,
                    away_ev=-15.0,
                    home_has_edge=True,
                    away_has_edge=False,
                    confluence_tier="TIER_1",
                    model_agreement=3,
                )
                session.add(pred)
                await session.commit()

            # 5. Realizar petición HTTP contra la API serializada usando httpx
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                with patch("app.api.v1.predictions.get_approved_model_version", return_value="nfl_temporal_v1"), \
                     patch("app.api.v1.predictions._production_model_enabled", return_value=True):
                    pred_resp = await client.get("/api/v1/predictions/today?sport=NFL&period=all")
                    assert pred_resp.status_code == 200, f"Error en predictions API: {pred_resp.text}"
                    pred_data = pred_resp.json()
                    assert pred_data["predictions_enabled"] is True

                    pick = next((p for p in pred_data["picks"] if p.get("canonical_external_id") == test_ext_id), None)
                    assert pick is not None, "El pick para el partido integrado no fue encontrado en /predictions/today"

                    # Verificar contrato serializado de predicciones:
                    # Las 6 procedencias por lado exactas
                    assert pick["ml_home_bookmaker"] == "draftkings"
                    assert pick["ml_away_bookmaker"] == "fanduel"
                    assert pick["spread_home_bookmaker"] == "draftkings"
                    assert pick["spread_away_bookmaker"] == "fanduel"
                    assert pick["total_over_bookmaker"] == "draftkings"
                    assert pick["total_under_bookmaker"] == "fanduel"

                    # Procedencia del benchmark no-vig
                    assert pick["h2h_benchmark_bookmaker"] == "draftkings"
                    assert pick["benchmark_bookmaker"] == "draftkings"
                    assert pick["benchmark_observed_at"] is not None

                    # Atribución por selección específica: KC es local y pick_side="home"
                    assert pick["bookmaker"] == "draftkings"
                    assert pick["pick_side"] == "home"

                    # Atribución en bloques de matchup analytics
                    assert pick["moneyline"]["home_bookmaker"] == "draftkings"
                    assert pick["moneyline"]["away_bookmaker"] == "fanduel"
                    assert pick["moneyline"]["h2h_benchmark_bookmaker"] == "draftkings"
                    assert pick["spread"]["home_bookmaker"] == "draftkings"
                    assert pick["spread"]["away_bookmaker"] == "fanduel"
                    assert pick["totals"]["over_bookmaker"] == "draftkings"
                    assert pick["totals"]["under_bookmaker"] == "fanduel"

                # 6. Validar también contrato HTTP de /api/v1/odds/nfl
                odds_resp = await client.get("/api/v1/odds/nfl")
                assert odds_resp.status_code == 200, f"Error en odds API: {odds_resp.text}"
                odds_json = odds_resp.json()
                api_game_odds = next((g for g in odds_json.get("games", []) if g["external_id"] == test_ext_id), None)
                assert api_game_odds is not None, "El juego no fue expuesto en /api/v1/odds/nfl"
                assert api_game_odds["ml_home_bookmaker"] == "draftkings"
                assert api_game_odds["ml_away_bookmaker"] == "fanduel"
                assert api_game_odds["spread_home_bookmaker"] == "draftkings"
                assert api_game_odds["spread_away_bookmaker"] == "fanduel"
                assert api_game_odds["total_over_bookmaker"] == "draftkings"
                assert api_game_odds["total_under_bookmaker"] == "fanduel"
                assert api_game_odds["h2h_benchmark_bookmaker"] == "draftkings"
                assert api_game_odds["benchmark_bookmaker"] == "draftkings"

        finally:
            # Limpieza rigurosa de los registros de prueba en PostgreSQL
            if created_game_id is not None:
                async with AsyncSessionLocal() as session:
                    await session.execute(delete(Prediction).where(Prediction.game_id == created_game_id))
                    await session.execute(delete(OddsSnapshot).where(OddsSnapshot.game_id == created_game_id))
                    await session.execute(delete(ProviderGameCrosswalk).where(ProviderGameCrosswalk.game_id == created_game_id))
                    await session.execute(delete(Game).where(Game.id == created_game_id))
                    await session.commit()
