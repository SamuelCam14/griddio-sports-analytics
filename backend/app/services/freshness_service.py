from datetime import datetime, timezone, timedelta
import structlog
from app.core.config import settings

logger = structlog.get_logger(__name__)

class FreshnessError(Exception):
    """Raised when data fails the freshness validation boundary."""
    pass

class ProviderError(Exception):
    """Raised when a data provider fails to respond correctly."""
    pass

class ConfigurationError(Exception):
    """Raised when critical configuration is missing."""
    pass

def is_fresh(last_update: datetime, game_start: datetime, sport: str, data_type: str = "odds", now_override: datetime = None) -> bool:
    """
    Validates if the fetched data is fresh enough relative to the game start time.
    Raises FreshnessError if data is invalid, expired, or fetched after game start.
    """
    if last_update is None or game_start is None:
        raise FreshnessError("Missing timestamps")
        
    if last_update.tzinfo is None or game_start.tzinfo is None:
        raise FreshnessError("Timestamps must be timezone-aware")
        
    now = now_override if now_override else datetime.now(timezone.utc)
    if last_update > now + timedelta(minutes=5): # allow small drift
        raise FreshnessError("last_update cannot be in the future")
        
    # Condition 1: Data updated exactly at or after game start is considered live/post-game, 
    # which we reject for pre-match predictions.
    if last_update >= game_start:
        raise FreshnessError(f"Data updated after game start (update: {last_update}, start: {game_start})")
    
    # Condition 2: Is the data too old? We evaluate age relative to game_start 
    # to ensure the prediction is based on relatively current information.
    age_hours = (game_start - last_update).total_seconds() / 3600.0
    
    if data_type == "odds":
        if sport.upper() == "NFL":
            threshold = settings.FRESHNESS_HOURS_NFL_ODDS
        elif sport.upper() == "NBA":
            threshold = settings.FRESHNESS_HOURS_NBA_ODDS
        else:
            raise FreshnessError(f"Unsupported sport for odds: {sport}")
    elif data_type == "news":
        threshold = settings.FRESHNESS_HOURS_NEWS
    else:
        raise FreshnessError(f"Unsupported data_type: {data_type}")
        
    if age_hours > threshold:
        logger.warning(
            "Data freshness expired",
            sport=sport,
            data_type=data_type,
            age_hours=round(age_hours, 2),
            threshold=threshold
        )
        raise FreshnessError(f"Data is too old: {round(age_hours, 2)} hours > {threshold} threshold")
        
    return True

