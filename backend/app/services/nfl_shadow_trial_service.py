"""
NFL Operational Shadow Trial & Paper Trading Engine — Phase 08 SSOT.

Implements frozen prospective shadow trial tracking, kill/continue criteria,
CLV measurement, and fail-closed operational governance.
Automated bet placement is strictly disallowed; production remains disabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ShadowPickRecord:
    """Record of a single prospectively tracked shadow recommendation."""
    server_pick_id: str
    presented_at_utc: str
    event_id: str
    sport: str
    home_team: str
    away_team: str
    selection: str  # "HOME" or "AWAY"
    model_version: str
    model_prob: float
    fair_market_prob: float
    bookmaker: str
    presented_odds_decimal: float
    presented_odds_american: int
    ev_per_dollar: float
    edge_pct: float
    quarter_kelly_stake: float
    status: str = "shadow_logged"  # "shadow_logged", "executed_manually", "skipped_by_user", "settled", "void"
    user_actual_stake: float = 0.0
    actual_accepted_odds: Optional[float] = None
    closing_odds_decimal: Optional[float] = None
    clv_pct: Optional[float] = None
    outcome: Optional[int] = None  # 1 (win), 0 (loss), None (pending)
    profit_loss: Optional[float] = None
    skip_reason: Optional[str] = None


@dataclass
class TrialKillCheckResult:
    """Outcome of automated kill criteria evaluation."""
    kill_triggered: bool
    verdict: str  # "continue_shadow_trial", "kill_immediately", "close_model", "revise_hypothesis"
    reasons: List[str] = field(default_factory=list)


class NFLShadowTrialService:
    """Governs the Phase 08 shadow trial and operational decision protocol."""

    FROZEN_MODEL_VERSION = "nfl_team_only_temporal_v1"
    FROZEN_MARKET_KEY = "h2h"
    PREREGISTERED_SAMPLE_HORIZON = 100  # Preregistered operational milestone/horizon (not a universal statistical rule)
    MIN_SETTLED_PICKS_FOR_BRIER = 30  # Minimum sample to evaluate Brier drift without small-sample noise
    MAX_LOSS_LIMIT_UNITS = 15.0  # Stop if cumulative loss exceeds 15 flat units
    PROTECTED_QUOTA_RESERVE = 75  # Credits threshold triggering kill

    def __init__(self, log_dir: Optional[Path] = None):
        from ml.data_pipeline.common import project_root
        self.log_dir = log_dir or project_root() / "data" / "trial"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "nfl_shadow_trial_log.json"

    def record_shadow_pick(
        self,
        event_id: str,
        home_team: str,
        away_team: str,
        selection: str,
        model_prob: float,
        fair_market_prob: float,
        bookmaker: str,
        presented_odds_decimal: float,
        presented_odds_american: int,
        ev_per_dollar: float,
        edge_pct: float,
        quarter_kelly_stake: float,
        closing_odds_decimal: Optional[float] = None,
    ) -> ShadowPickRecord:
        """Logs a prospective recommendation in shadow mode without automated wagering."""
        now_str = datetime.now(timezone.utc).isoformat()
        pick_id_raw = f"{event_id}_{selection}_{self.FROZEN_MODEL_VERSION}_{now_str}"
        pick_id = hashlib.sha256(pick_id_raw.encode("utf-8")).hexdigest()[:16]

        clv = None
        if closing_odds_decimal is not None and closing_odds_decimal > 1.0:
            # CLV = (presented_odds / closing_odds) - 1
            clv = round(((presented_odds_decimal / closing_odds_decimal) - 1.0) * 100.0, 2)

        record = ShadowPickRecord(
            server_pick_id=pick_id,
            presented_at_utc=now_str,
            event_id=event_id,
            sport="NFL",
            home_team=home_team,
            away_team=away_team,
            selection=selection,
            model_version=self.FROZEN_MODEL_VERSION,
            model_prob=round(model_prob, 4),
            fair_market_prob=round(fair_market_prob, 4),
            bookmaker=bookmaker,
            presented_odds_decimal=round(presented_odds_decimal, 4),
            presented_odds_american=presented_odds_american,
            ev_per_dollar=round(ev_per_dollar, 4),
            edge_pct=round(edge_pct, 4),
            quarter_kelly_stake=round(quarter_kelly_stake, 4),
            status="shadow_logged",
            user_actual_stake=0.0,  # Never automated
            actual_accepted_odds=None,
            closing_odds_decimal=closing_odds_decimal,
            clv_pct=clv,
            outcome=None,
            profit_loss=None,
        )

        self._append_to_log(record)
        return record

    def settle_pick(
        self,
        server_pick_id: str,
        outcome: int,
        profit_loss: float,
        closing_odds_decimal: Optional[float] = None,
    ) -> Optional[ShadowPickRecord]:
        """Settles an existing shadow pick with ground-truth outcome and units profit/loss."""
        entries = self._load_log_entries()
        updated_record = None

        for entry in entries:
            if entry.get("server_pick_id") == server_pick_id:
                entry["outcome"] = outcome
                entry["profit_loss"] = round(profit_loss, 4)
                entry["status"] = "settled"
                if closing_odds_decimal is not None and closing_odds_decimal > 1.0:
                    entry["closing_odds_decimal"] = closing_odds_decimal
                    pres = entry.get("presented_odds_decimal", 1.0)
                    entry["clv_pct"] = round(((pres / closing_odds_decimal) - 1.0) * 100.0, 2)
                updated_record = ShadowPickRecord(**entry)
                break

        if updated_record is not None:
            self._write_log_entries(entries)

        return updated_record

    def compute_trial_summary(self) -> Dict[str, Any]:
        """Calculates running performance, PnL, CLV, and sample counts from the log."""
        entries = self._load_log_entries()
        total_picks = len(entries)
        settled_entries = [e for e in entries if e.get("status") == "settled" and e.get("outcome") is not None]
        pending_picks = total_picks - len(settled_entries)

        wins = sum(1 for e in settled_entries if e.get("outcome") == 1)
        losses = sum(1 for e in settled_entries if e.get("outcome") == 0)
        cumulative_pnl = sum(e.get("profit_loss", 0.0) or 0.0 for e in settled_entries)

        win_rate = (wins / len(settled_entries)) if settled_entries else 0.0

        clv_values = [e.get("clv_pct") for e in entries if e.get("clv_pct") is not None]
        avg_clv = (sum(clv_values) / len(clv_values)) if clv_values else None

        brier_scores = []
        for e in settled_entries:
            prob = e.get("model_prob")
            outcome = e.get("outcome")
            if prob is not None and outcome in (0, 1):
                actual = 1.0 if outcome == 1 else 0.0
                brier_scores.append((prob - actual) ** 2)

        trailing_brier = (sum(brier_scores) / len(brier_scores)) if brier_scores else None

        return {
            "total_picks": total_picks,
            "settled_picks": len(settled_entries),
            "pending_picks": pending_picks,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "cumulative_units_pnl": round(cumulative_pnl, 4),
            "average_clv_pct": round(avg_clv, 2) if avg_clv is not None else None,
            "trailing_brier_score": round(trailing_brier, 4) if trailing_brier is not None else None,
            "horizon_target": self.PREREGISTERED_SAMPLE_HORIZON,
            "horizon_reached": total_picks >= self.PREREGISTERED_SAMPLE_HORIZON,
        }

    def evaluate_kill_criteria(
        self,
        quota_remaining: int,
        cumulative_units_pnl: float,
        trailing_brier_score: Optional[float] = None,
        majority_baseline_brier: float = 0.2469,
        source_is_healthy: bool = True,
        settled_picks: Optional[int] = None,
        min_settled_picks_for_brier: Optional[int] = None,
    ) -> TrialKillCheckResult:
        """
        Evaluates kill criteria against pre-registered operational boundaries:
        1. Non-finite or NaN inputs -> Fail-closed kill immediately.
        2. Quota reaches protected reserve (<= 75) -> Kill.
        3. Cumulative loss exceeds 15 units -> Kill.
        4. Source feed down/stale -> Kill.
        5. Model drift (Brier exceeds majority baseline) -> Evaluated only when
           settled picks sample size reaches minimum threshold (>= 30).
           Note: selective EV+ picks represent a distinct population from unselected
           league-wide games; evaluating Brier on tiny samples risks premature false-positive kills.
        """
        reasons = []

        if not math.isfinite(quota_remaining):
            return TrialKillCheckResult(
                kill_triggered=True,
                verdict="kill_immediately",
                reasons=["invalid_non_finite_quota"],
            )

        if not math.isfinite(cumulative_units_pnl):
            return TrialKillCheckResult(
                kill_triggered=True,
                verdict="kill_immediately",
                reasons=["invalid_non_finite_cumulative_pnl"],
            )

        if trailing_brier_score is not None and not math.isfinite(trailing_brier_score):
            return TrialKillCheckResult(
                kill_triggered=True,
                verdict="kill_immediately",
                reasons=["invalid_non_finite_trailing_brier"],
            )

        if not math.isfinite(majority_baseline_brier):
            return TrialKillCheckResult(
                kill_triggered=True,
                verdict="kill_immediately",
                reasons=["invalid_non_finite_majority_baseline_brier"],
            )

        if not source_is_healthy:
            reasons.append("data_source_unhealthy_or_down")

        if quota_remaining <= self.PROTECTED_QUOTA_RESERVE:
            reasons.append(f"quota_remaining_{quota_remaining}_below_protected_reserve_{self.PROTECTED_QUOTA_RESERVE}")

        if cumulative_units_pnl <= -self.MAX_LOSS_LIMIT_UNITS:
            reasons.append(f"drawdown_{abs(cumulative_units_pnl):.1f}u_exceeds_max_limit_{self.MAX_LOSS_LIMIT_UNITS}u")

        # Brier drift kill evaluation:
        # Require minimum settled sample size to prevent premature termination on minuscule sample noise.
        min_sample = min_settled_picks_for_brier if min_settled_picks_for_brier is not None else self.MIN_SETTLED_PICKS_FOR_BRIER
        if trailing_brier_score is not None:
            if settled_picks is not None and settled_picks < min_sample:
                # Sample size too small (< 30 settled picks); do not trigger noise-driven kill
                pass
            elif trailing_brier_score > majority_baseline_brier:
                reasons.append(f"model_drift_brier_{trailing_brier_score:.4f}_worse_than_majority_{majority_baseline_brier:.4f}")

        if reasons:
            return TrialKillCheckResult(
                kill_triggered=True,
                verdict="kill_immediately",
                reasons=reasons,
            )

        return TrialKillCheckResult(
            kill_triggered=False,
            verdict="continue_shadow_trial",
            reasons=[],
        )

    def evaluate_live_kill_criteria(
        self,
        quota_ledger_path: Optional[Path] = None,
        source_is_healthy: bool = True,
    ) -> TrialKillCheckResult:
        """Evaluates kill criteria using live ledger quota and current trial log."""
        from ml.data_pipeline.common import project_root
        ledger_path = quota_ledger_path or project_root() / "data" / "odds_quota_ledger.json"

        quota_remaining = 0
        try:
            if not ledger_path.is_file():
                return TrialKillCheckResult(
                    kill_triggered=True,
                    verdict="kill_immediately",
                    reasons=["quota_ledger_missing_fail_closed"],
                )
            ledger_data = json.loads(ledger_path.read_text(encoding="utf-8"))
            quota_remaining = int(ledger_data["summary"]["requests_remaining"])
        except Exception as e:
            return TrialKillCheckResult(
                kill_triggered=True,
                verdict="kill_immediately",
                reasons=[f"quota_ledger_corrupted_fail_closed_{type(e).__name__}"],
            )

        summary = self.compute_trial_summary()
        return self.evaluate_kill_criteria(
            quota_remaining=quota_remaining,
            cumulative_units_pnl=summary["cumulative_units_pnl"],
            trailing_brier_score=summary["trailing_brier_score"],
            source_is_healthy=source_is_healthy,
            settled_picks=summary["settled_picks"],
        )

    def _load_log_entries(self) -> List[Dict[str, Any]]:
        if self.log_file.is_file():
            try:
                data = json.loads(self.log_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return []

    def _write_log_entries(self, entries: List[Dict[str, Any]]) -> None:
        self.log_file.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    def _append_to_log(self, record: ShadowPickRecord) -> None:
        entries = self._load_log_entries()
        entries.append(record.__dict__)
        self._write_log_entries(entries)
