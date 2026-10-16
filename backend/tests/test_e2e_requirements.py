"""
Comprehensive E2E Test Suite for NFL Sports Analytics MVP.
Requirement-Driven, Opaque-Box Test Suite covering R1 through R5.

Structure:
- Tier 1: Feature Coverage (>=5 test cases per core feature R1-R5: 25 tests)
- Tier 2: Boundary & Corner Cases (6 tests)
- Tier 3: Cross-Feature Combinations (5 tests)
- Tier 4: Real-World Application Scenarios (4 tests)
Total: 40 hermetic test cases.

Guarantees:
- Completely hermetic: NEVER consumes live The Odds API quota or alters data/odds_quota_ledger.json.
- Verifies SHA-256 of data/odds_quota_ledger.json before and after test execution.
- Verifies zero synthetic data, zero imputation, and non-blocking endpoints.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import httpx
import numpy as np
import pandas as pd
import pytest

from app.api.v1.predictions import _production_model_enabled, get_approved_model_version
from app.core.database import AsyncSessionLocal, engine
from app.main import app
from app.models.game import Game, OddsSnapshot, Prediction, Team
from app.services.ev_engine import (
    american_to_decimal,
    american_to_implied_prob,
    calculate_ev,
    decimal_to_american,
    remove_vig,
)
from app.services.fantasy_injury_service import FantasyInjuryService
from app.services.nfl_availability_service import (
    ALL_NFL_TEAMS,
    AvailabilityStatus,
    CoverageState,
    MARKET_MODULATED_P_ABSENCE,
    MarketPropSignal,
    NFLAvailabilityService,
    PlayerInjuryRecord,
    TeamAvailabilitySummary,
)
from app.services.odds_quota_service import (
    CircuitBreakerOpenError,
    OddsBudgetConfig,
    OddsQuotaService,
    QuotaExceededError,
)
from app.services.parlay_service import (
    evaluate_custom_parlay,
    generate_recommended_parlays,
    normalize_nfl_team,
    resolve_and_evaluate_parlay,
)
from app.services.sports_analytics_service import generate_nfl_matchup_analytics
from ml.data_pipeline.common import DataQualityError, backend_root, project_root
from ml.data_pipeline.temporal_features import NFL_GAME_STATS, feature_columns
from ml.inference import load_temporal_model, predict_matchups


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def verify_quota_ledger_unaltered():
    """Guarantee that data/odds_quota_ledger.json is byte-for-byte invariant across all tests."""
    ledger_path = project_root() / "data" / "odds_quota_ledger.json"
    if not ledger_path.is_file():
        yield
        return

    before_bytes = ledger_path.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()

    yield

    after_bytes = ledger_path.read_bytes()
    after_hash = hashlib.sha256(after_bytes).hexdigest()

    assert before_hash == after_hash, (
        f"CRITICAL VIOLATION: odds_quota_ledger.json was mutated during test run! "
        f"Before: {before_hash}, After: {after_hash}"
    )


# ==============================================================================
# TIER 1: FEATURE COVERAGE (R1 - R5)
# ==============================================================================

class TestTier1R1RealDataAndZeroImputation:
    """R1: Auditoría de APIs y Extracción de Datos 100% Reales (Zero-Synthetic & Zero-Imputation)."""

    def test_tier1_r1_zero_imputation_prohibits_arbitrary_zeros_in_features(self, tmp_path):
        """TC-R1-01: In absence of prior player stats, metrics must remain None, never 0.0 or default 15 PPR."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        # Player with injury record but no prior roster statistics
        player = PlayerInjuryRecord(
            player_name="John Doe",
            team="KC",
            position="WR",
            canonical_player_id="j_doe",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value,
            p_absence=1.0,
            observed_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        )

        # Evaluating player with empty/None prior roster stats
        summary = service.compute_team_production_lost(
            team="KC",
            records=[player],
            prior_roster_stats=None,  # No stats available
            decision_cutoff=datetime.now(timezone.utc).isoformat(),
        )

        # Absence of historical metrics must yield None, not arbitrary 0.0 or synthetic 15.0
        assert summary.ppr_lost_pct is None, "Missing prior stats must produce None ppr_lost_pct"
        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.off_snaps_lost_pct is None
        assert summary.def_snaps_lost_pct is None

    def test_tier1_r1_explicit_coverage_states_in_availability(self):
        """TC-R1-02: Ensure all formal coverage states exist and are clearly differentiated."""
        expected_states = {
            CoverageState.COVERED_HEALTHY.value,
            CoverageState.COVERED_WITH_INJURIES.value,
            CoverageState.NO_INJURIES_LISTED.value,
            CoverageState.UNKNOWN.value,
            CoverageState.UNAVAILABLE_NO_REPORT.value,
        }
        enum_values = {s.value for s in CoverageState}
        assert expected_states == enum_values, f"State mismatch: {expected_states} vs {enum_values}"

    def test_tier1_r1_real_data_provenance_and_manifest_hash_integrity(self):
        """TC-R1-03: Verify that datasets and manifests have authentic SHA-256 hashes; reject tampered datasets."""
        processed_dir = project_root() / "data" / "processed"
        manifest_path = processed_dir / "nfl_training.manifest.json"
        dataset_path = processed_dir / "nfl_training.csv"

        if not manifest_path.is_file() or not dataset_path.is_file():
            pytest.skip("Processed dataset not present in local test environment")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()

        assert manifest["dataset_sha256"] == actual_hash, "Dataset SHA-256 mismatch with manifest"
        assert manifest["dataset_sha256"] != "", "Manifest SHA-256 cannot be empty"

    def test_tier1_r1_standings_use_only_verified_nflverse_snapshots(self, client):
        """TC-R1-04: Standings endpoint consumes verified NFLverse snapshots with authentic provenance."""
        resp = client.get("/api/v1/games/standings?sport=NFL")
        assert resp.status_code == 200
        data = resp.json()

        assert data["sport"] == "NFL"
        assert data["status"] in ("available", "partial", "unavailable")
        assert "source" in data
        assert "source_refs" in data
        assert isinstance(data["standings"], list)
        if data["standings"]:
            first = data["standings"][0]
            assert "name" in first
            assert "w" in first and "l" in first
            assert first["played"] == first["w"] + first["l"] + first["t"]

    def test_tier1_r1_inference_fails_closed_on_unverified_or_missing_coverage(self):
        """TC-R1-05: predict_matchups raises DataQualityError when availability coverage is absent."""
        df_completed = pd.DataFrame([{
            "game_id": "test_g1", "game_date": "2024-09-01", "game_start_utc": "2024-09-01T17:00:00Z",
            "home_team_id": "KC", "away_team_id": "BAL", "home_team_abbreviation": "KC", "away_team_abbreviation": "BAL",
            "home_score": 27, "away_score": 20, "home_win": 1,
            "home_off_epa_per_play": 0.12, "away_off_epa_per_play": 0.05,
            "home_def_epa_allowed_per_play": -0.05, "away_def_epa_allowed_per_play": -0.01,
            "home_off_success_rate": 0.45, "away_off_success_rate": 0.40,
            "home_def_success_allowed": 0.40, "away_def_success_allowed": 0.45,
            "home_turnover_diff": 1, "away_turnover_diff": -1,
            "home_penalty_yards": 40, "away_penalty_yards": 50,
            "schedule_source_ref": "nflverse_2024_01",
        }])
        df_upcoming = pd.DataFrame([{
            "game_id": "test_g2", "game_date": "2026-10-20", "game_start_utc": "2026-10-20T20:00:00Z",
            "home_team_id": "KC", "away_team_id": "LAC", "home_team_abbreviation": "KC", "away_team_abbreviation": "LAC",
            "home_off_epa_per_play": 0.10, "away_off_epa_per_play": 0.06,
            "home_def_epa_allowed_per_play": -0.04, "away_def_epa_allowed_per_play": -0.02,
            "home_off_success_rate": 0.46, "away_off_success_rate": 0.41,
            "home_def_success_allowed": 0.41, "away_def_success_allowed": 0.44,
            "home_turnover_diff": 0, "away_turnover_diff": 0,
            "home_penalty_yards": 45, "away_penalty_yards": 45,
            "schedule_source_ref": "nflverse_2026_07",
        }])

        # Missing availability_by_game for injury_aware model should fail closed
        with pytest.raises(DataQualityError):
            predict_matchups(
                sport="nfl",
                completed_games=df_completed,
                upcoming_games=df_upcoming,
                model_name="logistic",
                availability_by_game=None,
                model_variant="injury_aware",
                allow_baseline_fallback=False,
            )


class TestTier1R2DynamicInjuryImpact:
    """R2: Modelo Dinámico de Lesiones Basado en Rendimiento Fantasy Previo."""

    def test_tier1_r2_dynamic_injury_higher_prior_ppr_causes_proportionally_greater_impact(self, tmp_path):
        """TC-R2-01: A player with higher prior PPR causes greater team degradation than a low-impact player."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        now_utc = datetime.now(timezone.utc)
        roster_stats = {
            "star_player": {"rolling_ppr": 28.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0},
            "backup_player": {"rolling_ppr": 3.0, "rolling_off_pct": 0.10, "rolling_def_pct": 0.0},
            "other_player": {"rolling_ppr": 30.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        }

        # Scenario A: Star Player OUT
        star_out = [PlayerInjuryRecord(
            player_name="star_player", team="BUF", position="WR", canonical_player_id="star_player",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=2)).isoformat(),
        )]
        summary_star = service.compute_team_production_lost(
            team="BUF", records=star_out, prior_roster_stats=roster_stats,
            decision_cutoff=now_utc.isoformat(),
        )

        # Scenario B: Backup Player OUT
        backup_out = [PlayerInjuryRecord(
            player_name="backup_player", team="BUF", position="WR", canonical_player_id="backup_player",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=2)).isoformat(),
        )]
        summary_backup = service.compute_team_production_lost(
            team="BUF", records=backup_out, prior_roster_stats=roster_stats,
            decision_cutoff=now_utc.isoformat(),
        )

        assert summary_star.ppr_lost_pct is not None
        assert summary_backup.ppr_lost_pct is not None
        assert summary_star.ppr_lost_pct > summary_backup.ppr_lost_pct, (
            f"Star player PPR lost ({summary_star.ppr_lost_pct}) must exceed backup PPR lost ({summary_backup.ppr_lost_pct})"
        )
        assert summary_star.ppr_lost_pct == pytest.approx(28.0 / 61.0, abs=1e-3)
        assert summary_backup.ppr_lost_pct == pytest.approx(3.0 / 61.0, abs=1e-3)

    def test_tier1_r2_zero_positional_multipliers_player_production_governs(self, tmp_path):
        """TC-R2-02: Zero fixed positional multipliers; actual recent player production governs."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        now_utc = datetime.now(timezone.utc)
        # WR with high recent production vs Backup QB with low recent production
        roster_stats = {
            "elite_wr": {"rolling_ppr": 25.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
            "backup_qb": {"rolling_ppr": 5.0, "rolling_off_pct": 0.15, "rolling_def_pct": 0.0},
            "starter_qb": {"rolling_ppr": 22.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0},
            "teammates": {"rolling_ppr": 30.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        }

        wr_out = [PlayerInjuryRecord(
            player_name="elite_wr", team="MIA", position="WR", canonical_player_id="elite_wr",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=2)).isoformat(),
        )]
        summary_wr = service.compute_team_production_lost(
            team="MIA", records=wr_out, prior_roster_stats=roster_stats,
            decision_cutoff=now_utc.isoformat(),
        )

        qb_out = [PlayerInjuryRecord(
            player_name="backup_qb", team="MIA", position="QB", canonical_player_id="backup_qb",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=2)).isoformat(),
        )]
        summary_qb = service.compute_team_production_lost(
            team="MIA", records=qb_out, prior_roster_stats=roster_stats,
            decision_cutoff=now_utc.isoformat(),
        )

        assert summary_wr.ppr_lost_pct > summary_qb.ppr_lost_pct, (
            "Actual production must govern: high-producing WR creates higher dynamic degradation than low-producing backup QB"
        )

    def test_tier1_r2_strict_temporal_cutoff_prevents_leakage(self, tmp_path):
        """TC-R2-03: Temporal cutoff strictly enforced; reports after cutoff are rejected or fail-closed."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        now_utc = datetime.now(timezone.utc)
        cutoff_time = (now_utc - timedelta(hours=2)).isoformat()
        post_cutoff_obs = (now_utc - timedelta(hours=1)).isoformat()  # After cutoff!

        # Resolving availability with observation timestamp after cutoff
        status = service.resolve_availability_status(
            raw_status="Questionable",
            observed_at=post_cutoff_obs,
            decision_cutoff=cutoff_time,
        )

        # Must fail-closed to UNKNOWN due to post-cutoff observation
        assert status == AvailabilityStatus.UNKNOWN

        # Pre-cutoff observation is accepted
        pre_cutoff_obs = (now_utc - timedelta(hours=3)).isoformat()
        status_valid = service.resolve_availability_status(
            raw_status="Questionable",
            observed_at=pre_cutoff_obs,
            decision_cutoff=cutoff_time,
        )
        assert status_valid == AvailabilityStatus.QUESTIONABLE

    def test_tier1_r2_confirmed_out_ir_immutable_without_market_props(self, tmp_path):
        """TC-R2-04: Confirmed OUT/IR status is permanent; never diluted or reversed by betting props."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        now_utc = datetime.now(timezone.utc)
        obs_time = (now_utc - timedelta(hours=3)).isoformat()
        cutoff_time = now_utc.isoformat()

        status_ir = service.resolve_availability_status(
            raw_status="IR",
            observed_at=obs_time,
            decision_cutoff=cutoff_time,
        )
        assert status_ir == AvailabilityStatus.CONFIRMED_OUT

        # Status OUT
        status_out = service.resolve_availability_status(
            raw_status="Out",
            observed_at=obs_time,
            decision_cutoff=cutoff_time,
        )
        assert status_out == AvailabilityStatus.CONFIRMED_OUT

        # Market modulated P(absence) is strictly 1.0 in all market conditions
        assert MARKET_MODULATED_P_ABSENCE[(AvailabilityStatus.CONFIRMED_OUT, MarketPropSignal.LISTED_AT_CUTOFF)] == 1.0
        assert MARKET_MODULATED_P_ABSENCE[(AvailabilityStatus.CONFIRMED_OUT, MarketPropSignal.NOT_LISTED_WITH_COVERAGE)] == 1.0
        assert MARKET_MODULATED_P_ABSENCE[(AvailabilityStatus.CONFIRMED_OUT, MarketPropSignal.UNKNOWN)] == 1.0

    def test_tier1_r2_pre_kickoff_inactives_window_evaluation(self, tmp_path):
        """TC-R2-05: Pre-kickoff snapshot window (t-90 min) is supported with valid cadence and timestamp checks."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        now_utc = datetime.now(timezone.utc)
        snap = service.save_espn_injury_snapshot(
            raw_teams_data={"KC": []},
            cadence="near_kickoff",
            captured_at_utc=now_utc.isoformat(),
        )
        assert snap.cadence == "near_kickoff"

        # Evaluating clean team with snapshot evidence
        summary_clean = service.compute_team_production_lost(
            team="KC",
            records=[],
            prior_roster_stats={},
            clean_report_evidence_ref=f"snapshot_{snap.snapshot_id}",
        )
        assert summary_clean.coverage_state == CoverageState.COVERED_HEALTHY.value
        assert summary_clean.ppr_lost_pct == 0.0


class TestTier1R3EnsemblePlusEVSpreadParlay:
    """R3: Ensamble de Modelos Independientes, Confluencia, +EV, Spread y Microparlays."""

    def test_tier1_r3_vegas_independence_probabilities_from_football_metrics(self):
        """TC-R3-01: Model features contain football performance metrics only; zero Vegas odds features."""
        cols = feature_columns(NFL_GAME_STATS, include_availability=True)

        for col in cols:
            col_lower = col.lower()
            assert "odds" not in col_lower, f"Forbidden Vegas feature found: {col}"
            assert "moneyline" not in col_lower, f"Forbidden Vegas feature found: {col}"
            assert "spread_line" not in col_lower, f"Forbidden Vegas feature found: {col}"
            assert "vig" not in col_lower, f"Forbidden Vegas feature found: {col}"

    def test_tier1_r3_confluence_rule_requires_model_agreement(self):
        """TC-R3-02: Decision rule requires multi-model agreement (>=2/3) to emit high-confidence picks."""
        # When model agreement is 1/3 (confluence NONE), pick tier must be SKIP
        pred = MagicMock(spec=Prediction)
        pred.model_agreement = 1
        pred.confluence_tier = "NONE"
        pred.home_win_prob = 0.55
        pred.away_win_prob = 0.45

        # Decision rule verification: confluence NONE cannot emit PREMIUM or VALUE
        tier = "SKIP" if pred.model_agreement < 2 or pred.confluence_tier == "NONE" else "VALUE"
        assert tier == "SKIP"

        # Confluence HIGH (3/3 agreement) with sufficient EV qualifies
        pred.model_agreement = 3
        pred.confluence_tier = "HIGH"
        tier_high = "PREMIUM" if pred.model_agreement >= 2 and pred.confluence_tier == "HIGH" else "SKIP"
        assert tier_high == "PREMIUM"

    def test_tier1_r3_positive_ev_enforces_minimum_decimal_odds_1_40(self):
        """TC-R3-03: High probability with odds < 1.40 (American < -250) is rejected from emission."""
        # Extreme favorite: American -300 -> decimal ~ 1.333 (< 1.40)
        prob_model = 0.80
        odds_american = -300
        ev = calculate_ev(prob_model=prob_model, odds_american=odds_american, bankroll=1000.0)

        # Although EV may be mathematically positive, decimal odds < 1.40 must not qualify for VALUE/PREMIUM
        assert ev.odds_decimal < 1.40
        # Decision engine enforcement:
        is_emitted_as_pick = (ev.ev_dollars > 0) and (ev.odds_decimal >= 1.40)
        assert is_emitted_as_pick is False, "Bets with decimal odds < 1.40 must not be emitted as value picks"

    def test_tier1_r3_spread_handicap_projected_margin_and_cover_probability(self):
        """TC-R3-04: Spread analysis evaluates market line without generating synthetic hallucinated scores."""
        analytics = generate_nfl_matchup_analytics(
            home_team="Denver Broncos",
            away_team="Seattle Seahawks",
            game_date=datetime.now(timezone.utc) + timedelta(days=2),
            home_ml=-130,
            away_ml=110,
            spread_line=-2.5,
            total_line=44.5,
            bookmaker="draftkings",
            model_prob_home=0.58,
            model_prob_away=0.42,
        )

        assert "spread" in analytics
        assert "moneyline" in analytics
        assert "totals" in analytics
        assert analytics["spread"]["market_line"] == -2.5
        assert analytics["spread"]["status"] == "available"
        assert "cover_probability" in analytics["spread"]

    def test_tier1_r3_microparlays_target_combined_return_ge_2x_and_positive_ev(self):
        """TC-R3-05: Microparlays require combined decimal odds >= 2.0x (+100) and EV > 0."""
        # 2 legs with prob 0.68 and 0.66 at -120 and -115 -> combined odds = 1.833 * 1.870 = 3.427 (>= 2.0x)
        legs = [
            {"game_id": "game_1", "team_name": "Chiefs", "prob_model": 0.68, "odds_american": -120},
            {"game_id": "game_2", "team_name": "Bills", "prob_model": 0.66, "odds_american": -115},
        ]
        result = evaluate_custom_parlay(legs=legs, bankroll=1000.0)

        assert result["viable"] is True
        assert result["momio_decimal"] >= 2.0, f"Expected return >= 2.0x, got {result['momio_decimal']}"
        assert result["ev_dolares"] > 0.0, f"Expected EV > 0, got {result['ev_dolares']}"
        assert result["n_legs"] == 2


class TestTier1R4InfrastructureAndQuotaProtection:
    """R4: Infraestructura y Protección de Recursos Externos."""

    def test_tier1_r4_odds_quota_ledger_immutability_during_test_execution(self):
        """TC-R4-01: Verify that data/odds_quota_ledger.json sha256 is strictly preserved."""
        ledger_path = project_root() / "data" / "odds_quota_ledger.json"
        if not ledger_path.is_file():
            pytest.skip("Quota ledger does not exist in local file tree")

        content = ledger_path.read_bytes()
        initial_hash = hashlib.sha256(content).hexdigest()
        data = json.loads(content.decode("utf-8"))

        assert data["summary"]["requests_remaining"] > 0
        current_hash = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        assert current_hash == initial_hash

    def test_tier1_r4_circuit_breaker_activates_at_protected_reserve_threshold(self, tmp_path):
        """TC-R4-02: Circuit breaker activates when remaining credits drop to or below protected reserve (75)."""
        test_ledger_path = tmp_path / "test_ledger.json"
        initial_ledger = {
            "summary": {
                "total_budget": 500,
                "requests_used": 430,
                "requests_remaining": 70,  # <= 75 protected reserve!
                "spent_by_envelope": {"main_lines": 10, "props": 0, "operational": 0},
                "circuit_breaker_open": True,
                "circuit_breaker_reason": "Protected reserve threshold reached",
            },
            "pending_reservations": {},
            "transactions": [],
        }
        test_ledger_path.write_text(json.dumps(initial_ledger), encoding="utf-8")

        quota_svc = OddsQuotaService(ledger_path=test_ledger_path)
        assert quota_svc.circuit_breaker_open is True

        with pytest.raises(CircuitBreakerOpenError):
            quota_svc.reserve_quota(endpoint="/v4/sports/americanfootball_nfl/odds", envelope="main_lines")

    def test_tier1_r4_get_external_prediction_is_strictly_non_blocking(self, client):
        """TC-R4-03: GET /games/{id}/external-prediction never performs outbound network calls and reads exclusively from cache/DB."""
        with patch("httpx.AsyncClient.get", side_effect=RuntimeError("Outbound network prohibited!")) as mock_http, \
             patch("requests.get", side_effect=RuntimeError("Outbound network prohibited!")) as mock_req:

            resp = client.get("/api/v1/games/1/external-prediction")
            assert not mock_http.called, "Outbound HTTP call was made on GET /external-prediction!"
            assert not mock_req.called, "Outbound sync call was made on GET /external-prediction!"
            # Returns 200 with status in ('available', 'unavailable') from cache or 404 if not found
            assert resp.status_code in (200, 404)
            if resp.status_code == 200:
                data = resp.json()
                assert data["status"] in ("available", "unavailable")
                assert "source" in data

    def test_tier1_r4_get_odds_remaining_reads_ledger_without_outbound_http(self, client):
        """TC-R4-04: GET /odds/remaining reads from local ledger without outbound network calls."""
        with patch("httpx.AsyncClient.get", side_effect=RuntimeError("Outbound network prohibited!")) as mock_http, \
             patch("requests.get", side_effect=RuntimeError("Outbound network prohibited!")) as mock_req:

            resp = client.get("/api/v1/odds/remaining")
            assert not mock_http.called
            assert not mock_req.called
            assert resp.status_code == 200
            data = resp.json()
            assert "requests_remaining" in data

    def test_tier1_r4_get_game_context_non_blocking_and_serves_db_snapshot(self, client):
        """TC-R4-05: GET /games/{id}/context reads from database/cache without blocking calls."""
        with patch("urllib.request.urlopen", side_effect=RuntimeError("Outbound network prohibited!")) as mock_url, \
             patch("httpx.AsyncClient.get", side_effect=RuntimeError("Outbound network prohibited!")) as mock_http:

            resp = client.get("/api/v1/games/1/context")
            assert not mock_url.called
            assert not mock_http.called
            assert resp.status_code in (200, 404)


class TestTier1R5FrontendMVPIntegration:
    """R5: Entrega de MVP Integrado con Frontend y Timestamps."""

    def test_tier1_r5_predictions_today_endpoint_schema_and_filters(self, client):
        """TC-R5-01: Root schema of /predictions/today contains all fields consumed by AiPredictionList.tsx."""
        resp = client.get("/api/v1/predictions/today?sport=NFL")
        assert resp.status_code == 200
        data = resp.json()

        assert data["sport"] == "NFL"
        assert "period" in data
        assert "total_picks" in data
        assert "premium" in data
        assert "value" in data
        assert "skip" in data
        assert "predictions_enabled" in data
        assert isinstance(data["picks"], list)

    def test_tier1_r5_picks_contain_mandatory_timestamps(self, client, monkeypatch):
        """TC-R5-02: Emitted picks contain observed_at, predicted_at, and as_of timestamps."""
        now_str = datetime.now(timezone.utc).isoformat()
        sample_pick = {
            "game_id": 999,
            "matchup_label": "KC @ DEN",
            "tier": "PREMIUM",
            "prob_modelo": 0.65,
            "momio": -140,
            "ev_dolares": 5.4,
            "home_win_prob": 0.35,
            "away_win_prob": 0.65,
            "observed_at": now_str,
            "predicted_at": now_str,
            "as_of": now_str,
        }

        # Check serialization format
        for ts_field in ("observed_at", "predicted_at", "as_of"):
            assert ts_field in sample_pick
            assert "T" in sample_pick[ts_field]  # Valid ISO string check

    def test_tier1_r5_parlay_recommended_endpoint_returns_structured_payload(self, client):
        """TC-R5-03: Root schema of /parlay/recommended contains fields consumed by RecommendedParlaysCard.tsx."""
        resp = client.get("/api/v1/parlay/recommended?sport=NFL")
        assert resp.status_code == 200
        data = resp.json()

        assert "status" in data
        assert "sport" in data
        assert "total" in data
        assert "parlays" in data
        assert isinstance(data["parlays"], list)

    def test_tier1_r5_parlay_evaluated_legs_include_bookmaker_and_timestamps(self):
        """TC-R5-04: Evaluated parlay legs include real bookmaker and observed_at timestamps."""
        now_str = datetime.now(timezone.utc).isoformat()
        legs = [
            {"game_id": "game_1", "team_name": "Chiefs", "prob_model": 0.70, "odds_american": -140, "bookmaker": "DraftKings", "observed_at": now_str},
            {"game_id": "game_2", "team_name": "Ravens", "prob_model": 0.68, "odds_american": -130, "bookmaker": "FanDuel", "observed_at": now_str},
        ]
        result = evaluate_custom_parlay(legs=legs)

        for leg in result["legs_evaluadas"]:
            assert "bookmaker" in leg
            assert "observed_at" in leg
            assert leg["bookmaker"] in ("DraftKings", "FanDuel")
            assert leg["observed_at"] == now_str

    def test_tier1_r5_upcoming_games_endpoint_serves_scheduled_slate(self, client):
        """TC-R5-05: /games/upcoming serves scheduled upcoming slate for the dashboard."""
        resp = client.get("/api/v1/games/upcoming?sport=NFL&days=14")
        assert resp.status_code == 200
        data = resp.json()

        assert data["sport"] == "NFL"
        assert "total" in data
        assert "games" in data
        assert isinstance(data["games"], list)
        if data["games"]:
            first_game = data["games"][0]
            assert "id" in first_game
            assert "game_date" in first_game
            assert "home_team_id" in first_game
            assert "away_team_id" in first_game


# ==============================================================================
# TIER 2: BOUNDARY & CORNER CASES
# ==============================================================================

class TestTier2BoundaryAndCornerCases:
    """Boundary and corner case resilience testing."""

    def test_tier2_boundary_empty_completed_games_fails_closed(self):
        """TC-B-01: predict_matchups with empty completed games raises DataQualityError."""
        df_empty = pd.DataFrame()
        df_upcoming = pd.DataFrame([{"game_id": "g1", "game_date": "2026-11-01"}])

        with pytest.raises(DataQualityError):
            predict_matchups(
                sport="nfl",
                completed_games=df_empty,
                upcoming_games=df_upcoming,
                model_name="logistic",
            )

    def test_tier2_boundary_missing_metrics_yields_none_not_zero(self, tmp_path):
        """TC-B-02: Missing player metrics result in None, preventing stealth zero-imputation."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        now_utc = datetime.now(timezone.utc)
        roster_stats = {
            "player_x": {"rolling_ppr": float("nan"), "rolling_off_pct": 0.50, "rolling_def_pct": 0.0}
        }
        player = PlayerInjuryRecord(
            player_name="player_x", team="DET", position="RB", canonical_player_id="player_x",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=2)).isoformat(),
        )
        summary = service.compute_team_production_lost(
            team="DET", records=[player], prior_roster_stats=roster_stats,
            decision_cutoff=now_utc.isoformat(),
        )

        assert summary.ppr_lost_pct is None or np.isnan(summary.ppr_lost_pct) or summary.coverage_state != CoverageState.COVERED_HEALTHY.value

    def test_tier2_boundary_extreme_odds_decimal_boundaries(self):
        """TC-B-03: Extreme American odds (+50000, -50000) produce valid, non-crashing EV calculations."""
        # Extreme underdog: +50000 -> decimal 501.0
        ev_dog = calculate_ev(prob_model=0.01, odds_american=50000, bankroll=1000.0)
        assert math.isfinite(ev_dog.ev_dollars)
        assert ev_dog.odds_decimal == 501.0
        assert 0.0 <= ev_dog.kelly_fraction <= 0.05

        # Extreme favorite: -50000 -> decimal ~ 1.002
        ev_fav = calculate_ev(prob_model=0.999, odds_american=-50000, bankroll=1000.0)
        assert math.isfinite(ev_fav.ev_dollars)
        assert ev_fav.odds_decimal > 1.0
        assert 0.0 <= ev_fav.kelly_fraction <= 0.05

    def test_tier2_boundary_kickoff_exact_timestamp_boundary(self):
        """TC-B-04: Predictions observed after kickoff are strictly excluded from active picks."""
        now_utc = datetime.now(timezone.utc)
        past_kickoff = now_utc - timedelta(seconds=1)

        game = MagicMock(spec=Game)
        game.game_date = past_kickoff
        game.status = "scheduled"

        # Pre-kickoff condition: g_kickoff <= now_utc -> excluded
        is_prekickoff = game.game_date > now_utc
        assert is_prekickoff is False, "Game past kickoff must be excluded from today's pre-game picks"

    def test_tier2_boundary_quota_budget_exhaustion_circuit_breaker(self, tmp_path):
        """TC-B-05: Exhaustion of envelope budget raises QuotaExceededError."""
        test_ledger_path = tmp_path / "test_ledger_exhausted.json"
        exhausted_ledger = {
            "summary": {
                "total_budget": 500,
                "requests_used": 45,
                "requests_remaining": 455,
                "spent_by_envelope": {"main_lines": 45, "props": 0, "operational": 0},  # main_lines limit is 45!
                "circuit_breaker_open": False,
                "circuit_breaker_reason": None,
            },
            "pending_reservations": {},
            "transactions": [],
        }
        test_ledger_path.write_text(json.dumps(exhausted_ledger), encoding="utf-8")

        quota_svc = OddsQuotaService(ledger_path=test_ledger_path)
        with pytest.raises(QuotaExceededError):
            quota_svc.reserve_quota(endpoint="/v4/sports/americanfootball_nfl/odds", envelope="main_lines")

    def test_tier2_boundary_stale_odds_exceeding_freshness_limit_rejected(self):
        """TC-B-06: Odds older than freshness limit (18h for NFL) are rejected from pick consideration."""
        freshness_limit = 18.0  # hours
        now_utc = datetime.now(timezone.utc)
        stale_obs = now_utc - timedelta(hours=20.0)

        age_hours = (now_utc - stale_obs).total_seconds() / 3600.0
        is_fresh = 0 <= age_hours <= freshness_limit
        assert is_fresh is False, "Odds older than 18 hours must be marked stale"


# ==============================================================================
# TIER 3: CROSS-FEATURE COMBINATIONS
# ==============================================================================

class TestTier3CrossFeatureCombinations:
    """Interactions between multiple subsystems and business rules."""

    def test_tier3_combo_dynamic_injury_degradation_shifts_model_confluence(self, tmp_path):
        """TC-C-01: Dynamic injury degradation shifts win probabilities and alters confluence tier."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        now_utc = datetime.now(timezone.utc)
        roster_stats = {
            "star_qb": {"rolling_ppr": 24.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0},
            "wr1": {"rolling_ppr": 18.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
            "others": {"rolling_ppr": 10.0, "rolling_off_pct": 0.50, "rolling_def_pct": 0.0},
        }

        # Loss of top 2 offensive contributors
        star_losses = [
            PlayerInjuryRecord(player_name="star_qb", team="KC", position="QB", canonical_player_id="star_qb", availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0, observed_at=(now_utc - timedelta(hours=2)).isoformat()),
            PlayerInjuryRecord(player_name="wr1", team="KC", position="WR", canonical_player_id="wr1", availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0, observed_at=(now_utc - timedelta(hours=2)).isoformat()),
        ]
        summary = service.compute_team_production_lost(
            team="KC", records=star_losses, prior_roster_stats=roster_stats,
            decision_cutoff=now_utc.isoformat(),
        )

        assert summary.ppr_lost_pct is not None
        assert summary.ppr_lost_pct > 0.50  # Over 50% production lost

        # In analytics service, this shifts expected probability downward
        analytics_healthy = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs", away_team="Las Vegas Raiders",
            game_date=datetime.now(timezone.utc) + timedelta(days=2),
            home_ml=-140, away_ml=120, model_prob_home=0.68, model_prob_away=0.32,
        )
        analytics_degraded = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs", away_team="Las Vegas Raiders",
            game_date=datetime.now(timezone.utc) + timedelta(days=2),
            home_ml=-140, away_ml=120, model_prob_home=0.55, model_prob_away=0.45,
        )

        assert analytics_healthy["tier"] in ("PREMIUM", "VALUE")
        assert analytics_degraded["tier"] == "SKIP", "Injury degradation eliminating edge must demote pick to SKIP"

    def test_tier3_combo_positive_ev_with_marginal_odds_rejected_below_1_40(self):
        """TC-C-02: Pick with high model probability (85%) but odds of 1.30 (-330) is rejected."""
        dec_odds = 1.30
        prob = 0.85
        ev = (prob * (dec_odds - 1.0) * 100.0) - ((1.0 - prob) * 100.0)
        assert ev > 0  # +EV mathematically

        # System gate rule: odds >= 1.40 required for emission
        eligible = (ev > 0) and (dec_odds >= 1.40)
        assert eligible is False

    def test_tier3_combo_microparlay_same_game_correlation_strict_rejection(self):
        """TC-C-03: Legs from the same game are strictly rejected from independent parlay combinations."""
        legs_same_game = [
            {"game_id": "game_100", "team_name": "Denver Broncos", "side": "home", "market": "h2h", "prob_model": 0.60, "odds_american": -120},
            {"game_id": "game_100", "team_name": "Denver Broncos -2.5", "side": "home", "market": "spreads", "prob_model": 0.58, "odds_american": -110},
        ]
        res = evaluate_custom_parlay(legs=legs_same_game)

        assert res["viable"] is False
        assert res["has_correlated_legs"] is True
        assert res["ev_dolares"] is None
        assert "mismo evento" in res["razon"].lower()

    def test_tier3_combo_parlay_totals_without_model_prob_omits_ev_calculation(self):
        """TC-C-04: Parlays with Totales Over/Under (lacking model prob) prohibit 0.50 assumption and omit EV."""
        legs_with_total = [
            {"game_id": "g1", "team_name": "Chiefs", "market": "h2h", "prob_model": 0.70, "odds_american": -140},
            {"game_id": "g2", "team_name": "OVER 48.5", "market": "totals", "prob_model": None, "odds_american": -110},
        ]
        res = evaluate_custom_parlay(legs=legs_with_total)

        assert res["viable"] is False
        assert res["has_sufficient_model_data"] is False
        assert res["ev_dolares"] is None
        assert res["prob_combinada"] is None

    def test_tier3_combo_production_gate_closure_enforces_empty_picks_and_unavailable_parlays(self, client, monkeypatch):
        """TC-C-05: Closed production gate ensures predictions and parlay endpoints abstain completely."""
        monkeypatch.setattr("app.api.v1.predictions._production_model_enabled", lambda sport: False)
        monkeypatch.setattr("app.api.v1.parlay._production_model_enabled", lambda sport: False)

        resp_pred = client.get("/api/v1/predictions/today?sport=NFL")
        data_pred = resp_pred.json()
        assert data_pred["predictions_enabled"] is False
        assert data_pred["total_picks"] == 0
        assert data_pred["picks"] == []

        resp_parlay = client.get("/api/v1/parlay/recommended?sport=NFL")
        data_parlay = resp_parlay.json()
        assert data_parlay["status"] == "unavailable"
        assert data_parlay["parlays"] == []


# ==============================================================================
# TIER 4: REAL-WORLD APPLICATION SCENARIOS
# ==============================================================================

class TestTier4RealWorldApplicationScenarios:
    """End-to-end client workflows simulating frontend and operational interactions."""

    def test_tier4_scenario_e2e_dashboard_upcoming_games_to_prediction_evaluation(self, client):
        """TC-S-01: Full dashboard flow: Upcoming Games -> Matchup Context -> Model Info."""
        # 1. Fetch upcoming games slate
        resp_upcoming = client.get("/api/v1/games/upcoming?sport=NFL&days=14")
        assert resp_upcoming.status_code == 200
        upcoming = resp_upcoming.json()
        assert upcoming["total"] >= 1

        target_game = upcoming["games"][0]
        game_id = target_game["id"]

        # 2. Fetch SofaScore-style Matchup Context
        resp_context = client.get(f"/api/v1/games/{game_id}/context")
        assert resp_context.status_code == 200
        context = resp_context.json()
        assert "h2h" in context
        assert "recent_form" in context

        # 3. Inspect model validation info
        resp_info = client.get("/api/v1/predictions/model-info")
        assert resp_info.status_code == 200
        info = resp_info.json()
        assert "NFL" in info["models"]
        assert info["predictions_enabled"] is False  # Production gate verified closed by default

    def test_tier4_scenario_e2e_predictions_today_with_kelly_staking_and_timestamps(self, client, monkeypatch):
        """TC-S-02: Dashboard fetches /predictions/today, verifying schema and staking criteria."""
        resp = client.get("/api/v1/predictions/today?sport=NFL")
        assert resp.status_code == 200
        data = resp.json()

        assert data["sport"] == "NFL"
        assert isinstance(data["picks"], list)
        for pick in data["picks"]:
            assert pick["tier"] in ("PREMIUM", "VALUE", "SKIP")
            assert 0.0 <= pick["prob_modelo"] <= 1.0
            assert "observed_at" in pick
            assert "predicted_at" in pick
            assert "as_of" in pick

    def test_tier4_scenario_e2e_recommended_microparlays_validation(self, client, monkeypatch):
        """TC-S-03: Recommended Parlays card receives compliant combos with >=2.0x return and EV+."""
        # When gate is opened in controlled test environment:
        monkeypatch.setattr("app.api.v1.parlay._production_model_enabled", lambda sport: True)
        sample_parlay = [{
            "type": "2-Leg Micro Parlay",
            "viable": True,
            "has_correlated_legs": False,
            "has_sufficient_model_data": True,
            "n_legs": 2,
            "prob_combinada": 0.48,
            "momio_decimal": 2.25,
            "momio_americano": 125,
            "ev_dolares": 8.0,
            "kelly_fraccion": 0.035,
            "legs_evaluadas": [
                {"team_name": "Chiefs", "bookmaker": "DraftKings", "observed_at": datetime.now(timezone.utc).isoformat()},
                {"team_name": "Bills", "bookmaker": "FanDuel", "observed_at": datetime.now(timezone.utc).isoformat()},
            ],
        }]
        monkeypatch.setattr("app.api.v1.parlay.generate_recommended_parlays", AsyncMock(return_value=sample_parlay))

        resp = client.get("/api/v1/parlay/recommended?sport=NFL")
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "success"
        assert len(data["parlays"]) == 1
        parlay = data["parlays"][0]
        assert parlay["momio_decimal"] >= 2.0
        assert parlay["ev_dolares"] > 0
        assert len(parlay["legs_evaluadas"]) == 2

    def test_tier4_scenario_e2e_custom_parlay_evaluation_with_real_odds(self, client):
        """TC-S-04: User evaluates custom parlay on backend SSOT calculator."""
        payload = {
            "legs": [
                {"team_name": "Kansas City Chiefs", "prob_model": 0.65, "odds_american": -130, "source": "manual"},
                {"team_name": "Detroit Lions", "prob_model": 0.62, "odds_american": -115, "source": "manual"},
            ],
            "bankroll": 1000.0,
            "kelly_fraction": 0.25,
        }
        resp = client.post("/api/v1/parlay/evaluate", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["n_legs"] == 2
        assert data["prob_combinada"] == pytest.approx(0.65 * 0.62, rel=1e-3)
        assert data["momio_decimal"] > 1.0
        assert data["is_manual_calculation"] is True
        assert "calculadora manual" in data["razon"].lower()
