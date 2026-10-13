import json
from pathlib import Path
import pytest

from app.services.nfl_shadow_trial_service import (
    NFLShadowTrialService,
    ShadowPickRecord,
    TrialKillCheckResult,
)


@pytest.fixture
def trial_service(tmp_path):
    return NFLShadowTrialService(log_dir=tmp_path)


def test_shadow_pick_recording_and_clv(trial_service):
    """Logs prospective shadow recommendation with proper CLV and non-automated stake."""
    record = trial_service.record_shadow_pick(
        event_id="nfl_2026_kc_lac",
        home_team="Kansas City Chiefs",
        away_team="Los Angeles Chargers",
        selection="HOME",
        model_prob=0.64,
        fair_market_prob=0.58,
        bookmaker="draftkings",
        presented_odds_decimal=1.85,
        presented_odds_american=-118,
        ev_per_dollar=0.184,
        edge_pct=0.06,
        quarter_kelly_stake=0.035,
        closing_odds_decimal=1.75,  # line beat: closed shorter
    )

    assert isinstance(record, ShadowPickRecord)
    assert record.status == "shadow_logged"
    assert record.user_actual_stake == 0.0  # Zero automated wagering
    assert record.model_version == "nfl_team_only_temporal_v1"
    # CLV = (1.85 / 1.75) - 1 = +5.71%
    assert record.clv_pct is not None
    assert record.clv_pct > 5.0

    # Verify written to disk log
    log_data = json.loads(trial_service.log_file.read_text(encoding="utf-8"))
    assert len(log_data) == 1
    assert log_data[0]["server_pick_id"] == record.server_pick_id


def test_kill_criteria_quota_reserve_breach(trial_service):
    """Trigger kill immediately if quota reaches protected reserve (<= 75)."""
    # 75 remaining = reserve hit
    res = trial_service.evaluate_kill_criteria(quota_remaining=75, cumulative_units_pnl=0.0)
    assert res.kill_triggered is True
    assert res.verdict == "kill_immediately"
    assert any("below_protected_reserve" in r for r in res.reasons)

    # 74 remaining = below reserve
    res2 = trial_service.evaluate_kill_criteria(quota_remaining=74, cumulative_units_pnl=0.0)
    assert res2.kill_triggered is True


def test_kill_criteria_drawdown_limit_breach(trial_service):
    """Trigger kill immediately if drawdown reaches 15 units."""
    # Loss of 15.5 units
    res = trial_service.evaluate_kill_criteria(quota_remaining=275, cumulative_units_pnl=-15.5)
    assert res.kill_triggered is True
    assert res.verdict == "kill_immediately"
    assert any("exceeds_max_limit" in r for r in res.reasons)


def test_kill_criteria_model_drift_breach(trial_service):
    """Trigger kill if trailing Brier degrades past majority baseline with sufficient sample (>= 30)."""
    # Trailing Brier of 0.2550 is worse than 0.2469, evaluated with settled_picks=35
    res = trial_service.evaluate_kill_criteria(
        quota_remaining=275,
        cumulative_units_pnl=2.0,
        trailing_brier_score=0.2550,
        majority_baseline_brier=0.2469,
        settled_picks=35,
    )
    assert res.kill_triggered is True
    assert any("model_drift" in r for r in res.reasons)


def test_kill_criteria_model_drift_small_sample_does_not_prematurely_kill(trial_service):
    """Do NOT trigger premature kill on minuscule sample size (< 30) due to noise/variance."""
    # With only 5 settled picks, high Brier must not trigger kill
    res_small = trial_service.evaluate_kill_criteria(
        quota_remaining=275,
        cumulative_units_pnl=2.0,
        trailing_brier_score=0.2550,
        majority_baseline_brier=0.2469,
        settled_picks=5,
    )
    assert res_small.kill_triggered is False
    assert res_small.verdict == "continue_shadow_trial"

    # Exactly at boundary: 29 picks -> no kill, 30 picks -> kill
    res_29 = trial_service.evaluate_kill_criteria(
        quota_remaining=275,
        cumulative_units_pnl=2.0,
        trailing_brier_score=0.2550,
        majority_baseline_brier=0.2469,
        settled_picks=29,
    )
    assert res_29.kill_triggered is False

    res_30 = trial_service.evaluate_kill_criteria(
        quota_remaining=275,
        cumulative_units_pnl=2.0,
        trailing_brier_score=0.2550,
        majority_baseline_brier=0.2469,
        settled_picks=30,
    )
    assert res_30.kill_triggered is True
    assert any("model_drift" in r for r in res_30.reasons)


def test_kill_criteria_healthy_condition_continues_trial(trial_service):
    """Trial continues cleanly when all metrics stay within healthy boundaries."""
    res = trial_service.evaluate_kill_criteria(
        quota_remaining=275,
        cumulative_units_pnl=3.2,
        trailing_brier_score=0.2250,
        majority_baseline_brier=0.2469,
        source_is_healthy=True,
    )
    assert res.kill_triggered is False
    assert res.verdict == "continue_shadow_trial"
    assert len(res.reasons) == 0


def test_kill_criteria_fails_closed_on_nan_or_inf_metrics(trial_service):
    """Any non-finite metric (NaN, Inf) must immediately fail-closed and trigger kill."""
    # NaN PnL
    res_nan_pnl = trial_service.evaluate_kill_criteria(
        quota_remaining=275, cumulative_units_pnl=float("nan"),
    )
    assert res_nan_pnl.kill_triggered is True
    assert res_nan_pnl.verdict == "kill_immediately"
    assert "invalid_non_finite_cumulative_pnl" in res_nan_pnl.reasons

    # NaN Brier
    res_nan_brier = trial_service.evaluate_kill_criteria(
        quota_remaining=275, cumulative_units_pnl=0.0, trailing_brier_score=float("nan"),
    )
    assert res_nan_brier.kill_triggered is True
    assert "invalid_non_finite_trailing_brier" in res_nan_brier.reasons

    # Infinite Quota
    res_inf_quota = trial_service.evaluate_kill_criteria(
        quota_remaining=float("inf"), cumulative_units_pnl=0.0,
    )
    assert res_inf_quota.kill_triggered is True
    assert "invalid_non_finite_quota" in res_inf_quota.reasons


def test_log_file_corrupted_dict_recovers_gracefully(trial_service):
    """If log file on disk contains a non-list JSON root, logging must not raise AttributeError."""
    trial_service.log_file.write_text(json.dumps({"invalid": "root_object"}), encoding="utf-8")
    record = trial_service.record_shadow_pick(
        event_id="nfl_2026_kc_sf",
        home_team="Kansas City Chiefs",
        away_team="San Francisco 49ers",
        selection="HOME",
        model_prob=0.62,
        fair_market_prob=0.55,
        bookmaker="draftkings",
        presented_odds_decimal=1.91,
        presented_odds_american=-110,
        ev_per_dollar=0.18,
        edge_pct=0.07,
        quarter_kelly_stake=0.04,
    )
    assert record.status == "shadow_logged"
    data = json.loads(trial_service.log_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["server_pick_id"] == record.server_pick_id


def test_settle_pick_lifecycle_and_clv_recalculation(trial_service):
    """Settling a pick updates status, outcome, profit/loss, and closing line CLV."""
    record = trial_service.record_shadow_pick(
        event_id="nfl_2026_det_gb",
        home_team="Detroit Lions",
        away_team="Green Bay Packers",
        selection="HOME",
        model_prob=0.60,
        fair_market_prob=0.52,
        bookmaker="draftkings",
        presented_odds_decimal=2.00,
        presented_odds_american=+100,
        ev_per_dollar=0.20,
        edge_pct=0.08,
        quarter_kelly_stake=0.05,
    )
    assert record.outcome is None

    settled = trial_service.settle_pick(
        server_pick_id=record.server_pick_id,
        outcome=1,
        profit_loss=1.0,
        closing_odds_decimal=1.80,  # line closed shorter
    )
    assert settled is not None
    assert settled.status == "settled"
    assert settled.outcome == 1
    assert settled.profit_loss == 1.0
    assert settled.clv_pct is not None
    assert settled.clv_pct > 10.0  # (2.0 / 1.8) - 1 = +11.11%


def test_compute_trial_summary_and_horizon_tracking(trial_service):
    """Calculates running counts, win rate, PnL, CLV, and pre-registered sample horizon progress."""
    rec1 = trial_service.record_shadow_pick(
        event_id="e1", home_team="KC", away_team="LV", selection="HOME",
        model_prob=0.70, fair_market_prob=0.60, bookmaker="dk",
        presented_odds_decimal=1.60, presented_odds_american=-167,
        ev_per_dollar=0.12, edge_pct=0.10, quarter_kelly_stake=0.04,
        closing_odds_decimal=1.50,
    )
    rec2 = trial_service.record_shadow_pick(
        event_id="e2", home_team="BAL", away_team="CLE", selection="HOME",
        model_prob=0.65, fair_market_prob=0.58, bookmaker="dk",
        presented_odds_decimal=1.70, presented_odds_american=-143,
        ev_per_dollar=0.10, edge_pct=0.07, quarter_kelly_stake=0.03,
        closing_odds_decimal=1.75,
    )

    trial_service.settle_pick(rec1.server_pick_id, outcome=1, profit_loss=0.60)
    trial_service.settle_pick(rec2.server_pick_id, outcome=0, profit_loss=-1.00)

    summary = trial_service.compute_trial_summary()
    assert summary["total_picks"] == 2
    assert summary["settled_picks"] == 2
    assert summary["pending_picks"] == 0
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["win_rate"] == 0.50
    assert summary["cumulative_units_pnl"] == -0.40
    assert summary["average_clv_pct"] is not None
    assert summary["trailing_brier_score"] is not None
    assert summary["horizon_reached"] is False
    assert summary["horizon_target"] == 100


def test_evaluate_live_kill_criteria_with_ledger(trial_service, tmp_path):
    """Evaluates live kill check reading from a real quota ledger JSON."""
    ledger_file = tmp_path / "mock_quota_ledger.json"
    ledger_file.write_text(json.dumps({
        "summary": {"requests_remaining": 275}
    }), encoding="utf-8")

    res = trial_service.evaluate_live_kill_criteria(quota_ledger_path=ledger_file)
    assert res.kill_triggered is False
    assert res.verdict == "continue_shadow_trial"

    # Simulated reserve breach
    ledger_breach = tmp_path / "breach_ledger.json"
    ledger_breach.write_text(json.dumps({
        "summary": {"requests_remaining": 70}
    }), encoding="utf-8")

    res_breach = trial_service.evaluate_live_kill_criteria(quota_ledger_path=ledger_breach)
    assert res_breach.kill_triggered is True
    assert res_breach.verdict == "kill_immediately"

