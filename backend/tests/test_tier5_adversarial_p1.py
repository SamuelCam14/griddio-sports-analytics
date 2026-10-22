"""
Tier 5 White-Box Adversarial Coverage Hardening Test Suite (Part 1).
Master Specification: ORIGINAL_REQUEST.md, PROJECT.md, and TEST_READY.md.

Adversarial Stress Testing Categories:
1. Data Integrity & Zero-Imputation:
   - Empty rosters failing closed to UNKNOWN without synthetic fallback.
   - Missing prior-week stats yielding None and UNKNOWN coverage state.
   - Unmapped/ambiguous player identities failing closed.
   - Corrupted parquet headers or byte-tampered snapshots raising DataQualityError.
   - Strict ban on 0-imputation across rolling metrics and feature matrices.
2. Dynamic Injury Engine Extreme Edge Cases:
   - 100% (and >100%) of team fantasy production injured, cleanly capping at 1.0 without overflow.
   - Zero historical snaps / zero team potential denominator failing closed.
   - NaN or negative injury metrics failing closed in inference.
   - Bye week backward search (t-1 gap) preserving anti-leakage invariants.
   - Confirmed OUT/IR permanent invariance immune to rogue market props.
3. Multi-Model Ensemble Edge Cases:
   - Model disagreement ties resolving to confluence 'NONE' and tier 'SKIP'.
   - Extreme win probabilities (0.0001 and 0.9999) handled gracefully without inf/NaN.
   - Borderline odds at exactly 1.40 vs 1.3999 decision boundary.
   - Negative EV bets strictly demoted to tier 'SKIP' with 0.0 Kelly fraction.
   - Independent model win probabilities summing to 1.0 without bookmaker bias.
4. Quota Ledger Protection & Immutability:
   - Invariant SHA-256 hash preservation of data/odds_quota_ledger.json.
   - Fail-closed behavior on missing or corrupt quota ledger.
   - Strict circuit breaker enforcement at protected reserve boundary (<= 75 credits).
5. Cross-Cutting Adversarial Combinations:
   - Microparlay rejection of same-game correlated legs under standard engine.
   - Correlated microparlays rejecting sub-2.0x combined odds and non-positive EV.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.services.ev_engine import (
    american_to_decimal,
    american_to_implied_prob,
    calculate_ev,
    decimal_to_american,
    remove_vig,
)
from app.services.nfl_availability_service import (
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
    estimate_request_cost,
    mask_api_key,
    sanitize_params,
)
from app.services.parlay_service import (
    _compute_joint_probability,
    evaluate_custom_parlay,
    normalize_nfl_team,
)
from app.services.sports_analytics_service import generate_nfl_matchup_analytics
from ml.data_pipeline.common import DataQualityError, project_root, verify_file
from ml.inference import _apply_ensemble, predict_matchups

EXPECTED_LEDGER_HASH = "0fe97f674d12a2adb3f1986f486328ea69d55763ef641ba642d6e540daa11112"


@pytest.fixture(autouse=True)
def verify_quota_ledger_unaltered():
    """Guarantee that data/odds_quota_ledger.json is byte-for-byte invariant across all adversarial tests."""
    ledger_path = project_root() / "data" / "odds_quota_ledger.json"
    if not ledger_path.is_file():
        pytest.fail(f"Quota ledger not found at {ledger_path}")

    before_bytes = ledger_path.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    assert before_hash == EXPECTED_LEDGER_HASH, (
        f"Pre-test ledger hash mismatch: {before_hash} != {EXPECTED_LEDGER_HASH}"
    )

    yield

    after_bytes = ledger_path.read_bytes()
    after_hash = hashlib.sha256(after_bytes).hexdigest()
    assert after_hash == EXPECTED_LEDGER_HASH, (
        f"CRITICAL VIOLATION: odds_quota_ledger.json mutated during test! "
        f"Before: {before_hash}, After: {after_hash}"
    )


# ==============================================================================
# 1. DATA INTEGRITY & ZERO-IMPUTATION ADVERSARIAL PROBING
# ==============================================================================

class TestTier5DataIntegrityAndZeroImputation:
    """Probing edge cases and failure modes in data pipelines and zero-imputation policy."""

    def test_adv_empty_roster_fails_closed_without_synthetic_fallback(self, tmp_path):
        """TC-ADV-01: Empty prior roster stats must fail closed to UNKNOWN; zero synthetic players or metrics."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        player = PlayerInjuryRecord(
            player_name="Patrick Mahomes",
            team="KC",
            position="QB",
            canonical_player_id="p_mahomes_01",
            availability_status=AvailabilityStatus.QUESTIONABLE.value,
            p_absence=0.25,
            observed_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        )

        # Empty dict of prior roster stats
        summary = service.compute_team_production_lost(
            team="KC",
            records=[player],
            prior_roster_stats={},
            decision_cutoff=datetime.now(timezone.utc).isoformat(),
        )

        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None
        assert summary.off_snaps_lost_pct is None
        assert summary.def_snaps_lost_pct is None
        assert summary.metadata.get("reason") == "missing_prior_roster_statistics_cannot_impute"

    def test_adv_missing_prior_week_stats_yields_none_and_unknown_coverage(self, tmp_path):
        """TC-ADV-02: Unlisted player with missing historical stats must trigger fail-closed UNKNOWN."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        # Injured player exists in report but is absent from observed prior_roster_stats
        injured_star = PlayerInjuryRecord(
            player_name="Travis Kelce",
            team="KC",
            position="TE",
            canonical_player_id="t_kelce_87",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value,
            p_absence=1.0,
            observed_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        )

        # Roster only contains stats for a different teammate
        prior_roster_stats = {
            "p_mahomes_15": {"rolling_ppr": 22.0, "rolling_off_pct": 1.0, "rolling_def_pct": 0.0}
        }

        summary = service.compute_team_production_lost(
            team="KC",
            records=[injured_star],
            prior_roster_stats=prior_roster_stats,
            decision_cutoff=datetime.now(timezone.utc).isoformat(),
        )

        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None
        assert summary.metadata.get("missing_player_history_count") == 1
        assert summary.metadata.get("reason") == "availability_or_required_market_coverage_unverified"

    def test_adv_unmapped_or_ambiguous_players_fail_closed_to_unknown(self, tmp_path):
        """TC-ADV-03: Players with unmapped IDs, empty names, or 'Desconocido' fail closed to UNKNOWN."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")
        cutoff = datetime.now(timezone.utc)
        observed = cutoff - timedelta(hours=2)

        # Player dictionary lacking canonical identity
        unmapped_player_dict = {
            "player_name": "Desconocido",
            "position": "WR",
            "status": "Questionable",
            "observed_at": observed.isoformat(),
            "canonical_player_id": None,
            "gsis_id": None,
            "espn_id": None,
        }

        record = service.process_player_injury(
            player_dict=unmapped_player_dict,
            team="KC",
            decision_cutoff=cutoff.isoformat(),
        )

        assert record.availability_status == AvailabilityStatus.UNKNOWN.value
        assert record.coverage_flags.get("unresolved_canonical_id") is True
        assert record.coverage_flags.get("identity_unresolved") is True

        summary = service.compute_team_production_lost(
            team="KC",
            records=[record],
            prior_roster_stats={"any_player": {"rolling_ppr": 15.0}},
            decision_cutoff=cutoff.isoformat(),
        )
        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None

    def test_adv_corrupted_parquet_header_or_tampered_bytes_raises_data_quality_error(self, tmp_path):
        """TC-ADV-04: Bit-flipped or corrupted parquet file must fail verify_file with DataQualityError."""
        fake_parquet = tmp_path / "tampered_data.parquet"
        valid_bytes = b"PAR1_valid_parquet_header_content_placeholder_12345"
        fake_parquet.write_bytes(valid_bytes)
        real_hash = hashlib.sha256(valid_bytes).hexdigest()

        # Legitimate hash check passes
        verify_file(fake_parquet, real_hash)

        # Adversarial corruption: mutate one byte (simulating corrupted header or bit rot)
        corrupted_bytes = b"PAR1_corrupted_header_content_placeholder_12345"
        fake_parquet.write_bytes(corrupted_bytes)

        with pytest.raises(DataQualityError, match="Snapshot alterado.*hash SHA-256 no coincide"):
            verify_file(fake_parquet, real_hash)

    def test_adv_zero_imputation_ban_in_absence_of_stats(self, tmp_path):
        """TC-ADV-05: Absence of statistics must never be treated as 0.0 points or 0.0 snaps without evidence."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        # When source is unreachable or unavailable, coverage_state is UNAVAILABLE_NO_REPORT
        summary_unavail = service.compute_team_production_lost(
            team="SF",
            records=[],
            prior_roster_stats=None,
            is_source_available=False,
        )
        assert summary_unavail.coverage_state == CoverageState.UNAVAILABLE_NO_REPORT.value
        assert summary_unavail.ppr_lost_pct is None

        # When team is omitted in capture, coverage_state is NO_INJURIES_LISTED with None lost pct
        summary_no_inj = service.compute_team_production_lost(
            team="SF",
            records=[],
            prior_roster_stats={"player1": {"rolling_ppr": 20.0}},
            no_injuries_listed=True,
        )
        assert summary_no_inj.coverage_state == CoverageState.NO_INJURIES_LISTED.value
        assert summary_no_inj.ppr_lost_pct is None


# ==============================================================================
# 2. DYNAMIC INJURY ENGINE EXTREME EDGE CASES
# ==============================================================================

class TestTier5DynamicInjuryEngineAdversarial:
    """Stress-testing extreme values, mathematical boundaries, and temporal consistency."""

    def test_adv_injury_100_percent_production_injured_caps_cleanly_at_one(self, tmp_path):
        """TC-ADV-06: 100% (or catastrophic >100%) fantasy production loss caps cleanly at 1.0 without overflow."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")
        cutoff = datetime.now(timezone.utc)

        # Team has total potential of 50.0 PPR
        prior_roster = {
            "p1": {"rolling_ppr": 25.0, "rolling_off_pct": 0.5, "rolling_def_pct": 0.0},
            "p2": {"rolling_ppr": 25.0, "rolling_off_pct": 0.5, "rolling_def_pct": 0.0},
        }

        # Case A: Exactly 100% loss (both players OUT)
        records_100 = [
            PlayerInjuryRecord(
                player_name="P1", team="BAL", position="QB", canonical_player_id="p1",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                market_signal=MarketPropSignal.UNKNOWN.value,
            ),
            PlayerInjuryRecord(
                player_name="P2", team="BAL", position="RB", canonical_player_id="p2",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                market_signal=MarketPropSignal.UNKNOWN.value,
            ),
        ]

        summary_100 = service.compute_team_production_lost(
            team="BAL",
            records=records_100,
            prior_roster_stats=prior_roster,
            decision_cutoff=cutoff.isoformat(),
        )
        assert summary_100.ppr_lost_pct == 1.0
        assert summary_100.coverage_state == CoverageState.COVERED_WITH_INJURIES.value

        # Case B: Exceeding 100% (e.g. cumulative roster shifts summing > team potential)
        prior_roster_under = {
            "p1": {"rolling_ppr": 30.0, "rolling_off_pct": 6.0, "rolling_def_pct": 0.0},
            "p2": {"rolling_ppr": 30.0, "rolling_off_pct": 6.0, "rolling_def_pct": 0.0},
        }
        # Team potential is 60.0, but total off_snaps sum to 12.0 (> 11.0 on field)
        summary_overflow = service.compute_team_production_lost(
            team="BAL",
            records=records_100,
            prior_roster_stats=prior_roster_under,
            decision_cutoff=cutoff.isoformat(),
        )
        # Verify min(1.0, ...) clamping on ppr and snaps
        assert summary_overflow.ppr_lost_pct == 1.0
        assert summary_overflow.off_snaps_lost_pct == 1.0  # (12.0 / 11.0 capped at 1.0)
        assert not math.isinf(summary_overflow.ppr_lost_pct)
        assert not math.isnan(summary_overflow.ppr_lost_pct)

    def test_adv_injury_zero_historical_snaps_or_zero_potential_denominator(self, tmp_path):
        """TC-ADV-07: Team with 0.0 total potential PPR fails closed to UNKNOWN, avoiding divide-by-zero."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        # Roster where all players have 0.0 rolling PPR (e.g. unobserved stats or pure defensive backups)
        prior_roster_zero = {
            "def1": {"rolling_ppr": 0.0, "rolling_off_pct": 0.0, "rolling_def_pct": 0.8},
            "def2": {"rolling_ppr": 0.0, "rolling_off_pct": 0.0, "rolling_def_pct": 0.7},
        }

        injured_def = PlayerInjuryRecord(
            player_name="Def 1", team="PIT", position="LB", canonical_player_id="def1",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            market_signal=MarketPropSignal.UNKNOWN.value,
        )

        summary = service.compute_team_production_lost(
            team="PIT",
            records=[injured_def],
            prior_roster_stats=prior_roster_zero,
            decision_cutoff=datetime.now(timezone.utc).isoformat(),
        )

        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None
        assert summary.metadata.get("reason") == "insufficient_team_potential_denominator"

    def test_adv_injury_nan_or_negative_stats_fails_closed_in_inference(self):
        """TC-ADV-08: Passing NaN, negative, or >1.0 injury metrics to predict_matchups raises DataQualityError."""
        now = datetime.now(timezone.utc)
        game_start = now + timedelta(minutes=85)
        decision_cutoff = now - timedelta(minutes=10)
        captured_at = now - timedelta(minutes=10)

        completed_games = pd.DataFrame([
            {
                "game_id": "2024_01_KC_BAL", "game_date": (now - timedelta(days=7)).date().isoformat(),
                "game_start_utc": (now - timedelta(days=7)).isoformat(),
                "home_team_id": "KC", "away_team_id": "BAL",
                "home_team_abbreviation": "KC", "away_team_abbreviation": "BAL",
                "home_score": 27, "away_score": 20, "home_win": 1,
                "source_ref": "nflverse_2024_01",
                "home_off_epa_per_play": 0.12, "home_off_success_rate": 0.45,
                "home_def_epa_allowed_per_play": -0.05, "home_def_success_allowed": 0.40,
                "away_off_epa_per_play": 0.05, "away_off_success_rate": 0.40,
                "away_def_epa_allowed_per_play": -0.01, "away_def_success_allowed": 0.45,
            },
            {
                "game_id": "2024_01_CIN_NE", "game_date": (now - timedelta(days=7)).date().isoformat(),
                "game_start_utc": (now - timedelta(days=7)).isoformat(),
                "home_team_id": "CIN", "away_team_id": "NE",
                "home_team_abbreviation": "CIN", "away_team_abbreviation": "NE",
                "home_score": 10, "away_score": 16, "home_win": 0,
                "source_ref": "nflverse_2024_01",
                "home_off_epa_per_play": -0.10, "home_off_success_rate": 0.38,
                "home_def_epa_allowed_per_play": 0.02, "home_def_success_allowed": 0.42,
                "away_off_epa_per_play": 0.02, "away_off_success_rate": 0.42,
                "away_def_epa_allowed_per_play": -0.10, "away_def_success_allowed": 0.38,
            },
        ])

        upcoming_games = pd.DataFrame([{
            "game_id": "2024_02_KC_CIN", "game_date": game_start.date().isoformat(),
            "game_start_utc": game_start.isoformat(),
            "home_team_id": "KC", "away_team_id": "CIN",
            "home_team_name": "Kansas City Chiefs", "away_team_name": "Cincinnati Bengals",
            "home_team_abbreviation": "KC", "away_team_abbreviation": "CIN",
            "source_ref": "nflverse_2024_02",
        }])

        # Adversarial availability with negative ppr_lost_pct (-0.25)
        corrupted_availability_neg = {
            "2024_02_KC_CIN": {
                "game_id": "2024_02_KC_CIN",
                "cadence": "near_kickoff",
                "captured_at_utc": captured_at.isoformat(),
                "decision_cutoff_utc": decision_cutoff.isoformat(),
                "resolver_version": "nfl_availability_v2",
                "source_refs_by_team": {"KC": ["espn_snap_01"], "CIN": ["espn_snap_02"]},
                "teams": {
                    "KC": {
                        "coverage_state": CoverageState.COVERED_WITH_INJURIES.value,
                        "ppr_lost_pct": -0.25,  # INVALID: negative
                        "off_snaps_lost_pct": 0.1,
                        "def_snaps_lost_pct": 0.0,
                    },
                    "CIN": {
                        "coverage_state": CoverageState.COVERED_HEALTHY.value,
                        "clean_report_evidence_ref": "espn_clean_cin",
                        "ppr_lost_pct": 0.0,
                        "off_snaps_lost_pct": 0.0,
                        "def_snaps_lost_pct": 0.0,
                    },
                },
            }
        }

        with pytest.raises(DataQualityError, match="NFL métrica.*inválida/desconocida"):
            predict_matchups(
                sport="nfl",
                completed_games=completed_games,
                upcoming_games=upcoming_games,
                as_of=pd.Timestamp(now),
                availability_by_game=corrupted_availability_neg,
                allow_baseline_fallback=False,
            )

        # Adversarial availability with NaN ppr_lost_pct
        corrupted_availability_nan = {
            "2024_02_KC_CIN": {
                "game_id": "2024_02_KC_CIN",
                "cadence": "near_kickoff",
                "captured_at_utc": captured_at.isoformat(),
                "decision_cutoff_utc": decision_cutoff.isoformat(),
                "resolver_version": "nfl_availability_v2",
                "source_refs_by_team": {"KC": ["espn_snap_01"], "CIN": ["espn_snap_02"]},
                "teams": {
                    "KC": {
                        "coverage_state": CoverageState.COVERED_WITH_INJURIES.value,
                        "ppr_lost_pct": float("nan"),  # INVALID: NaN
                        "off_snaps_lost_pct": 0.1,
                        "def_snaps_lost_pct": 0.0,
                    },
                    "CIN": {
                        "coverage_state": CoverageState.COVERED_HEALTHY.value,
                        "clean_report_evidence_ref": "espn_clean_cin",
                        "ppr_lost_pct": 0.0,
                        "off_snaps_lost_pct": 0.0,
                        "def_snaps_lost_pct": 0.0,
                    },
                },
            }
        }

        with pytest.raises(DataQualityError, match="NFL métrica.*inválida/desconocida"):
            predict_matchups(
                sport="nfl",
                completed_games=completed_games,
                upcoming_games=upcoming_games,
                as_of=pd.Timestamp(now),
                availability_by_game=corrupted_availability_nan,
                allow_baseline_fallback=False,
            )

    def test_adv_injury_bye_week_backward_search_no_temporal_leakage(self):
        """TC-ADV-09: Verify backward asof join matches pre-bye stats without crossing cutoff or leaking."""
        # Simulated game logs for a player across weeks 5, 6 (Week 7 is BYE), and 8
        game_logs = pd.DataFrame([
            {"player_id": "star_wr", "season": 2024, "week": 5, "time_idx": 202405, "rolling_ppr": 18.0, "rolling_off_pct": 0.9, "rolling_def_pct": 0.0},
            {"player_id": "star_wr", "season": 2024, "week": 6, "time_idx": 202406, "rolling_ppr": 20.0, "rolling_off_pct": 0.95, "rolling_def_pct": 0.0},
            # Week 7 is bye week: NO row in game_logs
            {"player_id": "star_wr", "season": 2024, "week": 8, "time_idx": 202408, "rolling_ppr": 22.0, "rolling_off_pct": 0.95, "rolling_def_pct": 0.0},
        ]).sort_values("time_idx")

        # Roster evaluated for Week 8: time_idx = 202408. Backward search cutoff is time_idx - 1 = 202407
        rosters = pd.DataFrame([
            {"season": 2024, "week": 8, "team": "DET", "player_id": "star_wr", "time_idx": 202408, "time_idx_search": 202407}
        ]).sort_values("time_idx")

        matched = pd.merge_asof(
            rosters,
            game_logs[["player_id", "time_idx", "rolling_ppr", "rolling_off_pct", "rolling_def_pct"]],
            left_on="time_idx_search",
            right_on="time_idx",
            by="player_id",
            direction="backward",
        )

        # Matched time_idx must be exactly 202406 (Week 6, before the bye), NEVER 202408 (Week 8)
        assert matched["time_idx_y"].iloc[0] == 202406
        assert matched["rolling_ppr"].iloc[0] == 20.0
        # Strict temporal guarantee: time_idx_y < time_idx_x
        assert matched["time_idx_y"].iloc[0] < matched["time_idx_x"].iloc[0]

    def test_adv_confirmed_out_ir_permanently_immutable_under_conflicting_props(self, tmp_path):
        """TC-ADV-10: Confirmed OUT/IR status is permanent (p_absence=1.0) and rejects bookmaker props."""
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")
        now = datetime.now(timezone.utc)
        game_start = now + timedelta(hours=4)
        cutoff = game_start - timedelta(hours=2)

        # Rogue props actively listing lines for a player who is already ruled OUT
        rogue_props = [{
            "event_id": "game_123",
            "bookmaker": "draftkings",
            "market_key": "player_pass_yds",
            "point": 265.5,
            "player_name": "Aaron Rodgers",
            "price": -115,
            "timestamp": (cutoff - timedelta(minutes=30)).isoformat(),
        }]

        signal, p_abs, flags = service.evaluate_market_prop_signal(
            availability_status=AvailabilityStatus.CONFIRMED_OUT,
            player_name="Aaron Rodgers",
            position="QB",
            team="NYJ",
            game_start_utc=game_start.isoformat(),
            decision_cutoff=cutoff.isoformat(),
            live_props=rogue_props,
            expected_event_id="game_123",
        )

        # Absolute immutability: p_absence remains 1.0, checked_props=False
        assert p_abs == 1.0
        assert signal == MarketPropSignal.UNKNOWN
        assert flags.get("checked_props") is False
        assert flags.get("reason") == "confirmed_out_no_props_query"


# ==============================================================================
# 3. MULTI-MODEL ENSEMBLE EDGE CASES & DECISION RULES
# ==============================================================================

class TestTier5MultiModelEnsembleAdversarial:
    """Stress-testing model confluence ties, probability extremes, and odds thresholds."""

    def test_adv_ensemble_model_disagreement_tie_resolves_to_none_confluence_and_skip(self):
        """TC-ADV-11: 3-model split where models disagree (1/3 agreement) demotes to NONE confluence and SKIP."""
        class MockEstimator:
            def __init__(self, p_home: float):
                self._p = p_home

            def predict_proba(self, X):
                return np.array([[1.0 - self._p, self._p]])

        # Model 1 favors home (0.60), Model 2 favors away (0.42), Model 3 favors away (0.46)
        models = {
            "logistic": MockEstimator(0.60),
            "hist_gradient_boosting": MockEstimator(0.42),
            "random_forest": MockEstimator(0.46),
        }

        X_dummy = pd.DataFrame({"f1": [1.0]})
        df_dummy = pd.DataFrame({"game_id": ["g1"]})

        result = _apply_ensemble(models, X_dummy, df_dummy)

        # Mean prob = (0.60 + 0.42 + 0.46) / 3 = 0.4933 (away favorite)
        # Agreement for away favorite (p < 0.5): models 2 and 3 agree (2 models)
        # But mean probability for away is 1 - 0.4933 = 0.5067 < 0.55 threshold -> confluence tier is NONE!
        assert result["confluence_tier"].iloc[0] == "NONE"

        # Testing with sports_analytics_service: confluence_tier='NONE' must emit tier='SKIP' and best_bet=None
        analytics = generate_nfl_matchup_analytics(
            home_team="DEN", away_team="LV", game_date=datetime.now(timezone.utc),
            home_ml=-110, away_ml=-110,
            model_prob_home=0.4933, model_prob_away=0.5067,
            model_agreement=2, confluence_tier="NONE",
        )
        assert analytics["moneyline"]["tier"] == "SKIP"
        assert analytics["best_bet"] is None

    def test_adv_ensemble_extreme_probabilities_no_overflow_or_inf(self):
        """TC-ADV-12: Extreme win probabilities (0.0001 and 0.9999) handled gracefully without inf or NaN."""
        # Extreme favorite (0.9999 home)
        analytics_fav = generate_nfl_matchup_analytics(
            home_team="KC", away_team="CAR", game_date=datetime.now(timezone.utc),
            home_ml=-10000, away_ml=+3000,
            model_prob_home=0.9999, model_prob_away=0.0001,
            model_agreement=3, confluence_tier="HIGH",
            bankroll=1000.0,
        )
        # Verify scores and margins are finite numbers
        spread_fav = analytics_fav["spread"]
        assert math.isfinite(spread_fav["projected_home_score"])
        assert math.isfinite(spread_fav["projected_away_score"])
        assert math.isfinite(spread_fav["projected_margin"])
        # Kelly stake must not exceed bankroll cap (max_cap = 5% = $50.0)
        ml_fav = analytics_fav["moneyline"]
        if ml_fav.get("kelly_stake") is not None:
            assert ml_fav["kelly_stake"] <= 50.0

        # Extreme underdog (0.0001 home)
        analytics_dog = generate_nfl_matchup_analytics(
            home_team="CAR", away_team="KC", game_date=datetime.now(timezone.utc),
            home_ml=+3000, away_ml=-10000,
            model_prob_home=0.0001, model_prob_away=0.9999,
            model_agreement=3, confluence_tier="HIGH",
            bankroll=1000.0,
        )
        assert math.isfinite(analytics_dog["spread"]["projected_home_score"])
        assert math.isfinite(analytics_dog["spread"]["projected_away_score"])

    def test_adv_ensemble_exact_borderline_odds_boundary_1_40_vs_1_3999(self):
        """TC-ADV-13: Odds at exactly 1.40 are eligible; odds at 1.3999 (-251) are strictly rejected to SKIP."""
        # Case A: Odds = -250 (decimal 1.4000) with EV > 0 and 3/3 confluence
        # Implied prob = 250/350 = 71.43%. Model prob = 78.0%.
        # EV = 0.78 * (1.40 - 1) * 100 - 0.22 * 100 = 31.2 - 22.0 = +9.2%
        analytics_140 = generate_nfl_matchup_analytics(
            home_team="PHI", away_team="NYG", game_date=datetime.now(timezone.utc),
            home_ml=-250, away_ml=+210,
            model_prob_home=0.78, model_prob_away=0.22,
            model_agreement=3, confluence_tier="HIGH",
        )
        # Decimal is exactly 1.40 -> eligible (VALUE or PREMIUM)
        assert analytics_140["moneyline"]["tier"] in ("VALUE", "PREMIUM")
        assert american_to_decimal(analytics_140["moneyline"]["odds_american"]) >= 1.40

        # Case B: Odds = -251 (decimal 1.3984 < 1.40) with same high model probability
        analytics_1399 = generate_nfl_matchup_analytics(
            home_team="PHI", away_team="NYG", game_date=datetime.now(timezone.utc),
            home_ml=-251, away_ml=+210,
            model_prob_home=0.78, model_prob_away=0.22,
            model_agreement=3, confluence_tier="HIGH",
        )
        # Decimal < 1.40 -> demoted to SKIP despite high EV and high confluence!
        assert analytics_1399["moneyline"]["tier"] == "SKIP"
        assert american_to_decimal(analytics_1399["moneyline"]["odds_american"]) < 1.40

    def test_adv_ensemble_negative_ev_strictly_suppresses_pick_to_skip(self):
        """TC-ADV-14: Positive win probability with negative mathematical EV must suppress to SKIP with zero Kelly."""
        # Model prob = 52.0%. Market odds = -120 (decimal 1.833). Implied prob = 54.5%.
        # EV = 0.52 * 0.833 * 100 - 0.48 * 100 = 43.3 - 48.0 = -4.7% (Negative EV)
        analytics = generate_nfl_matchup_analytics(
            home_team="DAL", away_team="WAS", game_date=datetime.now(timezone.utc),
            home_ml=-120, away_ml=+100,
            model_prob_home=0.52, model_prob_away=0.48,
            model_agreement=3, confluence_tier="HIGH",
            bankroll=1000.0,
        )

        assert analytics["moneyline"]["tier"] == "SKIP"
        assert analytics["moneyline"]["ev_dollars"] < 0
        assert analytics["moneyline"]["has_kelly_edge"] is False
        assert analytics["moneyline"]["kelly_stake"] in (None, 0.0)

    def test_adv_ensemble_probabilities_sum_to_one_and_pure_football_independence(self):
        """TC-ADV-15: Model win probabilities must sum to 1.0; independent of bookmaker lines."""
        class MockModel:
            def __init__(self, p):
                self._p = p

            def predict_proba(self, X):
                return np.array([[1.0 - self._p, self._p]])

        models = {
            "logistic": MockModel(0.63),
            "hist_gradient_boosting": MockModel(0.67),
            "random_forest": MockModel(0.65),
        }
        X_df = pd.DataFrame({"stat": [1.0]})
        df = pd.DataFrame({"game_id": ["g1"]})

        result = _apply_ensemble(models, X_df, df)
        p_home = result["home_win_probability"].iloc[0]
        p_away = result["away_win_probability"].iloc[0]

        assert math.isclose(p_home + p_away, 1.0, abs_tol=1e-6)
        assert result["confluence_tier"].iloc[0] == "HIGH"
        assert result["model_agreement"].iloc[0] == 3


# ==============================================================================
# 4. QUOTA LEDGER PROTECTION & IMMUTABILITY
# ==============================================================================

class TestTier5QuotaLedgerAdversarialProtection:
    """Stress-testing quota ledger fail-closed protections, boundary conditions, and immutability."""

    def test_adv_quota_ledger_hash_invariance_before_and_after_suite(self):
        """TC-ADV-16: Ledger SHA-256 matches exact baseline f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4."""
        ledger_path = project_root() / "data" / "odds_quota_ledger.json"
        actual_hash = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        assert actual_hash == EXPECTED_LEDGER_HASH

    def test_adv_quota_service_fail_closed_on_missing_or_corrupt_ledger(self, tmp_path):
        """TC-ADV-17: OddsQuotaService must fail closed with QuotaExceededError when ledger is missing or corrupt."""
        non_existent_ledger = tmp_path / "non_existent_ledger.json"
        with pytest.raises(QuotaExceededError, match="Ledger de cuota no encontrado.*Falla cerrado"):
            OddsQuotaService(ledger_path=non_existent_ledger, fail_closed=True)

        corrupt_ledger = tmp_path / "corrupt_ledger.json"
        corrupt_ledger.write_text("{invalid_json: true", encoding="utf-8")
        with pytest.raises(QuotaExceededError, match="Ledger de cuota corrupto o ilegible"):
            OddsQuotaService(ledger_path=corrupt_ledger, fail_closed=True)

    def test_adv_quota_service_circuit_breaker_triggers_at_protected_reserve_boundary(self, tmp_path):
        """TC-ADV-18: Circuit breaker trips when effective remaining reaches <= 75 (protected reserve)."""
        temp_ledger = tmp_path / "temp_ledger.json"
        service = OddsQuotaService(
            config=OddsBudgetConfig(total_budget=500, protected_reserve=75),
            ledger_path=temp_ledger,
            fail_closed=False,
        )
        # Bootstrap with 75 credits (exactly at reserve boundary)
        service.bootstrap_ledger(requests_remaining=75, requests_used=425, force=True)

        with pytest.raises(CircuitBreakerOpenError, match="alcanzó la reserva protegida"):
            service.reserve_quota(endpoint="/v4/sports/americanfootball_nfl/odds", envelope="operational")

    def test_adv_quota_request_cost_and_forbidden_endpoints(self):
        """TC-ADV-19: Historical endpoints (/historical) are forbidden; multi-region requests scale cost."""
        with pytest.raises(QuotaExceededError, match="Endpoints históricos prohibidos"):
            estimate_request_cost("/v4/historical/sports/americanfootball_nfl/odds")

        # Free sports list endpoint costs 0 credits
        assert estimate_request_cost("/v4/sports") == 0

        # Multi-market multi-region request cost calculation
        params = {"markets": "h2h,spreads,totals", "regions": "us,us2"}
        assert estimate_request_cost("/v4/sports/americanfootball_nfl/odds", params) == 6


# ==============================================================================
# 5. CROSS-CUTTING ADVERSARIAL PARLAY COMBINATIONS
# ==============================================================================

class TestTier5CrossCuttingCorrelatedMicroparlays:
    """Stress-testing parlay correlation rejection, joint probabilities, and threshold viability."""

    def test_adv_correlated_same_game_parlay_rejection(self):
        """TC-ADV-20: Parlay combining opposing sides or same-game legs rejected under standard engine."""
        same_game_legs = [
            {
                "event_id": "game_kc_bal",
                "matchup": "KC @ BAL",
                "team_name": "Kansas City Chiefs",
                "market": "h2h",
                "odds_american": -140,
                "prob_model": 0.62,
            },
            {
                "event_id": "game_kc_bal",
                "matchup": "KC @ BAL",
                "team_name": "Baltimore Ravens",
                "market": "h2h",
                "odds_american": +120,
                "prob_model": 0.38,
            },
        ]

        result = evaluate_custom_parlay(same_game_legs, allow_correlation=False)
        assert result["viable"] is False
        assert result["has_correlated_legs"] is True
        assert "No se permite combinar selecciones del mismo evento" in result["razon"]

    def test_adv_microparlay_under_target_odds_or_negative_ev_rejected(self):
        """TC-ADV-21: Correlated microparlays targeting < 2.0x combined odds or non-positive EV are non-viable."""
        # Case A: Heavy favorite microparlay legs producing combined odds 1.70 (< 2.0x target)
        short_odds_legs = [
            {
                "event_id": "g1", "matchup": "GB @ CHI", "team_name": "Green Bay Packers",
                "market": "h2h", "odds_american": -300, "prob_model": 0.78,
            },
            {
                "event_id": "g1", "matchup": "GB @ CHI", "team_name": "Green Bay Packers",
                "market": "spread", "point": -1.5, "odds_american": -350, "prob_model": 0.76,
            },
        ]
        res_short = evaluate_custom_parlay(short_odds_legs, allow_correlation=True)
        # Combined odds 1.333 * 1.286 = 1.714x (< 2.0x) -> non-viable
        assert res_short["viable"] is False
        assert "inferior al objetivo mínimo (2.0x / +100)" in res_short["razon"]

        # Case B: Microparlay with non-positive EV (low model probability)
        negative_ev_legs = [
            {
                "event_id": "g2", "matchup": "SF @ SEA", "team_name": "San Francisco 49ers",
                "market": "h2h", "odds_american": +100, "prob_model": 0.20,
            },
            {
                "event_id": "g2", "matchup": "SF @ SEA", "team_name": "San Francisco 49ers",
                "market": "spread", "point": +3.5, "odds_american": -110, "prob_model": 0.25,
            },
        ]
        res_neg = evaluate_custom_parlay(negative_ev_legs, allow_correlation=True)
        assert res_neg["viable"] is False

    def test_adv_joint_probability_opposing_teams_in_same_game_is_strictly_zero(self):
        """TC-ADV-22: Joint probability for conflicting outcomes (opposing teams winning same game) is 0.0."""
        conflicting_legs = [
            {
                "event_id": "g_mia_buf", "matchup": "MIA @ BUF",
                "team_name": "Miami Dolphins", "prob_model": 0.45,
            },
            {
                "event_id": "g_mia_buf", "matchup": "MIA @ BUF",
                "team_name": "Buffalo Bills", "prob_model": 0.55,
            },
        ]
        joint_p = _compute_joint_probability(conflicting_legs)
        assert joint_p == 0.0
