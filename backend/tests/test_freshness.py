import pytest
from datetime import datetime, timezone, timedelta
from app.services.freshness_service import is_fresh, FreshnessError

def test_freshness_validates_game_start():
    game_start = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
    fetched_at = game_start + timedelta(seconds=1)
    with pytest.raises(FreshnessError, match="Data updated after game start"):
        is_fresh(fetched_at, game_start, sport="NFL", now_override=game_start)
    with pytest.raises(FreshnessError, match="Data updated after game start"):
        is_fresh(game_start, game_start, sport="NFL", now_override=game_start)

def test_freshness_rejects_expired_data(monkeypatch):
    game_start = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.core.config.settings.FRESHNESS_HOURS_NFL_ODDS", 12.0)
    fetched_at = game_start - timedelta(hours=13)
    with pytest.raises(FreshnessError, match="Data is too old"):
        is_fresh(fetched_at, game_start, sport="NFL", now_override=game_start)

def test_freshness_accepts_valid_data(monkeypatch):
    game_start = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.core.config.settings.FRESHNESS_HOURS_NFL_ODDS", 12.0)
    fetched_at = game_start - timedelta(hours=5)
    assert is_fresh(fetched_at, game_start, sport="NFL", now_override=game_start) is True

def test_freshness_exact_boundary(monkeypatch):
    """Test exact threshold limit and just above it."""
    game_start = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.core.config.settings.FRESHNESS_HOURS_NFL_ODDS", 12.0)
    
    # Exact limit
    exact_limit = game_start - timedelta(hours=12)
    assert is_fresh(exact_limit, game_start, sport="NFL", now_override=game_start) is True
    
    # Just above limit (1 second)
    just_above = game_start - timedelta(hours=12, seconds=1)
    with pytest.raises(FreshnessError, match="Data is too old"):
        is_fresh(just_above, game_start, sport="NFL", now_override=game_start)

def test_freshness_unknown_sport():
    """Reject unknown sports and data types."""
    game_start = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
    fetched_at = game_start - timedelta(hours=2)
    with pytest.raises(FreshnessError, match="Unsupported sport for odds: UNKNOWN"):
        is_fresh(fetched_at, game_start, sport="UNKNOWN", now_override=game_start)
    with pytest.raises(FreshnessError, match="Unsupported data_type: injuries"):
        is_fresh(fetched_at, game_start, sport="NFL", data_type="injuries", now_override=game_start)

def test_freshness_future_timestamp():
    """Fail if last_update is in the future relative to now."""
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    game_start = now + timedelta(hours=24)
    # last_update is 1 hour in the future relative to 'now'
    future_update = now + timedelta(hours=1)
    with pytest.raises(FreshnessError, match="last_update cannot be in the future"):
        is_fresh(future_update, game_start, sport="NFL", now_override=now)

def test_freshness_different_timezones(monkeypatch):
    """Ensure it handles different timezones properly."""
    monkeypatch.setattr("app.core.config.settings.FRESHNESS_HOURS_NFL_ODDS", 12.0)
    # game_start in UTC
    game_start = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
    # last_update in UTC-4, 11 hours before (so it should be fresh)
    # 20:00 UTC = 16:00 UTC-4. Let's say update was at 06:00 UTC-4 = 10:00 UTC (10 hours before)
    from datetime import tzinfo
    class UTCMinus4(tzinfo):
        def utcoffset(self, dt): return timedelta(hours=-4)
        def tzname(self, dt): return "UTC-4"
        def dst(self, dt): return timedelta(0)
        
    last_update = datetime(2026, 10, 10, 6, 0, tzinfo=UTCMinus4())
    assert is_fresh(last_update, game_start, sport="NFL", now_override=game_start) is True

def test_freshness_handles_missing_timestamps():
    with pytest.raises(FreshnessError, match="Missing timestamps"):
        is_fresh(None, datetime.now(timezone.utc), sport="NBA")

def test_freshness_handles_naive_timestamps():
    naive_game_start = datetime(2026, 10, 10, 20, 0)
    naive_fetched = datetime(2026, 10, 10, 18, 0)
    with pytest.raises(FreshnessError, match="Timestamps must be timezone-aware"):
        is_fresh(naive_fetched, naive_game_start, sport="NBA")
