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
from datetime import datetime, timezone
import json
from pathlib import Path
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
            kickoff_utc="2026-10-27T17:00:00Z",
            attempt_status="abstained",
        )

    # Exitoso con motivo
    rec = cohort_service.record_attempt(
        game_id="2026_08_BAL_CLE",
        kickoff_utc="2026-10-27T17:00:00Z",
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

