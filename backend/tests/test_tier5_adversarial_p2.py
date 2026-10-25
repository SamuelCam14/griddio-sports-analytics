"""
Tier 5 Adversarial Concurrency & Mathematical Hardening Test Suite (Part 2).
Author: Challenger 2 (challenger_tier5_2)
Project: NFL Sports Analytics MVP

Covers:
1. Spread / handicap mathematical edge cases:
   - Extreme lines (+45.0, -45.0)
   - Exact pushes (margin == spread line)
   - High total variances (total_line = 120.0, 16.0, None)
   - Inverted home/away spreads and contrarian lines
2. Correlated microparlays mathematical edge cases:
   - Degenerate covariance matrices and boundary probabilities (0.01, 0.99)
   - Negative correlation and contradictory same-game selections
   - Exact return threshold at 2.0x vs 1.99x
   - Negative EV combinations and uncompensated vig rejection
3. Celery pre-kickoff triggers & concurrency:
   - Rapid duplicate triggers in t-90m window
   - Idempotent PostgreSQL append_predictions with ON CONFLICT DO NOTHING
   - Concurrent race condition safety under parallel async sessions
   - Fail-closed validation on missing deterministic key components
4. API concurrency & zero outbound network calls:
   - 25 parallel async requests to /api/v1/predictions/today
   - 25 parallel async requests to /api/v1/parlay/recommended
   - Multithreaded mixed API stress test
   - Strict network interception verifying 0 external network requests
5. Quota ledger preservation:
   - Verification of data/odds_quota_ledger.json SHA-256 hash invariance
   - Circuit breaker and prohibited endpoints enforcement
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import httpx
import pytest
from scipy.stats import norm
from sqlalchemy import func, select, text

from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.game import Game, OddsSnapshot, Prediction, Team
from app.services.odds_quota_service import (
    CircuitBreakerOpenError,
    OddsBudgetConfig,
    OddsQuotaService,
    QuotaExceededError,
    estimate_request_cost,
)
from app.services.parlay_service import (
    _compute_joint_probability,
    evaluate_correlated_microparlay,
    evaluate_custom_parlay,
)
from app.services.persistence import append_predictions
from app.services.sports_analytics_service import generate_nfl_matchup_analytics
from app.workers.celery_app import capture_nfl_injuries_task
from ml.data_pipeline.common import project_root

EXPECTED_LEDGER_SHA256 = "929d8ad7c01c34211e7812d0f1d1e77010c0c149230d673eb8ec51df87c658f1"


@pytest.fixture(autouse=True)
def verify_quota_ledger_unaltered():
    """Guarantee that data/odds_quota_ledger.json is byte-for-byte invariant across all tests."""
    ledger_path = project_root() / "data" / "odds_quota_ledger.json"
    if not ledger_path.is_file():
        yield
        return

    before_bytes = ledger_path.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    assert before_hash == EXPECTED_LEDGER_SHA256, (
        f"Pre-test ledger hash mismatch: {before_hash} != {EXPECTED_LEDGER_SHA256}"
    )

    yield

    after_bytes = ledger_path.read_bytes()
    after_hash = hashlib.sha256(after_bytes).hexdigest()
    assert after_hash == EXPECTED_LEDGER_SHA256, (
        f"CRITICAL VIOLATION: odds_quota_ledger.json was mutated during test execution! "
        f"Expected {EXPECTED_LEDGER_SHA256}, got {after_hash}"
    )


class TestSpreadHandicapMathematicalHardening:
    """Mathematical and numerical edge-case stress testing for spread/handicap analytics."""

    def test_spread_extreme_lines_positive_45(self):
        """TC-SPREAD-01: Line at +45.0 on toss-up (home underdog +45) has near-1.0 cover prob, bounded Kelly."""
        g_date = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
        home = "Denver Broncos"
        away = "Kansas City Chiefs"

        res = generate_nfl_matchup_analytics(
            home_team=home,
            away_team=away,
            game_date=g_date,
            home_ml=-110,
            away_ml=-110,
            spread_line=45.0,
            total_line=44.0,
            model_prob_home=0.50,
            model_prob_away=0.50,
            bankroll=1000.0,
        )
        spread = res["spread"]
        assert spread["status"] == "available"
        assert spread["market_line"] == 45.0
        assert spread["pick"] == f"{home} +45.0"
        assert spread["cover_probability"] > 0.999
        assert spread["edge_pct"] > 49.0
        assert spread["ev_dollars"] > 90.0
        assert spread["tier"] == "PREMIUM"
        assert spread["has_kelly_edge"] is True
        # Kelly stake must be strictly positive and capped at max allowed fraction (0.03 * 1000 = $30)
        assert 0.0 < spread["kelly_stake"] <= 30.0
        # Score sum and difference consistency
        assert math.isclose(spread["projected_home_score"], 22.0, abs_tol=0.1)
        assert math.isclose(spread["projected_away_score"], 22.0, abs_tol=0.1)
        assert math.isclose(spread["projected_margin"], 0.0, abs_tol=0.1)

    def test_spread_extreme_lines_negative_45(self):
        """TC-SPREAD-02: Line at -45.0 on toss-up gives away team +45.0 with near-1.0 cover prob."""
        g_date = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
        home = "Denver Broncos"
        away = "Kansas City Chiefs"

        res = generate_nfl_matchup_analytics(
            home_team=home,
            away_team=away,
            game_date=g_date,
            home_ml=-110,
            away_ml=-110,
            spread_line=-45.0,
            total_line=44.0,
            model_prob_home=0.50,
            model_prob_away=0.50,
            bankroll=1000.0,
        )
        spread = res["spread"]
        assert spread["status"] == "available"
        assert spread["market_line"] == -45.0
        assert spread["pick"] == f"{away} +45.0"
        assert spread["cover_probability"] > 0.999
        assert spread["edge_pct"] > 49.0
        assert spread["ev_dollars"] > 90.0
        assert spread["tier"] == "PREMIUM"
        assert spread["has_kelly_edge"] is True
        assert 0.0 < spread["kelly_stake"] <= 30.0

    def test_spread_exact_push_pickem_zero_spread(self):
        """TC-SPREAD-03: Exact push at 0.0 line with 0.0 projected margin yields SKIP, None pick, Kelly=0."""
        g_date = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
        res = generate_nfl_matchup_analytics(
            home_team="Denver Broncos",
            away_team="Kansas City Chiefs",
            game_date=g_date,
            home_ml=-110,
            away_ml=-110,
            spread_line=0.0,
            total_line=44.0,
            model_prob_home=0.50,
            model_prob_away=0.50,
            bankroll=1000.0,
        )
        spread = res["spread"]
        assert spread["status"] == "available"
        assert spread["projected_margin"] == 0.0
        assert spread["cover_probability"] == 0.50
        assert spread["edge_pct"] == 0.0
        # House vig at -110 (1.9091) makes 50/50 proposition negative EV (-4.55%)
        assert spread["ev_dollars"] < 0.0
        assert spread["pick"] is None, "Exact push must not emit a spread pick"
        assert spread["tier"] == "SKIP"
        assert spread["has_kelly_edge"] is False
        assert spread["kelly_stake"] in (0.0, None)

    def test_spread_exact_push_margin_equals_spread_line(self):
        """TC-SPREAD-04: Non-zero push where projected margin (+7.0) exactly cancels market line (-7.0)."""
        g_date = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
        p_home = float(norm.cdf(7.0 / 13.5))

        res = generate_nfl_matchup_analytics(
            home_team="Denver Broncos",
            away_team="Kansas City Chiefs",
            game_date=g_date,
            home_ml=-230,
            away_ml=+190,
            spread_line=-7.0,
            total_line=44.0,
            model_prob_home=p_home,
            model_prob_away=1.0 - p_home,
            bankroll=1000.0,
        )
        spread = res["spread"]
        assert spread["status"] == "available"
        assert math.isclose(spread["projected_margin"], 7.0, abs_tol=0.1)
        assert math.isclose(spread["projected_home_score"], 25.5, abs_tol=0.1)
        assert math.isclose(spread["projected_away_score"], 18.5, abs_tol=0.1)
        # z_cover = (7.0 + (-7.0)) / 13.5 = 0.0 => cover prob = 0.50
        assert math.isclose(spread["cover_probability"], 0.50, abs_tol=1e-3)
        assert spread["pick"] is None
        assert spread["tier"] == "SKIP"
        assert spread["has_kelly_edge"] is False
        assert spread["kelly_stake"] in (0.0, None)

    def test_spread_high_total_variance_and_score_consistency(self):
        """TC-SPREAD-05: Projected margin is invariant to total_line, scores sum to total_line."""
        g_date = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
        p_home = 0.65
        p_away = 0.35

        # Case A: Extreme high shootout total (120.0)
        res_high = generate_nfl_matchup_analytics(
            home_team="Denver Broncos",
            away_team="Kansas City Chiefs",
            game_date=g_date,
            home_ml=-150,
            away_ml=+130,
            spread_line=-3.5,
            total_line=120.0,
            model_prob_home=p_home,
            model_prob_away=p_away,
        )
        spread_high = res_high["spread"]
        assert math.isclose(
            spread_high["projected_home_score"] + spread_high["projected_away_score"],
            120.0,
            abs_tol=0.2,
        )

        # Case B: Extreme low defensive total (16.0)
        res_low = generate_nfl_matchup_analytics(
            home_team="Denver Broncos",
            away_team="Kansas City Chiefs",
            game_date=g_date,
            home_ml=-150,
            away_ml=+130,
            spread_line=-3.5,
            total_line=16.0,
            model_prob_home=p_home,
            model_prob_away=p_away,
        )
        spread_low = res_low["spread"]
        assert math.isclose(
            spread_low["projected_home_score"] + spread_low["projected_away_score"],
            16.0,
            abs_tol=0.2,
        )

        # Case C: Total not available (None)
        res_none = generate_nfl_matchup_analytics(
            home_team="Denver Broncos",
            away_team="Kansas City Chiefs",
            game_date=g_date,
            home_ml=-150,
            away_ml=+130,
            spread_line=-3.5,
            total_line=None,
            model_prob_home=p_home,
            model_prob_away=p_away,
        )
        spread_none = res_none["spread"]

        # Invariance: projected margin and cover probability must be identical across all three!
        assert math.isclose(spread_high["projected_margin"], spread_low["projected_margin"], abs_tol=1e-2)
        assert math.isclose(spread_high["projected_margin"], spread_none["projected_margin"], abs_tol=1e-2)
        assert math.isclose(spread_high["cover_probability"], spread_low["cover_probability"], abs_tol=1e-4)
        assert math.isclose(spread_high["cover_probability"], spread_none["cover_probability"], abs_tol=1e-4)

    def test_spread_inverted_lines_contrarian_market_edge(self):
        """TC-SPREAD-06: Contrarian market error (model heavily favors away, market made home favorite)."""
        g_date = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
        home = "Denver Broncos"
        away = "Kansas City Chiefs"

        # Model favors Chiefs heavily (p_away = 0.80, p_home = 0.20)
        # But market line is Denver -3.5 (market favors Denver!)
        res = generate_nfl_matchup_analytics(
            home_team=home,
            away_team=away,
            game_date=g_date,
            home_ml=-170,
            away_ml=+145,
            spread_line=-3.5,
            total_line=44.0,
            model_prob_home=0.20,
            model_prob_away=0.80,
            model_agreement=3,
            confluence_tier="HIGH",
        )
        spread = res["spread"]
        assert spread["status"] == "available"
        assert spread["projected_margin"] < -10.0, "Away team projected margin must be negative from home perspective"
        # Since Chiefs are away and Denver is -3.5, Chiefs receive +3.5 while projected to win outright
        assert spread["pick"] == f"{away} +3.5"
        assert spread["cover_probability"] > 0.80
        assert spread["tier"] == "PREMIUM"
        assert spread["has_kelly_edge"] is True


class TestCorrelatedMicroparlaysMathematicalHardening:
    """Mathematical and numerical edge-case stress testing for correlated microparlays."""

    def test_microparlay_combined_odds_exact_threshold_2_0x_vs_1_99x(self):
        """TC-PARLAY-01: Boundary test: combined decimal odds >= 2.0x is eligible, 1.984x is rejected."""
        # Case A: Exact 2.00x (+100) return with positive EV
        # Leg 1: -200 (1.50x), p=0.70 (edge = 0.70 - 0.667 = +0.033)
        # Leg 2: -300 (1.333x), p=0.75 (edge = 0.75 - 0.750 = 0.0)
        # Combined decimal odds: 1.50 * 1.3333 = 2.00x
        legs_2x = [
            {"game_id": 101, "team_name": "Kansas City Chiefs", "market": "h2h", "prob_model": 0.70, "odds_american": -200},
            {"game_id": 101, "team_name": "Kansas City Chiefs", "market": "spread", "point": -3.5, "prob_model": 0.75, "odds_american": -300},
        ]
        res_2x = evaluate_correlated_microparlay(legs_2x)
        assert res_2x["viable"] is True
        assert res_2x["momio_decimal"] == 2.0
        assert res_2x["ev_dolares"] > 0.0
        assert "Microparlay correlacionado viable" in res_2x["razon"]

        # Case B: Sub-2.0x return (1.984x) with positive EV must be rejected
        legs_sub_2x = [
            {"game_id": 101, "team_name": "Kansas City Chiefs", "market": "h2h", "prob_model": 0.70, "odds_american": -200},
            {"game_id": 101, "team_name": "Kansas City Chiefs", "market": "spread", "point": -3.5, "prob_model": 0.77, "odds_american": -310},
        ]
        res_sub_2x = evaluate_correlated_microparlay(legs_sub_2x)
        assert res_sub_2x["viable"] is False
        assert res_sub_2x["momio_decimal"] < 2.0
        assert "inferior al objetivo mínimo (2.0x / +100)" in res_sub_2x["razon"]

    def test_microparlay_negative_ev_combinations_rejected(self):
        """TC-PARLAY-02: Microparlays where bookmaker vig eats edge yield negative EV and strict rejection."""
        legs_bad_ev = [
            {"game_id": 102, "team_name": "Kansas City Chiefs", "market": "h2h", "prob_model": 0.70, "odds_american": -500},
            {"game_id": 102, "team_name": "Kansas City Chiefs", "market": "spread", "point": -3.5, "prob_model": 0.72, "odds_american": -400},
        ]
        res = evaluate_correlated_microparlay(legs_bad_ev)
        assert res["viable"] is False
        assert "inferior al objetivo mínimo" in res["razon"] or "EV no positivo" in res["razon"]

        # Case where odds >= 2.0x but joint EV is negative
        legs_neg_ev_2x = [
            {"game_id": 103, "team_name": "Buffalo Bills", "market": "h2h", "prob_model": 0.40, "odds_american": -110},
            {"game_id": 103, "team_name": "Buffalo Bills", "market": "spread", "point": -1.5, "prob_model": 0.38, "odds_american": -300},
        ]
        res_neg = evaluate_correlated_microparlay(legs_neg_ev_2x)
        assert res_neg["viable"] is False
        assert res_neg["ev_dolares"] < 0.0 or "insuficiente" in res_neg["razon"] or "desfavorable" in res_neg["razon"]

    def test_microparlay_degenerate_covariance_matrices(self):
        """TC-PARLAY-03: Boundary model probabilities (0.01, 0.99) collapse covariance gracefully."""
        # Lower bound p = 0.01: variance p*(1-p) approaches 0.01
        legs_low = [
            {"game_id": 104, "team_name": "Chiefs", "market": "prop", "prob_model": 0.01},
            {"game_id": 104, "team_name": "Chiefs", "market": "prop", "prob_model": 0.01},
        ]
        p_low = _compute_joint_probability(legs_low)
        assert 0.0 < p_low <= 0.01
        assert math.isfinite(p_low)

        # Upper bound p = 0.99: variance p*(1-p) approaches 0.01
        legs_high = [
            {"game_id": 104, "team_name": "Chiefs", "market": "prop", "prob_model": 0.99},
            {"game_id": 104, "team_name": "Chiefs", "market": "prop", "prob_model": 0.99},
        ]
        p_high = _compute_joint_probability(legs_high)
        assert 0.90 <= p_high <= 0.99
        assert math.isfinite(p_high)

    def test_microparlay_negative_correlation_and_contradictory_outcomes(self):
        """TC-PARLAY-04: Contradictory sides in same game yield exactly 0.0 joint prob and -$100 EV."""
        legs_contradictory = [
            {"game_id": 105, "team_name": "Kansas City Chiefs", "market": "h2h", "prob_model": 0.65, "odds_american": -150},
            {"game_id": 105, "team_name": "Denver Broncos", "market": "h2h", "prob_model": 0.35, "odds_american": +130},
        ]
        joint_p = _compute_joint_probability(legs_contradictory)
        assert joint_p == 0.0, "Contradictory outcomes in same game must have 0.0 joint probability"

        res = evaluate_correlated_microparlay(legs_contradictory)
        assert res["viable"] is False
        assert res["prob_combinada"] == 0.0
        assert res["ev_dolares"] == -100.0
        assert "EV no positivo" in res["razon"]

    def test_standard_parlay_strictly_rejects_same_game_correlation(self):
        """TC-PARLAY-05: Standard evaluate_custom_parlay strictly prohibits same-game legs."""
        legs = [
            {"game_id": "game_kc_den", "team_name": "Kansas City Chiefs", "market": "h2h", "prob_model": 0.70, "odds_american": -150},
            {"game_id": "game_kc_den", "team_name": "Kansas City Chiefs", "market": "spread", "point": -3.5, "prob_model": 0.60, "odds_american": -110},
        ]
        res = evaluate_custom_parlay(legs, allow_correlation=False)
        assert res["viable"] is False
        assert res["has_correlated_legs"] is True
        assert "No se permite combinar selecciones del mismo evento" in res["razon"]


class TestCeleryPreKickoffTriggersAndConcurrency:
    """Pre-kickoff triggers, duplicate deduplication, and PostgreSQL atomic upsert testing."""

    def test_rapid_duplicate_prekickoff_triggers_idempotency(self, tmp_path):
        """TC-CELERY-01: Rapid duplicate near_kickoff triggers generate deterministic payload sha256."""
        from app.services.nfl_availability_service import NFLAvailabilityService

        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")
        mock_teams = {
            "KC": [
                {
                    "player_id": "p_kc_1",
                    "player_name": "Patrick Mahomes",
                    "position": "QB",
                    "injury_status": "Active",
                    "practice_status": "Full",
                }
            ],
            "DEN": [
                {
                    "player_id": "p_den_1",
                    "player_name": "Bo Nix",
                    "position": "QB",
                    "injury_status": "Active",
                    "practice_status": "Full",
                }
            ],
        }

        with patch("app.services.matchup_context_service.get_live_injuries", return_value=mock_teams), \
             patch("app.services.nfl_availability_service.NFLAvailabilityService", return_value=svc), \
             patch("app.workers.celery_app.evaluate_predictions_task.apply_async") as mock_eval:

            # Trigger 1
            res1 = capture_nfl_injuries_task(cadence="near_kickoff", affected_game_ids=["401547417"])
            assert res1["status"] == "success"
            assert res1["cadence"] == "near_kickoff"
            assert res1["payload_sha256"] is not None

            # Trigger 2 (immediate duplicate)
            res2 = capture_nfl_injuries_task(cadence="near_kickoff", affected_game_ids=["401547417"])
            assert res2["status"] == "success"
            # Deduplication: Identical payload produces bit-for-bit identical payload_sha256
            assert res1["payload_sha256"] == res2["payload_sha256"]
            assert mock_eval.call_count == 2

    @pytest.mark.asyncio
    async def test_postgresql_prediction_upsert_atomic_deduplication(self):
        """TC-CELERY-02: append_predictions uses ON CONFLICT DO NOTHING to guarantee atomic idempotency."""
        inf_hash = f"test_inf_hash_atomic_dedup_{int(datetime.now().timestamp())}"

        async with AsyncSessionLocal() as session:
            game_id = (await session.execute(select(Game.id).limit(1))).scalar_one_or_none()
            if not game_id:
                pytest.skip("No game available in database for foreign key join")

            row = {
                "game_id": game_id,
                "model_version": "nfl_temporal_v1",
                "artifact_sha256": "test_art_hash_dedup_01",
                "dataset_sha256": "test_data_hash_dedup_01",
                "inference_input_sha256": inf_hash,
                "home_win_prob": 0.65,
                "away_win_prob": 0.35,
            }

            try:
                # 1. First insert
                await append_predictions(session, [row])
                await session.commit()

                count_after_first = await session.scalar(
                    select(func.count(Prediction.id)).where(Prediction.inference_input_sha256 == inf_hash)
                )
                assert count_after_first == 1

                # 2. Duplicate insert with exact same deterministic key
                await append_predictions(session, [row])
                await session.commit()

                count_after_second = await session.scalar(
                    select(func.count(Prediction.id)).where(Prediction.inference_input_sha256 == inf_hash)
                )
                # Deduplication: exactly 1 row remains; 0 duplicate rows created
                assert count_after_second == 1

            finally:
                await session.execute(
                    text("DELETE FROM predictions WHERE inference_input_sha256 = :h"),
                    {"h": inf_hash},
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_postgresql_prediction_concurrent_race_condition_safety(self):
        """TC-CELERY-03: 10 parallel async sessions concurrently inserting identical prediction do not deadlock."""
        inf_hash = f"test_inf_hash_concurrent_race_{int(datetime.now().timestamp())}"

        async with AsyncSessionLocal() as session:
            game_id = (await session.execute(select(Game.id).limit(1))).scalar_one_or_none()
            if not game_id:
                pytest.skip("No game available in database for foreign key join")

        row = {
            "game_id": game_id,
            "model_version": "nfl_temporal_v1",
            "artifact_sha256": "test_art_hash_race_01",
            "dataset_sha256": "test_data_hash_race_01",
            "inference_input_sha256": inf_hash,
            "home_win_prob": 0.70,
            "away_win_prob": 0.30,
        }

        async def _insert_worker():
            async with AsyncSessionLocal() as s:
                await append_predictions(s, [row])
                await s.commit()

        try:
            results = await asyncio.gather(*(_insert_worker() for _ in range(10)), return_exceptions=True)
            for r in results:
                assert not isinstance(r, Exception), f"Concurrent insert threw exception: {r}"

            async with AsyncSessionLocal() as session:
                persisted_count = await session.scalar(
                    select(func.count(Prediction.id)).where(Prediction.inference_input_sha256 == inf_hash)
                )
                assert persisted_count == 1, f"Expected exactly 1 row after race condition, got {persisted_count}"

        finally:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("DELETE FROM predictions WHERE inference_input_sha256 = :h"),
                    {"h": inf_hash},
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_append_predictions_fails_closed_on_missing_deterministic_keys(self):
        """TC-CELERY-04: append_predictions rejects rows missing deterministic key components."""
        async with AsyncSessionLocal() as session:
            bad_row = {
                "game_id": 1,
                "model_version": "nfl_temporal_v1",
                # missing artifact_sha256, dataset_sha256, inference_input_sha256
            }
            with pytest.raises(ValueError, match="Missing required deterministic key component"):
                await append_predictions(session, [bad_row])


class TestAPIEndpointsConcurrencyAndZeroOutboundNetwork:
    """Concurrency, responsiveness, and zero-outbound-network testing for GET endpoints."""

    @pytest.mark.asyncio
    async def test_rapid_parallel_get_predictions_today_zero_external_network(self):
        """TC-API-01: 25 concurrent GET requests to /api/v1/predictions/today make zero external calls."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=RuntimeError("Prohibited outbound HTTP!")) as mock_async_http, \
                 patch("httpx.HTTPTransport.handle_request", side_effect=RuntimeError("Prohibited outbound HTTP!")) as mock_sync_http, \
                 patch("urllib.request.urlopen", side_effect=RuntimeError("Prohibited outbound urllib!")) as mock_url:

                tasks = [client.get("/api/v1/predictions/today?sport=NFL") for _ in range(25)]
                responses = await asyncio.gather(*tasks)

                assert not mock_async_http.called
                assert not mock_sync_http.called
                assert not mock_url.called
                assert len(responses) == 25
                for resp in responses:
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["sport"] == "NFL"
                    assert "period" in data
                    assert "as_of" in data
                    assert "picks" in data
                    assert isinstance(data["picks"], list)

    @pytest.mark.asyncio
    async def test_rapid_parallel_get_parlay_recommended_zero_external_network(self):
        """TC-API-02: 25 concurrent GET requests to /api/v1/parlay/recommended make zero external calls."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=RuntimeError("Prohibited outbound HTTP!")) as mock_async_http, \
                 patch("httpx.HTTPTransport.handle_request", side_effect=RuntimeError("Prohibited outbound HTTP!")) as mock_sync_http, \
                 patch("urllib.request.urlopen", side_effect=RuntimeError("Prohibited outbound urllib!")) as mock_url:

                tasks = [client.get("/api/v1/parlay/recommended?sport=NFL") for _ in range(25)]
                responses = await asyncio.gather(*tasks)

                assert not mock_async_http.called
                assert not mock_sync_http.called
                assert not mock_url.called
                assert len(responses) == 25
                for resp in responses:
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["status"] == "success"
                    assert data["sport"] == "NFL"
                    assert "parlays" in data
                    assert isinstance(data["parlays"], list)

    def test_concurrent_multithreaded_mixed_api_load(self):
        """TC-API-03: Multithreaded mixed read load across predictions and parlay endpoints."""
        sync_client = TestClient(app)

        def _fetch_predictions():
            return sync_client.get("/api/v1/predictions/today?sport=NFL")

        def _fetch_parlay():
            return sync_client.get("/api/v1/parlay/recommended?sport=NFL")

        with ThreadPoolExecutor(max_workers=8) as executor:
            pred_futures = [executor.submit(_fetch_predictions) for _ in range(12)]
            parlay_futures = [executor.submit(_fetch_parlay) for _ in range(12)]

            for f in pred_futures:
                resp = f.result()
                assert resp.status_code == 200
                assert resp.json()["sport"] == "NFL"

            for f in parlay_futures:
                resp = f.result()
                assert resp.status_code == 200
                assert resp.json()["status"] == "success"


class TestOddsQuotaLedgerProtection:
    """Verification of Odds API quota ledger immutability and circuit breaker defenses."""

    def test_quota_ledger_sha256_unaltered_across_suite(self):
        """TC-QUOTA-01: Verify SHA-256 hash of data/odds_quota_ledger.json matches authoritative baseline."""
        ledger_path = project_root() / "data" / "odds_quota_ledger.json"
        assert ledger_path.is_file(), f"Missing ledger file: {ledger_path}"

        raw_bytes = ledger_path.read_bytes()
        current_hash = hashlib.sha256(raw_bytes).hexdigest()
        assert current_hash == EXPECTED_LEDGER_SHA256, (
            f"Quota ledger hash altered! Expected {EXPECTED_LEDGER_SHA256}, got {current_hash}"
        )

        data = json.loads(raw_bytes.decode("utf-8"))
        assert data["config"]["total_budget"] == 500
        assert data["summary"]["requests_remaining"] == 209
        assert data["summary"]["requests_used"] == 291
        assert data["summary"]["circuit_breaker_open"] is False

    def test_quota_service_blocks_prohibited_historical_endpoints(self):
        """TC-QUOTA-02: Historical endpoint requests raise QuotaExceededError without touching ledger."""
        with pytest.raises(QuotaExceededError, match="Endpoints históricos prohibidos"):
            estimate_request_cost(endpoint="/v4/historical/sports/americanfootball_nfl/odds")

        ledger_path = project_root() / "data" / "odds_quota_ledger.json"
        assert hashlib.sha256(ledger_path.read_bytes()).hexdigest() == EXPECTED_LEDGER_SHA256

    def test_quota_service_circuit_breaker_locks_down_at_protected_reserve(self, tmp_path):
        """TC-QUOTA-03: Circuit breaker trips and refuses reservations when credits reach protected reserve."""
        test_ledger = tmp_path / "test_ledger_reserve.json"
        test_ledger.write_text(
            json.dumps({
                "config": {"total_budget": 500},
                "summary": {
                    "total_budget": 500,
                    "requests_used": 425,
                    "requests_remaining": 75,
                    "circuit_breaker_open": True,
                    "circuit_breaker_reason": "Protected reserve threshold reached",
                },
                "pending_reservations": {},
                "transactions": [],
            }),
            encoding="utf-8",
        )

        svc = OddsQuotaService(ledger_path=test_ledger)
        assert svc.circuit_breaker_open is True

        with pytest.raises(CircuitBreakerOpenError):
            svc.reserve_quota(endpoint="/v4/sports/americanfootball_nfl/odds", envelope="main_lines")
