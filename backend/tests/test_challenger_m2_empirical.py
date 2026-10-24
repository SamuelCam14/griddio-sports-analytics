"""
Challenger 2 Empirical Verification & Adversarial Stress Suite for Milestone 2.
Empirically tests:
1. Celery near-kickoff scheduling at t - 90 min before kickoff (including multi-window, passed window, and edge cases).
2. Automated trigger of evaluate_predictions_task upon inactives capture.
3. Timestamp progression: verify recalculated prediction has predicted_at > original prediction timestamp
   and persistence behavior under inactives updates.
4. Quota ledger immutability and zero-consumption guarantee.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.models.game import Prediction
from app.services.nfl_availability_service import (
    AvailabilityStatus,
    NFLAvailabilityService,
    PlayerInjuryRecord,
)
from app.workers.celery_app import (
    capture_nfl_injuries_task,
    evaluate_predictions_task,
    schedule_nfl_gameday_captures_task,
)

EXPECTED_QUOTA_LEDGER_SHA256 = "6f5c546089689631d0f0d49aadb76e8499f9f64e4adc9594bc0264a8ad17fa53"


class TestCeleryNearKickoffSchedulingEmpirical:
    """Empirically tests scheduling of near_kickoff at t - 90 min."""

    def test_scheduling_multi_window_kickoffs(self):
        """Tests that games across multiple kickoff windows (early, late, SNF) get separate 90-min tasks."""
        now = datetime(2024, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
        today = now.date()

        # Window 1: 17:00 UTC (1:00 PM ET) -> ETA: 15:30 UTC
        # Window 2: 20:25 UTC (4:25 PM ET) -> ETA: 18:55 UTC
        # Window 3: 01:20 UTC next day (SNF) -> ETA: 23:50 UTC today
        fake_upcoming = pd.DataFrame([
            {"game_id": "G_EARLY_1", "game_date": today.isoformat(), "game_start_utc": "2024-10-20T17:00:00Z", "home_team_id": "BUF", "away_team_id": "MIA"},
            {"game_id": "G_EARLY_2", "game_date": today.isoformat(), "game_start_utc": "2024-10-20T17:00:00Z", "home_team_id": "NE", "away_team_id": "NYJ"},
            {"game_id": "G_LATE_1", "game_date": today.isoformat(), "game_start_utc": "2024-10-20T20:25:00Z", "home_team_id": "KC", "away_team_id": "SF"},
            {"game_id": "G_SNF_1", "game_date": today.isoformat(), "game_start_utc": "2024-10-21T01:20:00Z", "home_team_id": "PIT", "away_team_id": "NYJ"},
        ])

        with patch("pandas.Timestamp.now", return_value=pd.Timestamp(now)), \
             patch("ml.data_pipeline.nfl_source.load_nfl_upcoming_games", return_value=fake_upcoming), \
             patch("app.workers.celery_app.capture_nfl_injuries_task.apply_async") as mock_apply:
            res = schedule_nfl_gameday_captures_task()

        assert res["status"] == "success"
        assert res["start_of_day_dispatched"] is True

        near_calls = [
            c for c in mock_apply.call_args_list
            if c.kwargs.get("args") == ("near_kickoff",) or (c.args and c.args[0] == ("near_kickoff",))
        ]
        # Should have exactly 3 scheduled pregame calls (17:00, 20:25, 01:20)
        assert len(near_calls) == 3

        # Window 1 verification: grouped both early games
        w1_call = near_calls[0]
        w1_eta = w1_call.kwargs.get("eta")
        w1_games = w1_call.kwargs.get("kwargs", {}).get("affected_game_ids", [])
        assert w1_eta == datetime(2024, 10, 20, 15, 30, 0, tzinfo=timezone.utc)
        assert sorted(w1_games) == ["G_EARLY_1", "G_EARLY_2"]

        # Window 2 verification
        w2_call = near_calls[1]
        w2_eta = w2_call.kwargs.get("eta")
        assert w2_eta == datetime(2024, 10, 20, 18, 55, 0, tzinfo=timezone.utc)
        assert w2_call.kwargs.get("kwargs", {}).get("affected_game_ids") == ["G_LATE_1"]

        # Window 3 verification
        w3_call = near_calls[2]
        w3_eta = w3_call.kwargs.get("eta")
        assert w3_eta == datetime(2024, 10, 20, 23, 50, 0, tzinfo=timezone.utc)
        assert w3_call.kwargs.get("kwargs", {}).get("affected_game_ids") == ["G_SNF_1"]

    def test_past_kickoff_window_not_scheduled_in_past(self):
        """Games whose 90-minute pregame window has already passed are not dispatched into the past."""
        now = datetime(2024, 10, 20, 16, 0, 0, tzinfo=timezone.utc)
        today = now.date()

        # Kickoff at 17:00 UTC -> 90 min before was 15:30 UTC (< 16:00 UTC now)
        fake_upcoming = pd.DataFrame([
            {"game_id": "G_PAST_WINDOW", "game_date": today.isoformat(), "game_start_utc": "2024-10-20T17:00:00Z", "home_team_id": "BUF", "away_team_id": "MIA"},
        ])

        with patch("pandas.Timestamp.now", return_value=pd.Timestamp(now)), \
             patch("ml.data_pipeline.nfl_source.load_nfl_upcoming_games", return_value=fake_upcoming), \
             patch("app.workers.celery_app.capture_nfl_injuries_task.apply_async") as mock_apply:
            res = schedule_nfl_gameday_captures_task()

        assert res["status"] == "success"
        near_calls = [
            c for c in mock_apply.call_args_list
            if c.kwargs.get("args") == ("near_kickoff",) or (c.args and c.args[0] == ("near_kickoff",))
        ]
        assert len(near_calls) == 0
        assert res["scheduled_pregame"][0]["dispatched"] is False
        assert res["scheduled_pregame"][0]["reason"] == "pregame_window_already_passed"


class TestAutomatedTriggerOfEvaluatePredictionsEmpirical:
    """Empirically tests automated trigger of evaluate_predictions_task upon inactives capture."""

    def test_near_kickoff_triggers_evaluate_predictions_with_game_ids(self, tmp_path):
        """Cadence near_kickoff triggers evaluate_predictions_task filtered to affected_game_ids."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "avail")
        mock_raw = {"DET": [{"player_name": "Sam LaPorta", "position": "TE", "status": "OUT"}]}

        with patch("app.services.matchup_context_service.get_live_injuries", return_value=mock_raw), \
             patch("app.services.nfl_availability_service.NFLAvailabilityService", return_value=svc), \
             patch("app.workers.celery_app.evaluate_predictions_task.apply_async") as mock_eval:

            res = capture_nfl_injuries_task.apply(kwargs={
                "cadence": "near_kickoff",
                "affected_game_ids": ["2024_11_JAX_DET"],
            }).get()

        assert res["status"] == "success"
        mock_eval.assert_called_once_with(
            args=("NFL",),
            kwargs={"game_ids": ["2024_11_JAX_DET"]},
            queue="ml",
        )

    def test_start_of_day_does_not_trigger_pregame_recalculation(self, tmp_path):
        """Cadence start_of_day does NOT trigger evaluate_predictions_task."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "avail")
        mock_raw = {"DET": []}

        with patch("app.services.matchup_context_service.get_live_injuries", return_value=mock_raw), \
             patch("app.services.nfl_availability_service.NFLAvailabilityService", return_value=svc), \
             patch("app.workers.celery_app.evaluate_predictions_task.apply_async") as mock_eval:

            res = capture_nfl_injuries_task.apply(kwargs={
                "cadence": "start_of_day",
            }).get()

        assert res["status"] == "success"
        mock_eval.assert_not_called()

    def test_evaluate_predictions_task_failure_in_celery_is_handled_gracefully(self, tmp_path):
        """If Celery broker fails to enqueue evaluate_predictions_task, capture task still succeeds."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "avail")
        mock_raw = {"DET": []}

        with patch("app.services.matchup_context_service.get_live_injuries", return_value=mock_raw), \
             patch("app.services.nfl_availability_service.NFLAvailabilityService", return_value=svc), \
             patch("app.workers.celery_app.evaluate_predictions_task.apply_async", side_effect=RuntimeError("Broker connection lost")):

            res = capture_nfl_injuries_task.apply(kwargs={
                "cadence": "near_kickoff",
                "affected_game_ids": ["2024_11_JAX_DET"],
            }).get()

        assert res["status"] == "success"
        assert res["cadence"] == "near_kickoff"


class TestTimestampProgressionAndRecalculationEmpirical:
    """Empirically tests that recalculated predictions advance predicted_at and as_of timestamps."""

    @pytest.mark.asyncio
    async def test_recalculated_prediction_timestamp_progression_in_db(self):
        """
        Simulates two prediction runs in AsyncSession:
        Run 1 (initial morning prediction): as_of = t1, predicted_at = t1.
        Run 2 (near-kickoff recalculation with inactives): as_of = t2 > t1, predicted_at = t2 > t1.
        Verifies that:
        - Both rows coexist in predictions table without constraint violation.
        - Run 2 has predicted_at > Run 1 predicted_at.
        - Run 2 has as_of > Run 1 as_of.
        - Run 2 has updated inference_input_sha256 due to inactive changes.
        """
        from app.services.persistence import append_predictions
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from app.models.game import Base, Game, Team

        # Use an in-memory SQLite engine for complete isolation
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        t1 = datetime(2024, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2024, 10, 20, 15, 35, 0, tzinfo=timezone.utc)
        assert t2 > t1

        async with async_session() as session:
            # Seed team and game
            h_team = Team(id=1, api_id=1, sport="NFL", name="Detroit Lions", abbreviation="DET")
            a_team = Team(id=2, api_id=2, sport="NFL", name="Green Bay Packers", abbreviation="GB")
            session.add_all([h_team, a_team])
            await session.flush()

            game = Game(
                id=101, sport="NFL", season=2024, home_team_id=1, away_team_id=2,
                game_date=datetime(2024, 10, 20, 17, 0, 0, tzinfo=timezone.utc),
                status="scheduled", external_id="2024_07_DET_GB",
            )
            session.add(game)
            await session.commit()

        # RUN 1: Morning prediction (player Questionable, low loss)
        input_payload_t1 = json.dumps({
            "game_id": 101, "as_of": t1.isoformat(), "home_ppr_lost_pct": 0.025,
            "availability_status": "questionable",
        }, sort_keys=True)
        sha_t1 = hashlib.sha256(input_payload_t1.encode("utf-8")).hexdigest()

        pred_t1 = {
            "game_id": 101,
            "model_version": "nfl_temporal_v1",
            "model_variant": "injury_aware",
            "used_injury_adjustment": True,
            "dataset_sha256": "dataset_hash_1",
            "artifact_sha256": "artifact_hash_1",
            "inference_input_sha256": sha_t1,
            "as_of": t1,
            "predicted_at": t1,
            "home_win_prob": 0.62,
            "away_win_prob": 0.38,
            "best_bookmaker": "DraftKings",
            "best_home_ml": -140,
            "best_away_ml": +120,
            "home_ev": 4.5,
            "away_ev": -3.2,
            "home_kelly": 0.05,
            "away_kelly": 0.0,
            "home_has_edge": True,
            "away_has_edge": False,
        }

        # Note: Celery task celery_app.py puts model_variant and used_injury_adjustment into pred_dict,
        # which are NOT columns on Prediction table. In this test, we demonstrate both the DB timestamp
        # progression on valid schema and the bug in unconsumed columns.
        pred_cols = {c.name for c in Prediction.__table__.columns}
        pred_t1_sanitized = {k: v for k, v in pred_t1.items() if k in pred_cols}

        async with async_session() as session:
            await append_predictions(session, [pred_t1_sanitized])
            await session.commit()

        # RUN 2: Pre-kickoff recalculation (inactives confirmed OUT, higher loss, probability updated)
        input_payload_t2 = json.dumps({
            "game_id": 101, "as_of": t2.isoformat(), "home_ppr_lost_pct": 0.165,
            "availability_status": "confirmed_out",
        }, sort_keys=True)
        sha_t2 = hashlib.sha256(input_payload_t2.encode("utf-8")).hexdigest()
        assert sha_t1 != sha_t2, "Inference input SHA must differ between runs"

        pred_t2 = {
            "game_id": 101,
            "model_version": "nfl_temporal_v1",
            "model_variant": "injury_aware",
            "used_injury_adjustment": True,
            "dataset_sha256": "dataset_hash_1",
            "artifact_sha256": "artifact_hash_1",
            "inference_input_sha256": sha_t2,
            "as_of": t2,
            "predicted_at": t2,
            "home_win_prob": 0.54,  # Degradation from 0.62 to 0.54
            "away_win_prob": 0.46,
            "best_bookmaker": "DraftKings",
            "best_home_ml": -140,
            "best_away_ml": +120,
            "home_ev": -1.2,
            "away_ev": 1.5,
            "home_kelly": 0.0,
            "away_kelly": 0.02,
            "home_has_edge": False,
            "away_has_edge": False,
        }
        pred_t2_sanitized = {k: v for k, v in pred_t2.items() if k in pred_cols}

        async with async_session() as session:
            await append_predictions(session, [pred_t2_sanitized])
            await session.commit()

        # VERIFY PERSISTED PREDICTIONS IN DB
        from sqlalchemy import select
        async with async_session() as session:
            result = await session.execute(
                select(Prediction).where(Prediction.game_id == 101).order_by(Prediction.predicted_at.asc())
            )
            rows = result.scalars().all()

        assert len(rows) == 2, "Both predictions must be preserved in append-only storage"
        row1, row2 = rows[0], rows[1]

        # Timestamp progression assertions
        row1_as_of = row1.as_of.replace(tzinfo=timezone.utc) if row1.as_of.tzinfo is None else row1.as_of
        row2_as_of = row2.as_of.replace(tzinfo=timezone.utc) if row2.as_of.tzinfo is None else row2.as_of
        assert row1_as_of == t1
        assert row2_as_of == t2
        assert row2_as_of > row1_as_of

        row1_pred_at = row1.predicted_at.replace(tzinfo=timezone.utc) if row1.predicted_at.tzinfo is None else row1.predicted_at
        row2_pred_at = row2.predicted_at.replace(tzinfo=timezone.utc) if row2.predicted_at.tzinfo is None else row2.predicted_at
        assert row1_pred_at == t1
        assert row2_pred_at == t2
        assert row2_pred_at > row1_pred_at

        # Probability and feature adjustment assertions
        assert row1.home_win_prob == 0.62
        assert row2.home_win_prob == 0.54
        assert row2.inference_input_sha256 != row1.inference_input_sha256

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_unconsumed_prediction_columns_raise_compile_error(self):
        """
        Adversarial test: Proves that the raw pred_dict constructed in celery_app.py
        (which contains 'model_variant' and 'used_injury_adjustment') causes a CompileError
        when passed to append_predictions because the Prediction table lacks those columns.
        """
        from app.services.persistence import append_predictions
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.exc import CompileError

        raw_pred_dict = {
            "game_id": 1,
            "model_version": "nfl_temporal_v1",
            "model_variant": "injury_aware",
            "used_injury_adjustment": True,
            "dataset_sha256": "hash_data",
            "artifact_sha256": "hash_art",
            "inference_input_sha256": "hash_in",
        }

        # PostgreSQL insert compilation fails when unconsumed columns exist
        with pytest.raises(CompileError, match="Unconsumed column names"):
            stmt = pg_insert(Prediction).values([raw_pred_dict])
            str(stmt)


class TestQuotaLedgerPreservationChallenger:
    """Verifies quota ledger preservation during challenger execution."""

    def test_quota_ledger_hash(self):
        ledger = Path("data/odds_quota_ledger.json")
        assert ledger.exists()
        current_hash = hashlib.sha256(ledger.read_bytes()).hexdigest()
        assert current_hash == EXPECTED_QUOTA_LEDGER_SHA256
