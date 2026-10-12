import pytest
from unittest.mock import patch, MagicMock
import httpx
from app.services.odds_service import fetch_odds
from app.services.freshness_service import ProviderError, ConfigurationError


@pytest.fixture(autouse=True)
def mock_odds_quota(monkeypatch, tmp_path):
    """Aislar el servicio de cuotas durante pruebas de proveedor para no alterar el ledger real."""
    mock_res = MagicMock(reservation_id="mock_1", estimated_cost=3, envelope="main_lines", api_key_masked="mock", params={})
    monkeypatch.setattr("app.services.odds_service.quota_service.reserve_quota", lambda **kw: mock_res)
    monkeypatch.setattr("app.services.odds_service.quota_service.record_transaction", lambda **kw: {})
    monkeypatch.setattr("app.services.odds_service.quota_service.persist_raw_response", lambda **kw: (tmp_path / "raw.json", tmp_path / "canon.json"))


@pytest.mark.asyncio
async def test_fetch_odds_missing_key(monkeypatch):
    """Raise ConfigurationError if API key is missing."""
    monkeypatch.setattr("app.services.odds_service.API_KEY", "")
    with pytest.raises(ConfigurationError, match="ODDS_API_KEY not configured"):
        await fetch_odds(sport="NFL")


@pytest.mark.asyncio
async def test_fetch_odds_empty_response(monkeypatch):
    """Return [] explicitly if provider gives 200 OK but empty."""
    monkeypatch.setattr("app.services.odds_service.API_KEY", "dummy")
    
    # Mock httpx client
    mock_resp = httpx.Response(200, request=httpx.Request("GET", "https://api"), json=[])
    
    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res = await fetch_odds(sport="NFL")
        assert res == []


@pytest.mark.asyncio
async def test_fetch_odds_429_too_many_requests(monkeypatch):
    """Raise ProviderError on 429."""
    monkeypatch.setattr("app.services.odds_service.API_KEY", "dummy")
    
    mock_resp = httpx.Response(429, request=httpx.Request("GET", "https://api"))
    
    with patch("httpx.AsyncClient.get", side_effect=httpx.HTTPStatusError("429 Too Many Requests", request=mock_resp.request, response=mock_resp)):
        with pytest.raises(ProviderError, match="HTTP error 429"):
            await fetch_odds(sport="NFL")


@pytest.mark.asyncio
async def test_fetch_odds_timeout(monkeypatch):
    """Raise ProviderError on Timeout."""
    monkeypatch.setattr("app.services.odds_service.API_KEY", "dummy")
    
    with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("Connection timeout")):
        with pytest.raises(ProviderError, match="Unexpected provider error"):
            await fetch_odds(sport="NFL")
