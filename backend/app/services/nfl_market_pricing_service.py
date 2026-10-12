"""
NFL Market Pricing and Expected Value (EV) Engine — Phase 05 SSOT.

Implements strict point-in-time price contracts, vig removal (de-vigging),
expected value (EV) calculations, fractional Kelly staking sensitivities,
and the historical vs prospective evaluation protocol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional

from app.services.math.odds_converter import (
    american_to_decimal,
    decimal_to_american,
    american_to_implied_prob,
    remove_vig,
)
from app.services.math.kelly import (
    calculate_kelly_fraction,
    calculate_kelly_amount,
)


@dataclass(frozen=True)
class PriceRecord:
    """Strict point-in-time market odds contract per Phase 05 specification."""
    event_id: str
    sportsbook: str
    market_key: str
    home_team: str
    away_team: str
    home_odds_american: int
    away_odds_american: int
    source_timestamp_utc: str
    fetched_timestamp_utc: str
    decision_cutoff_utc: str
    market_status: str = "open"
    mapping_confidence: float = 1.0
    home_odds_decimal: float = field(init=False)
    away_odds_decimal: float = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "home_odds_decimal", american_to_decimal(self.home_odds_american))
        object.__setattr__(self, "away_odds_decimal", american_to_decimal(self.away_odds_american))


@dataclass
class MarketPricedPick:
    """Evaluated pick comparing model probability to fair market price."""
    event_id: str
    sportsbook: str
    selection: str  # "HOME" or "AWAY"
    team_name: str
    model_prob: float
    market_raw_prob: float
    fair_market_prob: float
    odds_american: int
    odds_decimal: float
    edge_pct: float
    ev_per_dollar: float
    ev_pct: str
    quarter_kelly_fraction: float
    is_eligible: bool
    rejection_reason: Optional[str] = None
    market_vig: float = 0.0


class NFLMarketPricingService:
    """Pure point-in-time pricing and expected value service."""

    def __init__(
        self,
        min_edge_threshold: float = 0.03,
        max_kelly_fraction: float = 0.05,
        max_odds_age_hours: float = 24.0,
    ):
        self.min_edge = min_edge_threshold
        self.max_kelly = max_kelly_fraction
        self.max_odds_age_seconds = max_odds_age_hours * 3600.0

    def evaluate_game_pricing(
        self,
        model_prob_home: float,
        price_record: PriceRecord,
        as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Evaluates home and away sides against the market bookmaker price.
        Enforces point-in-time honesty:
        - Rejects odds observed after the decision cutoff.
        - Rejects stale odds exceeding freshness window.
        - Rejects suspended or low-confidence market mapping.
        """
        if not math.isfinite(model_prob_home) or not (0.0 <= model_prob_home <= 1.0):
            return {
                "status": "rejected",
                "reason": "invalid_model_probability",
                "picks": [],
                "historical_roi_status": "not_evaluable",
                "evaluation_note": "ROI historical backtest not measured without complete historical odds snapshots.",
            }

        # Timestamp validations
        try:
            source_dt = datetime.fromisoformat(price_record.source_timestamp_utc.replace("Z", "+00:00"))
            fetched_dt = datetime.fromisoformat(price_record.fetched_timestamp_utc.replace("Z", "+00:00"))
            cutoff_dt = datetime.fromisoformat(price_record.decision_cutoff_utc.replace("Z", "+00:00"))
        except Exception:
            return {
                "status": "rejected",
                "reason": "invalid_timestamp_format",
                "picks": [],
                "historical_roi_status": "not_evaluable",
                "evaluation_note": "ROI historical backtest not measured without complete historical odds snapshots.",
            }

        # 1. Reject if market is suspended
        if price_record.market_status.lower() != "open":
            return {
                "status": "rejected",
                "reason": f"market_{price_record.market_status.lower()}",
                "picks": [],
                "historical_roi_status": "not_evaluable",
                "evaluation_note": "ROI historical backtest not measured without complete historical odds snapshots.",
            }

        # 2. Reject causality violation (source publication in future relative to fetch)
        if source_dt > fetched_dt:
            return {
                "status": "rejected",
                "reason": "odds_source_after_fetch",
                "source_utc": price_record.source_timestamp_utc,
                "fetched_utc": price_record.fetched_timestamp_utc,
                "picks": [],
                "historical_roi_status": "not_evaluable",
                "evaluation_note": "ROI historical backtest not measured without complete historical odds snapshots.",
            }

        # 3. Reject if price was fetched or published strictly after the decision cutoff
        if fetched_dt > cutoff_dt or source_dt > cutoff_dt:
            return {
                "status": "rejected",
                "reason": "odds_post_cutoff",
                "fetched_utc": price_record.fetched_timestamp_utc,
                "source_utc": price_record.source_timestamp_utc,
                "cutoff_utc": price_record.decision_cutoff_utc,
                "picks": [],
                "historical_roi_status": "not_evaluable",
                "evaluation_note": "ROI historical backtest not measured without complete historical odds snapshots.",
            }

        # 4. Reject stale odds exceeding freshness window relative to as_of or cutoff
        anchor_dt = as_of if as_of is not None else cutoff_dt
        odds_age_seconds = (anchor_dt - fetched_dt).total_seconds()
        if odds_age_seconds > self.max_odds_age_seconds:
            return {
                "status": "rejected",
                "reason": "stale_odds",
                "odds_age_hours": round(odds_age_seconds / 3600.0, 2),
                "max_age_hours": round(self.max_odds_age_seconds / 3600.0, 2),
                "picks": [],
                "historical_roi_status": "not_evaluable",
                "evaluation_note": "ROI historical backtest not measured without complete historical odds snapshots.",
            }

        # 5. Reject if mapping confidence is low
        if price_record.mapping_confidence < 0.90:
            return {
                "status": "rejected",
                "reason": "low_mapping_confidence",
                "picks": [],
                "historical_roi_status": "not_evaluable",
                "evaluation_note": "ROI historical backtest not measured without complete historical odds snapshots.",
            }

        # 6. Reject invalid odds values (must be > 1.0 decimal)
        if price_record.home_odds_decimal <= 1.0 or price_record.away_odds_decimal <= 1.0:
            return {
                "status": "rejected",
                "reason": "invalid_odds_decimal",
                "picks": [],
                "historical_roi_status": "not_evaluable",
                "evaluation_note": "ROI historical backtest not measured without complete historical odds snapshots.",
            }

        # Calculate implied probabilities and de-vig
        p_home_raw = 1.0 / price_record.home_odds_decimal
        p_away_raw = 1.0 / price_record.away_odds_decimal
        p_home_fair, p_away_fair, vig = remove_vig(p_home_raw, p_away_raw)

        model_prob_away = 1.0 - model_prob_home

        # Home evaluation
        ev_home = (model_prob_home * (price_record.home_odds_decimal - 1.0)) - (1.0 - model_prob_home)
        edge_home = model_prob_home - p_home_fair
        kelly_home = calculate_kelly_fraction(
            model_prob_home,
            price_record.home_odds_decimal,
            fraction_multiplier=0.25,
            max_cap=self.max_kelly,
        )

        # Away evaluation
        ev_away = (model_prob_away * (price_record.away_odds_decimal - 1.0)) - (1.0 - model_prob_away)
        edge_away = model_prob_away - p_away_fair
        kelly_away = calculate_kelly_fraction(
            model_prob_away,
            price_record.away_odds_decimal,
            fraction_multiplier=0.25,
            max_cap=self.max_kelly,
        )

        home_pick = MarketPricedPick(
            event_id=price_record.event_id,
            sportsbook=price_record.sportsbook,
            selection="HOME",
            team_name=price_record.home_team,
            model_prob=round(model_prob_home, 4),
            market_raw_prob=round(p_home_raw, 4),
            fair_market_prob=round(p_home_fair, 4),
            odds_american=price_record.home_odds_american,
            odds_decimal=round(price_record.home_odds_decimal, 4),
            edge_pct=round(edge_home, 4),
            ev_per_dollar=round(ev_home, 4),
            ev_pct=f"{ev_home * 100:+.2f}%",
            quarter_kelly_fraction=round(kelly_home, 4),
            is_eligible=edge_home >= self.min_edge and ev_home > 0.0,
            rejection_reason=None if (edge_home >= self.min_edge and ev_home > 0.0) else "insufficient_edge",
            market_vig=round(vig, 4),
        )

        away_pick = MarketPricedPick(
            event_id=price_record.event_id,
            sportsbook=price_record.sportsbook,
            selection="AWAY",
            team_name=price_record.away_team,
            model_prob=round(model_prob_away, 4),
            market_raw_prob=round(p_away_raw, 4),
            fair_market_prob=round(p_away_fair, 4),
            odds_american=price_record.away_odds_american,
            odds_decimal=round(price_record.away_odds_decimal, 4),
            edge_pct=round(edge_away, 4),
            ev_per_dollar=round(ev_away, 4),
            ev_pct=f"{ev_away * 100:+.2f}%",
            quarter_kelly_fraction=round(kelly_away, 4),
            is_eligible=edge_away >= self.min_edge and ev_away > 0.0,
            rejection_reason=None if (edge_away >= self.min_edge and ev_away > 0.0) else "insufficient_edge",
            market_vig=round(vig, 4),
        )

        return {
            "status": "evaluated",
            "event_id": price_record.event_id,
            "sportsbook": price_record.sportsbook,
            "vig_pct": round(vig * 100, 2),
            "home": home_pick.__dict__,
            "away": away_pick.__dict__,
            "eligible_picks": [
                p.__dict__ for p in [home_pick, away_pick] if p.is_eligible
            ],
            # Phase 05 explicit protocol: Without verified historical odds DB, label not_evaluable
            "historical_roi_status": "not_evaluable",
            "evaluation_note": "ROI historical backtest not measured without complete historical odds snapshots.",
        }
