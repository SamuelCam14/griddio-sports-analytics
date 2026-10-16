"""
Integration tests for:
1. Game Identity Crosswalk & Persistence:
   Schedule ingest -> Odds ingest -> Association -> Read odds (canonical identity, zero duplicate records).
2. /predictions/today pre-game cutoff, future kickoff, pre-kickoff as_of, and per-market freshness.
"""
import pytest
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite driver required for integration tests")
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.game import Base, Game, Team, OddsSnapshot, Prediction, ProviderGameCrosswalk
from app.services.nfl_schedule_service import sync_nfl_games_from_source
from app.services.live_ingestion_service import sync_real_games
from app.services.odds_service import fetch_odds_from_db
from app.api.v1.predictions import get_today_picks


async def setup_integration_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory


@pytest.mark.asyncio
async def test_schedule_ingest_odds_ingest_crosswalk_integration():
    """
    Test complete lifecycle:
    1. Schedule ingest creates canonical Game with NFLverse external_id and records crosswalk.
    2. Odds ingest arrives with The Odds API external_id.
    3. Crosswalk matches existing canonical Game via teams + calendar day window.
    4. NO duplicate Game is created.
    5. OddsSnapshot is attached to the canonical Game.
    6. Odds service reads the odds attached to the canonical game.
    """
    engine, session_factory = await setup_integration_db()

    async with session_factory() as db:
        # Seed NFL teams
        chiefs = Team(id=1, api_id="kc", name="Kansas City Chiefs", abbreviation="KC", sport="NFL")
        ravens = Team(id=2, api_id="bal", name="Baltimore Ravens", abbreviation="BAL", sport="NFL")
        db.add_all([chiefs, ravens])
        await db.commit()

        now_utc = datetime.now(timezone.utc)
        kickoff_utc = now_utc + timedelta(hours=20)

        # 1. Schedule Ingest (NFLverse)
        schedule_df = pd.DataFrame([
            {
                "game_id": "2026_01_KC_BAL",
                "game_date": kickoff_utc.date(),
                "game_start_utc": kickoff_utc,
                "season": 2026,
                "home_team_id": "KC",
                "away_team_id": "BAL",
                "source": "nflverse schedules",
                "source_ref": "nflverse:schedule:2026:hash1",
            }
        ])

        with patch("app.services.nfl_schedule_service.load_nfl_upcoming_games", return_value=schedule_df):
            sched_result = await sync_nfl_games_from_source(db)
            assert sched_result["status"] == "success"
            assert sched_result["inserted"] == 1

        # Verify canonical Game exists
        games_res = await db.execute(select(Game).where(Game.sport == "NFL"))
        games = games_res.scalars().all()
        assert len(games) == 1
        canonical_game = games[0]
        assert canonical_game.external_id == "2026_01_KC_BAL"

        # Verify nflverse crosswalk exists
        cw_res = await db.execute(select(ProviderGameCrosswalk).where(ProviderGameCrosswalk.game_id == canonical_game.id))
        cw_entries = cw_res.scalars().all()
        assert any(cw.provider == "nflverse" and cw.provider_event_id == "2026_01_KC_BAL" for cw in cw_entries)

        # 2. Odds Ingest (The Odds API) with different provider event ID
        odds_event_id = "odds_api_event_kc_bal_99988"
        odds_data = {
            "external_id": odds_event_id,
            "home_team": "Kansas City Chiefs",
            "away_team": "Baltimore Ravens",
            "commence_time": kickoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "best_bookmaker": "draftkings",
            "spread_bookmaker": "fanduel",
            "total_bookmaker": "pinnacle",
            "home_ml": -140,
            "away_ml": 120,
            "home_spread": -3.5,
            "away_spread": 3.5,
            "total_line": 48.5,
            "over_ml": -110,
            "under_ml": -110,
            "ml_last_update": (now_utc - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "spread_last_update": (now_utc - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "totals_last_update": (now_utc - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        with patch("app.services.live_ingestion_service.fetch_odds", return_value=[odds_data]):
            with patch("app.services.espn_service.sync_espn_scores", return_value={"updated": 0}):
                odds_result = await sync_real_games("NFL", db)
                assert odds_result["status"] == "success"
                assert odds_result["games_synced"] == 1

        # 3. Assert NO second Game was created (Dual-record problem prevented!)
        games_after = (await db.execute(select(Game).where(Game.sport == "NFL"))).scalars().all()
        assert len(games_after) == 1, f"Expected exactly 1 canonical Game, found {len(games_after)}"
        assert games_after[0].id == canonical_game.id

        # 4. Assert Crosswalk now maps The Odds API event ID to the canonical Game.id
        cw_odds = (await db.execute(
            select(ProviderGameCrosswalk).where(
                ProviderGameCrosswalk.provider == "the_odds_api",
                ProviderGameCrosswalk.provider_event_id == odds_event_id,
            )
        )).scalar_one_or_none()
        assert cw_odds is not None
        assert cw_odds.game_id == canonical_game.id

        # 5. Assert OddsSnapshot attached to the canonical Game
        snaps = (await db.execute(select(OddsSnapshot).where(OddsSnapshot.game_id == canonical_game.id))).scalars().all()
        assert len(snaps) >= 1
        h2h_snap = next((s for s in snaps if s.market == "h2h"), None)
        assert h2h_snap is not None
        assert h2h_snap.home_ml == -140
        assert h2h_snap.away_ml == 120

        # 6. Read odds via odds service
        odds_read = await fetch_odds_from_db("NFL", db)
        assert len(odds_read) == 1
        assert odds_read[0]["external_id"] == "2026_01_KC_BAL"
        assert odds_read[0]["home_ml"] == -140
        assert odds_read[0]["is_stale"] is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_today_picks_pregame_cutoff_and_per_market_freshness_integration():
    """
    Test /predictions/today business rules:
    - Status must be 'scheduled' (live/final excluded)
    - Kickoff must be in future (past kickoff excluded)
    - Prediction as_of must be <= kickoff (post-kickoff prediction excluded)
    - Stale moneyline odds are rejected even if totals snapshot is fresh
    - Fresh moneyline odds pre-kickoff are emitted
    """
    engine, session_factory = await setup_integration_db()

    async with session_factory() as db:
        chiefs = Team(id=1, api_id="kc", name="Kansas City Chiefs", abbreviation="KC", sport="NFL")
        ravens = Team(id=2, api_id="bal", name="Baltimore Ravens", abbreviation="BAL", sport="NFL")
        db.add_all([chiefs, ravens])
        await db.commit()

        now_utc = datetime.now(timezone.utc)
        future_kickoff = now_utc + timedelta(days=2)
        past_kickoff = now_utc - timedelta(hours=3)

        approved_version = "nfl_temporal_v1"

        # Case 1: Game is 'final' (already finished) -> must be excluded
        game_final = Game(
            id=10, external_id="game_final_1", sport="NFL", season="2026",
            game_date=future_kickoff, home_team_id=1, away_team_id=2, status="final",
        )
        pred_final = Prediction(
            id=10, game_id=10, model_version=approved_version,
            dataset_sha256="d", artifact_sha256="a", inference_input_sha256="i1",
            as_of=now_utc - timedelta(hours=1),
            home_win_prob=0.6, away_win_prob=0.4,
            best_bookmaker="draftkings", best_home_ml=-130, best_away_ml=110,
            home_ev=3.5, home_has_edge=True,
        )
        snap_final = OddsSnapshot(
            id=10, game_id=10, bookmaker="draftkings", market="h2h",
            captured_at=now_utc - timedelta(hours=1), observed_at=now_utc - timedelta(hours=1),
            home_ml=-130, away_ml=110,
        )
        db.add_all([game_final, pred_final, snap_final])

        # Case 2: Game kickoff in past -> must be excluded
        game_past = Game(
            id=20, external_id="game_past_2", sport="NFL", season="2026",
            game_date=past_kickoff, home_team_id=1, away_team_id=2, status="scheduled",
        )
        pred_past = Prediction(
            id=20, game_id=20, model_version=approved_version,
            dataset_sha256="d", artifact_sha256="a", inference_input_sha256="i2",
            as_of=past_kickoff - timedelta(hours=2),
            home_win_prob=0.6, away_win_prob=0.4,
            best_bookmaker="draftkings", best_home_ml=-130, best_away_ml=110,
            home_ev=3.5, home_has_edge=True,
        )
        snap_past = OddsSnapshot(
            id=20, game_id=20, bookmaker="draftkings", market="h2h",
            captured_at=past_kickoff - timedelta(hours=2), observed_at=past_kickoff - timedelta(hours=2),
            home_ml=-130, away_ml=110,
        )
        db.add_all([game_past, pred_past, snap_past])

        # Case 3: Prediction as_of is POST-kickoff -> must be excluded
        game_post_pred = Game(
            id=30, external_id="game_post_pred_3", sport="NFL", season="2026",
            game_date=future_kickoff, home_team_id=1, away_team_id=2, status="scheduled",
        )
        pred_post_as_of = Prediction(
            id=30, game_id=30, model_version=approved_version,
            dataset_sha256="d", artifact_sha256="a", inference_input_sha256="i3",
            as_of=future_kickoff + timedelta(minutes=15),  # After kickoff!
            home_win_prob=0.6, away_win_prob=0.4,
            best_bookmaker="draftkings", best_home_ml=-130, best_away_ml=110,
            home_ev=3.5, home_has_edge=True,
        )
        snap_post_pred = OddsSnapshot(
            id=30, game_id=30, bookmaker="draftkings", market="h2h",
            captured_at=now_utc - timedelta(hours=1), observed_at=now_utc - timedelta(hours=1),
            home_ml=-130, away_ml=110,
        )
        db.add_all([game_post_pred, pred_post_as_of, snap_post_pred])

        # Case 4: Stale Moneyline (40h old) masked by Fresh Totals (1h old) -> MUST ABSTAIN!
        game_stale_ml = Game(
            id=40, external_id="game_stale_ml_4", sport="NFL", season="2026",
            game_date=future_kickoff, home_team_id=1, away_team_id=2, status="scheduled",
        )
        pred_stale_ml = Prediction(
            id=40, game_id=40, model_version=approved_version,
            dataset_sha256="d", artifact_sha256="a", inference_input_sha256="i4",
            as_of=now_utc - timedelta(hours=1),
            home_win_prob=0.6, away_win_prob=0.4,
            best_bookmaker="draftkings", best_home_ml=-130, best_away_ml=110,
            home_ev=3.5, home_has_edge=True,
        )
        stale_h2h_snap = OddsSnapshot(
            id=41, game_id=40, bookmaker="draftkings", market="h2h",
            captured_at=now_utc - timedelta(hours=40), observed_at=now_utc - timedelta(hours=40),
            home_ml=-130, away_ml=110,
        )
        fresh_totals_snap = OddsSnapshot(
            id=42, game_id=40, bookmaker="fanduel", market="totals",
            captured_at=now_utc - timedelta(hours=1), observed_at=now_utc - timedelta(hours=1),
            total_line=48.5, over_ml=-110, under_ml=-110,
        )
        db.add_all([game_stale_ml, pred_stale_ml, stale_h2h_snap, fresh_totals_snap])

        await db.commit()

        # Run get_today_picks for NFL
        with patch("app.api.v1.predictions.get_approved_model_version", return_value=approved_version), \
             patch("app.api.v1.predictions._production_model_enabled", return_value=True):
            response = await get_today_picks(sport="NFL", tier="all", period="all", db=db)
        assert response["predictions_enabled"] is True
        # All 4 cases above must be excluded!
        assert len(response["picks"]) == 0, f"Expected 0 picks due to cutoffs/staleness, got {len(response['picks'])}"

        # Case 5: Valid fresh scheduled game pre-kickoff -> MUST BE INCLUDED
        game_valid = Game(
            id=50, external_id="game_valid_5", sport="NFL", season="2026",
            game_date=future_kickoff, home_team_id=1, away_team_id=2, status="scheduled",
        )
        pred_valid = Prediction(
            id=50, game_id=50, model_version=approved_version,
            dataset_sha256="d", artifact_sha256="a", inference_input_sha256="i5",
            as_of=now_utc - timedelta(hours=2),
            home_win_prob=0.65, away_win_prob=0.35,
            best_bookmaker="draftkings", best_home_ml=-130, best_away_ml=110,
            home_ev=5.2, home_has_edge=True,
        )
        snap_valid_h2h = OddsSnapshot(
            id=51, game_id=50, bookmaker="draftkings", market="h2h",
            captured_at=now_utc - timedelta(hours=2), observed_at=now_utc - timedelta(hours=2),
            home_ml=-130, away_ml=110,
        )
        db.add_all([game_valid, pred_valid, snap_valid_h2h])
        await db.commit()

        with patch("app.api.v1.predictions.get_approved_model_version", return_value=approved_version), \
             patch("app.api.v1.predictions._production_model_enabled", return_value=True):
            response_valid = await get_today_picks(sport="NFL", tier="all", period="all", db=db)
        assert response_valid["predictions_enabled"] is True
        assert len(response_valid["picks"]) == 1
        assert response_valid["picks"][0]["game_id"] == 50
        assert response_valid["picks"][0]["pick_team_name"] == "Kansas City Chiefs"

    await engine.dispose()
