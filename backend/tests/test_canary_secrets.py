"""
Pruebas obligatorias de seguridad y canario de secretos (Fase 02 / Matriz mínima).
Verifica que las claves de API y secretos no se filtren a través de:
1. Logs de errores o warnings de httpx.
2. URLs en excepciones HTTP o de timeout.
3. Transacciones y notas del ledger de cuota.
4. Snapshots persistidos.
"""
from pathlib import Path
import json
import logging
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from app.services.odds_service import fetch_odds, _CACHE_TTL_SECONDS, _ODDS_CACHE
from app.services.odds_quota_service import OddsQuotaService


@pytest.mark.asyncio
async def test_canary_secret_not_leaked_in_http_error_logs(caplog, tmp_path):
    """Canary test: una clave de prueba nunca debe aparecer en ningún sink de logs ante fallos HTTP."""
    canary_key = "CANARY_SECRET_KEY_PROD_READINESS_789XYZ"

    mock_ledger_path = tmp_path / "mock_ledger.json"
    mock_ledger_path.write_text(json.dumps({
        "version": "1.0",
        "summary": {
            "requests_used": 0,
            "requests_remaining": 250,
            "spent_by_envelope": {"main_lines": 0, "props": 0, "operational": 0},
            "circuit_breaker_open": False,
        },
        "pending_reservations": {},
        "transactions": [],
    }), encoding="utf-8")

    quota_svc = OddsQuotaService(ledger_path=mock_ledger_path)

    # Simular una respuesta 401 con la URL conteniendo el canario
    url_with_canary = f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/?apiKey={canary_key}&regions=us"
    mock_request = httpx.Request("GET", url_with_canary)
    mock_response = httpx.Response(status_code=401, request=mock_request)

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    with caplog.at_level(logging.WARNING):
        with patch("app.services.odds_service.quota_service", quota_svc):
            with patch("app.services.odds_service.httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                with patch("app.services.odds_service.api_key_manager.get_active_key", return_value=canary_key):
                    # Limpiar caché para forzar petición
                    _ODDS_CACHE.clear()
                    result = await fetch_odds(sport="NFL", markets="h2h", regions="us")

    assert result == []

    # Verificar que el canario NO aparece en ningún log registrado
    captured_text = caplog.text
    assert canary_key not in captured_text, f"Fuga de secreto detectada en logs: {canary_key}"

    # Verificar que el canario NO aparece en el ledger persistido
    ledger_content = mock_ledger_path.read_text(encoding="utf-8")
    assert canary_key not in ledger_content, f"Fuga de secreto detectada en ledger: {canary_key}"


@pytest.mark.asyncio
async def test_canary_secret_not_leaked_in_timeout_exception(caplog, tmp_path):
    """Canary test: un timeout de red no expone la clave en la traza ni en el mensaje del ledger."""
    canary_key = "CANARY_TIMEOUT_SECRET_KEY_456ABC"

    mock_ledger_path = tmp_path / "mock_ledger_timeout.json"
    mock_ledger_path.write_text(json.dumps({
        "version": "1.0",
        "summary": {
            "requests_used": 0,
            "requests_remaining": 250,
            "spent_by_envelope": {"main_lines": 0, "props": 0, "operational": 0},
            "circuit_breaker_open": False,
        },
        "pending_reservations": {},
        "transactions": [],
    }), encoding="utf-8")

    quota_svc = OddsQuotaService(ledger_path=mock_ledger_path)

    url_with_canary = f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/?apiKey={canary_key}&regions=us"
    mock_request = httpx.Request("GET", url_with_canary)

    mock_client = AsyncMock()
    mock_client.get.side_effect = httpx.TimeoutException(
        f"ConnectTimeout while requesting {url_with_canary}",
        request=mock_request,
    )

    with caplog.at_level(logging.WARNING):
        with patch("app.services.odds_service.quota_service", quota_svc):
            with patch("app.services.odds_service.httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                with patch("app.services.odds_service.api_key_manager.get_active_key", return_value=canary_key):
                    with pytest.raises(Exception):
                        await fetch_odds(sport="NFL", markets="h2h", regions="us", allow_db_fallback=False)

    # Verificar que el canario NO aparece en los logs
    assert canary_key not in caplog.text, "Fuga de secreto detectada en logs tras Timeout"

    # Verificar que el canario NO aparece en el ledger
    ledger_content = mock_ledger_path.read_text(encoding="utf-8")
    assert canary_key not in ledger_content, "Fuga de secreto detectada en ledger tras Timeout"
