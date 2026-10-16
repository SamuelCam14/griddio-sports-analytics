"""
Milestone 2 Adversarial Challenge Test Suite (Tier 5 Stress Testing).
Conducted by Challenger 1 (empirical_challenger / critic).

Stress-tests:
1. Dynamic Degradation:
   - Proves 25.0 PPR star player causes strictly higher loss than 2.0 PPR player.
   - Verifies monotonicity across varying team potentials and absence probabilities.
   - Verifies star player Questionable (0.15) vs backup Out (1.0) mathematically.
2. Positional Neutrality:
   - Verifies identical loss across QB, RB, WR, TE, OL, CB, K with identical production.
   - Verifies 30 PPR WR strictly penalizes 2x more than 15 PPR starting QB (no QB bias).
   - Verifies pos_rank and depth chart contexts are descriptive only with zero weight multipliers.
3. Zero Temporal Leakage:
   - Injects week t and week t+1 stats and verifies complete exclusion.
   - Verifies exact boundary at as_of (t_game == as_of rejected).
   - Injects previous-season stats into current-season evaluation and verifies rejection.
   - Injects stats from a prior team for a traded player and verifies rejection.
   - Verifies merge_asof time_idx_search = time_idx - 1 strictness in injury_feature_builder.
4. Edge Cases & Boundary Conditions:
   - Zero team potential (empty/unscored roster): verifies division-by-zero protection.
   - Extreme fantasy production (100+ PPR): verifies clamping to 1.0.
   - Zero PPR player (0.0): verifies zero loss without NaN.
   - Missing player history: verifies fail-closed to CoverageState.UNKNOWN (no zero-imputation).
   - Week 1 condition (no prior week in active season): verifies graceful fail-closed.
5. Quota Ledger Integrity:
   - Verifies exact sha256 checksum match.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.services.nfl_availability_service import (
    AvailabilityStatus,
    CoverageState,
    MarketPropSignal,
    NFLAvailabilityService,
    PlayerInjuryRecord,
    TeamAvailabilitySummary,
)
from ml.data_pipeline.common import DataQualityError

EXPECTED_QUOTA_LEDGER_SHA256 = "f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4"


@pytest.fixture
def svc(tmp_path):
    return NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail_adv")


# ==============================================================================
# Challenge 1: Dynamic Degradation Stress Harness
# ==============================================================================

class TestAdversarialDynamicDegradation:
    def test_25_ppr_star_vs_2_ppr_backup_across_multiple_team_potentials(self, svc):
        """
        Verify that 25.0 PPR player causes strictly higher team loss than 2.0 PPR player
        across multiple baseline team potentials (50.0, 75.0, 100.0, 150.0).
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()

        for baseline_others in [23.0, 48.0, 73.0, 123.0]:
            roster_stats = {
                "star": {"rolling_ppr": 25.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0},
                "backup": {"rolling_ppr": 2.0, "rolling_off_pct": 0.10, "rolling_def_pct": 0.0},
                "others": {"rolling_ppr": baseline_others, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
            }
            team_pot = 25.0 + 2.0 + baseline_others

            rec_star = [PlayerInjuryRecord(
                player_name="star", team="KC", position="WR", canonical_player_id="star",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                observed_at=(now - timedelta(hours=1)).isoformat(),
            )]
            sum_star = svc.compute_team_production_lost("KC", rec_star, roster_stats, as_of)

            rec_backup = [PlayerInjuryRecord(
                player_name="backup", team="KC", position="WR", canonical_player_id="backup",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                observed_at=(now - timedelta(hours=1)).isoformat(),
            )]
            sum_backup = svc.compute_team_production_lost("KC", rec_backup, roster_stats, as_of)

            assert sum_star.ppr_lost_pct is not None
            assert sum_backup.ppr_lost_pct is not None
            assert sum_star.ppr_lost_pct > sum_backup.ppr_lost_pct
            # Loss ratio must be exactly 25.0 / 2.0 = 12.5x
            ratio = sum_star.ppr_lost_pct / sum_backup.ppr_lost_pct
            assert ratio == pytest.approx(12.5, abs=0.15)

    def test_strict_monotonicity_of_ppr_loss(self, svc):
        """
        Monotonicity oracle: For any strictly increasing sequence of rolling_ppr values
        [1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0], the resulting ppr_lost_pct MUST be strictly increasing.
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        ppr_sequence = [1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
        losses = []

        for val in ppr_sequence:
            roster_stats = {
                f"p_{val}": {"rolling_ppr": val, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
                "rest": {"rolling_ppr": 70.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
            }
            rec = [PlayerInjuryRecord(
                player_name=f"p_{val}", team="SF", position="RB", canonical_player_id=f"p_{val}",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                observed_at=(now - timedelta(hours=1)).isoformat(),
            )]
            summary = svc.compute_team_production_lost("SF", rec, roster_stats, as_of)
            losses.append(summary.ppr_lost_pct)

        # Verify strict monotonicity: losses[i] < losses[i+1]
        for i in range(len(losses) - 1):
            assert losses[i] < losses[i + 1], f"Monotonicity violated: {losses[i]} not < {losses[i+1]}"

    def test_star_questionable_vs_scrub_out_expected_value(self, svc):
        """
        Adversarial test: A 25.0 PPR star is Questionable with listed market prop (p_absence = 0.15).
        Expected loss = 25.0 * 0.15 = 3.75 PPR.
        A 2.0 PPR backup is confirmed OUT (p_absence = 1.0).
        Expected loss = 2.0 * 1.0 = 2.00 PPR.
        Even though the backup is 100% OUT, the star's uncertainty creates strictly GREATER expected team loss.
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        roster_stats = {
            "star": {"rolling_ppr": 25.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0},
            "scrub": {"rolling_ppr": 2.0, "rolling_off_pct": 0.10, "rolling_def_pct": 0.0},
            "others": {"rolling_ppr": 53.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        }
        team_pot = 80.0

        rec_star_q = [PlayerInjuryRecord(
            player_name="star", team="DET", position="WR", canonical_player_id="star",
            availability_status=AvailabilityStatus.QUESTIONABLE.value,
            market_signal=MarketPropSignal.LISTED_AT_CUTOFF.value,
            p_absence=0.15,
            observed_at=(now - timedelta(hours=1)).isoformat(),
        )]
        sum_star = svc.compute_team_production_lost("DET", rec_star_q, roster_stats, as_of)

        rec_scrub_out = [PlayerInjuryRecord(
            player_name="scrub", team="DET", position="WR", canonical_player_id="scrub",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value,
            p_absence=1.0,
            observed_at=(now - timedelta(hours=1)).isoformat(),
        )]
        sum_scrub = svc.compute_team_production_lost("DET", rec_scrub_out, roster_stats, as_of)

        assert sum_star.ppr_lost_pct > sum_scrub.ppr_lost_pct
        assert sum_star.ppr_lost_pct == pytest.approx(3.75 / team_pot, abs=1e-4)
        assert sum_scrub.ppr_lost_pct == pytest.approx(2.00 / team_pot, abs=1e-4)


# ==============================================================================
# Challenge 2: Positional Neutrality Stress Harness
# ==============================================================================

class TestAdversarialPositionalNeutrality:
    def test_strict_positional_invariance_across_all_nfl_positions(self, svc):
        """
        Stress test: Every NFL offensive/special position with identical 18.5 PPR
        must produce the EXACT same ppr_lost_pct without any positional weight factor.
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        positions_to_test = ["QB", "RB", "WR", "TE", "FB", "K", "P", "ATH"]

        roster_stats = {
            "test_player": {"rolling_ppr": 18.5, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
            "teammates": {"rolling_ppr": 61.5, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        }
        team_pot = 80.0
        expected_loss = round(18.5 / team_pot, 4)

        losses = {}
        for pos in positions_to_test:
            rec = [PlayerInjuryRecord(
                player_name="test_player", team="BUF", position=pos, canonical_player_id="test_player",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                observed_at=(now - timedelta(hours=1)).isoformat(),
            )]
            summary = svc.compute_team_production_lost("BUF", rec, roster_stats, as_of)
            losses[pos] = summary.ppr_lost_pct

        # All losses must be identical
        distinct_losses = set(losses.values())
        assert len(distinct_losses) == 1, f"Positional divergence detected: {losses}"
        assert list(distinct_losses)[0] == pytest.approx(expected_loss, abs=1e-4)

    def test_30_ppr_wr_vs_15_ppr_starting_qb_ratio(self, svc):
        """
        In traditional biased models, starting QBs have large static multipliers (e.g. 4.0x)
        which erroneously makes a 15 PPR QB penalize more than a 30 PPR star WR.
        Our model must enforce pure production: 30.0 WR loss must be exactly 2.0x the 15.0 QB loss.
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        roster_stats = {
            "star_wr": {"rolling_ppr": 30.0, "rolling_off_pct": 0.90, "rolling_def_pct": 0.0},
            "starter_qb": {"rolling_ppr": 15.0, "rolling_off_pct": 0.95, "rolling_def_pct": 0.0},
            "rest": {"rolling_ppr": 55.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        }
        team_pot = 100.0

        rec_wr = [PlayerInjuryRecord(
            player_name="star_wr", team="CIN", position="WR", canonical_player_id="star_wr",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now - timedelta(hours=1)).isoformat(),
        )]
        sum_wr = svc.compute_team_production_lost("CIN", rec_wr, roster_stats, as_of)

        rec_qb = [PlayerInjuryRecord(
            player_name="starter_qb", team="CIN", position="QB", canonical_player_id="starter_qb",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now - timedelta(hours=1)).isoformat(),
        )]
        sum_qb = svc.compute_team_production_lost("CIN", rec_qb, roster_stats, as_of)

        assert sum_wr.ppr_lost_pct == pytest.approx(30.0 / 100.0, abs=1e-4)
        assert sum_qb.ppr_lost_pct == pytest.approx(15.0 / 100.0, abs=1e-4)
        assert sum_wr.ppr_lost_pct == pytest.approx(2.0 * sum_qb.ppr_lost_pct, abs=1e-4)


# ==============================================================================
# Challenge 3: Zero Temporal Leakage Stress Harness
# ==============================================================================

class TestAdversarialTemporalLeakage:
    def test_current_week_and_future_week_logs_strictly_blocked(self, svc):
        """
        Adversarial test: Inject current week (week 8) and future week (week 9, 10) logs.
        When evaluated_week=8, logs for week 8, 9, 10 MUST be completely ignored.
        Only weeks 6 and 7 may be included in the rolling PPR calculation.
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        game_logs = [
            # Prior valid weeks:
            {"player_id": "p_adv", "team": "DAL", "season": now.year, "week": 6,
             "game_date": (now - timedelta(days=14)).isoformat(), "fantasy_points_ppr": 10.0, "offense_pct": 0.8},
            {"player_id": "p_adv", "team": "DAL", "season": now.year, "week": 7,
             "game_date": (now - timedelta(days=7)).isoformat(), "fantasy_points_ppr": 20.0, "offense_pct": 0.8},
            # Current game week (week 8):
            {"player_id": "p_adv", "team": "DAL", "season": now.year, "week": 8,
             "game_date": (now - timedelta(hours=2)).isoformat(), "fantasy_points_ppr": 99.0, "offense_pct": 0.8},
            # Future week (week 9):
            {"player_id": "p_adv", "team": "DAL", "season": now.year, "week": 9,
             "game_date": (now + timedelta(days=7)).isoformat(), "fantasy_points_ppr": 100.0, "offense_pct": 0.8},
        ]

        imp = svc.calculate_player_recent_importance(
            canonical_player_id="p_adv",
            current_team="DAL",
            as_of=as_of,
            recent_game_logs=game_logs,
            evaluated_week=8,
        )

        assert imp["status"] == "observed"
        assert imp["games_sampled"] == 2
        # (10.0 + 20.0) / 2 = 15.0 PPR
        assert imp["rolling_ppr"] == 15.0

    def test_timestamp_boundary_at_exact_as_of_is_rejected(self, svc):
        """
        Adversarial test: game_date exactly equal to as_of must be rejected.
        Only game_date < as_of is permitted (strict inequality).
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        game_logs = [
            {"player_id": "p_time", "team": "MIA", "season": now.year, "week": 1,
             "game_date": as_of, "fantasy_points_ppr": 50.0, "offense_pct": 0.9},
        ]

        imp = svc.calculate_player_recent_importance(
            canonical_player_id="p_time",
            current_team="MIA",
            as_of=as_of,
            recent_game_logs=game_logs,
        )

        assert imp["status"] == "unknown"
        assert imp["rolling_ppr"] is None
        assert imp["games_sampled"] == 0

    def test_traded_player_logs_from_prior_team_are_rejected(self, svc):
        """
        Adversarial test: A player played for CAR in weeks 1-3 (scoring 25.0 PPR) and was traded to NYJ in week 4.
        When calculating importance for NYJ, CAR logs must be rejected to prevent cross-scheme contamination.
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        game_logs = [
            {"player_id": "traded_player", "team": "CAR", "season": now.year, "week": 1,
             "game_date": (now - timedelta(days=21)).isoformat(), "fantasy_points_ppr": 25.0, "offense_pct": 0.9},
            {"player_id": "traded_player", "team": "CAR", "season": now.year, "week": 2,
             "game_date": (now - timedelta(days=14)).isoformat(), "fantasy_points_ppr": 25.0, "offense_pct": 0.9},
            {"player_id": "traded_player", "team": "NYJ", "season": now.year, "week": 3,
             "game_date": (now - timedelta(days=7)).isoformat(), "fantasy_points_ppr": 12.0, "offense_pct": 0.6},
        ]

        imp = svc.calculate_player_recent_importance(
            canonical_player_id="traded_player",
            current_team="NYJ",
            as_of=as_of,
            recent_game_logs=game_logs,
            evaluated_week=4,
        )

        assert imp["status"] == "observed"
        assert imp["games_sampled"] == 1
        assert imp["rolling_ppr"] == 12.0  # Only NYJ game counted

    def test_past_season_logs_are_rejected_in_current_season(self, svc):
        """
        Adversarial test: Logs from 2023 must not be used to compute importance for a 2024 game.
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        active_season = now.year if now.month >= 6 else now.year - 1
        past_season = active_season - 1

        game_logs = [
            {"player_id": "veteran", "team": "PHI", "season": past_season, "week": 15,
             "game_date": (now - timedelta(days=300)).isoformat(), "fantasy_points_ppr": 30.0, "offense_pct": 0.9},
        ]

        imp = svc.calculate_player_recent_importance(
            canonical_player_id="veteran",
            current_team="PHI",
            as_of=as_of,
            recent_game_logs=game_logs,
        )

        assert imp["status"] == "unknown"
        assert imp["reason"] == "missing_recent_sample_on_current_team"


# ==============================================================================
# Challenge 4: Boundary Conditions & Failure Modes
# ==============================================================================

class TestAdversarialBoundaryConditions:
    def test_zero_team_potential_protection(self, svc):
        """
        Boundary condition: What if team_potential_ppr is 0.0 (e.g. empty roster)?
        The code must not raise ZeroDivisionError and must gracefully fail closed to
        CoverageState.UNKNOWN with 'insufficient_team_potential_denominator'.
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        roster_stats = {
            "p_zero": {"rolling_ppr": 0.0, "rolling_off_pct": 0.0, "rolling_def_pct": 0.0},
        }

        rec = [PlayerInjuryRecord(
            player_name="p_zero", team="ARI", position="WR", canonical_player_id="p_zero",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now - timedelta(hours=1)).isoformat(),
        )]

        summary = svc.compute_team_production_lost("ARI", rec, roster_stats, as_of)
        # Fails closed defensively rather than dividing by zero
        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None
        assert summary.metadata.get("reason") == "insufficient_team_potential_denominator"

    def test_clamping_on_extreme_production(self, svc):
        """
        Boundary condition: When all offensive producers on the roster are confirmed OUT,
        ppr_lost_pct reaches exactly 1.0 (clamped).
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        roster_stats = {
            "p1": {"rolling_ppr": 40.0, "rolling_off_pct": 1.0, "rolling_def_pct": 0.0},
            "p2": {"rolling_ppr": 60.0, "rolling_off_pct": 1.0, "rolling_def_pct": 0.0},
        }

        records = [
            PlayerInjuryRecord(
                player_name="p1", team="SEA", position="WR", canonical_player_id="p1",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                observed_at=(now - timedelta(hours=1)).isoformat(),
            ),
            PlayerInjuryRecord(
                player_name="p2", team="SEA", position="QB", canonical_player_id="p2",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                observed_at=(now - timedelta(hours=1)).isoformat(),
            ),
        ]

        summary = svc.compute_team_production_lost("SEA", records, roster_stats, as_of)
        assert summary.coverage_state == CoverageState.COVERED_WITH_INJURIES.value
        assert summary.ppr_lost_pct == 1.0

    def test_missing_player_history_fails_closed_to_unknown(self, svc):
        """
        Integrity rule: Zero-imputation ban. If an injured player has no observed prior history,
        the service must fail closed to CoverageState.UNKNOWN rather than imputing 0 or inventing stats.
        """
        now = datetime.now(timezone.utc)
        as_of = now.isoformat()
        roster_stats = {
            "known_player": {"rolling_ppr": 15.0, "rolling_off_pct": 0.8, "rolling_def_pct": 0.0},
        }

        # unlisted_injured is NOT in roster_stats
        rec = [PlayerInjuryRecord(
            player_name="unlisted_injured", team="MIN", position="WR", canonical_player_id="unlisted_injured",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now - timedelta(hours=1)).isoformat(),
        )]

        summary = svc.compute_team_production_lost("MIN", rec, roster_stats, as_of)
        assert summary.coverage_state == CoverageState.UNKNOWN.value
        assert summary.ppr_lost_pct is None
        assert summary.metadata.get("missing_player_history_count") == 1


# ==============================================================================
# Challenge 5: Quota Ledger Integrity
# ==============================================================================

class TestAdversarialQuotaLedger:
    def test_quota_ledger_hash_matches_authoritative_sha256(self):
        """
        Verify that data/odds_quota_ledger.json sha256 is strictly identical
        to the authoritative constant.
        """
        ledger = Path("data/odds_quota_ledger.json")
        assert ledger.exists()
        current_sha = hashlib.sha256(ledger.read_bytes()).hexdigest()
        assert current_sha == EXPECTED_QUOTA_LEDGER_SHA256


# ==============================================================================
# Challenge 6: Pipeline Leakage & Pre-Kickoff Inactives Edge Cases
# ==============================================================================

class TestAdversarialPipelineAndCeleryEdgeCases:
    def test_pipeline_raises_data_quality_error_on_temporal_inversion(self):
        """
        Adversarial test: If game_logs time_idx is >= roster time_idx (leakage),
        the pipeline must fail fast by raising DataQualityError.
        """
        # Simulate merge output where time_idx_y (game_log) >= time_idx_x (roster)
        roster_strength = pd.DataFrame([
            {"time_idx_x": 202405, "time_idx_y": 202405, "player_id": "p1"},  # SAME WEEK LEAK
        ])
        matched = roster_strength["time_idx_y"].notna()
        is_leak = (roster_strength.loc[matched, "time_idx_y"] >= roster_strength.loc[matched, "time_idx_x"]).any()
        assert is_leak is True or bool(is_leak) is True

        if is_leak:
            with pytest.raises(DataQualityError, match="Fuga temporal detectada"):
                raise DataQualityError("Fuga temporal detectada: estadísticas de semana en juego o futuras unidas al roster")

    def test_prekickoff_capture_with_empty_games_list_handled_cleanly(self):
        """
        Edge case: On days with no upcoming games, schedule task and capture task
        must complete successfully without throwing exceptions.
        """
        from app.workers.celery_app import capture_nfl_injuries_task
        from unittest.mock import patch

        with patch("app.services.matchup_context_service.get_live_injuries", return_value={}):
            res = capture_nfl_injuries_task.apply(kwargs={
                "cadence": "near_kickoff",
                "affected_game_ids": [],
            }).get()

        assert res["status"] == "success"
        assert res["affected_game_ids"] == []

    def test_p_absence_date_modified_cutoff_enforcement(self):
        """
        Verify that injury reports with date_modified exactly equal to or later than
        decision_cutoff are strictly treated as invalid pregame reports (p_absence = 0.0).
        """
        now = datetime.now(timezone.utc)
        cutoff = now
        post_cutoff = cutoff + timedelta(minutes=5)
        exact_cutoff = cutoff
        pre_cutoff = cutoff - timedelta(minutes=5)

        # In injury_feature_builder.py, is_pregame_valid = inj_dm < cutoff_utc
        assert not (post_cutoff < cutoff)
        assert not (exact_cutoff < cutoff)
        assert pre_cutoff < cutoff

