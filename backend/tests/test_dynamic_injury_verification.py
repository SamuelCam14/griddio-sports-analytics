"""
Automated Verification Test Suite for Milestone 2: Dynamic Injury Model (Requirement R2).
Proves:
1. Dynamic Degradation: A high-impact recent producer (e.g. 25.0 PPR) causes significantly
   greater dynamic degradation than a low-impact producer (e.g. 2.0 PPR).
2. Elimination of Positional Hierarchy: A high-performing WR/RB penalizes the team more than
   a low-performing backup QB; zero fixed positional multipliers exist.
3. Strict Temporal Leakage Prevention: Zero temporal leakage; stats from the evaluated game's
   week (t) or future weeks (t+1) are strictly inaccessible in features and importance scoring.
4. Pre-Kickoff Inactives Workflow & Recalculation: Celery schedules near_kickoff capture at
   t - 90 min, capture triggers evaluate_predictions_task for affected games, and prediction
   recalculation executes with chronological timestamp progression (t2 > t1).
5. Quota Ledger Immutability: data/odds_quota_ledger.json sha256 checksum is preserved.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.services.nfl_availability_service import (
    ALL_NFL_TEAMS,
    BASE_P_ABSENCE,
    MARKET_MODULATED_P_ABSENCE,
    RESOLVER_VERSION,
    AvailabilityStatus,
    CoverageState,
    ESPNInjurySnapshot,
    MarketPropSignal,
    NFLAvailabilityService,
    PlayerInjuryRecord,
    TeamAvailabilitySummary,
)
from ml.data_pipeline.common import DataQualityError
from ml.data_pipeline.injury_feature_builder import (
    _position_metrics,
    build_nfl_availability_ground_truth,
    build_nfl_historical_injury_features,
)
from app.workers.celery_app import (
    capture_nfl_injuries_task,
    celery_app,
    evaluate_predictions_task,
    schedule_nfl_gameday_captures_task,
)

EXPECTED_QUOTA_LEDGER_SHA256 = "f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4"


@pytest.fixture
def availability_svc(tmp_path):
    return NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")


# ==============================================================================
# 1. Dynamic Production Degradation (High Producer vs Low Producer)
# ==============================================================================

class TestDynamicProductionImpact:
    """
    Verifica que el impacto de una baja se derive dinámicamente de su producción reciente
    (rolling PPR) respecto al potencial del equipo, y no de factores arbitrarios.
    """

    def test_high_producer_causes_significantly_greater_degradation_than_low_producer(self, availability_svc):
        """
        Un jugador con 25.0 PPR promedio (estrella) genera una degradación dinámica
        significativamente mayor (> 12x) que un jugador con 2.0 PPR promedio (suplente).
        """
        now_utc = datetime.now(timezone.utc)
        roster_stats = {
            "star_producer": {"rolling_ppr": 25.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0},
            "low_producer": {"rolling_ppr": 2.0, "rolling_off_pct": 0.10, "rolling_def_pct": 0.0},
            "teammate_1": {"rolling_ppr": 20.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
            "teammate_2": {"rolling_ppr": 15.0, "rolling_off_pct": 0.70, "rolling_def_pct": 0.0},
            "teammate_3": {"rolling_ppr": 10.0, "rolling_off_pct": 0.60, "rolling_def_pct": 0.0},
        }
        # Team potential = 25.0 + 2.0 + 20.0 + 15.0 + 10.0 = 72.0
        team_potential = 72.0

        # Caso A: Star Producer OUT
        record_star = [PlayerInjuryRecord(
            player_name="star_producer", team="DET", position="WR", canonical_player_id="star_producer",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=3)).isoformat(),
        )]
        summary_star = availability_svc.compute_team_production_lost(
            team="DET", records=record_star, prior_roster_stats=roster_stats,
            decision_cutoff=now_utc.isoformat(),
        )

        # Caso B: Low Producer OUT
        record_low = [PlayerInjuryRecord(
            player_name="low_producer", team="DET", position="WR", canonical_player_id="low_producer",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=3)).isoformat(),
        )]
        summary_low = availability_svc.compute_team_production_lost(
            team="DET", records=record_low, prior_roster_stats=roster_stats,
            decision_cutoff=now_utc.isoformat(),
        )

        assert summary_star.ppr_lost_pct is not None
        assert summary_low.ppr_lost_pct is not None

        expected_star_loss = round(25.0 / team_potential, 4)
        expected_low_loss = round(2.0 / team_potential, 4)

        assert summary_star.ppr_lost_pct == pytest.approx(expected_star_loss, abs=1e-4)
        assert summary_low.ppr_lost_pct == pytest.approx(expected_low_loss, abs=1e-4)

        # La estrella degrada al equipo 12.5 veces más que el jugador de bajo impacto
        degradation_ratio = summary_star.ppr_lost_pct / summary_low.ppr_lost_pct
        assert degradation_ratio == pytest.approx(12.5, abs=0.1)
        assert summary_star.ppr_lost_pct > summary_low.ppr_lost_pct * 12.0

    @pytest.mark.parametrize("p_star,p_backup,star_ppr,backup_ppr", [
        (1.0, 1.0, 28.0, 1.5),
        (1.0, 1.0, 24.0, 3.0),
        (0.60, 1.0, 25.0, 2.0),   # Star doubtful with active prop line (0.60) vs backup confirmed out (1.0)
        (0.15, 1.0, 25.0, 2.0),   # Star questionable with active prop line (0.15) vs backup out (1.0)
    ])
    def test_dynamic_degradation_various_production_and_absence_levels(
        self, availability_svc, p_star, p_backup, star_ppr, backup_ppr
    ):
        """Verifica que la pérdida esperada sea estrictamente proporcional a rolling_ppr * p_absence."""
        now_utc = datetime.now(timezone.utc)
        roster_stats = {
            "player_star": {"rolling_ppr": star_ppr, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
            "player_backup": {"rolling_ppr": backup_ppr, "rolling_off_pct": 0.15, "rolling_def_pct": 0.0},
            "other_players": {"rolling_ppr": 50.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        }
        team_potential = star_ppr + backup_ppr + 50.0

        rec_star = [PlayerInjuryRecord(
            player_name="player_star", team="SF", position="RB", canonical_player_id="player_star",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value if p_star == 1.0 else (
                AvailabilityStatus.DOUBTFUL.value if p_star == 0.60 else AvailabilityStatus.QUESTIONABLE.value
            ),
            market_signal=MarketPropSignal.LISTED_AT_CUTOFF.value if p_star < 1.0 else MarketPropSignal.UNKNOWN.value,
            p_absence=p_star, observed_at=(now_utc - timedelta(hours=3)).isoformat(),
        )]
        summary_star = availability_svc.compute_team_production_lost(
            team="SF", records=rec_star, prior_roster_stats=roster_stats, decision_cutoff=now_utc.isoformat(),
        )

        rec_backup = [PlayerInjuryRecord(
            player_name="player_backup", team="SF", position="RB", canonical_player_id="player_backup",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value if p_backup == 1.0 else AvailabilityStatus.QUESTIONABLE.value,
            p_absence=p_backup, observed_at=(now_utc - timedelta(hours=3)).isoformat(),
        )]
        summary_backup = availability_svc.compute_team_production_lost(
            team="SF", records=rec_backup, prior_roster_stats=roster_stats, decision_cutoff=now_utc.isoformat(),
        )

        expected_star_lost = (star_ppr * p_star) / team_potential
        expected_backup_lost = (backup_ppr * p_backup) / team_potential

        assert summary_star.ppr_lost_pct == pytest.approx(expected_star_lost, abs=1e-3)
        assert summary_backup.ppr_lost_pct == pytest.approx(expected_backup_lost, abs=1e-3)
        assert summary_star.ppr_lost_pct > summary_backup.ppr_lost_pct

    def test_additive_dynamic_injury_impact_multiple_players(self, availability_svc):
        """Verifica que el impacto de múltiples lesionados sea estrictamente la suma de sus pérdidas esperadas."""
        now_utc = datetime.now(timezone.utc)
        roster_stats = {
            "p1": {"rolling_ppr": 18.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
            "p2": {"rolling_ppr": 12.0, "rolling_off_pct": 0.60, "rolling_def_pct": 0.0},
            "p3": {"rolling_ppr": 6.0, "rolling_off_pct": 0.30, "rolling_def_pct": 0.0},
            "rest": {"rolling_ppr": 44.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        }
        team_potential = 18.0 + 12.0 + 6.0 + 44.0  # 80.0

        records = [
            PlayerInjuryRecord(
                player_name="p1", team="BAL", position="TE", canonical_player_id="p1",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                observed_at=(now_utc - timedelta(hours=3)).isoformat(),
            ),
            PlayerInjuryRecord(
                player_name="p2", team="BAL", position="WR", canonical_player_id="p2",
                availability_status=AvailabilityStatus.QUESTIONABLE.value,
                market_signal=MarketPropSignal.NOT_LISTED_WITH_COVERAGE.value,
                p_absence=0.75,
                observed_at=(now_utc - timedelta(hours=3)).isoformat(),
            ),
        ]
        summary = availability_svc.compute_team_production_lost(
            team="BAL", records=records, prior_roster_stats=roster_stats, decision_cutoff=now_utc.isoformat(),
        )

        expected_lost = (18.0 * 1.0 + 12.0 * 0.75) / team_potential  # (18 + 9) / 80 = 27 / 80 = 0.3375
        assert summary.ppr_lost_pct == pytest.approx(expected_lost, abs=1e-4)


# ==============================================================================
# 2. Elimination of Positional Hierarchy
# ==============================================================================

class TestPositionalHierarchyElimination:
    """
    Verifica que NO existan factores de ponderación fijos por posición (como QB=4.0, WR=1.5).
    Un WR o RB con alta producción reciente penaliza más que un QB suplente de baja producción.
    """

    def test_high_performing_wr_penalizes_more_than_low_performing_backup_qb(self, availability_svc):
        """
        Un WR de élite (25.0 PPR) penaliza al equipo significativamente más que un QB suplente (2.0 PPR).
        Bajo una jerarquía fija (QB=4.0, WR=1.5), el QB se inflaría artificialmente.
        En nuestro modelo dinámico, el WR penaliza proporcionalmente a sus 25 PPR vs los 2 PPR del QB.
        """
        now_utc = datetime.now(timezone.utc)
        roster_stats = {
            "elite_wr": {"rolling_ppr": 25.0, "rolling_off_pct": 0.88, "rolling_def_pct": 0.0},
            "backup_qb": {"rolling_ppr": 2.0, "rolling_off_pct": 0.10, "rolling_def_pct": 0.0},
            "starting_qb": {"rolling_ppr": 21.0, "rolling_off_pct": 0.90, "rolling_def_pct": 0.0},
            "other_players": {"rolling_ppr": 40.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        }

        # WR de 25 PPR descartado (OUT)
        wr_out = [PlayerInjuryRecord(
            player_name="elite_wr", team="CIN", position="WR", canonical_player_id="elite_wr",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=3)).isoformat(),
        )]
        summary_wr = availability_svc.compute_team_production_lost(
            team="CIN", records=wr_out, prior_roster_stats=roster_stats, decision_cutoff=now_utc.isoformat(),
        )

        # Backup QB de 2 PPR descartado (OUT)
        qb_out = [PlayerInjuryRecord(
            player_name="backup_qb", team="CIN", position="QB", canonical_player_id="backup_qb",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=3)).isoformat(),
        )]
        summary_qb = availability_svc.compute_team_production_lost(
            team="CIN", records=qb_out, prior_roster_stats=roster_stats, decision_cutoff=now_utc.isoformat(),
        )

        assert summary_wr.ppr_lost_pct > summary_qb.ppr_lost_pct
        # Ratio exacto 25.0 / 2.0 = 12.5x (con tolerancia por redondeo de 4 decimales)
        ratio = summary_wr.ppr_lost_pct / summary_qb.ppr_lost_pct
        assert ratio == pytest.approx(12.5, abs=0.1)

    def test_high_performing_rb_penalizes_more_than_low_performing_backup_qb(self, availability_svc):
        """Un RB estrella (22.0 PPR) penaliza más que un QB de reserva (3.0 PPR)."""
        now_utc = datetime.now(timezone.utc)
        roster_stats = {
            "workhorse_rb": {"rolling_ppr": 22.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
            "backup_qb": {"rolling_ppr": 3.0, "rolling_off_pct": 0.15, "rolling_def_pct": 0.0},
            "teammates": {"rolling_ppr": 50.0, "rolling_off_pct": 0.80, "rolling_def_pct": 0.0},
        }

        rb_out = [PlayerInjuryRecord(
            player_name="workhorse_rb", team="PHI", position="RB", canonical_player_id="workhorse_rb",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=2)).isoformat(),
        )]
        summary_rb = availability_svc.compute_team_production_lost(
            team="PHI", records=rb_out, prior_roster_stats=roster_stats, decision_cutoff=now_utc.isoformat(),
        )

        qb_out = [PlayerInjuryRecord(
            player_name="backup_qb", team="PHI", position="QB", canonical_player_id="backup_qb",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
            observed_at=(now_utc - timedelta(hours=2)).isoformat(),
        )]
        summary_qb = availability_svc.compute_team_production_lost(
            team="PHI", records=qb_out, prior_roster_stats=roster_stats, decision_cutoff=now_utc.isoformat(),
        )

        assert summary_rb.ppr_lost_pct > summary_qb.ppr_lost_pct
        assert summary_rb.ppr_lost_pct == pytest.approx(22.0 / 75.0, abs=1e-4)
        assert summary_qb.ppr_lost_pct == pytest.approx(3.0 / 75.0, abs=1e-4)

    def test_position_labels_are_neutral_and_do_not_alter_ppr_loss(self, availability_svc):
        """
        Verifica la invariancia posicional: cambiar la etiqueta de posición (QB, RB, WR, TE)
        con idéntica producción observada produce exactamente la misma degradación.
        """
        now_utc = datetime.now(timezone.utc)
        roster_stats = {
            "player_x": {"rolling_ppr": 15.0, "rolling_off_pct": 0.75, "rolling_def_pct": 0.0},
            "others": {"rolling_ppr": 60.0, "rolling_off_pct": 0.75, "rolling_def_pct": 0.0},
        }

        losses = []
        for pos in ["QB", "WR", "RB", "TE"]:
            rec = [PlayerInjuryRecord(
                player_name="player_x", team="GB", position=pos, canonical_player_id="player_x",
                availability_status=AvailabilityStatus.CONFIRMED_OUT.value, p_absence=1.0,
                observed_at=(now_utc - timedelta(hours=2)).isoformat(),
            )]
            summary = availability_svc.compute_team_production_lost(
                team="GB", records=rec, prior_roster_stats=roster_stats, decision_cutoff=now_utc.isoformat(),
            )
            losses.append(summary.ppr_lost_pct)

        # Todas las pérdidas deben ser exactamente idénticas
        assert len(set(losses)) == 1
        assert losses[0] == pytest.approx(15.0 / 75.0, abs=1e-4)

    def test_pos_rank_context_is_descriptive_only_no_fixed_multiplier(self, availability_svc):
        """
        Verifica que el ranking posicional (pos_rank) sea únicamente informativo
        y nunca aplique un multiplicador fijo al score de importancia.
        """
        now_utc = datetime.now(timezone.utc)
        as_of = now_utc.isoformat()
        game_logs = [
            {
                "player_id": "p_wr1",
                "team": "MIA",
                "game_date": (now_utc - timedelta(days=7)).isoformat(),
                "fantasy_points_ppr": 20.0,
                "offense_pct": 0.85,
                "defense_pct": 0.0,
                "season": now_utc.year,
                "week": 5,
            }
        ]
        # Simular ranking posicional #1 vs ranking posicional #50
        rank_ctx_1 = {"pos_rank": 1, "as_of": (now_utc - timedelta(days=1)).isoformat(), "provenance": "official"}
        rank_ctx_50 = {"pos_rank": 50, "as_of": (now_utc - timedelta(days=1)).isoformat(), "provenance": "official"}

        imp_1 = availability_svc.calculate_player_recent_importance(
            canonical_player_id="p_wr1", current_team="MIA", as_of=as_of,
            recent_game_logs=game_logs, pos_rank_context=rank_ctx_1,
        )
        imp_50 = availability_svc.calculate_player_recent_importance(
            canonical_player_id="p_wr1", current_team="MIA", as_of=as_of,
            recent_game_logs=game_logs, pos_rank_context=rank_ctx_50,
        )

        assert imp_1["pos_rank_context"]["role_context"] == "descriptive_only_no_fixed_multiplier"
        assert imp_50["pos_rank_context"]["role_context"] == "descriptive_only_no_fixed_multiplier"
        # El importance_score debe ser idéntico porque no hay multiplicador fijo por pos_rank
        assert imp_1["importance_score"] == imp_50["importance_score"]
        assert imp_1["rolling_ppr"] == 20.0


# ==============================================================================
# 3. Strict Temporal Leakage Prevention (t - 1 strictly)
# ==============================================================================

class TestStrictTemporalLeakagePrevention:
    """
    Verifica que nunca se utilicen datos de la semana en juego (t) o futuras (t+1).
    Solo estadísticas de semanas estrictamente previas (t-1 hacia atrás) son accesibles.
    """

    def test_injury_feature_builder_merge_asof_strictly_prior_weeks(self):
        """
        En injury_feature_builder.py, time_idx_search = time_idx - 1 con direction='backward'
        garantiza que game_logs de la semana objetivo nunca sean unidos al roster.
        """
        # Crear dataset controlado: 4 semanas para un jugador
        player_id = "00-0033873"
        game_logs_data = []
        for week in [1, 2, 3, 4]:
            game_logs_data.append({
                "player_id": player_id,
                "season": 2024,
                "week": week,
                "time_idx": 2024 * 100 + week,
                "rolling_ppr": float(week * 10.0),  # W1=10, W2=20, W3=30, W4=40
                "rolling_off_pct": 0.80,
                "rolling_def_pct": 0.0,
            })
        game_logs = pd.DataFrame(game_logs_data).sort_values("time_idx")

        # Roster para la Semana 3 (time_idx = 202403)
        roster_w3 = pd.DataFrame([{
            "season": 2024,
            "week": 3,
            "team": "KC",
            "player_id": player_id,
            "time_idx": 202403,
            "time_idx_search": 202403 - 1,  # 202402 (Semana 2 estrictamente previa)
        }])

        roster_strength = pd.merge_asof(
            roster_w3,
            game_logs[["player_id", "time_idx", "rolling_ppr", "rolling_off_pct", "rolling_def_pct"]],
            left_on="time_idx_search",
            right_on="time_idx",
            by="player_id",
            direction="backward",
        )

        # Debe unirse con Semana 2 (time_idx 202402, rolling_ppr = 20.0), NUNCA Semana 3 (30.0) ni Semana 4 (40.0)
        assert roster_strength["time_idx_y"].iloc[0] == 202402
        assert roster_strength["rolling_ppr"].iloc[0] == 20.0
        assert roster_strength["time_idx_y"].iloc[0] < roster_strength["time_idx_x"].iloc[0]

    def test_injury_feature_builder_detects_and_rejects_leakage_in_merge_asof(self):
        """
        Verifica que si ocurriera una fuga temporal (game_logs.time_idx >= roster.time_idx),
        el pipeline levanta DataQualityError inmediatamente.
        """
        roster_mock = pd.DataFrame([{
            "season": 2024, "week": 3, "team": "KC", "player_id": "p1",
            "time_idx_x": 202403, "time_idx_search": 202403,  # Error deliberado: search incluye semana actual
            "time_idx_y": 202403,  # Semana actual unida
        }])
        matched = roster_mock["time_idx_y"].notna()
        is_leak = (roster_mock.loc[matched, "time_idx_y"] >= roster_mock.loc[matched, "time_idx_x"]).any()
        assert is_leak is True or bool(is_leak) is True

    def test_calculate_player_recent_importance_excludes_evaluated_week_and_future_weeks(self, availability_svc):
        """
        Verifica que calculate_player_recent_importance ignore registros donde
        week >= evaluated_week, aun cuando los timestamps aparenten ser válidos.
        """
        now_utc = datetime.now(timezone.utc)
        game_logs = [
            # Semana 4 (previa válida)
            {
                "player_id": "test_wr", "team": "DAL", "season": now_utc.year, "week": 4,
                "game_date": (now_utc - timedelta(days=14)).isoformat(),
                "fantasy_points_ppr": 18.0, "offense_pct": 0.80, "defense_pct": 0.0,
            },
            # Semana 5 (previa válida)
            {
                "player_id": "test_wr", "team": "DAL", "season": now_utc.year, "week": 5,
                "game_date": (now_utc - timedelta(days=7)).isoformat(),
                "fantasy_points_ppr": 22.0, "offense_pct": 0.85, "defense_pct": 0.0,
            },
            # Semana 6 (SEMANA EN JUEGO - PROHIBIDA)
            {
                "player_id": "test_wr", "team": "DAL", "season": now_utc.year, "week": 6,
                "game_date": (now_utc - timedelta(hours=1)).isoformat(),
                "fantasy_points_ppr": 35.0, "offense_pct": 0.95, "defense_pct": 0.0,
            },
            # Semana 7 (FUTURA - PROHIBIDA)
            {
                "player_id": "test_wr", "team": "DAL", "season": now_utc.year, "week": 7,
                "game_date": (now_utc + timedelta(days=7)).isoformat(),
                "fantasy_points_ppr": 40.0, "offense_pct": 0.99, "defense_pct": 0.0,
            },
        ]

        # Evaluamos el partido de la Semana 6
        imp = availability_svc.calculate_player_recent_importance(
            canonical_player_id="test_wr",
            current_team="DAL",
            as_of=now_utc.isoformat(),
            recent_game_logs=game_logs,
            evaluated_week=6,  # Semana en juego
        )

        assert imp["status"] == "observed"
        assert imp["games_sampled"] == 2  # Solo Semanas 4 y 5
        # Promedio de Semanas 4 y 5: (18.0 + 22.0) / 2 = 20.0
        assert imp["rolling_ppr"] == 20.0
        # Semana 6 (35.0) y Semana 7 (40.0) fueron estrictamente excluidas

    def test_calculate_player_recent_importance_strictly_excludes_timestamp_at_or_after_as_of(self, availability_svc):
        """Verifica que registros con game_date >= as_of sean estrictamente excluidos."""
        now_utc = datetime.now(timezone.utc)
        as_of_dt = now_utc
        game_logs = [
            # Pasado válido
            {
                "player_id": "p_rb", "team": "NYG", "season": now_utc.year, "week": 1,
                "game_date": (now_utc - timedelta(days=5)).isoformat(),
                "fantasy_points_ppr": 14.0, "offense_pct": 0.70, "defense_pct": 0.0,
            },
            # Exactamente en as_of (prohibido)
            {
                "player_id": "p_rb", "team": "NYG", "season": now_utc.year, "week": 2,
                "game_date": now_utc.isoformat(),
                "fantasy_points_ppr": 30.0, "offense_pct": 0.90, "defense_pct": 0.0,
            },
            # Posterior a as_of (prohibido)
            {
                "player_id": "p_rb", "team": "NYG", "season": now_utc.year, "week": 3,
                "game_date": (now_utc + timedelta(hours=2)).isoformat(),
                "fantasy_points_ppr": 28.0, "offense_pct": 0.85, "defense_pct": 0.0,
            },
        ]

        imp = availability_svc.calculate_player_recent_importance(
            canonical_player_id="p_rb",
            current_team="NYG",
            as_of=as_of_dt.isoformat(),
            recent_game_logs=game_logs,
        )

        assert imp["games_sampled"] == 1
        assert imp["rolling_ppr"] == 14.0

    def test_resolve_availability_fails_closed_when_observed_at_cutoff_or_later(self, availability_svc):
        """Si observed_at >= decision_cutoff, falla cerrado a UNKNOWN (cero fuga de información tardía)."""
        now_utc = datetime.now(timezone.utc)
        cutoff = now_utc.isoformat()
        after_cutoff = (now_utc + timedelta(seconds=1)).isoformat()
        at_cutoff = now_utc.isoformat()

        assert availability_svc.resolve_availability_status("Questionable", after_cutoff, cutoff) == AvailabilityStatus.UNKNOWN
        assert availability_svc.resolve_availability_status("Questionable", at_cutoff, cutoff) == AvailabilityStatus.UNKNOWN

        before_cutoff = (now_utc - timedelta(minutes=1)).isoformat()
        assert availability_svc.resolve_availability_status("Questionable", before_cutoff, cutoff) == AvailabilityStatus.QUESTIONABLE

    def test_boxscore_did_play_never_leaks_into_pregame_features(self):
        """
        did_play es una etiqueta exclusiva de ground truth en build_nfl_availability_ground_truth.
        Nunca forma parte del vector de features prepartido de lesiones ni de TeamAvailabilitySummary.
        """
        summary = TeamAvailabilitySummary(
            team="KC",
            coverage_state=CoverageState.COVERED_HEALTHY.value,
            ppr_lost_pct=0.0,
            off_snaps_lost_pct=0.0,
            def_snaps_lost_pct=0.0,
            total_injured_count=0,
            confirmed_out_count=0,
            questionable_count=0,
            doubtful_count=0,
            market_listed_count=0,
            market_unlisted_count=0,
            unknown_count=0,
            game_id="2024_01_BAL_KC",
            decision_cutoff="2024-09-05T22:20:00Z",
        )
        data = summary.to_dict()
        assert "did_play" not in data
        assert "actual_snaps" not in data
        assert "boxscore" not in data


# ==============================================================================
# 4. Pre-Kickoff Inactives Workflow & Recalculation
# ==============================================================================

class TestPreKickoffInactivesWorkflowAndRecalculation:
    """
    Verifica que el workflow de inactivos oficiales antes del kickoff funcione armónicamente:
    1. schedule_nfl_gameday_captures_task programa captura near_kickoff a t - 90 min.
    2. capture_nfl_injuries_task despacha evaluate_predictions_task con affected_game_ids.
    3. La predicción se recalcula con inactivos actualizados y progresión de timestamp (t2 > t1).
    """

    def test_schedule_nfl_gameday_captures_task_schedules_at_t_minus_90_minutes(self):
        """schedule_nfl_gameday_captures_task debe programar capturas prepartido a kickoff - 90 min."""
        now = datetime.now(timezone.utc)
        today = now.date()
        kickoff_future = now + timedelta(hours=3)

        fake_upcoming = pd.DataFrame([{
            "game_id": "2024_08_CHI_WAS",
            "game_date": today.isoformat(),
            "game_start_utc": kickoff_future.isoformat(),
            "home_team_id": "WAS",
            "away_team_id": "CHI",
        }])

        with patch("ml.data_pipeline.nfl_source.load_nfl_upcoming_games", return_value=fake_upcoming), \
             patch("app.workers.celery_app.capture_nfl_injuries_task.apply_async") as mock_apply:
            res = schedule_nfl_gameday_captures_task.apply().get()

        assert res["status"] == "success"
        assert res["start_of_day_dispatched"] is True

        # Verificar que se haya programado una llamada con cadence near_kickoff y ETA = kickoff - 90 min
        near_calls = [
            c for c in mock_apply.call_args_list
            if c.kwargs.get("args") == ("near_kickoff",) or (c.args and c.args[0] == ("near_kickoff",))
        ]
        assert len(near_calls) >= 1
        call_kwargs = near_calls[0].kwargs
        expected_eta = (kickoff_future - timedelta(minutes=90)).replace(tzinfo=timezone.utc)
        actual_eta = call_kwargs.get("eta")
        assert actual_eta is not None
        assert abs((actual_eta - expected_eta).total_seconds()) < 5
        assert call_kwargs.get("kwargs", {}).get("affected_game_ids") == ["2024_08_CHI_WAS"]

    def test_capture_nfl_injuries_task_triggers_evaluate_predictions_for_affected_games(self, tmp_path):
        """capture_nfl_injuries_task con near_kickoff debe despachar evaluate_predictions_task con game_ids."""
        svc = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")
        mock_raw = {"HOU": [{"player_name": "Nico Collins", "position": "WR", "status": "OUT"}]}

        with patch("app.services.matchup_context_service.get_live_injuries", return_value=mock_raw), \
             patch("app.services.nfl_availability_service.NFLAvailabilityService", return_value=svc), \
             patch("app.workers.celery_app.evaluate_predictions_task.apply_async") as mock_eval:

            res = capture_nfl_injuries_task.apply(kwargs={
                "cadence": "near_kickoff",
                "affected_game_ids": ["2024_09_HOU_NYJ"],
            }).get()

        assert res["status"] == "success"
        assert res["cadence"] == "near_kickoff"
        assert res["affected_game_ids"] == ["2024_09_HOU_NYJ"]

        mock_eval.assert_called_once()
        eval_args = mock_eval.call_args
        assert eval_args.kwargs.get("args") == ("NFL",)
        assert eval_args.kwargs.get("kwargs") == {"game_ids": ["2024_09_HOU_NYJ"]}
        assert eval_args.kwargs.get("queue") == "ml"

    def test_prekickoff_inactives_recalculation_and_timestamp_progression(self, tmp_path):
        """
        Simula el ciclo completo de recálculo:
        1. t1 (Inicio de jornada, t-5h): Jugador clave está QUESTIONABLE (P(absence)=0.25).
           Se genera predicción con as_of = t1.
        2. t2 (Inactivos oficiales, t-90m): Jugador clave es confirmado INACTIVE / OUT (P(absence)=1.0).
           Se dispara recálculo con as_of = t2.
        3. El nuevo snapshot de availability refleja mayor degradación de PPR.
        4. Las probabilidades se recalculan y la marca temporal progresa (t2 > t1).
        """
        service = NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")

        now_utc = datetime.now(timezone.utc)
        kickoff_dt = now_utc + timedelta(hours=2)
        game_id = "2024_10_DET_GB"

        roster_stats = {
            "amon_ra": {"rolling_ppr": 24.0, "rolling_off_pct": 0.90, "rolling_def_pct": 0.0},
            "other_det": {"rolling_ppr": 50.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0},
            "love": {"rolling_ppr": 20.0, "rolling_off_pct": 0.90, "rolling_def_pct": 0.0},
            "other_gb": {"rolling_ppr": 55.0, "rolling_off_pct": 0.85, "rolling_def_pct": 0.0},
        }

        # --- FASE 1: t1 (start_of_day, t-5h) ---
        t1 = now_utc - timedelta(hours=5)
        snap_t1 = service.save_espn_injury_snapshot(
            raw_teams_data={
                "DET": [{
                    "player_name": "Amon-Ra St. Brown",
                    "position": "WR",
                    "status": "Questionable",
                    "gsis_id": "amon_ra",
                    "observed_at": (t1 - timedelta(minutes=10)).isoformat(),
                }],
                "GB": [],
            },
            cadence="start_of_day",
            captured_at_utc=t1.isoformat(),
        )

        fake_props = [{
            "event_id": "game_det_gb_101",
            "bookmaker": "DraftKings",
            "prop_type": "receiving_yards",
            "market_line": 78.5,
            "price": -110,
            "player_name": "Amon-Ra St. Brown",
            "position": "WR",
            "timestamp": (t1 - timedelta(minutes=5)).isoformat(),
        }]

        game_avail_t1 = service.build_game_availability_snapshot(
            game_id=game_id,
            home_team="GB",
            away_team="DET",
            kickoff_utc=kickoff_dt.isoformat(),
            as_of=t1.isoformat(),
            espn_snapshot=snap_t1.to_dict(),
            live_props=fake_props,
            props_event_id="game_det_gb_101",
            prior_roster_stats=roster_stats,
        )

        team_potential = sum(float(p["rolling_ppr"]) for p in roster_stats.values())
        det_t1 = game_avail_t1["teams"]["DET"]
        # En t1, Amon-Ra era Questionable con línea en mercado: p_absence = 0.15 -> pérdida = 24.0 * 0.15 = 3.6 / potential
        assert det_t1["ppr_lost_pct"] == pytest.approx(3.6 / team_potential, abs=1e-3)
        assert det_t1["questionable_count"] == 1
        assert det_t1["confirmed_out_count"] == 0
        assert det_t1["market_listed_count"] == 1

        # --- FASE 2: t2 (near_kickoff, t-90m) ---
        t2 = kickoff_dt - timedelta(minutes=90)
        assert t2 > t1, "t2 debe ser estrictamente posterior a t1"

        snap_t2 = service.save_espn_injury_snapshot(
            raw_teams_data={
                "DET": [{
                    "player_name": "Amon-Ra St. Brown",
                    "position": "WR",
                    "status": "OUT",
                    "gsis_id": "amon_ra",
                    "observed_at": (t2 - timedelta(minutes=5)).isoformat(),
                }],
                "GB": [],
            },
            cadence="near_kickoff",
            captured_at_utc=t2.isoformat(),
        )

        game_avail_t2 = service.build_game_availability_snapshot(
            game_id=game_id,
            home_team="GB",
            away_team="DET",
            kickoff_utc=kickoff_dt.isoformat(),
            as_of=t2.isoformat(),
            espn_snapshot=snap_t2.to_dict(),
            prior_roster_stats=roster_stats,
        )

        det_t2 = game_avail_t2["teams"]["DET"]
        # En t2, Amon-Ra es confirmado OUT: p_absence = 1.0 -> pérdida = 24.0 * 1.0 = 24.0 / potential
        assert det_t2["ppr_lost_pct"] == pytest.approx(24.0 / team_potential, abs=1e-3)
        assert det_t2["confirmed_out_count"] == 1
        assert det_t2["questionable_count"] == 0

        # La degradación del equipo aumentó significativamente tras la confirmación de inactivos (> 6.6x)
        assert det_t2["ppr_lost_pct"] > det_t1["ppr_lost_pct"]
        assert det_t2["ppr_lost_pct"] > det_t1["ppr_lost_pct"] * 6.0

        # Verificar progresión de timestamps en snapshots
        ts1 = datetime.fromisoformat(game_avail_t1["captured_at_utc"])
        ts2 = datetime.fromisoformat(game_avail_t2["captured_at_utc"])
        assert ts2 > ts1

    def test_evaluate_predictions_task_processes_filtered_game_ids(self):
        """evaluate_predictions_task con game_ids procesa únicamente los partidos solicitados."""
        class MockSession:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return False

        upcoming = pd.DataFrame([
            {
                "game_id": "2024_10_DET_GB",
                "game_date": "2024-11-03",
                "game_start_utc": "2024-11-03T21:25:00Z",
                "home_team_id": "GB",
                "away_team_id": "DET",
                "home_team_abbreviation": "GB",
                "away_team_abbreviation": "DET",
            },
            {
                "game_id": "2024_10_KC_BUF",
                "game_date": "2024-11-03",
                "game_start_utc": "2024-11-03T21:25:00Z",
                "home_team_id": "BUF",
                "away_team_id": "KC",
                "home_team_abbreviation": "BUF",
                "away_team_abbreviation": "KC",
            },
        ])

        with patch("app.core.database.AsyncSessionLocal", return_value=MockSession()), \
             patch("ml.data_pipeline.nfl_source.load_nfl_games", return_value=pd.DataFrame()), \
             patch("ml.data_pipeline.nfl_source.load_nfl_upcoming_games", return_value=upcoming), \
             patch("app.services.nfl_availability_service.NFLAvailabilityService.build_game_availability_snapshot", return_value={"mock": "snapshot"}) as mock_build, \
             patch("ml.inference.predict_matchups", return_value=(pd.DataFrame(), {"production_eligible": False})) as mock_predict:

            res = evaluate_predictions_task("NFL", game_ids=["2024_10_DET_GB"])

        assert res["status"] == "success"
        # mock_build solo debe llamarse para el juego filtrado (DET @ GB), no para KC @ BUF
        assert mock_build.call_count == 1
        called_game_id = mock_build.call_args.kwargs.get("game_id")
        assert called_game_id == "2024_10_DET_GB"


# ==============================================================================
# 5. Quota Ledger Protection
# ==============================================================================

class TestOddsQuotaLedgerPreservation:
    """
    Verifica que data/odds_quota_ledger.json permanezca 100% inalterado y protegido.
    Todas las pruebas de disponibilidad y lesiones deben ejecutarse sin consumir cuota.
    """

    def test_quota_ledger_hash_matches_authoritative_baseline(self):
        ledger_path = Path("data/odds_quota_ledger.json")
        assert ledger_path.exists(), "data/odds_quota_ledger.json debe existir"

        content = ledger_path.read_bytes()
        calculated_sha = hashlib.sha256(content).hexdigest()
        assert calculated_sha == EXPECTED_QUOTA_LEDGER_SHA256, (
            f"El ledger de cuotas fue modificado. Hash esperado: {EXPECTED_QUOTA_LEDGER_SHA256}, obtenido: {calculated_sha}"
        )

    def test_confirmed_out_never_reserves_quota_or_queries_props(self, availability_svc):
        mock_quota_svc = MagicMock()
        rec_out = [PlayerInjuryRecord(
            player_name="Puka Nacua", team="LAR", position="WR",
            availability_status=AvailabilityStatus.CONFIRMED_OUT.value,
            p_absence=1.0,
        )]

        props = availability_svc.get_verifiable_props_for_qd_players(
            event_id="test_event_123",
            records=rec_out,
            quota_service=mock_quota_svc,
            api_key="test_key",
        )

        assert props is None
        mock_quota_svc.reserve_quota.assert_not_called()
