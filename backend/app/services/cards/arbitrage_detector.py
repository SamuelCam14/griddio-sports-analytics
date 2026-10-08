"""
Módulo de Detección de Arbitraje y Anomalías de Mercado (Card Flipping).
Audita comisiones de marketplace (eBay 13.25%) y costos de envío.
"""
from typing import Dict, Any


def analyze_arbitrage_opportunity(
    listing_price: float,
    fair_market_value: float,
    platform_fee_pct: float = 0.13,  # Comisión típica de eBay (13.25%)
    shipping_buffer: float = 4.50,
) -> Dict[str, Any]:
    """
    Evalúa si comprar una carta al precio listado representa una oportunidad
    de arbitraje positivo (EV+) tras descontar comisiones de marketplace y envíos.
    """
    net_resale_proceeds = (fair_market_value * (1.0 - platform_fee_pct)) - shipping_buffer
    gross_profit = net_resale_proceeds - listing_price
    roi_pct = (gross_profit / listing_price) if listing_price > 0 else 0.0

    # Margen de seguridad: requiere al menos 15% de margen neto
    is_arbitrage = gross_profit > 10.0 and roi_pct >= 0.15

    return {
        "listing_price": listing_price,
        "fair_market_value": fair_market_value,
        "net_proceeds_after_fees": round(net_resale_proceeds, 2),
        "expected_profit_dollars": round(gross_profit, 2),
        "roi_percentage": round(roi_pct * 100, 2),
        "is_arbitrage_opportunity": is_arbitrage,
        "recommendation": (
            "OPORTUNIDAD EV+ (COMPRA SUBVALORADA)" if is_arbitrage
            else "FAIR VALUE (PRECIO DE MERCADO AJUSTADO)" if abs(roi_pct) < 0.10
            else "SOBREVALORADA (EV NEGATIVO)"
        ),
    }
