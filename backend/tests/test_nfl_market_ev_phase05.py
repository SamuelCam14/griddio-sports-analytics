from datetime import datetime, timezone
import pytest

from app.services.nfl_market_pricing_service import (
    NFLMarketPricingService,
    PriceRecord,
)


@pytest.fixture
def pricing_service():
    return NFLMarketPricingService(min_edge_threshold=0.03, max_kelly_fraction=0.05)


@pytest.fixture
def valid_price_record():
    return PriceRecord(
        event_id="nfl_2024_kc_bal",
        sportsbook="draftkings",
        market_key="h2h",
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        home_odds_american=-145,  # decimal ~ 1.6897
        away_odds_american=+125,  # decimal ~ 2.25
        source_timestamp_utc="2024-09-05T21:00:00Z",
        fetched_timestamp_utc="2024-09-05T21:15:00Z",
        decision_cutoff_utc="2024-09-05T22:00:00Z",
        market_status="open",
        mapping_confidence=1.0,
    )


def test_devig_and_fair_probabilities_sum_to_one(pricing_service, valid_price_record):
    """Fair probabilities extracted from sportsbook lines must sum to 1.0."""
    result = pricing_service.evaluate_game_pricing(0.65, valid_price_record)

    assert result["status"] == "evaluated"
    p_home_fair = result["home"]["fair_market_prob"]
    p_away_fair = result["away"]["fair_market_prob"]
    assert pytest.approx(p_home_fair + p_away_fair, abs=1e-4) == 1.0
    assert result["vig_pct"] > 0.0  # Vig is positive (house margin)


def test_positive_ev_eligible_pick_detection(pricing_service, valid_price_record):
    """Model assigning 66% win prob to -145 (fair prob ~57.2%) creates +EV pick."""
    result = pricing_service.evaluate_game_pricing(0.66, valid_price_record)

    assert result["status"] == "evaluated"
    assert len(result["eligible_picks"]) == 1
    pick = result["eligible_picks"][0]
    assert pick["selection"] == "HOME"
    assert pick["edge_pct"] >= 0.03
    assert pick["ev_per_dollar"] > 0.0
    assert 0.0 < pick["quarter_kelly_fraction"] <= 0.05


def test_insufficient_edge_is_rejected(pricing_service, valid_price_record):
    """When model probability is close to market, edge < 3% results in no eligible picks."""
    # Market fair prob is ~57.2%; model prob 58% has < 1% edge
    result = pricing_service.evaluate_game_pricing(0.58, valid_price_record)

    assert result["status"] == "evaluated"
    assert len(result["eligible_picks"]) == 0
    assert result["home"]["is_eligible"] is False
    assert result["home"]["rejection_reason"] == "insufficient_edge"


def test_rejection_of_post_cutoff_odds(pricing_service):
    """Odds captured after decision cutoff must be rejected with reason odds_post_cutoff."""
    post_cutoff_record = PriceRecord(
        event_id="nfl_2024_kc_bal",
        sportsbook="draftkings",
        market_key="h2h",
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        home_odds_american=-145,
        away_odds_american=+125,
        source_timestamp_utc="2024-09-05T22:30:00Z",
        fetched_timestamp_utc="2024-09-05T22:30:00Z",  # 30 mins after cutoff
        decision_cutoff_utc="2024-09-05T22:00:00Z",
        market_status="open",
        mapping_confidence=1.0,
    )
    result = pricing_service.evaluate_game_pricing(0.65, post_cutoff_record)

    assert result["status"] == "rejected"
    assert result["reason"] == "odds_post_cutoff"
    assert len(result["picks"]) == 0


def test_rejection_of_suspended_market(pricing_service, valid_price_record):
    """Suspended markets must be rejected without picking."""
    suspended_record = PriceRecord(
        event_id="nfl_2024_kc_bal",
        sportsbook="draftkings",
        market_key="h2h",
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        home_odds_american=-145,
        away_odds_american=+125,
        source_timestamp_utc="2024-09-05T21:00:00Z",
        fetched_timestamp_utc="2024-09-05T21:15:00Z",
        decision_cutoff_utc="2024-09-05T22:00:00Z",
        market_status="suspended",
        mapping_confidence=1.0,
    )
    result = pricing_service.evaluate_game_pricing(0.65, suspended_record)

    assert result["status"] == "rejected"
    assert result["reason"] == "market_suspended"


def test_historical_roi_marked_not_evaluable(pricing_service, valid_price_record):
    """Per Phase 05 acceptance criteria, without full historical odds, ROI is not_evaluable."""
    result = pricing_service.evaluate_game_pricing(0.65, valid_price_record)

    assert result["historical_roi_status"] == "not_evaluable"
    assert "not measured without complete historical odds" in result["evaluation_note"]


def test_rejection_of_stale_odds(pricing_service, valid_price_record):
    """Odds fetched older than max_odds_age_hours must be rejected with stale_odds."""
    # as_of is 48 hours after fetched timestamp
    as_of = datetime.fromisoformat("2024-09-07T21:15:00+00:00")
    result = pricing_service.evaluate_game_pricing(0.65, valid_price_record, as_of=as_of)

    assert result["status"] == "rejected"
    assert result["reason"] == "stale_odds"
    assert result["odds_age_hours"] >= 48.0


def test_rejection_of_odds_source_post_cutoff(pricing_service):
    """Odds where sportsbook publication timestamp is after cutoff must be rejected."""
    record = PriceRecord(
        event_id="nfl_2024_kc_bal",
        sportsbook="draftkings",
        market_key="h2h",
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        home_odds_american=-145,
        away_odds_american=+125,
        source_timestamp_utc="2024-09-05T22:15:00Z",  # source is post cutoff
        fetched_timestamp_utc="2024-09-05T22:20:00Z",
        decision_cutoff_utc="2024-09-05T22:00:00Z",
        market_status="open",
        mapping_confidence=1.0,
    )
    result = pricing_service.evaluate_game_pricing(0.65, record)

    assert result["status"] == "rejected"
    assert result["reason"] in ("odds_post_cutoff", "odds_source_post_cutoff")


def test_rejection_of_odds_source_after_fetch(pricing_service):
    """Causality violation: source timestamp in future relative to fetch time must be rejected."""
    record = PriceRecord(
        event_id="nfl_2024_kc_bal",
        sportsbook="draftkings",
        market_key="h2h",
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        home_odds_american=-145,
        away_odds_american=+125,
        source_timestamp_utc="2024-09-05T21:30:00Z",  # source after fetch
        fetched_timestamp_utc="2024-09-05T21:15:00Z",
        decision_cutoff_utc="2024-09-05T22:00:00Z",
        market_status="open",
        mapping_confidence=1.0,
    )
    result = pricing_service.evaluate_game_pricing(0.65, record)

    assert result["status"] == "rejected"
    assert result["reason"] == "odds_source_after_fetch"


def test_rejection_of_nan_or_invalid_model_probability(pricing_service, valid_price_record):
    """NaN, infinite, or out-of-range probabilities must fail-closed."""
    for bad_prob in (float("nan"), float("inf"), -0.1, 1.1):
        result = pricing_service.evaluate_game_pricing(bad_prob, valid_price_record)
        assert result["status"] == "rejected"
        assert result["reason"] == "invalid_model_probability"
        assert result["historical_roi_status"] == "not_evaluable"

