"""Card price forecasting is unavailable until genuine price history is ingested."""
from typing import Any, Dict, List


def project_depreciation_trajectory_prophet(
    initial_price: float,
    card_type: str = "NIL",
    days_forecast: int = 90,
) -> List[Dict[str, Any]]:
    """Return no forecast rather than manufacture historical prices from one quote.

    `initial_price`, `card_type`, and `days_forecast` remain in the signature for
    compatibility with older callers. A real dated transaction series is needed
    before a time-series model can produce a meaningful trajectory.
    """
    return []
