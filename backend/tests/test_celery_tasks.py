import pytest
from unittest.mock import patch
from app.workers.celery_app import ingest_odds_task, celery_app

# Patch celery app for tests so it doesn't need redis
celery_app.conf.update(
    task_always_eager=True,
    result_backend='cache+memory://'
)

def test_celery_ingest_odds_success():
    """Ensure the task returns successfully without error."""
    with patch("app.services.live_ingestion_service.sync_real_games") as mock_sync:
        async def mock_coro(*args, **kwargs):
            return {"status": "success", "sport": "NBA"}
        mock_sync.side_effect = mock_coro
        
        result = ingest_odds_task.apply(kwargs={"sport": "NBA"}).get()
        assert result == {"status": "success", "sport": "NBA"}

def test_celery_ingest_odds_provider_error():
    """Ensure provider errors raise exception to trigger retry and respect limits."""
    from app.services.freshness_service import ProviderError
    
    with patch("app.services.live_ingestion_service.sync_real_games") as mock_sync:
        async def mock_err(*args, **kwargs):
            return {"status": "error", "sport": "NBA", "error_type": "provider_error", "detail": "Rate limited"}
        mock_sync.side_effect = mock_err
        
        with patch.object(ingest_odds_task, "retry") as mock_retry:
            from celery.exceptions import Retry
            mock_retry.side_effect = Retry("Simulated retry")
            
            with pytest.raises(Retry):
                ingest_odds_task(sport="NBA")
                
            mock_retry.assert_called_once()
            
def test_celery_configuration_error_no_retry():
    """Ensure configuration errors don't trigger retries."""
    from app.services.freshness_service import ConfigurationError
    
    with patch("app.services.live_ingestion_service.sync_real_games") as mock_sync:
        async def mock_err(*args, **kwargs):
            raise ConfigurationError("No API key")
        mock_sync.side_effect = mock_err
        
        with patch.object(ingest_odds_task, "retry") as mock_retry:
            with pytest.raises(ConfigurationError):
                ingest_odds_task(sport="NBA")
            
            mock_retry.assert_not_called()