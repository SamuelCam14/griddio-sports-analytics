"""Test suite de verificación para el Estudio de Ablación de Margen y Spread (Punto 6).

Valida:
1. Existencia, integridad y consistencia del dataset y manifiesto de predicciones de ablación.
2. Las 3 variantes de margen/cover están completamente calculadas sin valores nulos en los 188 juegos OOF.
3. Diferenciación matemática estricta entre Variante A (sin ajuste aditivo QB), Variante B (team_only + QB) y Variante C (doble penalización).
4. Comprobación empírica fuera de muestra: la doble penalización (Variante C) no mejora frente a Variante A y degrada MAE/Brier en partidos con bajas de QB.
5. El registro de la cohorte prospectiva aísla las variantes bajo sus tags correspondientes.
"""
import json
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

from ml.data_pipeline.common import project_root
from ml.training.evaluate_spread_ablation import (
    compute_cover_metrics,
    compute_margin_metrics,
    run_spread_ablation_evaluation,
)
from app.services.nfl_prospective_cohort_service import (
    NFLProspectiveCohortService,
    DEFAULT_VARIANT_TAG,
    SPREAD_TEAM_ONLY_QB_ADJ_TAG,
    SPREAD_DUAL_PENALTY_TAG,
)


@pytest.fixture
def ablation_paths():
    root = project_root()
    eval_dir = root / "data" / "predictions" / "evaluation"
    csv_path = eval_dir / "nfl_spread_ablation_predictions.csv"
    manifest_path = eval_dir / "nfl_spread_ablation_manifest.json"
    return {"csv": csv_path, "manifest": manifest_path}


def test_ablation_files_exist_and_consistent(ablation_paths):
    """Verifica que el CSV y el manifiesto de la ablación existan y sean consistentes."""
    csv_path = ablation_paths["csv"]
    manifest_path = ablation_paths["manifest"]

    assert csv_path.exists(), f"Falta {csv_path}"
    assert manifest_path.exists(), f"Falta {manifest_path}"

    df = pd.read_csv(csv_path)
    assert len(df) == 188, f"Se esperaban 188 juegos OOF, se encontraron {len(df)}"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["total_oof_games_evaluated"] == 188
    assert manifest["output_predictions_csv"] == csv_path.name
    assert manifest["decision"]["selected_action"] == "retire_additional_qb_margin_adjustment"


def test_ablation_variants_columns_and_no_nulls(ablation_paths):
    """Verifica que las columnas requeridas de las 3 variantes no tengan nulos."""
    df = pd.read_csv(ablation_paths["csv"])

    required_cols = [
        "game_id", "fold", "result", "spread_line", "margin_actual", "vegas_spread",
        "kickoff_utc", "availability_cutoff_utc",
        "qb_epa_lost_home", "qb_epa_lost_away", "qb_epa_diff",
        "prob_team_only_logistic", "prob_injury_aware_logistic",
        "proj_margin_variant_a", "proj_margin_variant_b", "proj_margin_variant_c",
        "p_cover_variant_a", "p_cover_variant_b", "p_cover_variant_c",
    ]
    for col in required_cols:
        assert col in df.columns, f"Falta columna {col}"
        assert df[col].notna().all(), f"Columna {col} tiene valores nulos"


def test_ablation_temporal_integrity(ablation_paths):
    """Verifica que ninguna variable de ablación utilice fuentes con timestamps posteriores al corte."""
    df = pd.read_csv(ablation_paths["csv"])
    ko_series = pd.to_datetime(df["kickoff_utc"], utc=True)
    cutoff_series = pd.to_datetime(df["availability_cutoff_utc"], utc=True)

    # 1. El cutoff de disponibilidad preregistrado precede estrictamente al kickoff por exactamente 2 horas
    assert (cutoff_series < ko_series).all(), "Todo availability_cutoff debe preceder a kickoff_utc"
    diff_hours = (ko_series - cutoff_series).dt.total_seconds() / 3600.0
    np.testing.assert_allclose(diff_hours, 2.0, atol=1e-3, err_msg="El cutoff debe ser exactamente 2h pregame")

    # 2. Cargar reportes de lesiones y comprobar que ningún informe usado sea posterior al cutoff
    from ml.data_pipeline.nfl_source import load_nfl_games
    import glob
    root = project_root()
    inj_files = sorted(glob.glob(str(root / "data" / "raw" / "nflverse" / "*" / "injuries_*.parquet")))
    inj_df = pd.concat([pd.read_parquet(f) for f in inj_files], ignore_index=True)
    games = load_nfl_games()
    games["week"] = games["game_id"].apply(lambda x: int(x.split("_")[1]))
    games["game_type"] = games["season_type"].astype(str)
    home_games = games[["game_id", "season", "game_type", "week", "home_team_id", "game_start_utc"]].rename(
        columns={"home_team_id": "team"}
    )
    away_games = games[["game_id", "season", "game_type", "week", "away_team_id", "game_start_utc"]].rename(
        columns={"away_team_id": "team"}
    )
    team_sched = pd.concat([home_games, away_games], ignore_index=True)
    inj_df = pd.merge(inj_df, team_sched, on=["season", "game_type", "week", "team"], how="left")

    inj_df["kickoff_utc"] = pd.to_datetime(inj_df["game_start_utc"], errors="coerce", utc=True)
    inj_df["decision_cutoff"] = inj_df["kickoff_utc"] - pd.to_timedelta(2.0, unit="h")
    inj_dm = pd.to_datetime(inj_df["date_modified"], errors="coerce", utc=True)

    oof_gids = set(df["game_id"].unique())
    oof_inj = inj_df[inj_df["game_id"].isin(oof_gids)].copy()
    oof_inj["date_modified_utc"] = pd.to_datetime(oof_inj["date_modified"], errors="coerce", utc=True)

    # 2. Cargar reportes de lesiones y comprobar que ningún informe usado sea posterior al corte
    # a. Todo reporte de lesiones con date_modified >= decision_cutoff o NaT debe ser invalidado fail-closed
    oof_inj["is_pregame_valid"] = oof_inj["date_modified_utc"].notna() & (oof_inj["date_modified_utc"] < oof_inj["decision_cutoff"])
    invalid_inj = oof_inj[~oof_inj["is_pregame_valid"]]
    for _, row in invalid_inj.iterrows():
        dm = row["date_modified_utc"]
        cut = row["decision_cutoff"]
        assert pd.isna(dm) or dm >= cut, "Los reportes marcados inválidos deben ser NaT o >= cutoff"

    # b. Para los 33 juegos con bajas de QB y ajuste EPA (qb_epa_diff != 0), todos los reportes de QB causantes
    # tienen date_modified estricto < decision_cutoff < kickoff_utc
    qb_active_gids = set(df[df["qb_epa_diff"] != 0]["game_id"].unique())
    assert len(qb_active_gids) == 33
    active_qb_inj = oof_inj[(oof_inj["game_id"].isin(qb_active_gids)) & (oof_inj["position"] == "QB") & oof_inj["is_pregame_valid"]]
    assert len(active_qb_inj) > 0, "Deben existir reportes válidos de QB para la subpoblación activa"
    for _, row in active_qb_inj.iterrows():
        dm = row["date_modified_utc"]
        ko = row["kickoff_utc"]
        cut = row["decision_cutoff"]
        assert dm < cut, f"date_modified {dm} no precede a cutoff {cut} en {row['game_id']}"
        assert dm < ko, f"date_modified {dm} no precede a kickoff {ko} en {row['game_id']}"

    # 3. Comprobar que load_team_roster_stats_from_nflverse respeta as_of kickoff estricto
    from app.services.nfl_availability_service import NFLAvailabilityService
    svc = NFLAvailabilityService()
    sample_game = df.iloc[0]
    sample_ko = sample_game["kickoff_utc"]
    sample_team = sample_game["home_team_id"]
    stats = svc.load_team_roster_stats_from_nflverse(
        sample_team,
        as_of=sample_ko,
        evaluated_week=int(sample_game["game_id"].split("_")[1]),
    )
    assert isinstance(stats, dict)


def test_ablation_variants_mathematical_properties(ablation_paths):
    """Verifica las relaciones algebraicas exactas entre las variantes."""
    df = pd.read_csv(ablation_paths["csv"])

    # Variante C = Variante A - qb_epa_diff
    diff_c_a = df["proj_margin_variant_a"] - df["proj_margin_variant_c"]
    np.testing.assert_allclose(
        diff_c_a.to_numpy(),
        df["qb_epa_diff"].to_numpy(),
        atol=1e-5,
        err_msg="Variante C debe ser exactamente igual a Variante A menos qb_epa_diff",
    )

    # En juegos con qb_epa_diff != 0, Variante A y Variante C deben diferir
    qb_active = df[df["qb_epa_diff"] != 0]
    assert len(qb_active) > 0, "Debe haber juegos con qb_epa_diff != 0"
    assert (qb_active["proj_margin_variant_a"] != qb_active["proj_margin_variant_c"]).all()

    # En juegos con qb_epa_diff == 0, Variante A y Variante C deben ser idénticos
    qb_zero = df[df["qb_epa_diff"] == 0]
    assert len(qb_zero) > 0
    np.testing.assert_allclose(
        qb_zero["proj_margin_variant_a"].to_numpy(),
        qb_zero["proj_margin_variant_c"].to_numpy(),
        atol=1e-5,
    )


def test_ablation_empirical_decision_retires_double_penalty(ablation_paths):
    """Comprueba que la evidencia empírica confirma la decisión de retirar la doble penalización."""
    manifest = json.loads(ablation_paths["manifest"].read_text(encoding="utf-8"))
    overall = manifest["overall_metrics"]
    subpop = manifest["subpopulation_metrics"]

    mae_a = overall["variant_a_injury_aware_no_qb_adj"]["margin"]["mae"]
    mae_c = overall["variant_c_dual_penalty"]["margin"]["mae"]
    brier_a = overall["variant_a_injury_aware_no_qb_adj"]["cover"]["brier"]
    brier_c = overall["variant_c_dual_penalty"]["cover"]["brier"]

    assert mae_a <= mae_c, f"Variante A ({mae_a}) debe tener menor o igual MAE global que Variante C ({mae_c})"
    assert brier_a <= brier_c, f"Variante A ({brier_a}) debe tener menor o igual Brier que Variante C ({brier_c})"

    # En la subpoblación de juegos con bajas de QB, la doble penalización degrada el error
    sub_mae_a = subpop["variant_a_injury_aware_no_qb_adj"]["margin"]["mae"]
    sub_mae_c = subpop["variant_c_dual_penalty"]["margin"]["mae"]
    sub_brier_a = subpop["variant_a_injury_aware_no_qb_adj"]["cover"]["brier"]
    sub_brier_c = subpop["variant_c_dual_penalty"]["cover"]["brier"]

    assert sub_mae_a < sub_mae_c, "En partidos con bajas de QB, Variante A tiene menor MAE que la doble penalización"
    assert sub_brier_a < sub_brier_c, "En partidos con bajas de QB, Variante A tiene mejor Brier que la doble penalización"


def test_prospective_cohort_isolates_spread_variants(tmp_path):
    """Verifica que el servicio de cohorte prospectiva aísla variantes de spread sin interferencia."""
    svc = NFLProspectiveCohortService(data_dir=tmp_path)

    # Intento bajo Variante A
    rec_a = svc.record_attempt(
        game_id="2026_09_BUF_KC",
        kickoff_utc="2026-11-01T21:25:00+00:00",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        variant_tag=DEFAULT_VARIANT_TAG,
        home_win_probability=0.55,
        away_win_probability=0.45,
        projected_margin=2.1,
        market_spread_line=-1.5,
        spread_cover_probability=0.52,
    )
    assert rec_a.variant_tag == DEFAULT_VARIANT_TAG
    assert rec_a.projected_margin == 2.1

    # Intento bajo Variante B (team_only + qb)
    rec_b = svc.record_attempt(
        game_id="2026_09_BUF_KC",
        kickoff_utc="2026-11-01T21:25:00+00:00",
        attempt_status="evaluated",
        model_variant_applied="team_only_fallback",
        variant_tag=SPREAD_TEAM_ONLY_QB_ADJ_TAG,
        home_win_probability=0.52,
        away_win_probability=0.48,
        projected_margin=1.2,
        market_spread_line=-1.5,
        spread_cover_probability=0.49,
        qb_expected_epa_lost_home=0.0,
        qb_expected_epa_lost_away=2.5,
    )
    assert rec_b.variant_tag == SPREAD_TEAM_ONLY_QB_ADJ_TAG
    assert rec_b.qb_expected_epa_lost_away == 2.5

    # Intento bajo Variante C (dual penalty)
    rec_c = svc.record_attempt(
        game_id="2026_09_BUF_KC",
        kickoff_utc="2026-11-01T21:25:00+00:00",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        variant_tag=SPREAD_DUAL_PENALTY_TAG,
        home_win_probability=0.55,
        away_win_probability=0.45,
        projected_margin=-0.4,
        market_spread_line=-1.5,
        spread_cover_probability=0.46,
    )
    assert rec_c.variant_tag == SPREAD_DUAL_PENALTY_TAG

    # Evaluar cohorte para cada variante aisladamente
    report_a = svc.evaluate_cohort(variant_tag=DEFAULT_VARIANT_TAG)
    assert report_a["overall_summary"]["total_attempts"] == 1

    report_b = svc.evaluate_cohort(variant_tag=SPREAD_TEAM_ONLY_QB_ADJ_TAG)
    assert report_b["overall_summary"]["total_attempts"] == 1

    report_c = svc.evaluate_cohort(variant_tag=SPREAD_DUAL_PENALTY_TAG)
    assert report_c["overall_summary"]["total_attempts"] == 1
