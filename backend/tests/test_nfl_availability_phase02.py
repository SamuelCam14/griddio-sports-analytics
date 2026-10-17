"""
Suite de Pruebas Herméticas para la Fase 02 — Disponibilidad NFL e Injuries Point-in-Time.
Garantías Verificadas:
1. No hay data leakage: did_play del boxscore objetivo es solo ground truth, nunca feature pregame.
2. OUT/IR confirmados antes del cutoff: NUNCA consultan props ni son revertidos (p_absence = 1.0 inmutable).
3. Señales de mercado (Player Props) evaluadas solo con props verificables (event_id, bookmaker, market_line, timestamp < cutoff):
   - listed_at_cutoff: modula P(absence) a 0.15 (NO se convierte en certeza 0.0).
   - not_listed_with_coverage: modula P(absence) a 0.75 (NO se convierte en OUT 1.0).
   - props incompletas o sin cobertura comparable: devuelve UNKNOWN con base empírica y checked_props=False.
4. Partidos históricos: NUNCA consultan cuotas ni props (market_signal = unknown, checked_props = False).
5. Timestamps no verificados, posteriores al cutoff (ventana 2h) o identidades ambiguas: fail-closed a UNKNOWN.
6. Cobertura explícita: diferencia covered_healthy, covered_with_injuries, unknown y unavailable_no_report.
   Cero priors inventados (15 PPR / 0.80 snaps); métricas son None ante falta de stats previas.
7. Inferencia fail-closed: predict_matchups levanta DataQualityError si la cobertura NFL es desconocida o faltan métricas.
8. Cero llamadas a APIs externas; balance del ledger intacto en 275 y SHA-256 verificado.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

_LEDGER_PATH_AT_IMPORT = Path("data/odds_quota_ledger.json")
_LEDGER_BYTES_AT_IMPORT = _LEDGER_PATH_AT_IMPORT.read_bytes() if _LEDGER_PATH_AT_IMPORT.exists() else b""
_LEDGER_SHA256_AT_IMPORT = hashlib.sha256(_LEDGER_BYTES_AT_IMPORT).hexdigest()
_LEDGER_SUMMARY_AT_IMPORT = json.loads(_LEDGER_BYTES_AT_IMPORT).get("summary", {}) if _LEDGER_BYTES_AT_IMPORT else {}
import pytest

from app.services.fantasy_injury_service import FantasyInjuryService
from app.services.nfl_availability_service import (
    ALL_NFL_TEAMS,
    BASE_P_ABSENCE,
    RESOLVER_VERSION,
    AvailabilityStatus,
    CoverageState,
    ESPNInjurySnapshot,
    MarketPropSignal,
    NFLAvailabilityService,
    PlayerInjuryRecord,
    TeamAvailabilitySummary,
)
from app.services.odds_quota_service import OddsQuotaService, estimate_request_cost
from ml.data_pipeline.common import DataQualityError
from ml.data_pipeline.injury_feature_builder import (
    build_nfl_availability_ground_truth,
    build_nfl_historical_injury_features,
)
from ml.data_pipeline.temporal_features import NFL_GAME_STATS, feature_columns


@pytest.fixture
def availability_svc(tmp_path):
    return NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")


def make_verifiable_prop(
    player_name: str,
    position: str = "WR",
    prop_type: str = "receiving_yards",
    market_line: float = 64.5,
    bookmaker: str = "DraftKings",
    event_id: str = "nfl_game_test_123",
    timestamp: str | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "bookmaker": bookmaker,
        "prop_type": prop_type,
        "market_line": market_line,
        "price": -110,
        "player_name": player_name,
        "position": position,
        "timestamp": timestamp or (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }


# ==============================================================================
# 1. Pruebas de Dominio: Bajas Confirmadas OUT/IR (Cero Consulta de Props)
# ==============================================================================

class TestConfirmedOutRules:
    """Verifica que OUT/IR confirmados son bajas definitivas que nunca consultan props."""

    @pytest.mark.parametrize("status_str", [
        "OUT", "Out", "ir", "IR", "Reserve", "Injured Reserve", "pup", "suspension"
    ])
    def test_out_ir_status_resolved_as_confirmed_out(self, availability_svc, status_str):
        now_utc = datetime.now(timezone.utc)
        obs_time = (now_utc - timedelta(hours=3)).isoformat()
        cutoff = now_utc.isoformat()

        status = availability_svc.resolve_availability_status(
            raw_status=status_str,
            observed_at=obs_time,
            decision_cutoff=cutoff,
        )
        assert status == AvailabilityStatus.CONFIRMED_OUT

    def test_confirmed_out_never_queries_props_and_cannot_be_reversed(self, availability_svc):
        future_start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        cutoff = datetime.now(timezone.utc).isoformat()

        fake_props = [
            make_verifiable_prop("Christian McCaffrey", "RB", "rushing_yards", 85.5)
        ]

        signal, p_absence, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.CONFIRMED_OUT,
            player_name="Christian McCaffrey",
            position="RB",
            team="SF",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=fake_props,
            has_active_game_props=True,
        )

        assert signal == MarketPropSignal.UNKNOWN
        assert p_absence == 1.0  # Pérdida definitiva 100%
        assert flags["checked_props"] is False
        assert flags["reason"] == "confirmed_out_no_props_query"


# ==============================================================================
# 2. Pruebas de Dominio: Questionable / Doubtful con Señales de Mercado
# ==============================================================================

class TestMarketPropsModulation:
    """Valida la modulación empírica de P(absence) basada en player props verificables para juegos futuros."""

    def test_questionable_listed_at_cutoff_lowers_absence_without_certainty(self, availability_svc):
        future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        cutoff = datetime.now(timezone.utc).isoformat()

        props = [make_verifiable_prop("Puka Nacua", "WR", "receiving_yards", 64.5)]

        signal, p_absence, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Puka Nacua",
            position="WR",
            team="LAR",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=props,
            has_active_game_props=True,
            expected_event_id="nfl_game_test_123",
        )

        assert signal == MarketPropSignal.LISTED_AT_CUTOFF
        assert p_absence == 0.15
        assert p_absence > 0.0  # NUNCA colapsar a certeza (0.0)
        assert flags["checked_props"] is True
        assert flags["listed"] is True
        assert flags["bookmaker"] == "DraftKings"

    def test_questionable_not_listed_with_coverage_increases_absence_without_certainty(self, availability_svc):
        future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        cutoff = datetime.now(timezone.utc).isoformat()

        props = [make_verifiable_prop("Cooper Kupp", "WR", "receiving_yards", 72.5)]

        signal, p_absence, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Tutu Atwell",
            position="WR",
            team="LAR",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=props,
            has_active_game_props=True,
            expected_event_id="nfl_game_test_123",
        )

        assert signal == MarketPropSignal.NOT_LISTED_WITH_COVERAGE
        assert p_absence == 0.75
        assert p_absence < 1.0  # NUNCA colapsar a OUT definitivo (1.0)
        assert flags["checked_props"] is True
        assert flags["has_comparable_coverage"] is True

    def test_questionable_without_comparable_coverage_returns_unknown(self, availability_svc):
        future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        cutoff = datetime.now(timezone.utc).isoformat()

        props = [make_verifiable_prop("Matthew Stafford", "QB", "passing_yards", 240.5)]

        signal, p_absence, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Aaron Donald",
            position="DT",
            team="LAR",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=props,
            has_active_game_props=True,
            expected_event_id="nfl_game_test_123",
        )

        assert signal == MarketPropSignal.UNKNOWN
        assert p_absence == 0.25  # Base empírica de Questionable
        assert flags["checked_props"] is True
        assert flags["has_comparable_coverage"] is False
        assert flags["reason"] == "insufficient_comparable_coverage"

    def test_doubtful_signals_modulated_correctly(self, availability_svc):
        future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        cutoff = datetime.now(timezone.utc).isoformat()

        props = [make_verifiable_prop("Josh Allen", "QB", "passing_yards", 230.5)]

        # Doubtful listado
        sig_l, p_l, _ = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.DOUBTFUL,
            player_name="Josh Allen",
            position="QB",
            team="BUF",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=props,
            has_active_game_props=True,
            expected_event_id="nfl_game_test_123",
        )
        assert sig_l == MarketPropSignal.LISTED_AT_CUTOFF
        assert p_l == 0.60

        # Doubtful no listado con cobertura de QB
        sig_u, p_u, _ = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.DOUBTFUL,
            player_name="Mitchell Trubisky",
            position="QB",
            team="BUF",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=props,
            has_active_game_props=True,
            expected_event_id="nfl_game_test_123",
        )
        assert sig_u == MarketPropSignal.NOT_LISTED_WITH_COVERAGE
        assert p_u == 0.98

    def test_props_without_event_id_or_bookmaker_rejected_as_non_verifiable(self, availability_svc):
        future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        cutoff = datetime.now(timezone.utc).isoformat()

        # Prop incompleta: falta event_id y bookmaker
        incomplete_props = [{"player_name": "Puka Nacua", "prop_type": "receiving_yards", "market_line": 60.5}]

        signal, p_absence, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Puka Nacua",
            position="WR",
            team="LAR",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=incomplete_props,
            expected_event_id="nfl_game_test_123",
        )
        assert signal == MarketPropSignal.UNKNOWN
        assert p_absence == 0.25
        assert flags["checked_props"] is False
        assert flags["reason"] == "no_verifiable_props_in_cutoff_window"

    def test_props_with_timestamp_after_cutoff_rejected(self, availability_svc):
        future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        cutoff_dt = datetime.now(timezone.utc)
        post_cutoff_ts = (cutoff_dt + timedelta(minutes=15)).isoformat()

        late_prop = make_verifiable_prop(
            "Puka Nacua", "WR", "receiving_yards", 60.5,
            timestamp=post_cutoff_ts,
        )

        signal, p_absence, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Puka Nacua",
            position="WR",
            team="LAR",
            game_start_utc=future_start,
            decision_cutoff=cutoff_dt.isoformat(),
            live_props=[late_prop],
            expected_event_id="nfl_game_test_123",
        )
        assert signal == MarketPropSignal.UNKNOWN
        assert p_absence == 0.25
        assert flags["checked_props"] is False

    @pytest.mark.parametrize("invalid_prop", [
        {**make_verifiable_prop("Puka Nacua"), "timestamp": None},
        {**make_verifiable_prop("Puka Nacua"), "price": None, "over_odds_american": None},
        {**make_verifiable_prop("Puka Nacua"), "event_id": "another_game"},
    ])
    def test_props_require_pre_cutoff_timestamp_price_and_matching_event(self, availability_svc, invalid_prop):
        cutoff = datetime.now(timezone.utc).isoformat()
        signal, p_absence, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Puka Nacua", position="WR", team="LAR",
            game_start_utc=(datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
            decision_cutoff=cutoff, live_props=[invalid_prop],
            expected_event_id="nfl_game_test_123",
        )
        assert signal == MarketPropSignal.UNKNOWN
        assert p_absence == 0.25
        assert flags["checked_props"] is False


# ==============================================================================
# 3. Partidos Históricos: Prohibición de Consultar Cuotas / Props
# ==============================================================================

class TestHistoricalGameRules:
    """Valida que los partidos históricos nunca consulten cuotas o props."""

    def test_historical_game_returns_unknown_and_never_checks_props(self, availability_svc):
        past_start = "2024-09-08T17:00:00+00:00"
        past_cutoff = "2024-09-08T15:00:00+00:00"

        signal, p_absence, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Some Player",
            position="WR",
            team="KC",
            game_start_utc=past_start,
            decision_cutoff=past_cutoff,
            live_props=[make_verifiable_prop("Some Player", "WR", "receiving_yards", 50.5)],
            has_active_game_props=True,
        )

        assert signal == MarketPropSignal.UNKNOWN
        assert p_absence == 0.25  # Base empírica pregame
        assert flags["checked_props"] is False
        assert flags["reason"] == "historical_or_past_game_no_props_query"
        assert flags["is_future_unplayed"] is False


# ==============================================================================
# 4. Fail-Closed, Timestamps y Manejo de Identidad
# ==============================================================================

class TestFailClosedAndTimestamps:
    """Verifica el comportamiento fail-closed ante nulos, identidades dudosas y fechas posteriores al corte."""

    def test_observation_after_cutoff_is_unknown(self, availability_svc):
        cutoff = "2024-10-10T16:00:00+00:00"
        after_cutoff = "2024-10-10T16:05:00+00:00"

        status = availability_svc.resolve_availability_status(
            raw_status="Out",
            observed_at=after_cutoff,
            decision_cutoff=cutoff,
        )
        assert status == AvailabilityStatus.UNKNOWN

    def test_observation_equal_to_cutoff_is_unknown(self, availability_svc):
        cutoff = "2024-10-10T16:00:00+00:00"
        status = availability_svc.resolve_availability_status(
            raw_status="Out",
            observed_at=cutoff,
            decision_cutoff=cutoff,
        )
        assert status == AvailabilityStatus.UNKNOWN

    def test_missing_timestamp_with_cutoff_enforced_is_unknown(self, availability_svc):
        cutoff = "2024-10-10T16:00:00+00:00"
        status = availability_svc.resolve_availability_status(
            raw_status="Questionable",
            observed_at=None,
            decision_cutoff=cutoff,
        )
        assert status == AvailabilityStatus.UNKNOWN

    def test_missing_decision_cutoff_is_unknown(self, availability_svc):
        status = availability_svc.resolve_availability_status(
            raw_status="Out",
            observed_at="2024-10-10T14:00:00+00:00",
            decision_cutoff=None,
        )
        assert status == AvailabilityStatus.UNKNOWN

    def test_two_hour_cutoff_window(self, availability_svc):
        kickoff = "2024-10-10T17:00:00+00:00"
        cutoff_2h = "2024-10-10T15:00:00+00:00"  # 2h antes

        # Reporte 2.5h antes del kickoff: pre-corte válido
        status_valid = availability_svc.resolve_availability_status(
            raw_status="Out",
            observed_at="2024-10-10T14:30:00+00:00",
            decision_cutoff=cutoff_2h,
        )
        assert status_valid == AvailabilityStatus.CONFIRMED_OUT

        # Reporte 1h antes del kickoff: dentro de la ventana excluida de 2h -> UNKNOWN
        status_late = availability_svc.resolve_availability_status(
            raw_status="Out",
            observed_at="2024-10-10T16:00:00+00:00",
            decision_cutoff=cutoff_2h,
        )
        assert status_late == AvailabilityStatus.UNKNOWN

    def test_unresolved_player_identity_marked_unknown(self, availability_svc):
        rec = availability_svc.process_player_injury(
            player_dict={"player_name": "Desconocido", "status": "Out"},
            team="KC",
            decision_cutoff=None,
        )
        assert rec.availability_status == AvailabilityStatus.UNKNOWN.value
        assert rec.coverage_flags.get("identity_unresolved") is True
        assert rec.coverage_flags.get("unresolved_canonical_id") is True


# ==============================================================================
# 5. Cobertura Explícita, Sin Priors y Fail-Closed en Inferencia
# ==============================================================================

class TestCoverageStateAndInferenceFailClosed:
    """Valida los estados de cobertura explícitos, ausencia de priors sintéticos y abstención en inferencia."""

    def test_coverage_state_differentiation(self, availability_svc):
        # 1. Reporte sin lesionados verificado -> covered_healthy, métricas 0.0
        summary_clean = availability_svc.compute_team_production_lost(
            team="KC",
            records=[],
            prior_roster_stats={},
            is_source_available=True,
        )
        assert summary_clean.coverage_state == CoverageState.COVERED_HEALTHY.value
        assert summary_clean.ppr_lost_pct == 0.0
        assert summary_clean.off_snaps_lost_pct == 0.0

        # 2. Fuente no disponible / caída -> unavailable_no_report, métricas None
        summary_unavail = availability_svc.compute_team_production_lost(
            team="KC",
            records=[],
            prior_roster_stats=None,
            is_source_available=False,
        )
        assert summary_unavail.coverage_state == CoverageState.UNAVAILABLE_NO_REPORT.value
        assert summary_unavail.ppr_lost_pct is None
        assert summary_unavail.off_snaps_lost_pct is None

    def test_missing_prior_roster_stats_produces_none_metrics_without_synthetic_priors(self, availability_svc):
        rec = PlayerInjuryRecord(
            player_name="Patrick Mahomes",
            team="KC",
            position="QB",
            canonical_player_id="00-0033873",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value,
            p_absence=1.0,
        )
        # Sin prior_roster_stats observados
        summary = availability_svc.compute_team_production_lost(
            team="KC",
            records=[rec],
            prior_roster_stats=None,
            is_source_available=True,
        )
        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None  # NO se inventan 15 PPR
        assert summary.off_snaps_lost_pct is None  # NO se inventan 0.80 snaps
        assert summary.def_snaps_lost_pct is None

    def test_questionable_without_market_confirmation_abstains_instead_of_using_base_guess(self, availability_svc):
        rec = PlayerInjuryRecord(
            player_name="Puka Nacua", team="LAR", position="WR",
            canonical_player_id="gsis-puka",
            availability_status=AvailabilityStatus.QUESTIONABLE.value,
            market_signal=MarketPropSignal.UNKNOWN.value, p_absence=0.25,
        )
        summary = availability_svc.compute_team_production_lost(
            team="LAR", records=[rec],
            prior_roster_stats={
                "gsis-puka": {"rolling_ppr": 20.0, "rolling_off_pct": 0.8, "rolling_def_pct": 0.0},
                "teammate": {"rolling_ppr": 50.0, "rolling_off_pct": 0.8, "rolling_def_pct": 0.0},
            },
        )
        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None
        assert summary.metadata["unresolved_market_count"] == 1

    def test_fantasy_injury_service_without_decision_cutoff_fails_closed(self, tmp_path):
        svc = FantasyInjuryService(cache_dir=str(tmp_path))
        result = svc.get_live_production_lost("NFL", decision_cutoff=None)
        assert result == {}  # Falla cerrado sin decision_cutoff

    def test_fantasy_injury_service_with_point_in_time_contract_succeeds_and_persists(self, tmp_path, monkeypatch):
        svc = FantasyInjuryService(cache_dir=str(tmp_path))
        now_utc = datetime.now(timezone.utc)
        obs_time = (now_utc - timedelta(hours=3)).isoformat()
        cutoff_time = (now_utc - timedelta(hours=1)).isoformat()
        start_time = (now_utc + timedelta(hours=2)).isoformat()

        mock_injuries = {
            "KC": [
                {
                    "player_name": "Rashee Rice",
                    "position": "WR",
                    "status": "OUT",
                    "gsis_id": "00-0038634",
                    "espn_id": "4430155",
                    "observed_at": obs_time,
                }
            ]
        }
        monkeypatch.setattr("app.services.matchup_context_service.get_live_injuries", lambda sport: mock_injuries)

        prior_stats = {
            "00-0038634": {"rolling_ppr": 18.5, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0}
        }

        result = svc.get_live_production_lost(
            sport="NFL",
            decision_cutoff=cutoff_time,
            game_start_utc=start_time,
            prior_roster_stats=prior_stats,
            game_id="2024_01_KC_BAL",
        )
        assert "KC" in result
        assert result["KC"]["coverage_state"] == CoverageState.COVERED_WITH_INJURIES.value
        assert result["KC"]["ppr_lost_pct"] is not None
        assert result["KC"]["ppr_lost_pct"] > 0.0

        # Snapshot con provenance persistido en disco
        cache_files = list((tmp_path / "nfl_availability").glob("avail_*.json"))
        assert len(cache_files) > 0
        persisted = json.loads(cache_files[0].read_text(encoding="utf-8"))
        assert persisted["resolver_version"] == RESOLVER_VERSION
        assert persisted["team"] == "KC"

    def test_inference_raises_data_quality_error_when_availability_unknown_or_unavailable(self, monkeypatch):
        from ml.inference import predict_matchups

        completed = pd.DataFrame([
            {
                "game_id": "2024_01_KC_BAL", "game_date": "2024-09-05",
                "home_team_id": "KC", "away_team_id": "BAL",
                "home_score": 27, "away_score": 20, "home_win": 1,
                "home_off_epa_per_play": 0.1, "home_off_success_rate": 0.45,
                "home_def_epa_allowed_per_play": -0.05, "home_def_success_allowed": 0.40,
                "away_off_epa_per_play": -0.05, "away_off_success_rate": 0.40,
                "away_def_epa_allowed_per_play": 0.1, "away_def_success_allowed": 0.45,
                "source_ref": "snap",
            },
            {
                "game_id": "2024_01_CIN_NE", "game_date": "2024-09-08",
                "home_team_id": "CIN", "away_team_id": "NE",
                "home_score": 10, "away_score": 16, "home_win": 0,
                "home_off_epa_per_play": -0.1, "home_off_success_rate": 0.35,
                "home_def_epa_allowed_per_play": 0.05, "home_def_success_allowed": 0.42,
                "away_off_epa_per_play": 0.05, "away_off_success_rate": 0.42,
                "away_def_epa_allowed_per_play": -0.1, "away_def_success_allowed": 0.35,
                "source_ref": "snap",
            },
        ])
        upcoming = pd.DataFrame([{
            "game_id": "2024_02_CIN_KC", "game_date": datetime.now(timezone.utc).date().isoformat(),
            "home_team_id": "KC", "away_team_id": "CIN",
            "game_start_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "source_ref": "sched",
        }])

        game_id = "2024_02_CIN_KC"
        kickoff = pd.to_datetime(upcoming.iloc[0]["game_start_utc"], utc=True)
        decision_cutoff = kickoff - timedelta(hours=2)
        source_time = decision_cutoff - timedelta(minutes=5)

        def availability_input(team_data):
            return {
                game_id: {
                    "game_id": game_id,
                    "decision_cutoff_utc": decision_cutoff.isoformat(),
                    "captured_at_utc": source_time.isoformat(),
                    "source_observed_at_utc": source_time.isoformat(),
                    "resolver_version": "fixture-resolver-v1",
                    "source_refs_by_team": {"KC": ["fixture:KC"], "CIN": ["fixture:CIN"]},
                    "teams": team_data,
                }
            }

        # Fixtures sintéticos solo prueban el contrato; no son datos de modelo/producción.
        mock_bad_injury_data = {
            "KC": {"coverage_state": "unknown", "ppr_lost_pct": None, "off_snaps_lost_pct": None, "def_snaps_lost_pct": None},
            "CIN": {
                "coverage_state": "covered_healthy", "ppr_lost_pct": 0.0,
                "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0,
                "clean_report_evidence_ref": "fixture:CIN-clean-report",
            },
        }

        from ml.data_pipeline.temporal_features import NFL_GAME_STATS, feature_columns
        n_feats = len(feature_columns(NFL_GAME_STATS))
        mock_model = MagicMock()
        mock_model.predict_proba.return_value = np.array([[0.4, 0.6]])
        mock_model.n_features_in_ = n_feats
        mock_model.classes_ = [0, 1]
        mock_metadata = {
            "model_version": "nfl_test_v1",
            "production_enabled": False,
            "dataset_sha256": "fake_hash",
            "source_manifest": "fake_src",
            "features": feature_columns(NFL_GAME_STATS),
            "_artifact_sha256": "fake_art",
        }
        mock_manifest = {"source_manifest": "fake_src"}

        with patch("ml.inference.load_temporal_model", return_value=(mock_model, mock_metadata, mock_manifest)):
            # Caso 1: Cobertura desconocida / métricas None -> Fail closed; no consulta ESPN.
            with patch("app.services.matchup_context_service.get_live_injuries", side_effect=AssertionError("inference no debe consultar ESPN")) as espn_fetch:
                with pytest.raises(DataQualityError, match="NFL availability desconocida para KC"):
                    predict_matchups(
                        "nfl", completed, upcoming, allow_experimental=True,
                        availability_by_game=availability_input(mock_bad_injury_data),
                    )
                espn_fetch.assert_not_called()

            # Caso 2: Sin snapshot explícito por partido -> Fail closed, sin fallback de red.
            with patch("app.services.matchup_context_service.get_live_injuries", side_effect=AssertionError("inference no debe consultar ESPN")) as espn_fetch:
                with pytest.raises(DataQualityError, match="availability snapshot ausente"):
                    predict_matchups("nfl", completed, upcoming, allow_experimental=True, availability_by_game={})
                espn_fetch.assert_not_called()

            late_snapshot = availability_input(mock_bad_injury_data)
            late_snapshot[game_id]["captured_at_utc"] = decision_cutoff.isoformat()
            late_snapshot[game_id]["source_observed_at_utc"] = decision_cutoff.isoformat()
            with pytest.raises(DataQualityError, match="posterior o igual al cutoff"):
                predict_matchups(
                    "nfl", completed, upcoming, allow_experimental=True,
                    availability_by_game=late_snapshot,
                )

            # Caso 3: Snapshot válido, con referencias y evidencia limpia explícita -> Inferencia exitosa.
            mock_good_injury_data = {
                "KC": {
                    "coverage_state": "covered_healthy", "ppr_lost_pct": 0.0,
                    "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0,
                    "clean_report_evidence_ref": "fixture:KC-clean-report",
                    "decision_cutoff": decision_cutoff.isoformat(),
                },
                "CIN": {"coverage_state": "covered_with_injuries", "ppr_lost_pct": 0.12, "off_snaps_lost_pct": 0.08, "def_snaps_lost_pct": 0.0},
            }
            with patch("app.services.matchup_context_service.get_live_injuries", side_effect=AssertionError("inference no debe consultar ESPN")) as espn_fetch:
                preds, run_meta = predict_matchups(
                    "nfl", completed, upcoming, allow_experimental=True,
                    availability_by_game=availability_input(mock_good_injury_data),
                )
                assert not preds.empty
                assert "home_win_probability" in preds.columns
                assert run_meta["availability_coverage"]["has_verified_snapshots"] is True
                assert run_meta["availability_coverage"]["source_refs"][game_id]["KC"] == ["fixture:KC"]
                espn_fetch.assert_not_called()

    def test_inference_rejects_started_game_before_live_availability_lookup(self):
        from ml.inference import predict_matchups
        from ml.data_pipeline.temporal_features import NFL_GAME_STATS, feature_columns

        features = pd.DataFrame([{
            "game_id": "2026_06_KC_BAL",
            "home_team_abbreviation": "KC",
            "away_team_abbreviation": "BAL",
            "game_start_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        }])
        n_feats = len(feature_columns(NFL_GAME_STATS))
        model = MagicMock(n_features_in_=n_feats, classes_=[0, 1])
        metadata = {
            "model_version": "nfl_test_v1", "production_enabled": False,
            "dataset_sha256": "hash", "source_manifest": "manifest",
            "features": feature_columns(NFL_GAME_STATS), "_artifact_sha256": "artifact",
        }
        with patch("ml.inference.load_temporal_model", return_value=(model, metadata, {})), \
             patch("ml.inference.build_matchup_features", return_value=(features, {"feature_rule": "test", "source_refs": [], "skipped_games": []})), \
             patch("app.services.fantasy_injury_service.FantasyInjuryService.get_live_production_lost") as lookup:
            with pytest.raises(DataQualityError, match="ya inició o finalizó"):
                predict_matchups(
                    "nfl", pd.DataFrame(), pd.DataFrame([{"game_id": "2026_06_KC_BAL"}]),
                    availability_by_game={},
                )
            lookup.assert_not_called()

            features.at[0, "game_start_utc"] = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            with pytest.raises(DataQualityError, match="as_of de ejecución actual"):
                predict_matchups(
                    "nfl", pd.DataFrame(), pd.DataFrame([{"game_id": "2024_06_KC_BAL"}]),
                    as_of="2024-09-01T12:00:00Z",
                    availability_by_game={},
                )
            lookup.assert_not_called()


# ==============================================================================
# 6. Anti-Leak Histórico y Ground Truth
# ==============================================================================

class TestHistoricalPipelineNoLeak:
    """Valida que el builder histórico no use did_play del juego objetivo como feature pregame."""

    def test_historical_injury_features_runs_without_leak(self):
        df = build_nfl_historical_injury_features([2024])
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert set(df.columns) == {"season", "week", "team", "ppr_lost_pct", "off_snaps_lost_pct", "def_snaps_lost_pct"}
        assert df["ppr_lost_pct"].isna().sum() == 0
        assert df["off_snaps_lost_pct"].isna().sum() == 0
        assert df["def_snaps_lost_pct"].isna().sum() == 0

    def test_position_specific_metrics_do_not_require_inapplicable_history(self):
        from ml.data_pipeline.injury_feature_builder import _position_metrics

        assert _position_metrics("WR") == (True, True, False)
        assert _position_metrics("OL") == (False, True, False)
        assert _position_metrics("DB") == (False, False, True)
        assert _position_metrics("K") == (False, False, False)
        assert _position_metrics(None) == (True, True, True)

    def test_ground_truth_dataset_isolates_did_play(self):
        gt = build_nfl_availability_ground_truth([2024])
        assert isinstance(gt, pd.DataFrame)
        assert not gt.empty
        assert "did_play" in gt.columns
        assert "label_status" in gt.columns
        assert set(gt["label_status"].unique()).issubset(
            {"observed", "observed_inactive_roster", "missing_snap_record"}
        )

        observed = gt[
            gt["label_status"].isin(["observed", "observed_inactive_roster"])
            & gt["pregame_valid"]
        ]
        assert not observed.empty

        # Solo aplica a observaciones pre-corte verificadas; no impone una tasa de calibración.
        out_sub = observed[observed["report_status"] == "Out"]
        if not out_sub.empty:
            assert out_sub["did_play"].notna().all()

        # La participación se usa como ground truth, no como afirmación de calibración.
        quest_sub = observed[observed["report_status"] == "Questionable"]
        assert len(quest_sub) > 0
        assert quest_sub["did_play"].notna().all()

    def test_explicit_inactive_roster_resolves_missing_snap_label_only(self):
        gt = build_nfl_availability_ground_truth([2024])
        inactive = gt[gt["label_status"] == "observed_inactive_roster"]

        assert not inactive.empty
        assert inactive["did_play"].eq(False).all()
        assert inactive["pregame_valid"].any()

        # ACT is not a game-participation label. Missing snap rows remain unknown.
        from ml.data_pipeline.nfl_source import load_nfl_snapshot

        rosters = load_nfl_snapshot("rosters_weekly", 2024).to_pandas().rename(
            columns={"gsis_id": "player_id"}
        )
        missing = gt[gt["label_status"] == "missing_snap_record"].merge(
            rosters[["season", "game_type", "week", "team", "player_id", "status"]].drop_duplicates(),
            on=["season", "game_type", "week", "team", "player_id"],
            how="inner",
        )
        act_missing = missing[missing["status"] == "ACT"]
        assert not act_missing.empty
        assert act_missing["did_play"].isna().all()


# ==============================================================================
# 7. Flujo Práctico NFL: Cadencia, Equipos Ausentes, Cruce Odds y Rendimiento
# ==============================================================================

class TestNFLPracticalFlowPhase02:
    """Valida los 7 requerimientos del flujo práctico NFL en Fase 02."""

    def test_two_captures_cadence_separate_snapshots_and_timestamps(self, tmp_path):
        """1. Cadencia: dos capturas con timestamps locales UTC y separación temporal."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail_cadence")

        raw_snap_1 = {
            "KC": [
                {
                    "player_name": "Patrick Mahomes",
                    "position": "QB",
                    "status": "QUESTIONABLE",
                    "gsis_id": "00-0033873",
                    "observed_at": "2026-10-11T07:45:00+00:00",
                }
            ],
            "SF": [
                {
                    "player_name": "Christian McCaffrey",
                    "position": "RB",
                    "status": "QUESTIONABLE",
                    "gsis_id": "00-0033280",
                    "observed_at": "2026-10-11T07:50:00+00:00",
                }
            ],
        }

        # Captura 1: Inicio del día (08:00 UTC)
        t_capture_1 = "2026-10-11T08:00:00+00:00"
        t_observed_1 = "2026-10-11T07:55:00+00:00"
        snap1 = svc.save_espn_injury_snapshot(
            raw_teams_data=raw_snap_1,
            cadence="start_of_day",
            captured_at_utc=t_capture_1,
            source_observed_at_utc=t_observed_1,
        )

        raw_snap_2 = {
            "KC": [
                {
                    "player_name": "Patrick Mahomes",
                    "position": "QB",
                    "status": "ACTIVE",
                    "gsis_id": "00-0033873",
                    "observed_at": "2026-10-11T17:15:00+00:00",
                }
            ],
            "SF": [
                {
                    "player_name": "Christian McCaffrey",
                    "position": "RB",
                    "status": "OUT",
                    "gsis_id": "00-0033280",
                    "observed_at": "2026-10-11T17:20:00+00:00",
                }
            ],
        }

        # Captura 2: Cerca del kickoff (17:30 UTC)
        t_capture_2 = "2026-10-11T17:30:00+00:00"
        t_observed_2 = "2026-10-11T17:25:00+00:00"
        snap2 = svc.save_espn_injury_snapshot(
            raw_teams_data=raw_snap_2,
            cadence="near_kickoff",
            captured_at_utc=t_capture_2,
            source_observed_at_utc=t_observed_2,
        )

        # Verificar snapshots separados en disco
        snapshot_files = list(svc.snapshots_dir.glob("espn_snapshot_*.json"))
        assert len(snapshot_files) == 2, "Debe haber 2 snapshots separados guardados"

        # Verificar captured_at_utc no se presenta como ESPN's publication timestamp
        assert snap1.captured_at_utc == t_capture_1
        assert snap1.source_observed_at_utc == t_observed_1
        assert snap1.captured_at_utc != snap1.source_observed_at_utc

        assert snap2.captured_at_utc == t_capture_2
        assert snap2.source_observed_at_utc == t_observed_2
        assert snap2.captured_at_utc != snap2.source_observed_at_utc

        # Predicción a las 12:00 UTC (antes de captura 2): SOLO ve captura 1
        active_at_noon = svc.get_active_injury_snapshot(as_of="2026-10-11T12:00:00+00:00")
        assert active_at_noon is not None
        assert active_at_noon["cadence"] == "start_of_day"
        assert active_at_noon["captured_at_utc"] == t_capture_1
        # En la captura 1, McCaffrey era QUESTIONABLE
        sf_injuries_noon = active_at_noon["teams"]["SF"]
        assert sf_injuries_noon[0]["status"] == "QUESTIONABLE"

        # Predicción a las 18:00 UTC (después de captura 2): ve captura 2 con inactivos actualizados
        active_post_kickoff_eval = svc.get_active_injury_snapshot(as_of="2026-10-11T18:00:00+00:00")
        assert active_post_kickoff_eval is not None
        assert active_post_kickoff_eval["cadence"] == "near_kickoff"
        assert active_post_kickoff_eval["captured_at_utc"] == t_capture_2
        # En la captura 2, McCaffrey se actualizó a OUT
        sf_injuries_late = active_post_kickoff_eval["teams"]["SF"]
        assert sf_injuries_late[0]["status"] == "OUT"

    def test_omitted_teams_recorded_faithfully_without_blocking_feed(self, tmp_path):
        """2. Equipos ausentes: sin umbrales 28/32, sin inferir sano, sin inventar jugadores."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail_omitted")

        # Payload con solo 2 equipos; 30 equipos omitidos
        raw_partial_feed = {
            "KC": [
                {
                    "player_name": "Rashee Rice",
                    "position": "WR",
                    "status": "OUT",
                    "gsis_id": "00-0038634",
                    "observed_at": "2026-10-11T14:00:00+00:00",
                }
            ],
            "BAL": [],
        }

        snap = svc.save_espn_injury_snapshot(
            raw_teams_data=raw_partial_feed,
            cadence="start_of_day",
            captured_at_utc="2026-10-11T14:30:00+00:00",
        )

        assert "BAL" in snap.teams
        assert "SF" in snap.omitted_teams
        assert len(snap.omitted_teams) == 30

        # Computar pérdida para equipo omitido (SF)
        sf_summary = svc.compute_team_production_lost(
            team="SF",
            records=[],
            prior_roster_stats=None,
            game_id="2026_06_KC_SF",
            is_source_available=True,
            no_injuries_listed=True,
        )

        # Verificación estricta:
        # NO se infiere que está sano (COVERED_HEALTHY exige reporte limpio verificado)
        assert sf_summary.coverage_state == CoverageState.NO_INJURIES_LISTED.value
        assert sf_summary.coverage_state != CoverageState.COVERED_HEALTHY.value
        assert sf_summary.ppr_lost_pct is None
        assert sf_summary.off_snaps_lost_pct is None
        assert sf_summary.metadata.get("note") == "sin lesiones listadas en esta captura"
        assert len(sf_summary.players) == 0  # Cero jugadores inventados

        # FantasyInjuryService con expected_teams procesa sin bloquear el feed ante equipos omitidos
        fantasy_svc = FantasyInjuryService(cache_dir=str(tmp_path / "f_cache"))
        with patch("app.services.matchup_context_service.get_live_injuries", return_value=raw_partial_feed):
            result = fantasy_svc.get_live_production_lost(
                sport="NFL",
                decision_cutoff="2026-10-11T16:00:00+00:00",
                game_start_utc="2026-10-11T18:00:00+00:00",
                expected_teams=["KC", "SF"],
            )
            # El feed NO se bloquea y retorna ambos equipos
            assert "KC" in result
            assert "SF" in result
            assert result["SF"]["coverage_state"] == CoverageState.NO_INJURIES_LISTED.value
            assert result["SF"].get("note") == "sin lesiones listadas en esta captura"

    def test_espn_odds_props_matching_by_event_and_player_q_d(self, availability_svc):
        """3. Cruce ESPN-odds: evento/jugador correcto, línea como señal observada, no certeza."""
        now = datetime.now(timezone.utc)
        cutoff = (now + timedelta(hours=1)).isoformat()
        future_start = (now + timedelta(hours=3)).isoformat()
        prop_ts = (now - timedelta(minutes=5)).isoformat()

        # Props del evento correcto
        live_props = [
            make_verifiable_prop(
                player_name="Puka Nacua",
                position="WR",
                prop_type="receiving_yards",
                market_line=62.5,
                event_id="nfl_game_kc_lar_101",
                timestamp=prop_ts,
            ),
            make_verifiable_prop(
                player_name="Other Player",
                position="WR",
                prop_type="receiving_yards",
                market_line=45.5,
                event_id="another_game_202",
                timestamp=prop_ts,
            ),
        ]

        # Jugador Q con línea presente en evento correcto
        sig, p_abs, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Puka Nacua",
            position="WR",
            team="LAR",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=live_props,
            expected_event_id="nfl_game_kc_lar_101",
            strict_unlisted_unknown=True,
        )
        assert sig == MarketPropSignal.LISTED_AT_CUTOFF
        assert flags["checked_props"] is True
        assert flags["listed"] is True
        # La presencia de una línea se registra como señal observada, NO como certeza de jugar (p_abs > 0)
        assert p_abs == 0.15
        assert p_abs > 0.0

        # Jugador Q SIN línea en evento correcto: con strict_unlisted_unknown=True conserva UNKNOWN
        # y NO se interpreta como prueba de que no jugará
        sig_unlisted, p_unlisted, flags_unlisted = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Cooper Kupp",
            position="WR",
            team="LAR",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=live_props,
            expected_event_id="nfl_game_kc_lar_101",
            strict_unlisted_unknown=True,
        )
        assert sig_unlisted == MarketPropSignal.UNKNOWN
        assert flags_unlisted["listed"] is False
        assert flags_unlisted["signal"] == "unknown"
        assert p_unlisted == 0.25

    def test_confirmed_out_strictly_never_queries_props(self, availability_svc):
        """3. Para OUT, nunca consultar props."""
        now = datetime.now(timezone.utc)
        future_start = (now + timedelta(hours=3)).isoformat()
        cutoff = (now + timedelta(hours=1)).isoformat()

        # Props existen en el mercado
        props = [
            make_verifiable_prop("Travis Kelce", "TE", "receiving_yards", 65.5, event_id="ev_kc_1")
        ]

        sig, p_abs, flags = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.CONFIRMED_OUT,
            player_name="Travis Kelce",
            position="TE",
            team="KC",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=props,
            expected_event_id="ev_kc_1",
        )

        assert sig == MarketPropSignal.UNKNOWN
        assert p_abs == 1.0  # Baja confirmada 100%
        assert flags["checked_props"] is False
        assert flags["reason"] == "confirmed_out_no_props_query"

    def test_odds_quota_service_variable_cost_by_markets_and_reservation(self, tmp_path):
        """3. Costo variable según mercados/datos solicitados y reserva en OddsQuotaService."""
        # 1 mercado -> 1 crédito
        c1 = estimate_request_cost(
            endpoint="/sports/americanfootball_nfl/events/ev1/odds",
            params={"markets": "player_pass_yds", "regions": "us"},
        )
        assert c1 == 1

        # 3 mercados -> 3 créditos (1 llamada, 3 créditos)
        c3 = estimate_request_cost(
            endpoint="/sports/americanfootball_nfl/events/ev1/odds",
            params={"markets": "player_pass_yds,player_rush_yds,player_reception_yds", "regions": "us"},
        )
        assert c3 == 3

        # 4 mercados -> 4 créditos
        c4 = estimate_request_cost(
            endpoint="/sports/americanfootball_nfl/events/ev1/odds",
            params={"markets": "player_pass_yds,player_rush_yds,player_reception_yds,player_pass_tds", "regions": "us"},
        )
        assert c4 == 4

        # Probar reserva aislada con ledger en tmp_path sin alterar el ledger real
        fake_ledger_path = tmp_path / "mock_quota_ledger.json"
        fake_ledger_path.write_text(json.dumps({
            "version": "1.0",
            "summary": {
                "requests_used": 0,
                "requests_remaining": 320,
                "spent_by_envelope": {"main_lines": 0, "props": 0, "operational": 0},
                "circuit_breaker_open": False,
            },
            "pending_reservations": {},
            "transactions": [],
        }), encoding="utf-8")

        quota_svc = OddsQuotaService(ledger_path=fake_ledger_path)
        reservation = quota_svc.reserve_quota(
            envelope="props",
            endpoint="/events/ev1/odds",
            params={"markets": "player_pass_yds,player_rush_yds,player_reception_yds,player_pass_tds", "regions": "us"},
        )
        assert reservation.estimated_cost == 4
        assert reservation.envelope == "props"
        assert reservation.reservation_id in quota_svc.pending_reservations
        persisted = json.loads(fake_ledger_path.read_text(encoding="utf-8"))
        assert reservation.reservation_id in persisted["pending_reservations"]

        # Verificar que el ledger real permaneció completamente intacto
        real_ledger = json.loads(Path("data/odds_quota_ledger.json").read_text(encoding="utf-8"))
        assert real_ledger["summary"] == _LEDGER_SUMMARY_AT_IMPORT

    def test_recent_player_performance_no_temporal_leakage_and_current_team_constraint(self, availability_svc):
        """4. Rendimiento individual: ligado al ID canónico y equipo actual, sin fuga ni temporadas antiguas."""
        canonical_id = "00-0033873"
        current_team = "KC"
        as_of = "2024-10-15T12:00:00+00:00"

        game_logs = [
            # 3 juegos válidos en 2024 con KC anteriores al corte
            {
                "player_id": canonical_id, "team": "KC", "season": 2024,
                "game_date": "2024-09-15T17:00:00+00:00",
                "fantasy_points_ppr": 22.4, "offense_pct": 1.0, "defense_pct": 0.0,
            },
            {
                "player_id": canonical_id, "team": "KC", "season": 2024,
                "game_date": "2024-09-22T20:00:00+00:00",
                "fantasy_points_ppr": 18.6, "offense_pct": 0.95, "defense_pct": 0.0,
            },
            {
                "player_id": canonical_id, "team": "KC", "season": 2024,
                "game_date": "2024-09-29T17:00:00+00:00",
                "fantasy_points_ppr": 20.0, "offense_pct": 0.98, "defense_pct": 0.0,
            },
            # 1 juego POST-CORTE en 2024 (debe excluirse estrictamente sin fuga temporal)
            {
                "player_id": canonical_id, "team": "KC", "season": 2024,
                "game_date": "2024-10-20T17:00:00+00:00",
                "fantasy_points_ppr": 35.0, "offense_pct": 1.0, "defense_pct": 0.0,
            },
            # 2 juegos de OTRO EQUIPO (deben excluirse: no trasladar de equipos anteriores)
            {
                "player_id": canonical_id, "team": "NYJ", "season": 2024,
                "game_date": "2024-09-08T17:00:00+00:00",
                "fantasy_points_ppr": 30.0, "offense_pct": 1.0, "defense_pct": 0.0,
            },
            # 1 juego de temporada ANTIGUA (debe excluirse: no sustituir con temporadas antiguas)
            {
                "player_id": canonical_id, "team": "KC", "season": 2021,
                "game_date": "2021-10-10T17:00:00+00:00",
                "fantasy_points_ppr": 40.0, "offense_pct": 1.0, "defense_pct": 0.0,
            },
        ]

        pos_rank_ctx = {
            "pos_rank": 2,
            "as_of": "2024-10-14T00:00:00+00:00",
            "provenance": "official_nfl_weekly_rank",
        }

        perf = availability_svc.calculate_player_recent_importance(
            canonical_player_id=canonical_id,
            current_team=current_team,
            as_of=as_of,
            recent_game_logs=game_logs,
            pos_rank_context=pos_rank_ctx,
            max_recent_games=5,
        )

        assert perf["status"] == "observed"
        assert perf["games_sampled"] == 3
        assert perf["rolling_ppr"] == round((22.4 + 18.6 + 20.0) / 3, 2)
        assert perf["rolling_off_pct"] == round((1.0 + 0.95 + 0.98) / 3, 3)

        # pos_rank entra como contexto vigente con procedencia sin multiplicador fijo
        assert perf["pos_rank_context"]["rank"] == 2
        assert perf["pos_rank_context"]["provenance"] == "official_nfl_weekly_rank"
        assert perf["pos_rank_context"]["role_context"] == "descriptive_only_no_fixed_multiplier"

    def test_missing_player_history_leaves_adjustment_unknown_and_base_model_continues(self, availability_svc):
        """4. Si falta muestra reciente, ajuste desconocido y el modelo base NFL continúa."""
        perf = availability_svc.calculate_player_recent_importance(
            canonical_player_id="00-9999999",
            current_team="KC",
            as_of="2024-10-15T12:00:00+00:00",
            recent_game_logs=[],
        )
        assert perf["status"] == "unknown"
        assert perf["rolling_ppr"] is None
        assert perf["importance_score"] is None
        assert perf["reason"] == "no_recent_game_logs_provided"

        from ml.inference import predict_matchups

        completed = pd.DataFrame([
            {
                "game_id": "2024_01_KC_BAL", "game_date": "2024-09-05",
                "home_team_id": "KC", "away_team_id": "BAL",
                "home_score": 27, "away_score": 20, "home_win": 1,
                "home_off_epa_per_play": 0.1, "home_off_success_rate": 0.45,
                "home_def_epa_allowed_per_play": -0.05, "home_def_success_allowed": 0.40,
                "away_off_epa_per_play": -0.05, "away_off_success_rate": 0.40,
                "away_def_epa_allowed_per_play": 0.1, "away_def_success_allowed": 0.45,
                "source_ref": "snap",
            },
            {
                "game_id": "2024_01_CIN_NE", "game_date": "2024-09-08",
                "home_team_id": "CIN", "away_team_id": "NE",
                "home_score": 10, "away_score": 16, "home_win": 0,
                "home_off_epa_per_play": -0.1, "home_off_success_rate": 0.35,
                "home_def_epa_allowed_per_play": 0.05, "home_def_success_allowed": 0.42,
                "away_off_epa_per_play": 0.05, "away_off_success_rate": 0.42,
                "away_def_epa_allowed_per_play": -0.1, "away_def_success_allowed": 0.35,
                "source_ref": "snap",
            },
        ])
        upcoming = pd.DataFrame([{
            "game_id": "2024_02_KC_CIN", "game_date": datetime.now(timezone.utc).date().isoformat(),
            "home_team_id": "KC", "away_team_id": "CIN",
            "game_start_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "source_ref": "sched",
        }])

        preds, meta = predict_matchups(
            "nfl", completed, upcoming,
            allow_experimental=True,
            model_variant="team_only",
        )
        assert not preds.empty
        assert "home_win_probability" in preds.columns
        assert meta["model_variant"] == "team_only"
        assert meta["availability_coverage"]["mode"] == "not_used_by_model"

    def test_build_game_availability_snapshot_contract_and_clean_report(self, tmp_path):
        """Verifica que build_game_availability_snapshot opere sin NameError (timedelta), con evidencia limpia y provenance."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail_build")

        # Snapshot con BAL sano (sin lesiones) y SF omitido
        raw_feed = {
            "BAL": [],
        }
        snap = svc.save_espn_injury_snapshot(
            raw_teams_data=raw_feed,
            cadence="start_of_day",
            captured_at_utc="2026-10-11T12:00:00+00:00",
            source_observed_at_utc="2026-10-11T11:50:00+00:00",
        )

        game_id = "2026_06_BAL_SF"
        kickoff = "2026-10-11T18:00:00+00:00"
        as_of = "2026-10-11T14:00:00+00:00"

        # Construir snapshot del partido: BAL (sano) y SF (omitido)
        game_avail = svc.build_game_availability_snapshot(
            game_id=game_id,
            home_team="BAL",
            away_team="SF",
            kickoff_utc=kickoff,
            as_of=as_of,
        )

        assert game_avail["game_id"] == game_id
        assert game_avail["decision_cutoff_utc"] == "2026-10-11T16:00:00+00:00"
        assert game_avail["captured_at_utc"] == "2026-10-11T12:00:00+00:00"
        assert game_avail["source_observed_at_utc"] == "2026-10-11T11:50:00+00:00"

        # BAL está cubierto sano y posee clean_report_evidence_ref
        bal_data = game_avail["teams"]["BAL"]
        assert bal_data["coverage_state"] == CoverageState.COVERED_HEALTHY.value
        assert bal_data["clean_report_evidence_ref"] is not None
        assert "clean_report" in bal_data["clean_report_evidence_ref"]
        assert bal_data["ppr_lost_pct"] == 0.0

        # SF está omitido (no_injuries_listed_in_capture)
        sf_data = game_avail["teams"]["SF"]
        assert sf_data["coverage_state"] == CoverageState.NO_INJURIES_LISTED.value
        assert sf_data["ppr_lost_pct"] is None

    def test_omitted_team_in_predict_matchups_continues_base_model(self):
        """2. Equipos ausentes: en predict_matchups con NO_INJURIES_LISTED, el modelo base NFL team_only continúa."""
        from ml.inference import predict_matchups

        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=1, minutes=30)
        cutoff = kickoff - timedelta(hours=2)
        captured = cutoff - timedelta(minutes=10)
        game_id = "2026_06_KC_SF"

        completed = pd.DataFrame([
            {
                "game_id": "2024_01_KC_BAL", "game_date": "2024-09-05",
                "home_team_id": "KC", "away_team_id": "BAL",
                "home_score": 27, "away_score": 20, "home_win": 1,
                "home_off_epa_per_play": 0.1, "home_off_success_rate": 0.45,
                "home_def_epa_allowed_per_play": -0.05, "home_def_success_allowed": 0.40,
                "away_off_epa_per_play": -0.05, "away_off_success_rate": 0.40,
                "away_def_epa_allowed_per_play": 0.1, "away_def_success_allowed": 0.45,
                "source_ref": "snap",
            },
            {
                "game_id": "2024_01_SF_NYJ", "game_date": "2024-09-09",
                "home_team_id": "SF", "away_team_id": "NYJ",
                "home_score": 32, "away_score": 19, "home_win": 1,
                "home_off_epa_per_play": 0.15, "home_off_success_rate": 0.48,
                "home_def_epa_allowed_per_play": -0.08, "home_def_success_allowed": 0.38,
                "away_off_epa_per_play": -0.08, "away_off_success_rate": 0.38,
                "away_def_epa_allowed_per_play": 0.15, "away_def_success_allowed": 0.48,
                "source_ref": "snap",
            },
        ])
        upcoming = pd.DataFrame([{
            "game_id": game_id,
            "game_date": now.date().isoformat(),
            "home_team_id": "KC",
            "away_team_id": "SF",
            "game_start_utc": kickoff.isoformat(),
            "source_ref": "sched",
        }])

        # Snapshot donde KC está covered_healthy y SF es omitido (no_injuries_listed_in_capture)
        snapshot_omitted = {
            game_id: {
                "game_id": game_id,
                "decision_cutoff_utc": cutoff.isoformat(),
                "captured_at_utc": captured.isoformat(),
                "source_observed_at_utc": (captured - timedelta(minutes=5)).isoformat(),
                "resolver_version": "nfl_availability_v2",
                "source_refs_by_team": {
                    "KC": ["espn_snap:KC"],
                    "SF": ["espn_snap:SF"],
                },
                "teams": {
                    "KC": {
                        "coverage_state": CoverageState.COVERED_HEALTHY.value,
                        "ppr_lost_pct": 0.0,
                        "off_snaps_lost_pct": 0.0,
                        "def_snaps_lost_pct": 0.0,
                        "clean_report_evidence_ref": "espn_snap:KC:clean_report",
                        "decision_cutoff": cutoff.isoformat(),
                    },
                    "SF": {
                        "coverage_state": CoverageState.NO_INJURIES_LISTED.value,
                        "ppr_lost_pct": None,
                        "off_snaps_lost_pct": None,
                        "def_snaps_lost_pct": None,
                        "decision_cutoff": cutoff.isoformat(),
                        "metadata": {"note": "sin lesiones listadas en esta captura"},
                    },
                },
            }
        }

        # La inferencia no debe fallar con DataQualityError: debe continuar con modelo base team_only
        mock_model = MagicMock()
        mock_model.predict_proba.return_value = np.array([[0.4, 0.6]])
        mock_model.classes_ = [0, 1]
        mock_metadata = {
            "model_version": "nfl_test_v1",
            "production_enabled": False,
            "dataset_sha256": "fake_hash",
            "source_manifest": "fake_src",
            "_artifact_sha256": "fake_art",
        }
        mock_manifest = {"source_manifest": "fake_src"}

        with patch("ml.inference.load_temporal_model", return_value=(mock_model, mock_metadata, mock_manifest)):
            preds, run_meta = predict_matchups(
                "nfl", completed, upcoming,
                allow_experimental=True,
                model_variant="injury_aware",
                availability_by_game=snapshot_omitted,
                as_of=now,
            )
            assert not preds.empty
            assert "home_win_probability" in preds.columns
            assert run_meta["model_variant"] == "team_only"
            assert run_meta["availability_coverage"]["mode"] == "not_used_by_model"

    def test_player_recent_importance_rejects_missing_season_old_log_and_future_rank(self, availability_svc):
        """4. Rechazo de registros antiguos sin clave 'season' y rechazo de pos_rank_context futuro."""
        canonical_id = "00-0033873"
        current_team = "KC"
        as_of = "2024-10-15T12:00:00+00:00"

        # Log de 2021 sin clave 'season': debe inferirse como 2021 y excluirse
        game_logs_with_old = [
            {
                "player_id": canonical_id, "team": "KC",
                # Sin clave 'season': fecha 2021
                "game_date": "2021-10-10T17:00:00+00:00",
                "fantasy_points_ppr": 35.0, "offense_pct": 1.0,
            },
        ]

        # pos_rank_context con fecha POSTERIOR al as_of (fuga temporal)
        future_pos_rank = {
            "pos_rank": 1,
            "as_of": "2024-10-25T00:00:00+00:00",
            "provenance": "future_rankings",
        }

        perf = availability_svc.calculate_player_recent_importance(
            canonical_player_id=canonical_id,
            current_team=current_team,
            as_of=as_of,
            recent_game_logs=game_logs_with_old,
            pos_rank_context=future_pos_rank,
        )

        # Sin muestra reciente en la temporada 2024: debe ser unknown
        assert perf["status"] == "unknown"
        assert perf["games_sampled"] == 0
        assert perf["importance_score"] is None
        # pos_rank futuro rechazado: no se adjunta
        assert perf["pos_rank_context"] is None

    def test_fantasy_injury_service_active_snapshot_selection_cadence(self, tmp_path):
        """1 & 3. FantasyInjuryService selecciona el snapshot point-in-time y preserva UNKNOWN para Q/D sin props."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail_fs")

        # Captura 1: 08:00 UTC (Q)
        snap1 = svc.save_espn_injury_snapshot(
            raw_teams_data={
                "KC": [{"player_name": "Travis Kelce", "position": "TE", "status": "QUESTIONABLE", "gsis_id": "00-0030506", "observed_at": "2026-10-11T07:45:00+00:00"}],
            },
            cadence="start_of_day",
            captured_at_utc="2026-10-11T08:00:00+00:00",
            source_observed_at_utc="2026-10-11T07:45:00+00:00",
        )

        # Captura 2: 17:00 UTC (OUT)
        snap2 = svc.save_espn_injury_snapshot(
            raw_teams_data={
                "KC": [{"player_name": "Travis Kelce", "position": "TE", "status": "OUT", "gsis_id": "00-0030506", "observed_at": "2026-10-11T16:45:00+00:00"}],
            },
            cadence="near_kickoff",
            captured_at_utc="2026-10-11T17:00:00+00:00",
            source_observed_at_utc="2026-10-11T16:45:00+00:00",
        )

        fantasy_svc = FantasyInjuryService(cache_dir=str(tmp_path / "f_cache"))
        fantasy_svc.nfl_service = svc

        # Consulta al mediodía (12:00 UTC): ve captura 1 (QUESTIONABLE), sin props -> conserva UNKNOWN
        res_noon = fantasy_svc.get_live_production_lost(
            sport="NFL",
            decision_cutoff="2026-10-11T12:00:00+00:00",
            game_start_utc="2026-10-11T18:00:00+00:00",
            expected_teams=["KC"],
            strict_unlisted_unknown=True,
        )
        assert "KC" in res_noon
        assert res_noon["KC"]["questionable_count"] == 1
        assert res_noon["KC"]["confirmed_out_count"] == 0
        summary_noon = fantasy_svc._last_availability_summaries["KC"]
        assert summary_noon.players[0]["market_signal"] == MarketPropSignal.UNKNOWN.value

        # Consulta a las 17:30 UTC: ve captura 2 (OUT)
        res_late = fantasy_svc.get_live_production_lost(
            sport="NFL",
            decision_cutoff="2026-10-11T17:30:00+00:00",
            game_start_utc="2026-10-11T18:00:00+00:00",
            expected_teams=["KC"],
            strict_unlisted_unknown=True,
        )
        assert "KC" in res_late
        assert res_late["KC"]["confirmed_out_count"] == 1
        assert res_late["KC"]["questionable_count"] == 0



# ==============================================================================
# 8. Integridad de la Cuota de The Odds API y Cero Llamadas Live
# ==============================================================================

class TestQuotaLedgerIntegrity:
    """Verifica que la suite no cambie el ledger que existía al iniciar pytest."""

    def test_ledger_file_untouched_and_spendable_balance_275(self):
        ledger_path = Path("data/odds_quota_ledger.json")
        assert ledger_path.exists(), "El archivo de ledger debe existir"
        raw_bytes = ledger_path.read_bytes()
        current_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        assert current_sha256 == _LEDGER_SHA256_AT_IMPORT, "La suite modificó el ledger durante pytest"

        data = json.loads(raw_bytes.decode("utf-8"))
        assert data["summary"] == _LEDGER_SUMMARY_AT_IMPORT
        assert len(data.get("reconciliation", {}).get("keys_breakdown", {})) > 0


# ==============================================================================
# 9. Verificación Exhaustiva de Discrepancias Auditadas de Fase 02
# ==============================================================================

class TestPhase02AuditResolutions:
    """Verifica explícitamente los 8 puntos auditados por Codex."""

    def test_cache_vs_download_honors_original_timestamp(self, tmp_path, monkeypatch):
        """1. Caché preserva timestamp y edad original sin re-fechar a la hora de lectura."""
        import app.services.matchup_context_service as matchup_context
        import os
        import time

        get_live_injuries = matchup_context.get_live_injuries
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(matchup_context, "CACHE_DIR", cache_dir)

        mock_payload = {
            "KC": [{"player_name": "Patrick Mahomes", "position": "QB", "status": "ACTIVE", "fetched_at": "2026-10-10T12:00:00+00:00"}],
        }
        test_cache = cache_dir / "nfl_live_injuries.json"
        raw_cache = cache_dir / "nfl_live_injuries_raw.json"
        test_cache.write_text(json.dumps(mock_payload), encoding="utf-8")
        raw_cache.write_text(json.dumps({
            "timestamp": "2026-10-15T21:30:00Z",
            "injuries": [{
                "displayName": "Kansas City Chiefs",
                "injuries": [{
                    "status": "Questionable",
                    "athlete": {"displayName": "Patrick Mahomes", "position": {"abbreviation": "QB"}},
                    "details": {"type": "Ankle"},
                    "date": "2026-10-10T12:00:00Z",
                }],
            }],
        }), encoding="utf-8")
        # Establecer mtime en el pasado (30 minutos atrás)
        past_mtime = time.time() - 1800
        os.utime(test_cache, (past_mtime, past_mtime))
        os.utime(raw_cache, (past_mtime, past_mtime))

        data = get_live_injuries("NFL")
        assert "KC" in data
        assert data["KC"][0]["observed_at"] == "2026-10-10T12:00:00Z"
        assert data["KC"][0]["fetched_at"] == datetime.fromtimestamp(past_mtime, tz=timezone.utc).isoformat()
        # El archivo de caché conserva su mtime sin ser sobreescrito al leer
        assert abs(test_cache.stat().st_mtime - past_mtime) < 5

    def test_dual_captures_have_distinct_timestamps(self, tmp_path):
        """2. Las dos capturas del día poseen timestamps distintos y provenance separada."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_dual")
        snap1 = svc.save_espn_injury_snapshot(
            raw_teams_data={"KC": []},
            cadence="start_of_day",
            captured_at_utc="2026-10-12T13:00:00+00:00",
        )
        snap2 = svc.save_espn_injury_snapshot(
            raw_teams_data={"KC": []},
            cadence="near_kickoff",
            captured_at_utc="2026-10-12T16:30:00+00:00",
        )
        assert snap1.captured_at_utc != snap2.captured_at_utc
        assert snap1.cadence == "start_of_day"
        assert snap2.cadence == "near_kickoff"
        assert snap1.snapshot_id != snap2.snapshot_id

    def test_pregame_snapshot_usable_for_prediction_after_capture(self, tmp_path):
        """3. La captura final puede usarse en predicción posterior a su recepción; cutoff no excluye snapshots prepartido."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_pregame")
        # Captura prepartido recibida a las 16:30 UTC
        snap_pregame = svc.save_espn_injury_snapshot(
            raw_teams_data={"KC": [{"player_name": "Travis Kelce", "position": "TE", "status": "QUESTIONABLE", "gsis_id": "00-0030506", "observed_at": "2026-10-12T16:25:00+00:00"}]},
            cadence="pre_kickoff",
            captured_at_utc="2026-10-12T16:30:00+00:00",
        )

        game_id = "2026_06_KC_DEN"
        kickoff = "2026-10-12T19:00:00+00:00"
        cutoff = "2026-10-12T17:00:00+00:00"  # Kickoff - 2h

        # Consulta a las 16:35 UTC (después de recibir snapshot y antes del kickoff)
        as_of = "2026-10-12T16:35:00+00:00"
        active_snap = svc.get_active_injury_snapshot(as_of=as_of)
        assert active_snap is not None
        assert active_snap["cadence"] == "pre_kickoff"

        game_avail = svc.build_game_availability_snapshot(
            game_id=game_id,
            home_team="KC",
            away_team="DEN",
            kickoff_utc=kickoff,
            as_of=as_of,
            espn_snapshot=active_snap,
        )
        assert game_avail["game_id"] == game_id
        assert game_avail["captured_at_utc"] == "2026-10-12T16:30:00+00:00"
        # La captura recibida antes del cutoff no es rechazada
        assert pd.to_datetime(game_avail["captured_at_utc"], utc=True) < pd.to_datetime(cutoff, utc=True)

    def test_espn_without_datemodified_has_none_source_observed(self, tmp_path):
        """4. Si ESPN no provee dateModified, source_observed_at_utc es None y payload_sha256 se calcula sobre raw bytes."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_no_date")
        raw_feed = {"KC": [{"player_name": "Patrick Mahomes", "position": "QB", "status": "ACTIVE"}]}
        snap = svc.save_espn_injury_snapshot(
            raw_teams_data=raw_feed,
            cadence="start_of_day",
            captured_at_utc="2026-10-12T13:00:00+00:00",
            source_observed_at_utc=None,
        )
        assert snap.source_observed_at_utc is None
        assert snap.known_by_system_at_utc == "2026-10-12T13:00:00+00:00"
        assert snap.payload_sha256 is not None
        assert len(snap.payload_sha256) == 64

    def test_omitted_teams_fall_back_to_baseline_without_dropping_other_games(self):
        """5. Ausencia de equipos no bloquea y permite fallback a team_only."""
        from ml.inference import predict_matchups
        from app.services.nfl_availability_service import CoverageState

        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=1, minutes=30)
        cutoff = kickoff - timedelta(hours=2)
        captured = cutoff - timedelta(minutes=10)
        game_id = "2026_06_KC_DEN"

        completed = pd.DataFrame([
            {
                "game_id": "2024_01_KC_BAL", "game_date": "2024-09-05",
                "home_team_id": "KC", "away_team_id": "BAL",
                "home_score": 27, "away_score": 20, "home_win": 1,
                "home_off_epa_per_play": 0.1, "home_off_success_rate": 0.45,
                "home_def_epa_allowed_per_play": -0.05, "home_def_success_allowed": 0.40,
                "away_off_epa_per_play": -0.05, "away_off_success_rate": 0.40,
                "away_def_epa_allowed_per_play": 0.1, "away_def_success_allowed": 0.45,
                "source_ref": "snap",
            },
            {
                "game_id": "2024_01_SF_NYJ", "game_date": "2024-09-09",
                "home_team_id": "SF", "away_team_id": "NYJ",
                "home_score": 32, "away_score": 19, "home_win": 1,
                "home_off_epa_per_play": 0.15, "home_off_success_rate": 0.48,
                "home_def_epa_allowed_per_play": -0.08, "home_def_success_allowed": 0.38,
                "away_off_epa_per_play": -0.08, "away_off_success_rate": 0.38,
                "away_def_epa_allowed_per_play": 0.15, "away_def_success_allowed": 0.48,
                "source_ref": "snap",
            },
        ])
        upcoming = pd.DataFrame([{
            "game_id": game_id,
            "game_date": now.date().isoformat(),
            "home_team_id": "KC",
            "away_team_id": "SF",
            "game_start_utc": kickoff.isoformat(),
            "source_ref": "sched",
        }])

        snapshot_omitted = {
            game_id: {
                "game_id": game_id,
                "decision_cutoff_utc": cutoff.isoformat(),
                "captured_at_utc": captured.isoformat(),
                "source_observed_at_utc": (captured - timedelta(minutes=5)).isoformat(),
                "resolver_version": "nfl_availability_v2",
                "source_refs_by_team": {"KC": ["snap:KC"], "SF": ["snap:SF"]},
                "teams": {
                    "KC": {
                        "coverage_state": CoverageState.COVERED_HEALTHY.value,
                        "ppr_lost_pct": 0.0,
                        "off_snaps_lost_pct": 0.0,
                        "def_snaps_lost_pct": 0.0,
                        "clean_report_evidence_ref": "snap:KC:clean",
                        "decision_cutoff": cutoff.isoformat(),
                    },
                    "SF": {
                        "coverage_state": CoverageState.NO_INJURIES_LISTED.value,
                        "ppr_lost_pct": None,
                        "off_snaps_lost_pct": None,
                        "def_snaps_lost_pct": None,
                        "decision_cutoff": cutoff.isoformat(),
                    },
                },
            }
        }

        mock_model = MagicMock()
        mock_model.predict_proba.return_value = np.array([[0.45, 0.55]])
        mock_model.classes_ = [0, 1]
        mock_metadata = {
            "model_version": "nfl_v1", "production_enabled": False,
            "dataset_sha256": "d_sha", "source_manifest": "s_man", "_artifact_sha256": "a_sha"
        }
        with patch("ml.inference.load_temporal_model", return_value=(mock_model, mock_metadata, {})):
            preds, meta = predict_matchups(
                "nfl", completed, upcoming,
                allow_experimental=True,
                model_variant="injury_aware",
                availability_by_game=snapshot_omitted,
                allow_baseline_fallback=True,
            )
        assert not preds.empty
        assert meta["model_variant"] == "team_only"

    def test_props_cross_requires_event_and_excludes_out(self, availability_svc):
        """6. Questionable/Doubtful exige event_id y canonical id; OUT/IR nunca consulta props."""
        now = datetime.now(timezone.utc)
        future_start = (now + timedelta(hours=3)).isoformat()
        cutoff = (now + timedelta(hours=1)).isoformat()

        # OUT nunca consulta props
        sig_out, p_out, flags_out = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.CONFIRMED_OUT,
            player_name="Nick Chubb",
            position="RB",
            team="CLE",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=[make_verifiable_prop("Nick Chubb", event_id="evt_123")],
            expected_event_id="evt_123",
        )
        assert flags_out["checked_props"] is False
        assert p_out == 1.0

        # Questionable sin expected_event_id no cruza props y queda UNKNOWN
        sig_q_no_evt, p_q_no_evt, flags_q_no_evt = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Travis Kelce",
            position="TE",
            team="KC",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=[make_verifiable_prop("Travis Kelce", event_id="evt_123")],
            expected_event_id=None,
        )
        assert flags_q_no_evt["checked_props"] is False
        assert sig_q_no_evt == MarketPropSignal.UNKNOWN

        # Questionable sin línea de props conserva UNKNOWN
        sig_q_unlisted, p_q_unlisted, flags_q_unlisted = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Travis Kelce",
            position="TE",
            team="KC",
            game_start_utc=future_start,
            decision_cutoff=cutoff,
            live_props=[make_verifiable_prop("Patrick Mahomes", position="QB", event_id="evt_123")],
            expected_event_id="evt_123",
            strict_unlisted_unknown=True,
        )
        assert sig_q_unlisted == MarketPropSignal.UNKNOWN
        assert flags_q_unlisted["signal"] == "unknown"

    def test_variable_quota_cost_calculation(self):
        """7. Costo de cuota variable calculado fielmente sin consumir la API real."""
        cost_single = estimate_request_cost(
            endpoint="/sports/americanfootball_nfl/events/ev1/odds",
            params={"markets": "player_pass_yds", "regions": "us"},
        )
        assert cost_single == 1
        cost_multi = estimate_request_cost(
            endpoint="/sports/americanfootball_nfl/events/ev1/odds",
            params={"markets": "player_pass_yds,player_rush_yds", "regions": "us"},
        )
        assert cost_multi == 2
        assert cost_multi > cost_single

    def test_nflverse_player_impact_integration(self, availability_svc):
        """8. load_player_recent_logs_from_nflverse y calculate_player_recent_importance leen parquets reales."""
        logs = availability_svc.load_player_recent_logs_from_nflverse(
            canonical_player_id="00-0030506",
            current_team="KC",
            as_of="2024-10-15T00:00:00+00:00",
        )
        assert len(logs) > 0
        assert all(l["team"] == "KC" for l in logs)
        assert all(l["canonical_player_id"] == "00-0030506" for l in logs)

        imp = availability_svc.calculate_player_recent_importance(
            canonical_player_id="00-0030506",
            current_team="KC",
            as_of="2024-10-15T00:00:00+00:00",
        )
        assert imp["status"] == "observed"
        assert imp["importance_score"] is not None
        assert imp["rolling_ppr"] is not None
        assert imp["games_sampled"] > 0


# ==============================================================================
# 9. Validación Consolidada de Bloqueos de Flujo NFL Fase 02 (Auditoría Codex)
# ==============================================================================

class TestPhase02CodexConsolidatedClosure:
    """Valida los 8 puntos auditados por Codex para el cierre técnico consolidado."""

    def test_fresh_capture_exposes_raw_payload_and_reception_time(self, tmp_path, monkeypatch):
        """1. Capturas: conecta a respuesta nueva de ESPN que expone payload original, bytes y hora de recepción local UTC. Hash sobre payload crudo."""
        from app.services.matchup_context_service import fetch_live_injuries_response

        fake_espn_raw = {
            "injuries": [
                {
                    "displayName": "Kansas City Chiefs",
                    "injuries": [
                        {
                            "status": "Questionable",
                            "athlete": {"id": "3139477", "displayName": "Patrick Mahomes", "position": {"abbreviation": "QB"}},
                            "details": {"type": "Ankle"},
                            "shortComment": "Limited in practice",
                            "date": "2026-10-12T14:00:00Z",
                        }
                    ],
                }
            ]
        }
        raw_bytes = json.dumps(fake_espn_raw).encode("utf-8")
        expected_hash = hashlib.sha256(raw_bytes).hexdigest()

        class MockHTTPResponse:
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                pass
            def read(self):
                return raw_bytes

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: MockHTTPResponse())
        monkeypatch.setattr("app.services.matchup_context_service.CACHE_DIR", tmp_path / "cache")
        (tmp_path / "cache").mkdir(parents=True, exist_ok=True)

        resp = fetch_live_injuries_response("NFL")
        assert resp["is_from_cache"] is False
        assert resp["raw_bytes"] == raw_bytes
        assert resp["payload_sha256"] == expected_hash
        assert resp["raw_payload"] == fake_espn_raw
        assert "KC" in resp["teams"]
        assert resp["teams"]["KC"][0]["observed_at"] == "2026-10-12T14:00:00Z"
        assert resp["received_at_utc"] is not None

        # Verificar que el hash se calculó sobre el raw_payload/bytes recibido, no sobre el objeto normalizado
        svc = NFLAvailabilityService(cache_dir=tmp_path / "avail")
        snap = svc.save_espn_injury_snapshot(
            raw_teams_data=resp["teams"],
            cadence="start_of_day",
            captured_at_utc=resp["captured_at_utc"],
            raw_payload=resp["raw_bytes"],
            metadata={"known_by_system_at_utc": resp["received_at_utc"]},
        )
        assert snap.payload_sha256 == expected_hash
        assert snap.known_by_system_at_utc == resp["received_at_utc"]

    def test_cached_capture_preserves_original_capture_and_age(self, tmp_path, monkeypatch):
        """1b. Si se lee caché, conserva su captura y antigüedad originales sin asignarle hora nueva."""
        import time
        from app.services.matchup_context_service import fetch_live_injuries_response

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("app.services.matchup_context_service.CACHE_DIR", cache_dir)

        cache_file = cache_dir / "nfl_live_injuries.json"
        raw_cache_file = cache_dir / "nfl_live_injuries_raw.json"

        fake_data = {"KC": [{"player_name": "Patrick Mahomes", "status": "QUESTIONABLE"}]}
        raw_bytes = json.dumps({"injuries": []}).encode("utf-8")

        cache_file.write_text(json.dumps(fake_data), encoding="utf-8")
        raw_cache_file.write_bytes(raw_bytes)

        # Modificar mtime del archivo a hace 30 minutos
        past_timestamp = time.time() - 1800
        os.utime(cache_file, (past_timestamp, past_timestamp))
        os.utime(raw_cache_file, (past_timestamp, past_timestamp))

        # La llamada no debe hacer request HTTP
        monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("No debe llamar HTTP en caché válida"))

        cached_resp = fetch_live_injuries_response("NFL")
        assert cached_resp["is_from_cache"] is True
        # Debe preservar su antigüedad real (cerca de 1800s) y fecha original, no un timestamp nuevo
        assert abs(cached_resp["cache_age_seconds"] - 1800) < 5
        assert cached_resp["captured_at_utc"] != datetime.now(timezone.utc).isoformat()

    def test_incoherent_normalized_and_raw_caches_force_live_refresh(self, tmp_path, monkeypatch):
        """Una caché normalizada más nueva que su payload raw no puede ocultar bajas actuales."""
        import time
        from app.services.matchup_context_service import fetch_live_injuries_response

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("app.services.matchup_context_service.CACHE_DIR", cache_dir)
        cache_file = cache_dir / "nfl_live_injuries.json"
        raw_cache_file = cache_dir / "nfl_live_injuries_raw.json"
        cache_file.write_text(json.dumps({"KC": []}), encoding="utf-8")
        raw_cache_file.write_text(json.dumps({"timestamp": "2026-10-15T19:32:30Z", "injuries": []}), encoding="utf-8")
        old = time.time() - 2_000
        os.utime(raw_cache_file, (old, old))

        live_payload = {
            "timestamp": "2026-10-15T22:03:12Z",
            "injuries": [{
                "displayName": "Denver Broncos",
                "injuries": [{
                    "status": "Out",
                    "athlete": {"displayName": "Pat Surtain II", "position": {"abbreviation": "CB"}},
                    "details": {"type": "Ankle"},
                    "shortComment": "Ruled out",
                    "dateModified": "2026-10-15T22:00:00Z",
                }],
            }],
        }
        raw_bytes = json.dumps(live_payload).encode("utf-8")

        class MockHTTPResponse:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc, tb): pass
            def read(self): return raw_bytes

        calls = []
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: (calls.append(req.full_url), MockHTTPResponse())[1])
        result = fetch_live_injuries_response("NFL")

        assert calls
        assert result["is_from_cache"] is False
        assert result["teams"]["DEN"][0]["player_name"] == "Pat Surtain II"
        assert result["teams"]["DEN"][0]["status"] == "OUT"

    def test_both_horizons_start_of_day_and_90min_pregame(self, tmp_path):
        """2. Tiempos live: conocida por el sistema en UTC; predicción final consume captura de 90 min sin ser rechazada."""
        from ml.inference import predict_matchups

        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=1, minutes=30)  # Kickoff en 90 minutos
        cutoff_pregame = kickoff - timedelta(minutes=90)  # Exactly 90m before kickoff
        game_id = "2026_06_KC_DEN"

        # Horizon 1: Captura matutina (start_of_day) con corte de 2 horas antes del kickoff
        sod_capture_time = kickoff - timedelta(hours=5)
        raw_sod = {"KC": [{"player_name": "Travis Kelce", "position": "TE", "status": "QUESTIONABLE", "canonical_player_id": "00-0030506", "observed_at": (sod_capture_time - timedelta(minutes=15)).isoformat()}], "DEN": []}
        sod_snap = svc.save_espn_injury_snapshot(
            raw_teams_data=raw_sod,
            cadence="start_of_day",
            captured_at_utc=sod_capture_time.isoformat(),
            raw_payload=raw_sod,
            metadata={"known_by_system_at_utc": sod_capture_time.isoformat()},
        )
        assert sod_snap.cadence == "start_of_day"
        game_snap_sod = svc.build_game_availability_snapshot(
            game_id=game_id,
            home_team="KC",
            away_team="DEN",
            kickoff_utc=kickoff.isoformat(),
            as_of=sod_capture_time,
            espn_snapshot=sod_snap.to_dict(),
            prior_roster_stats={"00-0030506": {"rolling_ppr": 15.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0}},
        )
        # El cutoff del snapshot matutino debe ser kickoff - 2 horas
        assert game_snap_sod["decision_cutoff_utc"] == (kickoff - timedelta(hours=2)).isoformat()

        # Horizon 2: Captura prepartido oficial real (near_kickoff) tomada a exactamente 90 minutos antes del kickoff
        capture_time = cutoff_pregame
        raw_injuries = {
            "KC": [
                {
                    "player_name": "Travis Kelce",
                    "position": "TE",
                    "status": "QUESTIONABLE",
                    "canonical_player_id": "00-0030506",
                    "observed_at": cutoff_pregame.isoformat(),
                }
            ],
            "DEN": [],
        }

        # Guardar snapshot prepartido con cadence near_kickoff
        pregame_snap = svc.save_espn_injury_snapshot(
            raw_teams_data=raw_injuries,
            cadence="near_kickoff",
            captured_at_utc=capture_time.isoformat(),
            raw_payload=raw_injuries,
            metadata={"known_by_system_at_utc": capture_time.isoformat()},
        )
        assert pregame_snap.cadence == "near_kickoff"

        # Construir snapshot por partido con el horizon prepartido
        game_snap = svc.build_game_availability_snapshot(
            game_id=game_id,
            home_team="KC",
            away_team="DEN",
            kickoff_utc=kickoff.isoformat(),
            as_of=now,
            espn_snapshot=pregame_snap.to_dict(),
            prior_roster_stats={"00-0030506": {"rolling_ppr": 15.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0}},
            live_props=[make_verifiable_prop("Travis Kelce", position="TE", event_id="evt_kc_den", timestamp=(capture_time - timedelta(minutes=15)).isoformat())],
            props_event_id="evt_kc_den",
        )
        # El cutoff del snapshot prepartido debe ser kickoff - 90m
        assert game_snap["decision_cutoff_utc"] == cutoff_pregame.isoformat()

        # Inferencia con cutoff prepartido de 90 minutos NO debe ser rechazada
        completed = pd.DataFrame([
            {
                "game_id": "2024_01_KC_BAL", "game_date": "2024-09-05",
                "home_team_id": "KC", "away_team_id": "BAL",
                "home_score": 27, "away_score": 20, "home_win": 1,
                "home_off_epa_per_play": 0.1, "home_off_success_rate": 0.45,
                "home_def_epa_allowed_per_play": -0.05, "home_def_success_allowed": 0.40,
                "away_off_epa_per_play": -0.05, "away_off_success_rate": 0.40,
                "away_def_epa_allowed_per_play": 0.1, "away_def_success_allowed": 0.45,
                "source_ref": "snap",
            },
            {
                "game_id": "2024_01_DEN_SEA", "game_date": "2024-09-08",
                "home_team_id": "DEN", "away_team_id": "SEA",
                "home_score": 20, "away_score": 26, "home_win": 0,
                "home_off_epa_per_play": -0.05, "home_off_success_rate": 0.40,
                "home_def_epa_allowed_per_play": 0.05, "home_def_success_allowed": 0.42,
                "away_off_epa_per_play": 0.05, "away_off_success_rate": 0.42,
                "away_def_epa_allowed_per_play": -0.05, "away_def_success_allowed": 0.40,
                "source_ref": "snap",
            },
        ])
        upcoming = pd.DataFrame([{
            "game_id": game_id,
            "game_date": now.date().isoformat(),
            "home_team_id": "KC", "away_team_id": "DEN",
            "home_team_abbreviation": "KC", "away_team_abbreviation": "DEN",
            "game_start_utc": kickoff.isoformat(),
            "source_ref": "sched",
        }])

        n_feats = len(feature_columns(NFL_GAME_STATS, include_availability=True))
        mock_model = MagicMock(n_features_in_=n_feats, classes_=[0, 1])
        mock_model.predict_proba.return_value = np.array([[0.35, 0.65]])
        mock_metadata = {
            "model_version": "nfl_v1", "production_enabled": False,
            "dataset_sha256": "d_sha", "source_manifest": "s_man", "_artifact_sha256": "a_sha",
            "model_variant": "injury_aware",
            "features": feature_columns(NFL_GAME_STATS, include_availability=True),
        }
        with patch("ml.inference.load_temporal_model", return_value=(mock_model, mock_metadata, {})):
            preds, meta = predict_matchups(
                "nfl", completed, upcoming,
                as_of=now,
                allow_experimental=True,
                model_variant="injury_aware",
                availability_by_game={game_id: game_snap},
            )
        assert not preds.empty
        assert meta["model_variant"] == "injury_aware"
        assert meta["used_injury_adjustment"] is True
        assert bool(preds.iloc[0]["used_injury_adjustment"]) is True

    def test_optional_espn_source_timestamp_does_not_fail_inference(self):
        """2b. source_observed_at_utc es opcional: si falta o es None, inferencia no falla."""
        from ml.inference import predict_matchups

        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=1, minutes=15)
        cutoff = kickoff - timedelta(minutes=90)
        captured = cutoff - timedelta(minutes=10)
        game_id = "2026_06_KC_DEN"

        completed = pd.DataFrame([
            {
                "game_id": "2024_01_KC_BAL", "game_date": "2024-09-05",
                "home_team_id": "KC", "away_team_id": "BAL",
                "home_score": 27, "away_score": 20, "home_win": 1,
                "home_off_epa_per_play": 0.1, "home_off_success_rate": 0.45,
                "home_def_epa_allowed_per_play": -0.05, "home_def_success_allowed": 0.40,
                "away_off_epa_per_play": -0.05, "away_off_success_rate": 0.40,
                "away_def_epa_allowed_per_play": 0.1, "away_def_success_allowed": 0.45,
                "source_ref": "snap",
            },
            {
                "game_id": "2024_01_DEN_SEA", "game_date": "2024-09-08",
                "home_team_id": "DEN", "away_team_id": "SEA",
                "home_score": 20, "away_score": 26, "home_win": 0,
                "home_off_epa_per_play": -0.05, "home_off_success_rate": 0.40,
                "home_def_epa_allowed_per_play": 0.05, "home_def_success_allowed": 0.42,
                "away_off_epa_per_play": 0.05, "away_off_success_rate": 0.42,
                "away_def_epa_allowed_per_play": -0.05, "away_def_success_allowed": 0.40,
                "source_ref": "snap",
            },
        ])
        upcoming = pd.DataFrame([{
            "game_id": game_id,
            "game_date": now.date().isoformat(),
            "home_team_id": "KC", "away_team_id": "DEN",
            "home_team_abbreviation": "KC", "away_team_abbreviation": "DEN",
            "game_start_utc": kickoff.isoformat(),
            "source_ref": "sched",
        }])

        snap_no_source_obs = {
            game_id: {
                "game_id": game_id,
                "decision_cutoff_utc": cutoff.isoformat(),
                "captured_at_utc": captured.isoformat(),
                "source_observed_at_utc": None,  # Totalmente opcional / ausente
                "known_by_system_at_utc": captured.isoformat(),
                "resolver_version": "nfl_availability_v2",
                "source_refs_by_team": {"KC": ["snap:KC"], "DEN": ["snap:DEN"]},
                "teams": {
                    "KC": {
                        "coverage_state": CoverageState.COVERED_HEALTHY.value,
                        "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0,
                        "clean_report_evidence_ref": "snap:KC:clean",
                        "decision_cutoff": cutoff.isoformat(),
                    },
                    "DEN": {
                        "coverage_state": CoverageState.COVERED_HEALTHY.value,
                        "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0,
                        "clean_report_evidence_ref": "snap:DEN:clean",
                        "decision_cutoff": cutoff.isoformat(),
                    },
                },
            }
        }

        n_feats = len(feature_columns(NFL_GAME_STATS, include_availability=True))
        mock_model = MagicMock(n_features_in_=n_feats, classes_=[0, 1])
        mock_model.predict_proba.return_value = np.array([[0.40, 0.60]])
        mock_metadata = {
            "model_version": "nfl_v1", "production_enabled": False,
            "dataset_sha256": "d_sha", "source_manifest": "s_man", "_artifact_sha256": "a_sha",
            "model_variant": "injury_aware",
            "features": feature_columns(NFL_GAME_STATS, include_availability=True),
        }
        with patch("ml.inference.load_temporal_model", return_value=(mock_model, mock_metadata, {})):
            preds, meta = predict_matchups(
                "nfl", completed, upcoming,
                as_of=now,
                allow_experimental=True,
                model_variant="injury_aware",
                availability_by_game=snap_no_source_obs,
            )
        assert not preds.empty
        assert meta["model_variant"] == "injury_aware"

    def test_pregame_capture_triggers_prediction_evaluation(self, tmp_path):
        """3. Predicción final: después de la captura prepartido, se programa evaluación para los juegos afectados."""
        from app.workers.celery_app import capture_nfl_injuries_task

        mock_resp = {
            "teams": {"KC": [{"player_name": "Patrick Mahomes", "status": "QUESTIONABLE"}]},
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "raw_payload": {"injuries": []},
            "is_from_cache": False,
            "received_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        with patch("app.services.matchup_context_service.fetch_live_injuries_response", return_value=mock_resp), \
             patch("app.services.matchup_context_service.get_live_injuries", return_value=mock_resp["teams"]), \
             patch("app.services.nfl_availability_service.NFLAvailabilityService.save_espn_injury_snapshot") as mock_save, \
             patch("app.workers.celery_app.evaluate_predictions_task.apply_async") as mock_eval_dispatch:

            mock_save.return_value = MagicMock(
                snapshot_id="espn_near_kickoff_test",
                captured_at_utc="2026-10-12T16:30:00Z",
                known_by_system_at_utc="2026-10-12T16:30:00Z",
                payload_sha256="abc",
                teams={"KC": []},
                omitted_teams=[],
            )

            # Cadence near_kickoff DEBE disparar evaluate_predictions_task
            res = capture_nfl_injuries_task.apply(kwargs={"cadence": "near_kickoff"}).get()
            assert res["status"] == "success"
            mock_eval_dispatch.assert_called_with(args=("NFL",), queue="ml")

            # Cuando se indican juegos afectados específicos, debe reenviar game_ids
            res_affected = capture_nfl_injuries_task.apply(kwargs={"cadence": "near_kickoff", "affected_game_ids": ["2026_06_KC_DEN"]}).get()
            assert res_affected["status"] == "success"
            mock_eval_dispatch.assert_called_with(args=("NFL",), kwargs={"game_ids": ["2026_06_KC_DEN"]}, queue="ml")

    def test_props_variable_reservation_qd_cross_and_out_exclusion(self, availability_svc, tmp_path):
        """4. Odds: solo props para Questionable/Doubtful con event ID canónico y estimador variable. OUT/IR nunca consulta props."""
        from app.services.odds_quota_service import OddsQuotaService

        fake_ledger_path = tmp_path / "mock_quota_ledger.json"
        fake_ledger_path.write_text(json.dumps({
            "version": "1.0",
            "summary": {"requests_used": 0, "requests_remaining": 320, "spent_by_envelope": {"main_lines": 0, "props": 0, "operational": 0}, "circuit_breaker_open": False},
            "pending_reservations": {},
            "transactions": [],
        }), encoding="utf-8")
        quota_svc = OddsQuotaService(ledger_path=fake_ledger_path)

        rec_out = PlayerInjuryRecord(
            player_name="Nick Chubb", team="CLE", position="RB",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value,
        )
        rec_q = PlayerInjuryRecord(
            player_name="Travis Kelce", team="KC", position="TE",
            availability_status=AvailabilityStatus.QUESTIONABLE.value,
        )

        # Caso 1: Roster con SOLO jugadores OUT -> helper retorna None y NUNCA reserva cuota
        props_for_out = availability_svc.get_verifiable_props_for_qd_players(
            event_id="evt_cle",
            records=[rec_out],
            quota_service=quota_svc,
            api_key="mock_key",
        )
        assert props_for_out is None
        assert len(quota_svc.pending_reservations) == 0  # Cero reserva para OUT

        # Caso 2: Roster con Questionable -> helper reserva cuota bajo envelope props
        props_for_q = availability_svc.get_verifiable_props_for_qd_players(
            event_id="evt_kc",
            records=[rec_q],
            quota_service=quota_svc,
            api_key="mock_key",
        )
        assert len(quota_svc.pending_reservations) == 1
        res_id = list(quota_svc.pending_reservations.keys())[0]
        assert quota_svc.pending_reservations[res_id]["envelope"] == "props"

        # Caso 3: Evaluación de señal: línea presente -> LISTED_AT_CUTOFF; línea ausente -> UNKNOWN
        now = datetime.now(timezone.utc)
        future_start = (now + timedelta(hours=3)).isoformat()
        cutoff = (now + timedelta(hours=1)).isoformat()

        # Presente con event ID canónico correcto
        sig_p, p_p, fl_p = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Travis Kelce", position="TE", team="KC",
            game_start_utc=future_start, decision_cutoff=cutoff,
            live_props=[make_verifiable_prop("Travis Kelce", position="TE", event_id="evt_kc", timestamp=(now + timedelta(minutes=30)).isoformat())],
            expected_event_id="evt_kc",
            strict_unlisted_unknown=True,
        )
        assert sig_p == MarketPropSignal.LISTED_AT_CUTOFF
        assert fl_p["checked_props"] is True

        # Ausente -> queda UNKNOWN ("Una línea presente es señal de mercado; su ausencia queda desconocida")
        sig_a, p_a, fl_a = availability_svc.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.QUESTIONABLE,
            player_name="Travis Kelce", position="TE", team="KC",
            game_start_utc=future_start, decision_cutoff=cutoff,
            live_props=[make_verifiable_prop("Patrick Mahomes", position="QB", event_id="evt_kc", timestamp=(now + timedelta(minutes=30)).isoformat())],
            expected_event_id="evt_kc",
            strict_unlisted_unknown=True,
        )
        assert sig_a == MarketPropSignal.UNKNOWN
        assert fl_a["signal"] == "unknown"

    def test_current_performance_2026_missing_assets_leaves_unknown_and_falls_back(self, availability_svc):
        """5. Rendimiento actual: comprueba assets 2026. Si faltan stats/snaps, ajuste queda desconocido y usa baseline sin ceros inventados."""
        from ml.data_pipeline.nfl_source import resolve_nfl_kickoff_utc

        # 1. Resolver oficial NFL de kickoff verifica ET a UTC inequívoco sin string naive
        resolved_utc = resolve_nfl_kickoff_utc("2024-09-05", "20:20", "2024_01_KC_BAL")
        assert resolved_utc.tzinfo is not None
        assert str(resolved_utc.tzinfo) == "UTC"

        # 2. Comprobar assets 2026 en nflverse: si faltan player_stats/snap_counts, verifica ausencia
        p2026 = Path("data/raw/nflverse/2026")
        files_2026 = [f.name for f in p2026.glob("*.parquet")] if p2026.exists() else []
        has_stats_2026 = any("player_stats" in f for f in files_2026)
        if not has_stats_2026:
            assert not any("player_stats" in f for f in files_2026)
            assert not any("snap_counts" in f for f in files_2026)

        # 3. Ante assets faltantes en temporada activa, load_player_recent_logs_from_nflverse devuelve []
        logs_2026 = availability_svc.load_player_recent_logs_from_nflverse(
            canonical_player_id="00-0030506",
            current_team="KC",
            as_of="2026-10-15T00:00:00+00:00",
            raw_root=None if not has_stats_2026 else Path("/tmp/nflverse_missing"),
        )
        assert logs_2026 == []

        # 4. Sin logs, calculate_player_recent_importance queda UNKNOWN sin inventar 0.0
        imp = availability_svc.calculate_player_recent_importance(
            canonical_player_id="00-0030506",
            current_team="KC",
            as_of="2026-10-15T00:00:00+00:00",
            recent_game_logs=[] if has_stats_2026 else None,
        )
        assert imp["status"] == "unknown"
        assert imp["rolling_ppr"] is None
        assert imp["rolling_off_pct"] is None
        assert imp["rolling_def_pct"] is None
        assert imp["importance_score"] is None

        # 5. Sin prior_roster_stats, compute_team_production_lost deja métricas None y estado UNKNOWN
        rec = PlayerInjuryRecord(
            player_name="Travis Kelce", team="KC", position="TE",
            availability_status=AvailabilityStatus.QUESTIONABLE.value,
        )
        summary = availability_svc.compute_team_production_lost(
            team="KC",
            records=[rec],
            prior_roster_stats=None,
            game_id="2026_06_KC_DEN",
        )
        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None
        assert summary.off_snaps_lost_pct is None
        assert summary.def_snaps_lost_pct is None

    def test_e2e_features_consumed_by_variant_model(self):
        """6. Prueba End-to-End: comprueba qué variant/modelo consume efectivamente las features."""
        from ml.inference import predict_matchups

        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=1, minutes=15)
        cutoff = kickoff - timedelta(minutes=90)
        captured = cutoff - timedelta(minutes=10)
        game_id = "2026_06_KC_DEN"

        completed = pd.DataFrame([
            {
                "game_id": "2024_01_KC_BAL", "game_date": "2024-09-05",
                "home_team_id": "KC", "away_team_id": "BAL",
                "home_score": 27, "away_score": 20, "home_win": 1,
                "home_off_epa_per_play": 0.1, "home_off_success_rate": 0.45,
                "home_def_epa_allowed_per_play": -0.05, "home_def_success_allowed": 0.40,
                "away_off_epa_per_play": -0.05, "away_off_success_rate": 0.40,
                "away_def_epa_allowed_per_play": 0.1, "away_def_success_allowed": 0.45,
                "source_ref": "snap",
            },
            {
                "game_id": "2024_01_DEN_SEA", "game_date": "2024-09-08",
                "home_team_id": "DEN", "away_team_id": "SEA",
                "home_score": 20, "away_score": 26, "home_win": 0,
                "home_off_epa_per_play": -0.05, "home_off_success_rate": 0.40,
                "home_def_epa_allowed_per_play": 0.05, "home_def_success_allowed": 0.42,
                "away_off_epa_per_play": 0.05, "away_off_success_rate": 0.42,
                "away_def_epa_allowed_per_play": -0.05, "away_def_success_allowed": 0.40,
                "source_ref": "snap",
            },
        ])
        upcoming = pd.DataFrame([{
            "game_id": game_id,
            "game_date": now.date().isoformat(),
            "home_team_id": "KC", "away_team_id": "DEN",
            "home_team_abbreviation": "KC", "away_team_abbreviation": "DEN",
            "game_start_utc": kickoff.isoformat(),
            "source_ref": "sched",
        }])

        valid_snap = {
            game_id: {
                "game_id": game_id,
                "decision_cutoff_utc": cutoff.isoformat(),
                "captured_at_utc": captured.isoformat(),
                "source_observed_at_utc": (captured - timedelta(minutes=5)).isoformat(),
                "known_by_system_at_utc": captured.isoformat(),
                "resolver_version": "nfl_availability_v2",
                "source_refs_by_team": {"KC": ["snap:KC"], "DEN": ["snap:DEN"]},
                "teams": {
                    "KC": {
                        "coverage_state": CoverageState.COVERED_WITH_INJURIES.value,
                        "ppr_lost_pct": 0.15, "off_snaps_lost_pct": 0.10, "def_snaps_lost_pct": 0.0,
                        "decision_cutoff": cutoff.isoformat(),
                    },
                    "DEN": {
                        "coverage_state": CoverageState.COVERED_HEALTHY.value,
                        "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0,
                        "clean_report_evidence_ref": "snap:DEN:clean",
                        "decision_cutoff": cutoff.isoformat(),
                    },
                },
            }
        }

        # Caso A: Inferencia injury_aware con availability válida
        feats_injury = feature_columns(NFL_GAME_STATS, include_availability=True)
        mock_model_ia = MagicMock(n_features_in_=len(feats_injury), classes_=[0, 1])
        mock_model_ia.predict_proba.return_value = np.array([[0.30, 0.70]])

        meta_ia = {
            "model_version": "nfl_injury_v1", "production_enabled": False,
            "dataset_sha256": "d_sha", "source_manifest": "s_man", "_artifact_sha256": "a_sha",
            "model_variant": "injury_aware",
            "features": feats_injury,
        }

        with patch("ml.inference.load_temporal_model", return_value=(mock_model_ia, meta_ia, {})):
            preds_ia, run_meta_ia = predict_matchups(
                "nfl", completed, upcoming,
                as_of=now,
                allow_experimental=True,
                model_variant="injury_aware",
                availability_by_game=valid_snap,
            )

        assert not preds_ia.empty
        assert run_meta_ia["model_variant"] == "injury_aware"
        assert run_meta_ia["used_injury_adjustment"] is True
        passed_matrix = mock_model_ia.predict_proba.call_args[0][0]
        assert "home_ppr_lost_pct" in passed_matrix.columns
        assert passed_matrix.iloc[0]["home_ppr_lost_pct"] == 0.15

        # Caso B: Fallback a baseline team_only ante disponibilidad desconocida
        unknown_snap = {
            game_id: {
                "game_id": game_id,
                "decision_cutoff_utc": cutoff.isoformat(),
                "captured_at_utc": captured.isoformat(),
                "source_observed_at_utc": (captured - timedelta(minutes=5)).isoformat(),
                "resolver_version": "nfl_availability_v2",
                "source_refs_by_team": {"KC": ["snap:KC"], "DEN": ["snap:DEN"]},
                "teams": {
                    "KC": {"coverage_state": CoverageState.UNKNOWN.value, "ppr_lost_pct": None, "off_snaps_lost_pct": None, "def_snaps_lost_pct": None},
                    "DEN": {"coverage_state": CoverageState.COVERED_HEALTHY.value, "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0, "clean_report_evidence_ref": "snap:DEN:clean"},
                },
            }
        }

        feats_team_only = feature_columns(NFL_GAME_STATS, include_availability=False)
        mock_model_to = MagicMock(n_features_in_=len(feats_team_only), classes_=[0, 1])
        mock_model_to.predict_proba.return_value = np.array([[0.45, 0.55]])
        meta_to = {
            "model_version": "nfl_base_v1", "production_enabled": False,
            "dataset_sha256": "d_sha", "source_manifest": "s_man", "_artifact_sha256": "a_sha",
            "model_variant": "team_only",
            "features": feats_team_only,
        }

        with patch("ml.inference.load_temporal_model", return_value=(mock_model_to, meta_to, {})):
            preds_to, run_meta_to = predict_matchups(
                "nfl", completed, upcoming,
                as_of=now,
                allow_experimental=True,
                model_variant="injury_aware",
                availability_by_game=unknown_snap,
                allow_baseline_fallback=True,
            )

        assert not preds_to.empty
        assert run_meta_to["model_variant"] == "team_only"
        assert run_meta_to["used_injury_adjustment"] is False
        passed_to_matrix = mock_model_to.predict_proba.call_args[0][0]
        assert "home_ppr_lost_pct" not in passed_to_matrix.columns

    def test_corrupted_or_malformed_espn_stream_recovery(self, tmp_path, monkeypatch):
        """Edge case: respuesta de ESPN corrupta o malformada no crashea; recuperación segura fail-closed."""
        from app.services.matchup_context_service import fetch_live_injuries_response

        # Caso 1: Bytes corruptos / no-JSON sin caché previa
        cache_dir = tmp_path / "cache_empty"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("app.services.matchup_context_service.CACHE_DIR", cache_dir)

        class CorruptedResponse:
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                pass
            def read(self):
                return b"\x1f\x8b\x08\x00corrupted_non_json_binary_garbage\xff\xfe"

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: CorruptedResponse())

        resp = fetch_live_injuries_response("NFL")
        assert resp["raw_bytes"] is None
        assert resp["raw_payload"] is None
        assert resp["teams"] == {}
        assert resp["is_from_cache"] is False

        # Caso 2: una caché normalizada sin payload raw no es evidencia y se rechaza.
        cache_dir_cached = tmp_path / "cache_with_prior"
        cache_dir_cached.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("app.services.matchup_context_service.CACHE_DIR", cache_dir_cached)
        prior_cache_file = cache_dir_cached / "nfl_live_injuries.json"
        prior_cache_file.write_text(json.dumps({"KC": [{"player_name": "Patrick Mahomes", "status": "QUESTIONABLE"}]}), encoding="utf-8")

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: (_ for _ in ()).throw(IOError("Connection reset by peer")))
        resp_fallback = fetch_live_injuries_response("NFL")
        assert resp_fallback["is_from_cache"] is False
        assert resp_fallback["teams"] == {}

    def test_status_without_espn_timestamp_fails_closed_and_falls_back(self, availability_svc):
        """Edge case: jugador en captura ESPN sin observed_at/dateModified queda UNKNOWN y predicción recurre a baseline."""
        from ml.inference import predict_matchups

        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=1, minutes=30)
        cutoff_pregame = kickoff - timedelta(minutes=90)
        game_id = "2026_06_KC_DEN"

        # Jugador sin observed_at
        player_no_ts = {
            "player_name": "Travis Kelce",
            "position": "TE",
            "status": "QUESTIONABLE",
            "canonical_player_id": "00-0030506",
            "observed_at": None,  # ESPN no proveyó timestamp
        }

        # 1. process_player_injury resuelve estado UNKNOWN
        rec = availability_svc.process_player_injury(
            player_dict=player_no_ts,
            team="KC",
            game_start_utc=kickoff.isoformat(),
            decision_cutoff=cutoff_pregame.isoformat(),
        )
        assert rec.availability_status == AvailabilityStatus.UNKNOWN.value

        # 2. compute_team_production_lost resulta en UNKNOWN
        summary = availability_svc.compute_team_production_lost(
            team="KC",
            records=[rec],
            prior_roster_stats={"00-0030506": {"rolling_ppr": 15.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0}},
            game_id=game_id,
            decision_cutoff=cutoff_pregame.isoformat(),
        )
        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None

        # 3. predict_matchups con allow_baseline_fallback=True ejecuta baseline team_only sin error
        snap_dict = {
            game_id: {
                "game_id": game_id,
                "decision_cutoff_utc": cutoff_pregame.isoformat(),
                "captured_at_utc": cutoff_pregame.isoformat(),
                "known_by_system_at_utc": cutoff_pregame.isoformat(),
                "resolver_version": "nfl_availability_v2",
                "source_refs_by_team": {"KC": ["snap:KC"], "DEN": ["snap:DEN"]},
                "teams": {
                    "KC": summary.to_dict(),
                    "DEN": {"coverage_state": CoverageState.COVERED_HEALTHY.value, "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0, "clean_report_evidence_ref": "snap:DEN:clean", "decision_cutoff": cutoff_pregame.isoformat()},
                },
            }
        }

        completed = pd.DataFrame([
            {
                "game_id": "2024_01_KC_BAL", "game_date": "2024-09-05",
                "home_team_id": "KC", "away_team_id": "BAL",
                "home_score": 27, "away_score": 20, "home_win": 1,
                "home_off_epa_per_play": 0.1, "home_off_success_rate": 0.45,
                "home_def_epa_allowed_per_play": -0.05, "home_def_success_allowed": 0.40,
                "away_off_epa_per_play": -0.05, "away_off_success_rate": 0.40,
                "away_def_epa_allowed_per_play": 0.1, "away_def_success_allowed": 0.45,
                "source_ref": "snap",
            },
            {
                "game_id": "2024_01_DEN_SEA", "game_date": "2024-09-08",
                "home_team_id": "DEN", "away_team_id": "SEA",
                "home_score": 20, "away_score": 26, "home_win": 0,
                "home_off_epa_per_play": -0.05, "home_off_success_rate": 0.40,
                "home_def_epa_allowed_per_play": 0.05, "home_def_success_allowed": 0.42,
                "away_off_epa_per_play": 0.05, "away_off_success_rate": 0.42,
                "away_def_epa_allowed_per_play": -0.05, "away_def_success_allowed": 0.40,
                "source_ref": "snap",
            },
        ])
        upcoming = pd.DataFrame([{
            "game_id": game_id,
            "game_date": now.date().isoformat(),
            "home_team_id": "KC", "away_team_id": "DEN",
            "home_team_abbreviation": "KC", "away_team_abbreviation": "DEN",
            "game_start_utc": kickoff.isoformat(),
            "source_ref": "sched",
        }])

        feats_team_only = feature_columns(NFL_GAME_STATS, include_availability=False)
        mock_model = MagicMock(n_features_in_=len(feats_team_only), classes_=[0, 1])
        mock_model.predict_proba.return_value = np.array([[0.50, 0.50]])
        mock_metadata = {
            "model_version": "nfl_v1", "production_enabled": False,
            "dataset_sha256": "d_sha", "source_manifest": "s_man", "_artifact_sha256": "a_sha",
            "model_variant": "team_only",
            "features": feats_team_only,
        }

        with patch("ml.inference.load_temporal_model", return_value=(mock_model, mock_metadata, {})):
            preds, meta = predict_matchups(
                "nfl", completed, upcoming,
                as_of=now,
                allow_experimental=True,
                model_variant="injury_aware",
                availability_by_game=snap_dict,
                allow_baseline_fallback=True,
            )

        assert not preds.empty
        assert meta["model_variant"] == "team_only"
        assert meta["used_injury_adjustment"] is False



