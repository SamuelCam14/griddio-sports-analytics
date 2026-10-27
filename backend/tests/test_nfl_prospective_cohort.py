"""
Tests para NFLProspectiveCohortService — Protocolo Punto 2 del Plan de Corrección.

Verifica:
1. Rechazo estricto de partidos anteriores o iguales al congelamiento (no prospectivos).
2. Registro inmutable de intentos (evaluados y abstenciones) con hashes y timestamps.
3. Validación de contratos (probabilidades suman 1.0, motivos de abstención obligatorios).
4. Liquidación (settlement) idempotente y precisa.
5. Segmentación analítica en los 4 subconjuntos requeridos:
   - injury_aware_verified
   - team_only_fallback
   - market_benchmarkable (vs fair market sin vig)
   - abstentions y tasa de cobertura
6. Aislamiento por variant_tag (para experimentos futuros de VORP / spread).
7. Actualización del manifiesto con hash SHA-256 y conteos.
"""
from datetime import datetime, timezone, timedelta
import dataclasses
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from app.services.nfl_prospective_cohort_service import (
    CANDIDATE_FREEZING_TIMESTAMP,
    CANDIDATE_ARTIFACT_SHA256,
    DEFAULT_VARIANT_TAG,
    CohortAttemptRecord,
    NFLProspectiveCohortService,
)


@pytest.fixture
def cohort_service(tmp_path):
    return NFLProspectiveCohortService(data_dir=tmp_path)


def test_rejects_non_prospective_kickoff_before_or_at_freezing_date(cohort_service):
    """Partidos con kickoff <= fecha de congelamiento no son prospectivos y se rechazan."""
    # 2025-01-26 (fecha de fin del dataset histórico)
    with pytest.raises(ValueError, match="No es prospectivo"):
        cohort_service.record_attempt(
            game_id="2024_20_KC_BUF",
            kickoff_utc="2025-01-26T23:30:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.55,
            away_win_probability=0.45,
        )

    # 2026-10-25T22:27:48+00:00 (fecha exacta de registro de congelamiento)
    with pytest.raises(ValueError, match="No es prospectivo"):
        cohort_service.record_attempt(
            game_id="2026_07_SEA_DEN",
            kickoff_utc="2026-10-25T22:27:48+00:00",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.55,
            away_win_probability=0.45,
        )


def test_records_prospective_evaluated_injury_aware_attempt(cohort_service):
    """Registra intento evaluado prospectivo (kickoff > fecha de congelamiento)."""
    record = cohort_service.record_attempt(
        game_id="2026_08_MIN_LAR",
        kickoff_utc="2026-10-26T00:15:00Z",
        attempt_timestamp_utc="2026-10-25T23:30:00Z",
        as_of_cutoff_utc="2026-10-25T23:30:00Z",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        home_win_probability=0.62,
        away_win_probability=0.38,
        coverage_state_home="COVERED_WITH_INJURIES",
        coverage_state_away="COVERED_HEALTHY",
        model_agreement=3,
        confluence_tier="HIGH",
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -150,
            "away_ml": +130,
            "home_fair_prob": 0.582,
            "away_fair_prob": 0.418,
            "captured_at_utc": "2026-10-25T23:30:00Z",
            "is_stale": False,
        },
    )

    assert isinstance(record, CohortAttemptRecord)
    assert record.game_id == "2026_08_MIN_LAR"
    assert record.attempt_status == "evaluated"
    assert record.model_variant_applied == "injury_aware"
    assert record.artifact_sha256 == CANDIDATE_ARTIFACT_SHA256
    assert record.variant_tag == DEFAULT_VARIANT_TAG

    # Verificar persistencia en archivo
    records = cohort_service.load_all_records()
    assert len(records) == 1
    assert records[0].record_id == record.record_id

    # Verificar manifiesto
    manifest = json.loads(cohort_service.manifest_file.read_text(encoding="utf-8"))
    assert manifest["total_attempts"] == 1
    assert manifest["total_evaluated"] == 1
    assert manifest["total_abstained"] == 0


def test_records_abstention_with_reason(cohort_service):
    """Registra abstención exigiendo motivo específico y desglosa en el reporte."""
    # Falla si no se proporciona abstention_reason
    with pytest.raises(ValueError, match="abstention_reason"):
        cohort_service.record_attempt(
            game_id="2026_08_BAL_CLE",
            kickoff_utc="2026-11-01T20:00:00Z",
            attempt_status="abstained",
        )

    # Exitoso con motivo
    rec = cohort_service.record_attempt(
        game_id="2026_08_BAL_CLE",
        kickoff_utc="2026-11-01T20:00:00Z",
        attempt_status="abstained",
        abstention_reason="MISSING_INJURY_SNAPSHOT",
        coverage_state_home="UNKNOWN",
        coverage_state_away="COVERED_HEALTHY",
    )
    assert rec.attempt_status == "abstained"
    assert rec.model_variant_applied is None
    assert rec.abstention_reason == "MISSING_INJURY_SNAPSHOT"


def test_settlement_and_subgroup_evaluation(cohort_service):
    """Verifica liquidación y separación estricta de métricas por los 4 subconjuntos."""
    # 1. Partido Injury-Aware con Cuota (Victoria Local)
    cohort_service.record_attempt(
        game_id="game_1",
        kickoff_utc="2026-10-28T00:00:00Z",
        attempt_timestamp_utc="2026-10-27T22:30:00Z",
        as_of_cutoff_utc="2026-10-27T22:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        home_win_probability=0.65,
        away_win_probability=0.35,
        market_odds={
            "bookmaker": "draftkings",
            "home_ml": -150,
            "away_ml": 130,
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.40,
            "captured_at_utc": "2026-10-27T22:00:00Z",
            "is_stale": False,
        },
    )

    # 2. Partido Injury-Aware sin Cuota (Derrota Local)
    cohort_service.record_attempt(
        game_id="game_2",
        kickoff_utc="2026-10-28T03:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        home_win_probability=0.40,
        away_win_probability=0.60,
        market_odds=None,
    )

    # 3. Partido Fallback Team-Only (Victoria Local)
    cohort_service.record_attempt(
        game_id="game_3",
        kickoff_utc="2026-10-28T17:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="team_only_fallback",
        home_win_probability=0.58,
        away_win_probability=0.42,
    )

    # 4. Partido Abstenido
    cohort_service.record_attempt(
        game_id="game_4",
        kickoff_utc="2026-10-28T20:00:00Z",
        attempt_status="abstained",
        abstention_reason="INVALID_COVERAGE_STATE",
    )

    # Liquidar resultados
    assert cohort_service.record_settlement(game_id="game_1", home_score=24, away_score=17) is True  # Home win
    assert cohort_service.record_settlement(game_id="game_2", home_score=10, away_score=21) is True  # Away win
    assert cohort_service.record_settlement(game_id="game_3", home_score=31, away_score=28) is True  # Home win

    # Evaluar cohorte
    report = cohort_service.evaluate_cohort()

    # Overall summary
    assert report["overall_summary"]["total_attempts"] == 4
    assert report["overall_summary"]["total_evaluated"] == 3
    assert report["overall_summary"]["total_abstained"] == 1
    assert report["overall_summary"]["coverage_rate_pct"] == 75.0
    assert report["overall_summary"]["total_settled"] == 3

    # Segment A: injury_aware_verified (game_1 y game_2)
    seg_a = report["segment_a_injury_aware_verified"]
    assert seg_a["total_count"] == 2
    assert seg_a["settled_count"] == 2
    # game_1: true=1, prob=0.65 -> brier = (1-0.65)^2 = 0.1225
    # game_2: true=0, prob=0.40 -> brier = (0-0.40)^2 = 0.1600
    # Mean brier = (0.1225 + 0.1600) / 2 = 0.14125
    assert seg_a["brier_score"] == 0.1413
    assert seg_a["accuracy"] == 1.0  # Ambos pronósticos acertados (>0.5 y <0.5)

    # Segment B: team_only_fallback (game_3)
    seg_b = report["segment_b_team_only_fallback"]
    assert seg_b["total_count"] == 1
    assert seg_b["settled_count"] == 1
    # game_3: true=1, prob=0.58 -> brier = (1-0.58)^2 = 0.1764
    assert seg_b["brier_score"] == 0.1764

    # Segment C: market_benchmarkable (game_1)
    seg_c = report["segment_c_market_benchmarkable"]
    assert seg_c["total_count"] == 1
    assert seg_c["settled_count"] == 1
    # model: 0.1225 | market fair: (1 - 0.60)^2 = 0.1600
    # model beats market: 0.1600 - 0.1225 = +0.0375
    assert seg_c["model_beats_market_fair_brier"] is True
    assert seg_c["brier_advantage"] == 0.0375

    # Segment D: abstentions
    seg_d = report["segment_d_abstentions_and_coverage"]
    assert seg_d["total_abstained"] == 1
    assert seg_d["abstention_rate_pct"] == 25.0
    assert "INVALID_COVERAGE_STATE" in seg_d["reasons_breakdown"]


def test_variant_tag_isolation_prevents_cross_contamination(cohort_service):
    """Partidos registrados bajo tags distintos no se mezclan entre sí."""
    # Intento bajo baseline congelado
    cohort_service.record_attempt(
        game_id="game_v0",
        kickoff_utc="2026-10-30T00:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        variant_tag=DEFAULT_VARIANT_TAG,
        home_win_probability=0.60,
        away_win_probability=0.40,
    )

    # Intento bajo variante experimental futura de VORP
    cohort_service.record_attempt(
        game_id="game_v1",
        kickoff_utc="2026-10-30T00:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        variant_tag="experimental_vorp_qb_v1",
        home_win_probability=0.72,
        away_win_probability=0.28,
    )

    rep_baseline = cohort_service.evaluate_cohort(variant_tag=DEFAULT_VARIANT_TAG)
    assert rep_baseline["overall_summary"]["total_attempts"] == 1

    rep_vorp = cohort_service.evaluate_cohort(variant_tag="experimental_vorp_qb_v1")
    assert rep_vorp["overall_summary"]["total_attempts"] == 1


def test_append_only_immutability_and_event_sourcing(cohort_service):
    """
    Verifica que el registro es estrictamente append-only:
    - Las liquidaciones no sobreescriben ni truncan bytes existentes.
    - Los bytes previos permanecen como prefijo idéntico tras un settlement.
    """
    rec = cohort_service.record_attempt(
        game_id="game_immutability_1",
        kickoff_utc="2026-11-05T18:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        home_win_probability=0.55,
        away_win_probability=0.45,
    )
    assert cohort_service.registry_file.exists()
    bytes_before = cohort_service.registry_file.read_bytes()
    len_before = len(bytes_before)
    assert len_before > 0

    # Registrar settlement
    settled = cohort_service.record_settlement(
        game_id="game_immutability_1",
        home_score=27,
        away_score=20,
    )
    assert settled is True

    bytes_after = cohort_service.registry_file.read_bytes()
    len_after = len(bytes_after)
    # Debe haber crecido estrictamente
    assert len_after > len_before

    # Los primeros len_before bytes deben ser exactamente idénticos (no mutación / append-only)
    assert bytes_after[:len_before] == bytes_before

    # Reconstrucción de estado
    records = cohort_service.load_all_records()
    assert len(records) == 1
    assert records[0].is_settled is True
    assert records[0].home_win == 1
    assert records[0].home_score == 27
    assert records[0].away_score == 20

    # Comprobar log de eventos
    events = cohort_service.load_all_events()
    assert len(events) == 2
    assert events[0]["record_type"] == "attempt"
    assert events[1]["record_type"] == "settlement"


def test_duplicate_attempt_policy_enforcement(cohort_service):
    """Verifica que la política explícita de duplicados impida duplicados por defecto."""
    cohort_service.record_attempt(
        game_id="game_dup_1",
        kickoff_utc="2026-11-05T20:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        home_win_probability=0.60,
        away_win_probability=0.40,
    )

    # Intento idéntico bajo política por defecto ('reject') -> ValueError
    with pytest.raises(ValueError, match="Intento duplicado detectado"):
        cohort_service.record_attempt(
            game_id="game_dup_1",
            kickoff_utc="2026-11-05T20:00:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.60,
            away_win_probability=0.40,
            duplicate_policy="reject",
        )

    # Con duplicate_policy='ignore' retorna el registro existente sin duplicar en el archivo
    events_before = len(cohort_service.load_all_events())
    existing = cohort_service.record_attempt(
        game_id="game_dup_1",
        kickoff_utc="2026-11-05T20:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        home_win_probability=0.60,
        away_win_probability=0.40,
        duplicate_policy="ignore",
    )
    assert existing.game_id == "game_dup_1"
    assert len(cohort_service.load_all_events()) == events_before


def test_temporal_cutoff_validation_rules(cohort_service):
    """Verifica el rechazo estricto de intentos con timestamps posteriores o iguales al kickoff."""
    # 1. attempt_timestamp_utc >= kickoff_utc -> ValueError
    with pytest.raises(ValueError, match="attempt_timestamp_utc .* debe ser estrictamente anterior"):
        cohort_service.record_attempt(
            game_id="game_temp_1",
            kickoff_utc="2026-11-02T18:00:00Z",
            attempt_timestamp_utc="2026-11-02T18:00:00Z",  # kickoff exacto
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.50,
            away_win_probability=0.50,
        )

    with pytest.raises(ValueError, match="attempt_timestamp_utc .* debe ser estrictamente anterior"):
        cohort_service.record_attempt(
            game_id="game_temp_2",
            kickoff_utc="2026-11-02T18:00:00Z",
            attempt_timestamp_utc="2026-11-02T18:30:00Z",  # post-kickoff
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.50,
            away_win_probability=0.50,
        )

    # 2. as_of_cutoff_utc >= kickoff_utc -> ValueError
    with pytest.raises(ValueError, match="as_of_cutoff_utc .* debe ser estrictamente anterior"):
        cohort_service.record_attempt(
            game_id="game_temp_3",
            kickoff_utc="2026-11-02T18:00:00Z",
            attempt_timestamp_utc="2026-11-02T16:00:00Z",
            as_of_cutoff_utc="2026-11-02T18:05:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.50,
            away_win_probability=0.50,
        )

    # 3. as_of_cutoff_utc > attempt_timestamp_utc -> ValueError
    with pytest.raises(ValueError, match="as_of_cutoff_utc .* no puede ser posterior"):
        cohort_service.record_attempt(
            game_id="game_temp_4",
            kickoff_utc="2026-11-02T18:00:00Z",
            attempt_timestamp_utc="2026-11-02T15:00:00Z",
            as_of_cutoff_utc="2026-11-02T16:00:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.50,
            away_win_probability=0.50,
        )


def test_market_odds_validation_rules(cohort_service):
    """Verifica validación rigurosa de cuotas de mercado (pre-kickoff, sin vig, fair probs válidas)."""
    # 1. captured_at_utc >= kickoff_utc
    with pytest.raises(ValueError, match="captured_at_utc .* deben ser estrictamente anteriores"):
        cohort_service.record_attempt(
            game_id="game_market_1",
            kickoff_utc="2026-11-03T18:00:00Z",
            attempt_timestamp_utc="2026-11-03T17:00:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.55,
            away_win_probability=0.45,
            market_odds={
                "home_fair_prob": 0.52,
                "away_fair_prob": 0.48,
                "captured_at_utc": "2026-11-03T18:01:00Z",
                "is_stale": False,
            },
        )

    # 2. captured_at_utc > as_of_cutoff_utc
    with pytest.raises(ValueError, match="no pueden ser posteriores a as_of_cutoff_utc"):
        cohort_service.record_attempt(
            game_id="game_market_2",
            kickoff_utc="2026-11-03T18:00:00Z",
            attempt_timestamp_utc="2026-11-03T17:00:00Z",
            as_of_cutoff_utc="2026-11-03T16:00:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.55,
            away_win_probability=0.45,
            market_odds={
                "home_fair_prob": 0.52,
                "away_fair_prob": 0.48,
                "captured_at_utc": "2026-11-03T16:30:00Z",
                "is_stale": False,
            },
        )

    # 3. is_stale == True -> ValueError
    with pytest.raises(ValueError, match="marcado como obsoleto"):
        cohort_service.record_attempt(
            game_id="game_market_3",
            kickoff_utc="2026-11-03T18:00:00Z",
            attempt_timestamp_utc="2026-11-03T17:00:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.55,
            away_win_probability=0.45,
            market_odds={
                "home_fair_prob": 0.52,
                "away_fair_prob": 0.48,
                "captured_at_utc": "2026-11-03T16:00:00Z",
                "is_stale": True,
            },
        )

    # 4. fair probs no suman 1.0
    with pytest.raises(ValueError, match="deben sumar 1.0"):
        cohort_service.record_attempt(
            game_id="game_market_4",
            kickoff_utc="2026-11-03T18:00:00Z",
            attempt_timestamp_utc="2026-11-03T17:00:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.55,
            away_win_probability=0.45,
            market_odds={
                "home_fair_prob": 0.60,
                "away_fair_prob": 0.60,
                "captured_at_utc": "2026-11-03T16:00:00Z",
                "is_stale": False,
            },
        )

    # 5. captured_at_utc ausente -> ValueError (prohibida la síntesis temporal)
    with pytest.raises(ValueError, match="requiere un timestamp verificado 'captured_at_utc'"):
        cohort_service.record_attempt(
            game_id="game_market_5",
            kickoff_utc="2026-11-03T18:00:00Z",
            attempt_timestamp_utc="2026-11-03T17:00:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.55,
            away_win_probability=0.45,
            market_odds={
                "home_fair_prob": 0.52,
                "away_fair_prob": 0.48,
                "is_stale": False,
            },
        )


def test_celery_omits_market_odds_when_timestamp_missing(cohort_service):
    """
    Verifica que si un snapshot de odds en Celery carece de captured_at,
    market_odds se omite por completo (None) sin sintetizar as_of_iso,
    y el intento se registra como no benchmarkable limpiamente.
    """
    # Simula la lógica de Celery cuando odds_snap no tiene captured_at
    class MockOddsSnapNoTime:
        captured_at = None
        bookmaker = "draftkings"
        home_ml = -150
        away_ml = 130

    odds_snap = MockOddsSnapNoTime()
    as_of_iso = "2026-11-05T14:00:00Z"
    ko_utc_dt = datetime.fromisoformat("2026-11-05T18:00:00+00:00")
    best_home_ml = odds_snap.home_ml
    best_away_ml = odds_snap.away_ml

    # Lógica de construcción idéntica a Celery
    m_odds = None
    if best_home_ml and best_away_ml and odds_snap and getattr(odds_snap, "captured_at", None):
        cap_time = odds_snap.captured_at.isoformat()
        cap_dt = datetime.fromisoformat(cap_time.replace("Z", "+00:00"))
        as_of_dt = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
        if cap_dt < ko_utc_dt and cap_dt <= as_of_dt:
            m_odds = {"captured_at_utc": cap_time}

    # market_odds debe ser estrictamente None (omitido, sin timestamp sintético)
    assert m_odds is None

    # Registrar en cohorte con market_odds=None es válido
    rec = cohort_service.record_attempt(
        game_id="game_celery_no_odds_ts",
        kickoff_utc="2026-11-05T18:00:00Z",
        attempt_timestamp_utc=as_of_iso,
        as_of_cutoff_utc=as_of_iso,
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        home_win_probability=0.55,
        away_win_probability=0.45,
        market_odds=m_odds,
    )
    assert rec.market_odds is None

    # En el reporte, este partido no infla el subconjunto benchmarkable
    report = cohort_service.evaluate_cohort()
    seg_c = report["segment_c_market_benchmarkable"]
    assert seg_c["total_count"] == 0

def test_settlement_idempotency_prevents_duplicate_events(cohort_service):
    """Llamadas sucesivas a record_settlement con el mismo marcador no duplican eventos."""
    cohort_service.record_attempt(
        game_id="game_idem_1",
        kickoff_utc="2026-11-08T18:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        home_win_probability=0.60,
        away_win_probability=0.40,
    )
    # Primera liquidación
    assert cohort_service.record_settlement(game_id="game_idem_1", home_score=24, away_score=17) is True
    events_first = len(cohort_service.load_all_events())
    assert events_first == 2  # 1 attempt + 1 settlement
    file_bytes_first = cohort_service.registry_file.read_bytes()

    # Segunda liquidación con marcador idéntico (simula sync repetido de ESPN)
    assert cohort_service.record_settlement(game_id="game_idem_1", home_score=24, away_score=17) is True
    events_second = len(cohort_service.load_all_events())
    assert events_second == events_first
    assert cohort_service.registry_file.read_bytes() == file_bytes_first


def test_settlement_of_abstained_attempt_does_not_distort_evaluation(cohort_service):
    """Liquidación de un partido abstenido no rompe evaluate_cohort ni cuenta como predicción acertada/fallada."""
    cohort_service.record_attempt(
        game_id="game_abs_1",
        kickoff_utc="2026-11-08T18:00:00Z",
        attempt_status="abstained",
        abstention_reason="INCOMPLETE_SNAP_DATA",
    )
    # El partido se juega y finaliza
    assert cohort_service.record_settlement(game_id="game_abs_1", home_score=30, away_score=27) is True

    report = cohort_service.evaluate_cohort()
    assert report["overall_summary"]["total_attempts"] == 1
    assert report["overall_summary"]["total_evaluated"] == 0
    assert report["overall_summary"]["total_abstained"] == 1
    assert report["overall_summary"]["total_settled"] == 0  # No hay predicciones evaluadas liquidadas
    assert report["segment_a_injury_aware_verified"]["total_count"] == 0
    assert report["segment_d_abstentions_and_coverage"]["total_abstained"] == 1


def test_team_only_variant_normalization(cohort_service):
    """Soporta 'team_only' y lo normaliza de forma segura a 'team_only_fallback'."""
    rec = cohort_service.record_attempt(
        game_id="game_to_1",
        kickoff_utc="2026-11-09T01:00:00Z",
        attempt_status="evaluated",
        model_variant_applied="team_only",
        home_win_probability=0.52,
        away_win_probability=0.48,
    )
    assert rec.model_variant_applied == "team_only_fallback"

    report = cohort_service.evaluate_cohort()
    assert report["segment_b_team_only_fallback"]["total_count"] == 1


def test_concurrent_writes_and_file_locking(cohort_service):
    """Verifica que múltiples escrituras concurrentes usan el file lock y mantienen integridad."""
    import concurrent.futures

    def record_dummy_attempt(idx):
        return cohort_service.record_attempt(
            game_id=f"game_thread_{idx}",
            kickoff_utc="2026-11-10T18:00:00Z",
            attempt_timestamp_utc="2026-11-10T12:00:00Z",
            as_of_cutoff_utc="2026-11-10T12:00:00Z",
            attempt_status="evaluated",
            model_variant_applied="injury_aware",
            home_win_probability=0.55,
            away_win_probability=0.45,
            duplicate_policy="ignore",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(record_dummy_attempt, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 10
    records = cohort_service.load_all_records()
    assert len(records) == 10


def test_cohort_eligibility_rejects_non_prospective_or_past_kickoff(cohort_service):
    """Rechaza partidos anteriores o iguales a la fecha de congelamiento o posteriores al kickoff."""
    # 1. Kickoff en fecha de congelamiento o anterior
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc="2026-10-25T22:27:48+00:00",
        attempt_timestamp_utc="2026-10-25T21:00:00Z",
    )
    assert ok is False
    assert "anterior o igual al congelamiento" in reason

    # 2. Intento posterior o igual al kickoff
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc="2026-11-01T18:00:00Z",
        attempt_timestamp_utc="2026-11-01T18:05:00Z",
    )
    assert ok is False
    assert "posterior o igual al kickoff" in reason


def test_cohort_eligibility_decision_window(cohort_service):
    """Verifica que el límite de ventana pre-kickoff (default 2h) se aplique estrictamente."""
    ko = "2026-11-01T20:00:00Z"

    # Intento 5 horas antes de kickoff (> 2.0h) -> No elegible para cohorte oficial
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc="2026-11-01T15:00:00Z",
        decision_window_hours=2.0,
    )
    assert ok is False
    assert "Fuera de la ventana pre-kickoff oficial" in reason
    assert cohort_service.is_cohort_eligible(
        kickoff_utc=ko,
        attempt_timestamp_utc="2026-11-01T15:00:00Z",
        decision_window_hours=2.0,
    ) is False

    # Intento 1 hora antes de kickoff (<= 2.0h) -> Elegible si no hay cuotas o cuotas válidas
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc="2026-11-01T19:00:00Z",
        decision_window_hours=2.0,
    )
    assert ok is True
    assert "Elegible para cohorte prospectiva oficial" in reason
    assert cohort_service.is_cohort_eligible(
        kickoff_utc=ko,
        attempt_timestamp_utc="2026-11-01T19:00:00Z",
        decision_window_hours=2.0,
    ) is True


def test_cohort_eligibility_market_odds_validations(cohort_service):
    """Valida requisitos fail-closed de cuotas de mercado (captura, frescura, probabilidades justas)."""
    ko = "2026-11-01T20:00:00Z"
    attempt = "2026-11-01T19:00:00Z"

    # 1. Cuota marcada como stale (is_stale=True) -> Rechazada
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        market_odds={
            "captured_at_utc": "2026-11-01T18:30:00Z",
            "is_stale": True,
            "home_fair_prob": 0.55,
            "away_fair_prob": 0.45,
        },
    )
    assert ok is False
    assert "is_stale=True" in reason

    # 2. Cuota sin captured_at_utc verificado -> Rechazada
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        market_odds={
            "home_fair_prob": 0.55,
            "away_fair_prob": 0.45,
        },
    )
    assert ok is False
    assert "carece de captured_at_utc verificado" in reason

    # 3. Cuota con antigüedad mayor a freshness_limit_hours -> Rechazada
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        freshness_limit_hours=2.0,
        market_odds={
            "captured_at_utc": "2026-11-01T15:00:00Z",  # 4h de antigüedad
            "home_fair_prob": 0.55,
            "away_fair_prob": 0.45,
        },
    )
    assert ok is False
    assert "Cuota de mercado vencida" in reason

    # 4. Probabilidades justas que no suman 1.0 -> Rechazada
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        freshness_limit_hours=2.0,
        market_odds={
            "captured_at_utc": "2026-11-01T18:30:00Z",
            "home_fair_prob": 0.60,
            "away_fair_prob": 0.60,
        },
    )
    assert ok is False
    assert "no suman 1.0" in reason

    # 5. Probabilidades justas no numéricas o inválidas -> Rechazada
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        market_odds={
            "captured_at_utc": "2026-11-01T18:30:00Z",
            "home_fair_prob": "invalid",
            "away_fair_prob": 0.5,
        },
    )
    assert ok is False
    assert "no numéricas" in reason

    # 6. Momios moneyline inválidos (0, fuera de rango o corruptos) -> Rechazada
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        market_odds={
            "captured_at_utc": "2026-11-01T18:45:00Z",
            "is_stale": False,
            "home_fair_prob": 0.58,
            "away_fair_prob": 0.42,
            "home_ml": 0,
            "away_ml": 130,
        },
    )
    assert ok is False
    assert "Momios moneyline inválidos" in reason

    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        market_odds={
            "captured_at_utc": "2026-11-01T18:45:00Z",
            "is_stale": False,
            "home_fair_prob": 0.58,
            "away_fair_prob": 0.42,
            "home_ml": -50,
            "away_ml": 130,
        },
    )
    assert ok is False
    assert "Momios moneyline inválidos" in reason

    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        market_odds={
            "captured_at_utc": "2026-11-01T18:45:00Z",
            "is_stale": False,
            "home_fair_prob": 0.58,
            "away_fair_prob": 0.42,
            "home_ml": "corrupt",
            "away_ml": 130,
        },
    )
    assert ok is False
    assert "no numéricos" in reason

    # 7. Probabilidades justas ausentes (ambas None o una None) -> Rechazada fail-closed
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        market_odds={
            "captured_at_utc": "2026-11-01T18:45:00Z",
            "is_stale": False,
            "home_ml": -150,
            "away_ml": 130,
        },
    )
    assert ok is False
    assert "Faltan probabilidades justas" in reason

    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        market_odds={
            "captured_at_utc": "2026-11-01T18:45:00Z",
            "is_stale": False,
            "home_fair_prob": 0.55,
            "away_fair_prob": None,
            "home_ml": -150,
            "away_ml": 130,
        },
    )
    assert ok is False
    assert "Faltan probabilidades justas" in reason

    # 8. Cuota válida y fresca dentro de la ventana de 2h -> Elegible
    ok, reason = cohort_service.check_cohort_eligibility(
        kickoff_utc=ko,
        attempt_timestamp_utc=attempt,
        freshness_limit_hours=2.0,
        decision_window_hours=2.0,
        market_odds={
            "captured_at_utc": "2026-11-01T18:45:00Z",
            "is_stale": False,
            "home_fair_prob": 0.58,
            "away_fair_prob": 0.42,
            "home_ml": -150,
            "away_ml": 130,
        },
    )
    assert ok is True
    assert "Elegible para cohorte prospectiva oficial" in reason


def test_get_registered_attempt_exact_and_cross_match(cohort_service):
    """Verifica consulta de intentos por coincidencia exacta de ID y canonical_external_id sin falsos positivos de subcadena."""
    ko = "2026-11-01T20:00:00Z"
    cohort_service.record_attempt(
        game_id="10",
        kickoff_utc=ko,
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        home_win_probability=0.6,
        away_win_probability=0.4,
    )

    # Coincidencia exacta de game_id
    rec = cohort_service.get_registered_attempt(game_id="10")
    assert rec is not None
    assert rec.game_id == "10"

    # Coincidencia exacta con canonical_external_id
    rec_ext = cohort_service.get_registered_attempt(game_id="999", canonical_external_id="10")
    assert rec_ext is not None
    assert rec_ext.game_id == "10"

    # Subcadena "1" NO debe coincidir con "10" (falso positivo prevenido)
    rec_sub = cohort_service.get_registered_attempt(game_id="1")
    assert rec_sub is None


def test_correlate_attempt_with_prediction_scenarios(cohort_service):
    """Verifica todos los caminos de decisión fail-closed de correlate_attempt_with_prediction."""
    now = datetime(2026, 11, 1, 19, 0, 0, tzinfo=timezone.utc)
    pred_ts = datetime(2026, 11, 1, 18, 45, 0, tzinfo=timezone.utc)
    ko = "2026-11-01T20:00:00Z"

    valid_market_odds = {
        "bookmaker": "draftkings",
        "home_ml": -160,
        "away_ml": 140,
        "home_fair_prob": 0.60,
        "away_fair_prob": 0.40,
        "captured_at_utc": "2026-11-01T18:40:00+00:00",
        "is_stale": False,
    }

    valid_record = cohort_service.record_attempt(
        game_id="2026_09_KC_LV",
        kickoff_utc=ko,
        attempt_timestamp_utc=pred_ts.isoformat(),
        as_of_cutoff_utc=pred_ts.isoformat(),
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        variant_tag=DEFAULT_VARIANT_TAG,
        home_win_probability=0.68,
        away_win_probability=0.32,
        market_odds=valid_market_odds,
    )

    # 1. Caso positivo exacto (todos los campos idénticos y válidos)
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is True
    assert "confirmada" in detail.lower()

    # 2. Status no evaluado (abstención)
    rec_abst = cohort_service.record_attempt(
        game_id="2026_09_KC_LV_abs",
        kickoff_utc=ko,
        attempt_timestamp_utc=pred_ts.isoformat(),
        as_of_cutoff_utc=pred_ts.isoformat(),
        attempt_status="abstained",
        abstention_reason="qb_uncertainty",
        variant_tag=DEFAULT_VARIANT_TAG,
    )
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_abst,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "no está evaluado" in detail

    # 3. Variante discordante
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag="spread_dual_penalty",
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "variante registrada" in detail.lower() and "discordante" in detail.lower()

    # 4. Versión de modelo: discordante, ausente en served, ausente en record
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="legacy_v0",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "versión de modelo registrada" in detail.lower() and "discordante" in detail.lower()

    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version=None,
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "predicción servida carece de model_version" in detail.lower()

    rec_no_mv = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_mv, k, v)
    rec_no_mv.model_version = None
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_mv,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "intento registrado carece de model_version" in detail.lower()

    # 5. pred_ts nulo
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=None,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "timestamp de inferencia" in detail.lower()

    # 6. attempt_timestamp_utc: ausente, inválido, descorrelacionado (>120s)
    rec_no_att_ts = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_att_ts, k, v)
    rec_no_att_ts.attempt_timestamp_utc = None
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_att_ts,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "carece de attempt_timestamp_utc" in detail.lower()

    rec_bad_att_ts = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_bad_att_ts, k, v)
    rec_bad_att_ts.attempt_timestamp_utc = "not-a-timestamp"
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_bad_att_ts,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "timestamp de intento inválido" in detail.lower()

    old_pred_ts = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=old_pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "timestamp de registro" in detail.lower() and "descorrelacionado" in detail.lower()

    # 7. as_of_cutoff_utc: ausente, formato inválido, descorrelacionado (>120s)
    rec_no_cutoff = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_cutoff, k, v)
    rec_no_cutoff.as_of_cutoff_utc = None
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_cutoff,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "carece de as_of_cutoff_utc" in detail.lower()

    rec_bad_cutoff = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_bad_cutoff, k, v)
    rec_bad_cutoff.as_of_cutoff_utc = "garbage-cutoff"
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_bad_cutoff,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "cutoff de registro" in detail.lower() and "inválido" in detail.lower()

    rec_old_cutoff = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_old_cutoff, k, v)
    rec_old_cutoff.as_of_cutoff_utc = (pred_ts - timedelta(hours=1)).isoformat()
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_old_cutoff,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "cutoff de registro" in detail.lower() and "descorrelacionado" in detail.lower()

    # 8. Probabilidades de modelo: ausente en served, ausente en record, discordante
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=None,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "carece de probabilidades de victoria" in detail.lower()

    rec_no_prob = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_prob, k, v)
    rec_no_prob.home_win_probability = None
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_prob,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "carece de probabilidades de victoria" in detail.lower()

    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.55,
        away_win_prob=0.45,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "discordante con predicción" in detail.lower()

    # 9. market_odds: ausente en served, ausente en record, stale
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=None,
    )
    assert corr is False
    assert "predicción servida carece de snapshot de cuotas de mercado" in detail.lower()

    rec_no_odds = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_odds, k, v)
    rec_no_odds.market_odds = None
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_odds,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "intento registrado carece de snapshot de cuotas de mercado" in detail.lower()

    stale_odds = dict(valid_market_odds)
    stale_odds["is_stale"] = True
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=stale_odds,
    )
    assert corr is False
    assert "obsoleto" in detail.lower()

    rec_stale = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_stale, k, v)
    stale_rec_odds = dict(valid_market_odds)
    stale_rec_odds["is_stale"] = True
    rec_stale.market_odds = stale_rec_odds
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_stale,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "obsoleto" in detail.lower()

    # is_stale ausente en served y en registered
    no_stale_served = dict(valid_market_odds)
    no_stale_served.pop("is_stale")
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=no_stale_served,
    )
    assert corr is False
    assert "carece del campo is_stale" in detail.lower()

    rec_no_stale = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_stale, k, v)
    no_stale_rec = dict(valid_market_odds)
    no_stale_rec.pop("is_stale")
    rec_no_stale.market_odds = no_stale_rec
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_stale,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "carece del campo is_stale" in detail.lower()

    # is_stale con valor/tipo inválido en served y registered (e.g. "false", None, 0)
    for inv_val in ["false", None, 0]:
        inv_stale_served = dict(valid_market_odds)
        inv_stale_served["is_stale"] = inv_val
        corr, detail = cohort_service.correlate_attempt_with_prediction(
            valid_record,
            pred_ts=pred_ts,
            model_version="nfl_temporal_v1",
            expected_variant_tag=DEFAULT_VARIANT_TAG,
            home_win_prob=0.68,
            away_win_prob=0.32,
            market_odds=inv_stale_served,
        )
        assert corr is False
        assert "is_stale inválido" in detail.lower()

        rec_inv_stale = MagicMock(spec=CohortAttemptRecord)
        for k, v in valid_record.__dict__.items():
            setattr(rec_inv_stale, k, v)
        inv_stale_reg = dict(valid_market_odds)
        inv_stale_reg["is_stale"] = inv_val
        rec_inv_stale.market_odds = inv_stale_reg
        corr, detail = cohort_service.correlate_attempt_with_prediction(
            rec_inv_stale,
            pred_ts=pred_ts,
            model_version="nfl_temporal_v1",
            expected_variant_tag=DEFAULT_VARIANT_TAG,
            home_win_prob=0.68,
            away_win_prob=0.32,
            market_odds=valid_market_odds,
        )
        assert corr is False
        assert "is_stale inválido" in detail.lower()

    # 10. captured_at_utc: ausente en served, ausente en record, inválido, discordante
    no_cap_odds = dict(valid_market_odds)
    no_cap_odds.pop("captured_at_utc")
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=no_cap_odds,
    )
    assert corr is False
    assert "carece de captured_at_utc verificado" in detail.lower()

    rec_no_cap = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_cap, k, v)
    rec_no_cap.market_odds = dict(no_cap_odds)
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_cap,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "carece de captured_at_utc verificado" in detail.lower()

    bad_cap_odds = dict(valid_market_odds)
    bad_cap_odds["captured_at_utc"] = "not-a-date"
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=bad_cap_odds,
    )
    assert corr is False
    assert "inválido" in detail.lower()

    diff_cap_odds = dict(valid_market_odds)
    diff_cap_odds["captured_at_utc"] = "2026-11-01T17:00:00+00:00"
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=diff_cap_odds,
    )
    assert corr is False
    assert "timestamp de captura de mercado discordante" in detail.lower()

    # 11. fair probabilities: ausentes, fuera de rango, no suman 1.0, discordantes
    no_fair_odds = dict(valid_market_odds)
    no_fair_odds.pop("home_fair_prob")
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=no_fair_odds,
    )
    assert corr is False
    assert "probabilidades justas" in detail.lower()

    rec_no_fair = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_fair, k, v)
    rec_no_fair.market_odds = dict(no_fair_odds)
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_fair,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "probabilidades justas" in detail.lower()

    out_range_odds = dict(valid_market_odds)
    out_range_odds["home_fair_prob"] = 1.2
    out_range_odds["away_fair_prob"] = -0.2
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=out_range_odds,
    )
    assert corr is False
    assert "fuera de rango" in detail.lower()

    no_sum_odds = dict(valid_market_odds)
    no_sum_odds["home_fair_prob"] = 0.50
    no_sum_odds["away_fair_prob"] = 0.30
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=no_sum_odds,
    )
    assert corr is False
    assert "no suman 1.0" in detail.lower()

    disc_fair_odds = dict(valid_market_odds)
    disc_fair_odds["home_fair_prob"] = 0.52
    disc_fair_odds["away_fair_prob"] = 0.48
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=disc_fair_odds,
    )
    assert corr is False
    assert "probabilidad justa home de mercado discordante" in detail.lower()

    # 12. Bookmaker: ausente, discordante
    no_bm_odds = dict(valid_market_odds)
    no_bm_odds.pop("bookmaker")
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=no_bm_odds,
    )
    assert corr is False
    assert "carece de bookmaker" in detail.lower()

    rec_no_bm = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_bm, k, v)
    rec_no_bm.market_odds = dict(no_bm_odds)
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_bm,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "carece de bookmaker" in detail.lower()

    disc_bm_odds = dict(valid_market_odds)
    disc_bm_odds["bookmaker"] = "fanduel"
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=disc_bm_odds,
    )
    assert corr is False
    assert "bookmaker de mercado discordante" in detail.lower()

    # 13. Moneylines: ausentes, no numéricos, discordantes
    no_ml_odds = dict(valid_market_odds)
    no_ml_odds.pop("home_ml")
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=no_ml_odds,
    )
    assert corr is False
    assert "momios moneyline" in detail.lower()

    rec_no_ml = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_no_ml, k, v)
    rec_no_ml.market_odds = dict(no_ml_odds)
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_no_ml,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "momios moneyline" in detail.lower()

    disc_hml_odds = dict(valid_market_odds)
    disc_hml_odds["home_ml"] = -140
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=disc_hml_odds,
    )
    assert corr is False
    assert "moneyline home de mercado discordante" in detail.lower()

    disc_aml_odds = dict(valid_market_odds)
    disc_aml_odds["away_ml"] = 120
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=disc_aml_odds,
    )
    assert corr is False
    assert "moneyline away de mercado discordante" in detail.lower()

    # Moneylines fraccionarios (no enteros): en servido y en registrado (sin redondeo)
    for frac_val in [-160.4, 140.25]:
        frac_served_odds = dict(valid_market_odds)
        frac_served_odds["home_ml"] = frac_val
        corr, detail = cohort_service.correlate_attempt_with_prediction(
            valid_record,
            pred_ts=pred_ts,
            model_version="nfl_temporal_v1",
            expected_variant_tag=DEFAULT_VARIANT_TAG,
            home_win_prob=0.68,
            away_win_prob=0.32,
            market_odds=frac_served_odds,
        )
        assert corr is False
        assert "fraccionario no entero" in detail.lower()

        rec_frac = MagicMock(spec=CohortAttemptRecord)
        for k, v in valid_record.__dict__.items():
            setattr(rec_frac, k, v)
        frac_rec_odds = dict(valid_market_odds)
        frac_rec_odds["home_ml"] = frac_val
        rec_frac.market_odds = frac_rec_odds
        corr, detail = cohort_service.correlate_attempt_with_prediction(
            rec_frac,
            pred_ts=pred_ts,
            model_version="nfl_temporal_v1",
            expected_variant_tag=DEFAULT_VARIANT_TAG,
            home_win_prob=0.68,
            away_win_prob=0.32,
            market_odds=valid_market_odds,  # Servido es -160 estricto
        )
        assert corr is False
        assert "fraccionario no entero" in detail.lower()

    # Moneylines no finitos (inf, -inf, nan)
    for inf_val in [float("inf"), float("-inf"), float("nan")]:
        inf_served = dict(valid_market_odds)
        inf_served["home_ml"] = inf_val
        corr, detail = cohort_service.correlate_attempt_with_prediction(
            valid_record,
            pred_ts=pred_ts,
            model_version="nfl_temporal_v1",
            expected_variant_tag=DEFAULT_VARIANT_TAG,
            home_win_prob=0.68,
            away_win_prob=0.32,
            market_odds=inf_served,
        )
        assert corr is False
        assert "no es finito" in detail.lower()

        rec_inf = MagicMock(spec=CohortAttemptRecord)
        for k, v in valid_record.__dict__.items():
            setattr(rec_inf, k, v)
        inf_rec_odds = dict(valid_market_odds)
        inf_rec_odds["away_ml"] = inf_val
        rec_inf.market_odds = inf_rec_odds
        corr, detail = cohort_service.correlate_attempt_with_prediction(
            rec_inf,
            pred_ts=pred_ts,
            model_version="nfl_temporal_v1",
            expected_variant_tag=DEFAULT_VARIANT_TAG,
            home_win_prob=0.68,
            away_win_prob=0.32,
            market_odds=valid_market_odds,
        )
        assert corr is False
        assert "no es finito" in detail.lower()

    # Moneylines cero (0)
    zero_served = dict(valid_market_odds)
    zero_served["home_ml"] = 0
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        valid_record,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=zero_served,
    )
    assert corr is False
    assert "no puede ser cero" in detail.lower()

    rec_zero = MagicMock(spec=CohortAttemptRecord)
    for k, v in valid_record.__dict__.items():
        setattr(rec_zero, k, v)
    zero_rec_odds = dict(valid_market_odds)
    zero_rec_odds["away_ml"] = 0
    rec_zero.market_odds = zero_rec_odds
    corr, detail = cohort_service.correlate_attempt_with_prediction(
        rec_zero,
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        expected_variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.68,
        away_win_prob=0.32,
        market_odds=valid_market_odds,
    )
    assert corr is False
    assert "no puede ser cero" in detail.lower()

    # Moneylines en intervalo imposible (-100, 100)
    for bad_price in [50, -50, 99, -99]:
        bad_served = dict(valid_market_odds)
        bad_served["home_ml"] = bad_price
        corr, detail = cohort_service.correlate_attempt_with_prediction(
            valid_record,
            pred_ts=pred_ts,
            model_version="nfl_temporal_v1",
            expected_variant_tag=DEFAULT_VARIANT_TAG,
            home_win_prob=0.68,
            away_win_prob=0.32,
            market_odds=bad_served,
        )
        assert corr is False
        assert "intervalo inválido" in detail.lower()

    # Moneylines booleanos o no numéricos
    for bad_val in [True, False, "abc"]:
        bad_type_served = dict(valid_market_odds)
        bad_type_served["home_ml"] = bad_val
        corr, detail = cohort_service.correlate_attempt_with_prediction(
            valid_record,
            pred_ts=pred_ts,
            model_version="nfl_temporal_v1",
            expected_variant_tag=DEFAULT_VARIANT_TAG,
            home_win_prob=0.68,
            away_win_prob=0.32,
            market_odds=bad_type_served,
        )
        assert corr is False


def test_validate_moneyline_scenarios(cohort_service):
    """Prueba exhaustiva de _validate_moneyline para enteros, finitud, no cero y rango."""
    # Válidos
    assert cohort_service._validate_moneyline(-160, "test") == (-160, None)
    assert cohort_service._validate_moneyline(140, "test") == (140, None)
    assert cohort_service._validate_moneyline(-110, "test") == (-110, None)
    assert cohort_service._validate_moneyline(100, "test") == (100, None)
    assert cohort_service._validate_moneyline(-100, "test") == (-100, None)
    assert cohort_service._validate_moneyline(-160.0, "test") == (-160, None)
    assert cohort_service._validate_moneyline("-160", "test") == (-160, None)

    # Fraccionarios
    val, err = cohort_service._validate_moneyline(-160.4, "test")
    assert val is None and "fraccionario no entero" in err

    val, err = cohort_service._validate_moneyline(140.25, "test")
    assert val is None and "fraccionario no entero" in err

    # Infinitos y NaN
    val, err = cohort_service._validate_moneyline(float("inf"), "test")
    assert val is None and "no es finito" in err

    val, err = cohort_service._validate_moneyline(float("-inf"), "test")
    assert val is None and "no es finito" in err

    val, err = cohort_service._validate_moneyline(float("nan"), "test")
    assert val is None and "no es finito" in err

    # Cero
    val, err = cohort_service._validate_moneyline(0, "test")
    assert val is None and "no puede ser cero" in err

    # Rango (-100, 100)
    for bad in [50, -50, 99, -99]:
        val, err = cohort_service._validate_moneyline(bad, "test")
        assert val is None and "intervalo inválido" in err

    # Booleanos y no numéricos
    val, err = cohort_service._validate_moneyline(True, "test")
    assert val is None and "no puede ser booleano" in err

    val, err = cohort_service._validate_moneyline(False, "test")
    assert val is None and "no puede ser booleano" in err

    val, err = cohort_service._validate_moneyline("abc", "test")
    assert val is None and "no numéricos" in err

    val, err = cohort_service._validate_moneyline(None, "test")
    assert val is None and "carece de valor" in err


def test_find_correlated_attempt_multi_candidates(cohort_service):
    """find_correlated_attempt discrimina entre varios registros y selecciona el exacto."""
    ko = "2026-11-01T20:00:00Z"
    pred_ts = datetime(2026, 11, 1, 18, 45, 0, tzinfo=timezone.utc)
    market_odds = {
        "bookmaker": "draftkings",
        "home_ml": -175,
        "away_ml": 150,
        "home_fair_prob": 0.62,
        "away_fair_prob": 0.38,
        "captured_at_utc": "2026-11-01T18:40:00+00:00",
        "is_stale": False,
    }

    # Intento 1: Variante distinta
    cohort_service.record_attempt(
        game_id="2026_09_SF_LAR",
        kickoff_utc=ko,
        attempt_timestamp_utc=pred_ts.isoformat(),
        as_of_cutoff_utc=pred_ts.isoformat(),
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        variant_tag="spread_team_only_with_qb_adj",
        home_win_probability=0.60,
        away_win_probability=0.40,
        market_odds=market_odds,
    )

    # Intento 2: Variante oficial DEFAULT_VARIANT_TAG
    rec_target = cohort_service.record_attempt(
        game_id="2026_09_SF_LAR",
        kickoff_utc=ko,
        attempt_timestamp_utc=pred_ts.isoformat(),
        as_of_cutoff_utc=pred_ts.isoformat(),
        attempt_status="evaluated",
        model_variant_applied="injury_aware",
        variant_tag=DEFAULT_VARIANT_TAG,
        home_win_probability=0.72,
        away_win_probability=0.28,
        market_odds=market_odds,
        duplicate_policy="ignore",
    )

    res, reason = cohort_service.find_correlated_attempt(
        game_id="2026_09_SF_LAR",
        pred_ts=pred_ts,
        model_version="nfl_temporal_v1",
        variant_tag=DEFAULT_VARIANT_TAG,
        home_win_prob=0.72,
        away_win_prob=0.28,
        market_odds=market_odds,
    )
    assert res is not None
    assert res.variant_tag == DEFAULT_VARIANT_TAG
    assert res.home_win_probability == 0.72

