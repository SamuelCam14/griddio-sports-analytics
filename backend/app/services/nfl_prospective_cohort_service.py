"""
NFL Prospective Cohort Registry & Evaluation Service — Protocolo Punto 2.

Registra rigurosamente cada partido intentado (evaluaciones y abstenciones)
en la cohorte prospectiva a partir del congelamiento del candidato (octubre 2026).
Garantiza el aislamiento por variante (para no mezclar con experimentos de VORP/spread)
y segrega el análisis en cuatro dimensiones:
1. Predicciones injury_aware verificadas.
2. Fallbacks al modelo team_only.
3. Subconjunto benchmarkable contra mercado sin vig.
4. Desglose de abstenciones y tasa de cobertura.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score


# Cutoff estricto de congelamiento del candidato:
# Cualquier partido con kickoff anterior a esta marca pertenece a la historia ya inspeccionada.
CANDIDATE_FREEZING_TIMESTAMP = "2026-10-25T22:27:48+00:00"
CANDIDATE_MODEL_VERSION = "nfl_temporal_v1"
CANDIDATE_SELECTED_MODEL = "logistic"
CANDIDATE_ARTIFACT_SHA256 = "c672f6a3cd5de9a23fa76a8351005a557c0e106cc008d4c22f09c34986fa28c0"
CANDIDATE_DATASET_SHA256 = "94f7d8cc560237b76c2bc92d9932a279d7815560de0ae29882b499cfd7f2ba48"
DEFAULT_VARIANT_TAG = "baseline_logistic_c672f6a3"
SPREAD_TEAM_ONLY_QB_ADJ_TAG = "spread_team_only_with_qb_adj"
SPREAD_DUAL_PENALTY_TAG = "spread_dual_penalty"


@dataclass
class MarketOddsSnapshot:
    """Captura de cuotas de mercado pre-kickoff."""
    bookmaker: str
    home_ml: int
    away_ml: int
    home_fair_prob: float
    away_fair_prob: float
    captured_at_utc: str
    is_stale: bool = False


@dataclass
class CohortAttemptRecord:
    """Registro unívoco de un partido intentado en la cohorte prospectiva."""
    record_id: str
    game_id: str
    sport: str
    kickoff_utc: str
    attempt_timestamp_utc: str
    as_of_cutoff_utc: str
    attempt_status: str  # "evaluated" | "abstained"
    model_variant_applied: Optional[str]  # "injury_aware" | "team_only_fallback" | None
    model_version: str
    selected_model: str
    artifact_sha256: str
    dataset_sha256: str
    variant_tag: str  # Tag de variante para aislar cambios futuros de VORP / spread
    abstention_reason: Optional[str] = None
    coverage_state_home: Optional[str] = None
    coverage_state_away: Optional[str] = None
    features: Optional[Dict[str, float]] = None
    home_win_probability: Optional[float] = None
    away_win_probability: Optional[float] = None
    model_agreement: Optional[int] = None
    confluence_tier: Optional[str] = None
    market_odds: Optional[Dict[str, Any]] = None
    projected_margin: Optional[float] = None
    market_spread_line: Optional[float] = None
    spread_cover_probability: Optional[float] = None
    qb_expected_epa_lost_home: Optional[float] = None
    qb_expected_epa_lost_away: Optional[float] = None
    is_settled: bool = False
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    home_win: Optional[int] = None  # 1 (local), 0 (visitante)
    settled_at_utc: Optional[str] = None


class NFLProspectiveCohortService:
    """Gestiona el registro inmutable y la evaluación de la cohorte prospectiva."""

    def __init__(self, data_dir: Optional[Path] = None):
        from ml.data_pipeline.common import project_root
        self.data_dir = data_dir or project_root() / "data" / "prospective_cohort"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.data_dir / "nfl_cohort_registry.jsonl"
        self.manifest_file = self.data_dir / "cohort_manifest.json"

    def _parse_iso(self, ts: str) -> datetime:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    @contextmanager
    def _file_lock(self, timeout: float = 5.0):
        """Bloqueo concurrente entre procesos para escrituras append-only y manifiesto."""
        lock_file = self.data_dir / ".cohort_registry.lock"
        start_time = time.monotonic()
        f_lock = open(lock_file, "w")
        try:
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        f_lock.seek(0)
                        msvcrt.locking(f_lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(f_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() - start_time >= timeout:
                        raise TimeoutError(f"No se pudo adquirir el lock de registro en {lock_file} tras {timeout}s")
                    time.sleep(0.02)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    f_lock.seek(0)
                    msvcrt.locking(f_lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f_lock.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                f_lock.close()
            except Exception:
                pass

    def check_cohort_eligibility(
        self,
        *,
        kickoff_utc: str | datetime,
        attempt_timestamp_utc: Optional[str | datetime] = None,
        as_of_cutoff_utc: Optional[str | datetime] = None,
        market_odds: Optional[Dict[str, Any] | MarketOddsSnapshot] = None,
        freshness_limit_hours: Optional[float] = None,
        decision_window_hours: Optional[float] = 2.0,
    ) -> tuple[bool, str]:
        """
        Evalúa si un partido y snapshot de cuotas cumplen estrictamente con los
        requisitos de elegibilidad para ser registrados como intento oficial en
        la cohorte prospectiva NFL.

        Requisitos:
        1. Kickoff estrictamente posterior al congelamiento del candidato (CANDIDATE_FREEZING_TIMESTAMP).
        2. Intento (attempt_timestamp) estrictamente anterior al kickoff.
        3. as_of_cutoff anterior al kickoff y no posterior al intento.
        4. Dentro de la ventana pre-kickoff oficial si decision_window_hours está fijado (default 2.0h).
        5. Snapshot de mercado verificado pre-kickoff, no obsoleto, con captured_at <= cutoff
           y dentro de freshness_limit_hours si está fijado.
        """
        try:
            kickoff_dt = (
                kickoff_utc if isinstance(kickoff_utc, datetime)
                else self._parse_iso(kickoff_utc)
            )
            if not kickoff_dt.tzinfo:
                kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
        except Exception as e:
            return False, f"kickoff_utc inválido: {e}"

        freezing_dt = self._parse_iso(CANDIDATE_FREEZING_TIMESTAMP)
        if kickoff_dt <= freezing_dt:
            return False, f"Kickoff {kickoff_dt.isoformat()} anterior o igual al congelamiento ({CANDIDATE_FREEZING_TIMESTAMP})"

        try:
            if attempt_timestamp_utc:
                attempt_dt = (
                    attempt_timestamp_utc if isinstance(attempt_timestamp_utc, datetime)
                    else self._parse_iso(attempt_timestamp_utc)
                )
                if not attempt_dt.tzinfo:
                    attempt_dt = attempt_dt.replace(tzinfo=timezone.utc)
            else:
                attempt_dt = datetime.now(timezone.utc)
        except Exception as e:
            return False, f"attempt_timestamp_utc inválido: {e}"

        if attempt_dt >= kickoff_dt:
            return False, f"attempt_timestamp ({attempt_dt.isoformat()}) posterior o igual al kickoff ({kickoff_dt.isoformat()})"

        try:
            if as_of_cutoff_utc:
                cutoff_dt = (
                    as_of_cutoff_utc if isinstance(as_of_cutoff_utc, datetime)
                    else self._parse_iso(as_of_cutoff_utc)
                )
                if not cutoff_dt.tzinfo:
                    cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)
            else:
                cutoff_dt = attempt_dt
        except Exception as e:
            return False, f"as_of_cutoff_utc inválido: {e}"

        if cutoff_dt >= kickoff_dt:
            return False, f"as_of_cutoff ({cutoff_dt.isoformat()}) posterior o igual al kickoff ({kickoff_dt.isoformat()})"

        if cutoff_dt > attempt_dt:
            return False, f"as_of_cutoff ({cutoff_dt.isoformat()}) posterior al intento ({attempt_dt.isoformat()})"

        if decision_window_hours is not None:
            hours_to_ko = (kickoff_dt - attempt_dt).total_seconds() / 3600.0
            if hours_to_ko > decision_window_hours:
                return False, f"Fuera de la ventana pre-kickoff oficial ({round(hours_to_ko, 1)}h > {decision_window_hours}h)"

        if market_odds is not None:
            if isinstance(market_odds, MarketOddsSnapshot):
                odds_data = asdict(market_odds)
            else:
                odds_data = dict(market_odds)

            if odds_data.get("is_stale") is True:
                return False, "Snapshot de cuotas marcado como obsoleto (is_stale=True)"

            cap_ts = odds_data.get("captured_at_utc") or odds_data.get("captured_at")
            if not cap_ts:
                return False, "Snapshot de cuotas carece de captured_at_utc verificado"

            try:
                cap_dt = cap_ts if isinstance(cap_ts, datetime) else self._parse_iso(str(cap_ts))
                if not cap_dt.tzinfo:
                    cap_dt = cap_dt.replace(tzinfo=timezone.utc)
            except Exception as e:
                return False, f"captured_at inválido: {e}"

            if cap_dt >= kickoff_dt:
                return False, f"captured_at ({cap_dt.isoformat()}) posterior o igual al kickoff ({kickoff_dt.isoformat()})"

            if cap_dt > cutoff_dt:
                return False, f"captured_at ({cap_dt.isoformat()}) posterior a as_of_cutoff ({cutoff_dt.isoformat()})"

            if freshness_limit_hours is not None:
                age_h = (attempt_dt - cap_dt).total_seconds() / 3600.0
                if age_h < 0:
                    return False, "Cuota de mercado con timestamp en el futuro relativo al intento"
                if age_h > freshness_limit_hours:
                    return False, f"Cuota de mercado vencida (antigüedad {round(age_h, 1)}h > límite {freshness_limit_hours}h)"

            h_fair = odds_data.get("home_fair_prob")
            a_fair = odds_data.get("away_fair_prob")
            if h_fair is None or a_fair is None:
                return False, "Faltan probabilidades justas home_fair_prob y/o away_fair_prob (ambas requeridas)"
            try:
                h_f = float(h_fair)
                a_f = float(a_fair)
                if not (0.0 < h_f < 1.0) or not (0.0 < a_f < 1.0):
                    return False, f"Probabilidades justas fuera de rango (0, 1): home={h_f}, away={a_f}"
                if not np.isclose(h_f + a_f, 1.0, atol=1e-2):
                    return False, f"Probabilidades justas no suman 1.0 ({h_f + a_f})"
            except (TypeError, ValueError) as e:
                return False, f"Probabilidades justas no numéricas: {e}"

            h_ml = odds_data.get("home_ml")
            a_ml = odds_data.get("away_ml")
            if h_ml is not None or a_ml is not None:
                if h_ml is None or a_ml is None:
                    return False, "Faltan momios de moneyline (home_ml o away_ml)"
                try:
                    h_ml_f = float(h_ml)
                    a_ml_f = float(a_ml)
                    if (
                        not math.isfinite(h_ml_f) or not math.isfinite(a_ml_f)
                        or h_ml_f == 0 or a_ml_f == 0
                        or (-100 < h_ml_f < 100) or (-100 < a_ml_f < 100)
                    ):
                        return False, f"Momios moneyline inválidos o fuera de rango: home={h_ml}, away={a_ml}"
                except (TypeError, ValueError) as e:
                    return False, f"Momios moneyline no numéricos: {e}"

        return True, "Elegible para cohorte prospectiva oficial"

    def get_registered_attempt(
        self,
        game_id: str | int,
        canonical_external_id: Optional[str | int] = None,
        variant_tag: Optional[str] = None,
    ) -> Optional[CohortAttemptRecord]:
        """
        Consulta si existe un intento registrado en el log append-only para el game_id
        o canonical_external_id dado. Compara por coincidencia exacta de identificadores.
        """
        target_ids = {str(game_id).strip()}
        if canonical_external_id is not None:
            target_ids.add(str(canonical_external_id).strip())
        records = self.load_all_records()
        for r in records:
            r_gid = str(r.game_id).strip()
            if r_gid in target_ids and (
                variant_tag is None or r.variant_tag == variant_tag
            ):
                return r
        return None

    @staticmethod
    def _validate_moneyline(val: Any, label: str) -> tuple[Optional[int], Optional[str]]:
        """
        Valida fail-closed que un momio moneyline sea un entero válido, finito, no cero
        y fuera del intervalo imposible (-100, 100).
        """
        if val is None:
            return None, f"Momio moneyline {label} carece de valor (None)"
        if isinstance(val, bool):
            return None, f"Momio moneyline {label} no puede ser booleano: {val!r}"
        try:
            f_val = float(val)
        except (TypeError, ValueError) as e:
            return None, f"Momios moneyline {label} no numéricos: {val!r} ({e})"

        if not math.isfinite(f_val):
            return None, f"Momio moneyline {label} no es finito: {val!r}"

        if not f_val.is_integer():
            return None, f"Momio moneyline {label} contiene valor fraccionario no entero: {val!r}"

        int_val = int(f_val)
        if int_val == 0:
            return None, f"Momio moneyline {label} no puede ser cero"

        if -100 < int_val < 100:
            return None, f"Momio moneyline {label} ({int_val}) dentro del intervalo inválido (-100, 100)"

        return int_val, None

    def correlate_attempt_with_prediction(
        self,
        attempt: CohortAttemptRecord,
        *,
        pred_ts: datetime,
        model_version: Optional[str] = None,
        expected_variant_tag: str = DEFAULT_VARIANT_TAG,
        home_win_prob: Optional[float] = None,
        away_win_prob: Optional[float] = None,
        market_odds: Optional[Dict[str, Any] | MarketOddsSnapshot] = None,
        max_time_diff_seconds: float = 120.0,
    ) -> tuple[bool, str]:
        """
        Correlaciona rigurosa y fail-closed un registro append-only de cohorte
        con una predicción servida.

        Exige y valida fail-closed:
        1. attempt_status == 'evaluated'
        2. variant_tag presente e idéntico a expected_variant_tag (DEFAULT_VARIANT_TAG)
        3. model_version presente e idéntica en ambos lados
        4. pred_ts presente y válido; attempt_timestamp_utc presente, parseable y <= max_time_diff_seconds
        5. as_of_cutoff_utc presente, parseable y <= max_time_diff_seconds
        6. Probabilidades de victoria del modelo presentes en ambos lados y coincidentes (atol=1e-3)
        7. market_odds presente en ambos lados con:
           - is_stale es estrictamente el booleano False en ambos (requerido is_stale is False)
           - captured_at_utc verificado, parseable y coincidente en ambos
           - home_fair_prob y away_fair_prob presentes, en (0, 1), sumando 1.0, y coincidentes (atol=1e-3)
           - bookmaker presente, no vacío y coincidente en ambos
           - moneylines (home_ml y away_ml) finitos, enteros válidos, no cero, fuera de (-100, 100) y coincidentes sin redondeo
        """
        # 1. Estatus de evaluación
        att_status = getattr(attempt, "attempt_status", None)
        if att_status != "evaluated":
            reason = getattr(attempt, "abstention_reason", None) or "no evaluado"
            return False, f"Intento registrado no está evaluado (status='{att_status}', motivo='{reason}')"

        # 2. Variante
        att_var = getattr(attempt, "variant_tag", None)
        if not att_var or att_var != expected_variant_tag:
            return False, f"Variante registrada ('{att_var}') discordante con variante oficial ('{expected_variant_tag}')"

        # 3. Versión de modelo (fail-closed: presente e idéntica en ambos lados)
        if not model_version or not str(model_version).strip():
            return False, "Predicción servida carece de model_version verificado"
        att_mv = getattr(attempt, "model_version", None)
        if not att_mv or not str(att_mv).strip():
            return False, "Intento registrado carece de model_version verificado"
        if str(att_mv).strip() != str(model_version).strip():
            return False, f"Versión de modelo registrada ('{att_mv}') discordante con la predicción ('{model_version}')"

        # 4. Inferencia temporal (pred_ts y attempt_timestamp_utc)
        if pred_ts is None:
            return False, "Predicción carece de timestamp de inferencia válido"
        pred_ts_utc = pred_ts if pred_ts.tzinfo else pred_ts.replace(tzinfo=timezone.utc)

        att_ts_val = getattr(attempt, "attempt_timestamp_utc", None)
        if not att_ts_val:
            return False, "Intento registrado carece de attempt_timestamp_utc"
        try:
            if isinstance(att_ts_val, datetime):
                att_ts = att_ts_val if att_ts_val.tzinfo else att_ts_val.replace(tzinfo=timezone.utc)
            else:
                att_ts = self._parse_iso(str(att_ts_val))
                if not att_ts.tzinfo:
                    att_ts = att_ts.replace(tzinfo=timezone.utc)
        except Exception as e:
            return False, f"Timestamp de intento inválido ({e})"

        time_diff = abs((att_ts - pred_ts_utc).total_seconds())
        if time_diff > max_time_diff_seconds:
            diff_str = f"{round(time_diff / 3600.0, 1)}h" if time_diff >= 3600 else f"{round(time_diff / 60.0, 1)}m"
            return False, f"Timestamp de registro ({att_ts_val}) descorrelacionado con la inferencia ({pred_ts_utc.isoformat()}, diferencia de {diff_str})"

        # 5. as_of_cutoff_utc (fail-closed: presente, parseable y dentro de la tolerancia)
        cutoff_val = getattr(attempt, "as_of_cutoff_utc", None)
        if not cutoff_val:
            return False, "Intento registrado carece de as_of_cutoff_utc verificado"
        try:
            if isinstance(cutoff_val, datetime):
                cutoff_ts = cutoff_val if cutoff_val.tzinfo else cutoff_val.replace(tzinfo=timezone.utc)
            else:
                cutoff_ts = self._parse_iso(str(cutoff_val))
                if not cutoff_ts.tzinfo:
                    cutoff_ts = cutoff_ts.replace(tzinfo=timezone.utc)
        except Exception as e:
            return False, f"Cutoff de registro ({cutoff_val}) tiene formato inválido ({e})"

        c_diff = abs((cutoff_ts - pred_ts_utc).total_seconds())
        if c_diff > max_time_diff_seconds:
            diff_str = f"{round(c_diff / 3600.0, 1)}h" if c_diff >= 3600 else f"{round(c_diff / 60.0, 1)}m"
            return False, f"Cutoff de registro ({cutoff_val}) descorrelacionado con la inferencia ({pred_ts_utc.isoformat()}, diferencia de {diff_str})"

        # 6. Probabilidades de victoria del modelo
        if home_win_prob is None or away_win_prob is None:
            return False, "Predicción servida carece de probabilidades de victoria (home_win_prob o away_win_prob)"
        att_h = getattr(attempt, "home_win_probability", None)
        att_a = getattr(attempt, "away_win_probability", None)
        if att_h is None or att_a is None:
            return False, "Intento registrado carece de probabilidades de victoria (home_win_probability o away_win_probability)"

        try:
            s_hp = float(home_win_prob)
            s_ap = float(away_win_prob)
            r_hp = float(att_h)
            r_ap = float(att_a)
        except (TypeError, ValueError) as e:
            return False, f"Probabilidades de victoria no numéricas: {e}"

        if not np.isclose(r_hp, s_hp, atol=1e-3):
            return False, f"Probabilidad home registrada ({round(r_hp, 4)}) discordante con predicción ({round(s_hp, 4)})"
        if not np.isclose(r_ap, s_ap, atol=1e-3):
            return False, f"Probabilidad away registrada ({round(r_ap, 4)}) discordante con predicción ({round(s_ap, 4)})"

        # 7. Snapshot de mercado (fail-closed: presente en ambos lados con todos los campos)
        if market_odds is None:
            return False, "Predicción servida carece de snapshot de cuotas de mercado (market_odds)"
        att_odds = getattr(attempt, "market_odds", None)
        if att_odds is None:
            return False, "Intento registrado carece de snapshot de cuotas de mercado (market_odds)"

        if isinstance(market_odds, MarketOddsSnapshot):
            srv_odds = asdict(market_odds)
        elif isinstance(market_odds, dict):
            srv_odds = dict(market_odds)
        else:
            return False, "Snapshot de mercado servido tiene un formato inválido"

        if isinstance(att_odds, MarketOddsSnapshot):
            reg_odds = asdict(att_odds)
        elif isinstance(att_odds, dict):
            reg_odds = dict(att_odds)
        else:
            return False, "Snapshot de mercado registrado tiene un formato inválido"

        # 7a. Obsoleto / stale (fail-closed: requiere estrictamente el booleano False en ambos lados)
        if "is_stale" not in srv_odds:
            return False, "Snapshot de mercado servido carece del campo is_stale (requerido is_stale is False)"
        srv_stale = srv_odds.get("is_stale")
        if srv_stale is True:
            return False, "Snapshot de mercado servido está marcado como obsoleto (is_stale=True)"
        if srv_stale is not False:
            return False, f"Snapshot de mercado servido tiene is_stale inválido (requerido booleano False, valor: {srv_stale!r})"

        if "is_stale" not in reg_odds:
            return False, "Snapshot de mercado registrado carece del campo is_stale (requerido is_stale is False)"
        reg_stale = reg_odds.get("is_stale")
        if reg_stale is True:
            return False, "Snapshot de mercado registrado está marcado como obsoleto (is_stale=True)"
        if reg_stale is not False:
            return False, f"Snapshot de mercado registrado tiene is_stale inválido (requerido booleano False, valor: {reg_stale!r})"

        # 7b. captured_at_utc verificado y coincidente
        srv_cap = srv_odds.get("captured_at_utc") or srv_odds.get("captured_at")
        if not srv_cap:
            return False, "Snapshot de mercado servido carece de captured_at_utc verificado"
        reg_cap = reg_odds.get("captured_at_utc") or reg_odds.get("captured_at")
        if not reg_cap:
            return False, "Snapshot de mercado registrado carece de captured_at_utc verificado"

        try:
            srv_cap_dt = srv_cap if isinstance(srv_cap, datetime) else self._parse_iso(str(srv_cap))
            if not srv_cap_dt.tzinfo:
                srv_cap_dt = srv_cap_dt.replace(tzinfo=timezone.utc)
        except Exception as e:
            return False, f"captured_at_utc servido inválido ({e})"

        try:
            reg_cap_dt = reg_cap if isinstance(reg_cap, datetime) else self._parse_iso(str(reg_cap))
            if not reg_cap_dt.tzinfo:
                reg_cap_dt = reg_cap_dt.replace(tzinfo=timezone.utc)
        except Exception as e:
            return False, f"captured_at_utc registrado inválido ({e})"

        if abs((srv_cap_dt - reg_cap_dt).total_seconds()) > 2.0:
            return False, f"Timestamp de captura de mercado discordante (registrado: {reg_cap} vs actual: {srv_cap})"

        # 7c. Probabilidades justas (fair probabilities)
        srv_h_fair = srv_odds.get("home_fair_prob")
        srv_a_fair = srv_odds.get("away_fair_prob")
        if srv_h_fair is None or srv_a_fair is None:
            return False, "Snapshot de mercado servido carece de probabilidades justas (home_fair_prob / away_fair_prob)"
        reg_h_fair = reg_odds.get("home_fair_prob")
        reg_a_fair = reg_odds.get("away_fair_prob")
        if reg_h_fair is None or reg_a_fair is None:
            return False, "Snapshot de mercado registrado carece de probabilidades justas (home_fair_prob / away_fair_prob)"

        try:
            s_hf = float(srv_h_fair)
            s_af = float(srv_a_fair)
            r_hf = float(reg_h_fair)
            r_af = float(reg_a_fair)
        except (TypeError, ValueError) as e:
            return False, f"Probabilidades justas de mercado no numéricas: {e}"

        for name, val in [("servida home", s_hf), ("servida away", s_af), ("registrada home", r_hf), ("registrada away", r_af)]:
            if not (0.0 < val < 1.0):
                return False, f"Probabilidad justa de mercado {name} fuera de rango (0, 1): {val}"

        if not np.isclose(s_hf + s_af, 1.0, atol=1e-2):
            return False, f"Probabilidades justas servidas no suman 1.0 ({s_hf + s_af})"
        if not np.isclose(r_hf + r_af, 1.0, atol=1e-2):
            return False, f"Probabilidades justas registradas no suman 1.0 ({r_hf + r_af})"

        if not np.isclose(r_hf, s_hf, atol=1e-3):
            return False, f"Probabilidad justa home de mercado discordante (registrada: {r_hf} vs actual: {s_hf})"
        if not np.isclose(r_af, s_af, atol=1e-3):
            return False, f"Probabilidad justa away de mercado discordante (registrada: {r_af} vs actual: {s_af})"

        # 7d. Bookmaker
        srv_bm = srv_odds.get("bookmaker")
        if not srv_bm or not str(srv_bm).strip():
            return False, "Snapshot de mercado servido carece de bookmaker verificado"
        reg_bm = reg_odds.get("bookmaker")
        if not reg_bm or not str(reg_bm).strip():
            return False, "Snapshot de mercado registrado carece de bookmaker verificado"
        if str(srv_bm).strip().lower() != str(reg_bm).strip().lower():
            return False, f"Bookmaker de mercado discordante (registrado: '{reg_bm}' vs actual: '{srv_bm}')"

        # 7e. Moneylines (home_ml y away_ml)
        srv_h_ml = srv_odds.get("home_ml") if srv_odds.get("home_ml") is not None else srv_odds.get("home_moneyline")
        srv_a_ml = srv_odds.get("away_ml") if srv_odds.get("away_ml") is not None else srv_odds.get("away_moneyline")
        if srv_h_ml is None or srv_a_ml is None:
            return False, "Snapshot de mercado servido carece de momios moneyline (home_ml o away_ml)"
        reg_h_ml = reg_odds.get("home_ml") if reg_odds.get("home_ml") is not None else reg_odds.get("home_moneyline")
        reg_a_ml = reg_odds.get("away_ml") if reg_odds.get("away_ml") is not None else reg_odds.get("away_moneyline")
        if reg_h_ml is None or reg_a_ml is None:
            return False, "Snapshot de mercado registrado carece de momios moneyline (home_ml o away_ml)"

        s_hml, err = self._validate_moneyline(srv_h_ml, "servido home")
        if err:
            return False, err
        s_aml, err = self._validate_moneyline(srv_a_ml, "servido away")
        if err:
            return False, err
        r_hml, err = self._validate_moneyline(reg_h_ml, "registrado home")
        if err:
            return False, err
        r_aml, err = self._validate_moneyline(reg_a_ml, "registrado away")
        if err:
            return False, err

        if s_hml != r_hml:
            return False, f"Moneyline home de mercado discordante (registrado: {r_hml} vs actual: {s_hml})"
        if s_aml != r_aml:
            return False, f"Moneyline away de mercado discordante (registrado: {r_aml} vs actual: {s_aml})"

        return True, "Inscripción en cohorte confirmada y 100% correlacionada con la predicción"

    def find_correlated_attempt(
        self,
        *,
        game_id: str | int,
        canonical_external_id: Optional[str | int] = None,
        pred_ts: datetime,
        model_version: Optional[str] = None,
        variant_tag: str = DEFAULT_VARIANT_TAG,
        home_win_prob: Optional[float] = None,
        away_win_prob: Optional[float] = None,
        market_odds: Optional[Dict[str, Any]] = None,
        all_records: Optional[List[CohortAttemptRecord]] = None,
    ) -> tuple[Optional[CohortAttemptRecord], str]:
        """
        Busca y selecciona entre los registros de la cohorte aquel que correlacione
        exactamente con la predicción servida. Maneja duplicados y liquidaciones.
        """
        target_ids = {str(game_id).strip()}
        if canonical_external_id is not None:
            target_ids.add(str(canonical_external_id).strip())

        if all_records is not None:
            candidates = [
                r for r in all_records
                if str(getattr(r, "game_id", "")).strip() in target_ids
            ]
        else:
            records = self.load_all_records()
            candidates = [
                r for r in records
                if str(getattr(r, "game_id", "")).strip() in target_ids
            ]
            if not candidates:
                single_att = self.get_registered_attempt(
                    game_id=game_id,
                    canonical_external_id=canonical_external_id,
                    variant_tag=variant_tag,
                )
                if single_att is not None:
                    candidates = [single_att]

        if not candidates:
            return None, "Early Lean: Elegible en ventana pre-kickoff pero pendiente de registro en cohorte prospectiva"

        rejection_reasons = []
        for cand in candidates:
            is_corr, detail = self.correlate_attempt_with_prediction(
                cand,
                pred_ts=pred_ts,
                model_version=model_version,
                expected_variant_tag=variant_tag,
                home_win_prob=home_win_prob,
                away_win_prob=away_win_prob,
                market_odds=market_odds,
            )
            if is_corr:
                return cand, detail
            rejection_reasons.append(detail)

        first_reason = rejection_reasons[0] if rejection_reasons else "Registro no coincide"
        return None, f"Early Lean: {first_reason}"

    def is_cohort_eligible(
        self,
        *,
        kickoff_utc: str | datetime,
        attempt_timestamp_utc: Optional[str | datetime] = None,
        as_of_cutoff_utc: Optional[str | datetime] = None,
        market_odds: Optional[Dict[str, Any] | MarketOddsSnapshot] = None,
        freshness_limit_hours: Optional[float] = None,
        decision_window_hours: Optional[float] = 2.0,
    ) -> bool:
        """Retorna True si cumple estrictamente los requisitos de elegibilidad de cohorte oficial."""
        eligible, _ = self.check_cohort_eligibility(
            kickoff_utc=kickoff_utc,
            attempt_timestamp_utc=attempt_timestamp_utc,
            as_of_cutoff_utc=as_of_cutoff_utc,
            market_odds=market_odds,
            freshness_limit_hours=freshness_limit_hours,
            decision_window_hours=decision_window_hours,
        )
        return eligible

    def record_attempt(
        self,
        *,
        game_id: str,
        kickoff_utc: str,
        attempt_status: str,
        attempt_timestamp_utc: Optional[str] = None,
        as_of_cutoff_utc: Optional[str] = None,
        duplicate_policy: str = "reject",
        model_variant_applied: Optional[str] = None,
        variant_tag: str = DEFAULT_VARIANT_TAG,
        abstention_reason: Optional[str] = None,
        coverage_state_home: Optional[str] = None,
        coverage_state_away: Optional[str] = None,
        features: Optional[Dict[str, float]] = None,
        home_win_probability: Optional[float] = None,
        away_win_probability: Optional[float] = None,
        model_agreement: Optional[int] = None,
        confluence_tier: Optional[str] = None,
        market_odds: Optional[Dict[str, Any] | MarketOddsSnapshot] = None,
        projected_margin: Optional[float] = None,
        market_spread_line: Optional[float] = None,
        spread_cover_probability: Optional[float] = None,
        qb_expected_epa_lost_home: Optional[float] = None,
        qb_expected_epa_lost_away: Optional[float] = None,
        artifact_sha256: str = CANDIDATE_ARTIFACT_SHA256,
        model_version: str = CANDIDATE_MODEL_VERSION,
        selected_model: str = CANDIDATE_SELECTED_MODEL,
        dataset_sha256: str = CANDIDATE_DATASET_SHA256,
    ) -> CohortAttemptRecord:
        """Registra un intento pregame asegurando que sea estrictamente prospectivo."""
        kickoff_dt = self._parse_iso(kickoff_utc)
        freezing_dt = self._parse_iso(CANDIDATE_FREEZING_TIMESTAMP)

        if kickoff_dt <= freezing_dt:
            raise ValueError(
                f"El partido {game_id} tiene kickoff {kickoff_utc} anterior o igual al congelamiento "
                f"del candidato ({CANDIDATE_FREEZING_TIMESTAMP}). No es prospectivo."
            )

        # 1. Validación de temporalidad: attempt_timestamp_utc y as_of_cutoff_utc deben preceder estrictamente al kickoff
        if attempt_timestamp_utc:
            attempt_dt = self._parse_iso(attempt_timestamp_utc)
        else:
            attempt_dt = datetime.now(timezone.utc)
            attempt_timestamp_utc = attempt_dt.isoformat()

        if attempt_dt >= kickoff_dt:
            raise ValueError(
                f"attempt_timestamp_utc ({attempt_timestamp_utc}) debe ser estrictamente anterior "
                f"al kickoff ({kickoff_utc})"
            )

        if as_of_cutoff_utc:
            cutoff_dt = self._parse_iso(as_of_cutoff_utc)
        else:
            cutoff_dt = attempt_dt
            as_of_cutoff_utc = attempt_timestamp_utc

        if cutoff_dt >= kickoff_dt:
            raise ValueError(
                f"as_of_cutoff_utc ({as_of_cutoff_utc}) debe ser estrictamente anterior "
                f"al kickoff ({kickoff_utc})"
            )

        if cutoff_dt > attempt_dt:
            raise ValueError(
                f"as_of_cutoff_utc ({as_of_cutoff_utc}) no puede ser posterior a "
                f"attempt_timestamp_utc ({attempt_timestamp_utc})"
            )

        # 2. Control de duplicados explícito
        existing_records = [
            r for r in self.load_all_records()
            if r.game_id == game_id and r.variant_tag == variant_tag
        ]
        if existing_records:
            if duplicate_policy in {"reject", "raise", "error"}:
                raise ValueError(
                    f"Intento duplicado detectado para game_id='{game_id}' y variant_tag='{variant_tag}'. "
                    f"Política explícita '{duplicate_policy}' rechaza duplicados."
                )
            elif duplicate_policy == "ignore":
                return existing_records[0]
            elif duplicate_policy != "allow":
                raise ValueError(f"Política de duplicados desconocida: '{duplicate_policy}'")

        # 3. Validación de contratos de estatus
        if attempt_status not in {"evaluated", "abstained"}:
            raise ValueError(f"attempt_status inválido: {attempt_status}. Debe ser 'evaluated' o 'abstained'")

        if attempt_status == "evaluated":
            if model_variant_applied in {"team_only", "team_only_fallback"}:
                model_variant_applied = "team_only_fallback"
            elif model_variant_applied != "injury_aware":
                raise ValueError("Partidos evaluados requieren model_variant_applied ('injury_aware' o 'team_only_fallback')")
            if home_win_probability is None or away_win_probability is None:
                raise ValueError("Partidos evaluados requieren probabilidades de victoria")
            if not np.isclose(home_win_probability + away_win_probability, 1.0, atol=1e-3):
                raise ValueError("Las probabilidades del modelo deben sumar 1.0")
        elif attempt_status == "abstained":
            if not abstention_reason:
                raise ValueError("Partidos con abstención requieren un motivo 'abstention_reason'")

        # 4. Validación rigurosa de snapshot de mercado si está presente
        market_odds_dict = None
        if market_odds is not None:
            if isinstance(market_odds, MarketOddsSnapshot):
                odds_data = asdict(market_odds)
            else:
                odds_data = dict(market_odds)

            if odds_data.get("is_stale") is True:
                raise ValueError("El snapshot de cuotas está marcado como obsoleto (is_stale=True). Rechazado.")

            cap_ts = odds_data.get("captured_at_utc")
            if not cap_ts:
                raise ValueError(
                    "El snapshot de cuotas de mercado requiere un timestamp verificado 'captured_at_utc'. "
                    "Prohibida la atribución/síntesis temporal desde as_of_cutoff."
                )

            cap_dt = self._parse_iso(cap_ts)
            if cap_dt >= kickoff_dt:
                raise ValueError(
                    f"Las cuotas de mercado con captured_at_utc ({cap_ts}) deben ser estrictamente "
                    f"anteriores al kickoff ({kickoff_utc})"
                )
            if cap_dt > cutoff_dt:
                raise ValueError(
                    f"Las cuotas de mercado con captured_at_utc ({cap_ts}) no pueden ser posteriores "
                    f"a as_of_cutoff_utc ({as_of_cutoff_utc})"
                )

            h_fair = odds_data.get("home_fair_prob")
            a_fair = odds_data.get("away_fair_prob")
            if h_fair is not None or a_fair is not None:
                if h_fair is None or a_fair is None:
                    raise ValueError("El snapshot de cuotas requiere tanto home_fair_prob como away_fair_prob")
                h_f = float(h_fair)
                a_f = float(a_fair)
                if not (0.0 < h_f < 1.0) or not (0.0 < a_f < 1.0):
                    raise ValueError(f"Probabilidades justas de mercado fuera del rango (0, 1): home={h_f}, away={a_f}")
                if not np.isclose(h_f + a_f, 1.0, atol=1e-2):
                    raise ValueError(f"Las probabilidades justas de mercado deben sumar 1.0: {h_f} + {a_f} = {h_f + a_f}")

            market_odds_dict = odds_data

        record_id_raw = f"{game_id}_{variant_tag}_{kickoff_utc}"
        record_id = hashlib.sha256(record_id_raw.encode("utf-8")).hexdigest()[:16]

        record = CohortAttemptRecord(
            record_id=record_id,
            game_id=game_id,
            sport="NFL",
            kickoff_utc=kickoff_utc,
            attempt_timestamp_utc=attempt_timestamp_utc,
            as_of_cutoff_utc=as_of_cutoff_utc,
            attempt_status=attempt_status,
            model_variant_applied=model_variant_applied if attempt_status == "evaluated" else None,
            model_version=model_version,
            selected_model=selected_model,
            artifact_sha256=artifact_sha256,
            dataset_sha256=dataset_sha256,
            variant_tag=variant_tag,
            abstention_reason=abstention_reason if attempt_status == "abstained" else None,
            coverage_state_home=coverage_state_home,
            coverage_state_away=coverage_state_away,
            features=features,
            home_win_probability=round(home_win_probability, 4) if home_win_probability is not None else None,
            away_win_probability=round(away_win_probability, 4) if away_win_probability is not None else None,
            model_agreement=model_agreement,
            confluence_tier=confluence_tier,
            market_odds=market_odds_dict,
            projected_margin=round(projected_margin, 2) if projected_margin is not None else None,
            market_spread_line=round(market_spread_line, 1) if market_spread_line is not None else None,
            spread_cover_probability=round(spread_cover_probability, 4) if spread_cover_probability is not None else None,
            qb_expected_epa_lost_home=round(qb_expected_epa_lost_home, 2) if qb_expected_epa_lost_home is not None else None,
            qb_expected_epa_lost_away=round(qb_expected_epa_lost_away, 2) if qb_expected_epa_lost_away is not None else None,
        )

        # Escritura estrictamente append-only al log bajo bloqueo concurrente
        record_payload = asdict(record)
        record_payload["record_type"] = "attempt"
        with self._file_lock():
            with open(self.registry_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record_payload) + "\n")
            self._update_manifest()
        return record

    def record_settlement(
        self,
        *,
        game_id: str,
        home_score: int,
        away_score: int,
        variant_tag: Optional[str] = None,
    ) -> bool:
        """
        Liquida el resultado de un partido previamente registrado en la cohorte.
        Conserva el historial de forma estrictamente append-only sin sobreescribir el archivo.
        Es idempotente si los partidos coincidentes ya están liquidados con el mismo resultado exacto.
        """
        if not self.registry_file.exists():
            return False

        with self._file_lock():
            records = self.load_all_records()
            matching = [
                r for r in records
                if r.game_id == game_id and (variant_tag is None or r.variant_tag == variant_tag)
            ]
            if not matching:
                return False

            # Idempotencia: si todos los registros coincidentes ya están liquidados con el mismo marcador, no duplicar evento
            if all(r.is_settled and r.home_score == home_score and r.away_score == away_score for r in matching):
                return True

            now_utc = datetime.now(timezone.utc).isoformat()
            home_win = 1 if home_score > away_score else 0

            settlement_event = {
                "record_type": "settlement",
                "game_id": game_id,
                "variant_tag": variant_tag,
                "home_score": int(home_score),
                "away_score": int(away_score),
                "home_win": home_win,
                "settled_at_utc": now_utc,
            }

            # Escritura estrictamente append-only al registro
            with open(self.registry_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(settlement_event) + "\n")

            self._update_manifest()
            return True

    def load_all_records(self) -> List[CohortAttemptRecord]:
        """Carga y reconstruye el estado actual de los intentos a partir del log append-only."""
        if not self.registry_file.exists():
            return []

        attempts_map: Dict[tuple[str, str], CohortAttemptRecord] = {}
        for line in self.registry_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            rec_type = entry.get("record_type", "attempt")
            if rec_type == "attempt":
                entry.pop("record_type", None)
                valid_keys = {f.name for f in fields(CohortAttemptRecord)}
                record = CohortAttemptRecord(**{k: v for k, v in entry.items() if k in valid_keys})
                key = (record.game_id, record.variant_tag)
                attempts_map[key] = record
            elif rec_type == "settlement":
                gid = entry["game_id"]
                vtag = entry.get("variant_tag")
                h_score = entry["home_score"]
                a_score = entry["away_score"]
                h_win = entry["home_win"]
                s_utc = entry["settled_at_utc"]
                for key, rec in attempts_map.items():
                    if rec.game_id == gid and (vtag is None or rec.variant_tag == vtag):
                        rec.is_settled = True
                        rec.home_score = h_score
                        rec.away_score = a_score
                        rec.home_win = h_win
                        rec.settled_at_utc = s_utc

        return list(attempts_map.values())

    def load_all_events(self) -> List[Dict[str, Any]]:
        """Retorna todos los eventos en orden cronológico append-only."""
        if not self.registry_file.exists():
            return []
        events = []
        for line in self.registry_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events

    def _update_manifest(self) -> None:
        """Actualiza el manifiesto con los hashes y conteos actuales."""
        content = self.registry_file.read_bytes() if self.registry_file.exists() else b""
        records = self.load_all_records()
        manifest_data = {
            "registry_file": self.registry_file.name,
            "registry_sha256": hashlib.sha256(content).hexdigest(),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "freezing_registration_timestamp": CANDIDATE_FREEZING_TIMESTAMP,
            "candidate": {
                "model_version": CANDIDATE_MODEL_VERSION,
                "selected_model": CANDIDATE_SELECTED_MODEL,
                "artifact_sha256": CANDIDATE_ARTIFACT_SHA256,
                "dataset_sha256": CANDIDATE_DATASET_SHA256,
            },
            "total_attempts": len(records),
            "total_evaluated": sum(1 for r in records if r.attempt_status == "evaluated"),
            "total_abstained": sum(1 for r in records if r.attempt_status == "abstained"),
            "total_settled": sum(1 for r in records if r.is_settled and r.attempt_status == "evaluated"),
        }
        self.manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    def evaluate_cohort(self, variant_tag: str = DEFAULT_VARIANT_TAG) -> Dict[str, Any]:
        """
        Genera el informe analítico de la cohorte separando estrictamente:
        1. Predicciones injury_aware verificadas.
        2. Fallbacks al modelo team_only.
        3. Subconjunto benchmarkable contra mercado sin vig.
        4. Desglose de abstenciones y cobertura.
        """
        all_records = self.load_all_records()
        records = [r for r in all_records if r.variant_tag == variant_tag]

        if not records:
            return {
                "status": "pending_prospective_cohort",
                "variant_tag": variant_tag,
                "message": "Aún no hay partidos intentados en la cohorte prospectiva para esta variante.",
                "total_attempts": 0,
            }

        total_attempts = len(records)
        evaluated_records = [r for r in records if r.attempt_status == "evaluated"]
        abstained_records = [r for r in records if r.attempt_status == "abstained"]

        # 4. Análisis de Abstenciones y Cobertura
        abstention_counts = {}
        for r in abstained_records:
            reason = r.abstention_reason or "UNKNOWN_REASON"
            abstention_counts[reason] = abstention_counts.get(reason, 0) + 1

        abstention_breakdown = {
            reason: {
                "count": count,
                "pct_of_abstentions": round((count / len(abstained_records)) * 100.0, 1) if abstained_records else 0.0,
                "pct_of_total_attempts": round((count / total_attempts) * 100.0, 1),
            }
            for reason, count in sorted(abstention_counts.items(), key=lambda x: -x[1])
        }

        coverage_rate_pct = round((len(evaluated_records) / total_attempts) * 100.0, 2)

        def _calc_metrics(sub_records: List[CohortAttemptRecord]) -> Dict[str, Any]:
            settled = [
                r for r in sub_records
                if r.is_settled and r.home_win is not None and r.home_win_probability is not None
            ]
            if not settled:
                return {
                    "total_count": len(sub_records),
                    "settled_count": 0,
                    "status": "pending_settlement",
                }
            y_true = np.array([r.home_win for r in settled], dtype=int)
            y_prob = np.array([r.home_win_probability for r in settled], dtype=float)
            y_pred = (y_prob >= 0.5).astype(int)

            # ECE
            bins = np.linspace(0.0, 1.0, 11)
            bin_idx = np.clip(np.digitize(y_prob, bins) - 1, 0, 9)
            ece = 0.0
            for b in range(10):
                mask = bin_idx == b
                if np.sum(mask) > 0:
                    bin_acc = float(np.mean(y_true[mask]))
                    bin_conf = float(np.mean(y_prob[mask]))
                    ece += (np.sum(mask) / len(settled)) * abs(bin_acc - bin_conf)

            # AUC (controlando caso si y_true contiene solo 0s o 1s)
            try:
                auc_val = float(roc_auc_score(y_true, y_prob)) if len(np.unique(y_true)) > 1 else None
            except Exception:
                auc_val = None

            return {
                "total_count": len(sub_records),
                "settled_count": len(settled),
                "brier_score": round(float(brier_score_loss(y_true, y_prob)), 4),
                "log_loss": round(float(log_loss(y_true, y_prob, labels=[0, 1])), 4),
                "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
                "auc": round(auc_val, 4) if auc_val is not None else None,
                "expected_calibration_error": round(float(ece), 4),
            }

        # 1. Segmento A: Predicciones injury_aware verificadas
        inj_verified_records = [r for r in evaluated_records if r.model_variant_applied == "injury_aware"]
        segment_a_metrics = _calc_metrics(inj_verified_records)

        # 2. Segmento B: Fallbacks al modelo team_only
        team_fallback_records = [r for r in evaluated_records if r.model_variant_applied == "team_only_fallback"]
        segment_b_metrics = _calc_metrics(team_fallback_records)

        # 3. Segmento C: Subconjunto benchmarkable contra mercado sin vig
        market_benchmarkable_records = [
            r for r in evaluated_records
            if r.market_odds and isinstance(r.market_odds, dict)
            and r.market_odds.get("home_fair_prob") is not None
            and not r.market_odds.get("is_stale", False)
        ]

        settled_market = [r for r in market_benchmarkable_records if r.is_settled and r.home_win is not None]
        if settled_market:
            y_true_m = np.array([r.home_win for r in settled_market], dtype=int)
            y_model_p = np.array([r.home_win_probability for r in settled_market], dtype=float)
            y_fair_p = np.array([r.market_odds["home_fair_prob"] for r in settled_market], dtype=float)

            model_brier = float(brier_score_loss(y_true_m, y_model_p))
            market_fair_brier = float(brier_score_loss(y_true_m, y_fair_p))
            model_ll = float(log_loss(y_true_m, y_model_p, labels=[0, 1]))
            market_ll = float(log_loss(y_true_m, y_fair_p, labels=[0, 1]))
            brier_advantage = market_fair_brier - model_brier  # > 0 si el modelo bate al mercado

            segment_c_metrics = {
                "total_count": len(market_benchmarkable_records),
                "settled_count": len(settled_market),
                "model_brier": round(model_brier, 4),
                "market_fair_brier": round(market_fair_brier, 4),
                "brier_advantage": round(brier_advantage, 4),
                "model_beats_market_fair_brier": bool(brier_advantage > 0),
                "model_log_loss": round(model_ll, 4),
                "market_fair_log_loss": round(market_ll, 4),
                "mean_absolute_edge": round(float(np.mean(np.abs(y_model_p - y_fair_p))), 4),
            }
        else:
            segment_c_metrics = {
                "total_count": len(market_benchmarkable_records),
                "settled_count": 0,
                "status": "pending_settlement",
            }

        return {
            "evaluation_title": "NFL Prospective Cohort Evaluation Report",
            "variant_tag": variant_tag,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "candidate_frozen": {
                "model_version": CANDIDATE_MODEL_VERSION,
                "selected_model": CANDIDATE_SELECTED_MODEL,
                "artifact_sha256": CANDIDATE_ARTIFACT_SHA256,
                "dataset_sha256": CANDIDATE_DATASET_SHA256,
                "freezing_timestamp": CANDIDATE_FREEZING_TIMESTAMP,
            },
            "overall_summary": {
                "total_attempts": total_attempts,
                "total_evaluated": len(evaluated_records),
                "total_abstained": len(abstained_records),
                "coverage_rate_pct": coverage_rate_pct,
                "total_settled": sum(1 for r in evaluated_records if r.is_settled),
            },
            "segment_a_injury_aware_verified": segment_a_metrics,
            "segment_b_team_only_fallback": segment_b_metrics,
            "segment_c_market_benchmarkable": segment_c_metrics,
            "segment_d_abstentions_and_coverage": {
                "total_abstained": len(abstained_records),
                "abstention_rate_pct": round(100.0 - coverage_rate_pct, 2),
                "reasons_breakdown": abstention_breakdown,
            },
        }
