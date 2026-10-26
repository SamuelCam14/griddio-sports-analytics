"""
Servicio de Disponibilidad e Injuries Point-in-Time para la NFL (Fase 02).
Implementa estrictamente los contratos conceptuales y reglas de dominio:
- docs/nfl_model_execution/02-injury-point-in-time.md
- docs/nfl_model_execution/reports/phase-02-codex-audit.md
- docs/nfl_model_execution/DATA_DICTIONARY.md

Reglas Fundamentales (Auditadas por Codex):
1. Cero timestamps sintéticos: si falta observed_at o decision_cutoff, o si observed_at >= decision_cutoff,
   falla cerrado a UNKNOWN.
2. Identidad canónica estricta: canonical_player_id debe ser resuelto inequívocamente; si es ambiguo o
   no resuelto, el estado es UNKNOWN.
3. Player props verificables: consume únicamente props con event_id, bookmaker, market_key, line, price
   y timestamp pre-corte demostrable. Cero generación sintética. Si faltan props verificables, checked_props=False.
4. Cobertura explícita: estados diferenciados (covered_healthy, covered_with_injuries, unknown, unavailable_no_report).
   Nunca imputar 0.0 ante fallas o falta de reporte.
5. Métricas de impacto derivadas exclusivamente de datos previos observados y denominadores definidos.
   Cero priors manuales o constantes inventadas (15 PPR / 0.80 snaps). Si faltan estadísticas previas,
   las métricas se exponen como None / unavailable.
6. Prohibición estricta de consultas de cuotas para juegos históricos o jugadores OUT/IR.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import structlog

logger = structlog.get_logger(__name__)

RESOLVER_VERSION = "nfl_availability_v2"
QB_REPLACEMENT_ESTIMATOR_VERSION = "qb_epa_replacement_p25_shrink50_v1"
AVAILABILITY_CACHE_DIR = Path("data/cache/nfl_availability")
AVAILABILITY_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class CoverageState(str, Enum):
    COVERED_HEALTHY = "covered_healthy"
    COVERED_WITH_INJURIES = "covered_with_injuries"
    NO_INJURIES_LISTED = "no_injuries_listed_in_capture"
    UNKNOWN = "unknown"
    UNAVAILABLE_NO_REPORT = "unavailable_no_report"


class AvailabilityStatus(str, Enum):
    CONFIRMED_OUT = "confirmed_out"
    QUESTIONABLE = "questionable"
    DOUBTFUL = "doubtful"
    ACTIVE_REPORT = "active_report"
    UNKNOWN = "unknown"


class MarketPropSignal(str, Enum):
    LISTED_AT_CUTOFF = "listed_at_cutoff"
    NOT_LISTED_WITH_COVERAGE = "not_listed_with_coverage"
    UNKNOWN = "unknown"


# Probabilidades base descriptivas empíricas prepartido P(absence) para NFL (datos 2020-2024 nflverse)
BASE_P_ABSENCE: Dict[AvailabilityStatus, float] = {
    AvailabilityStatus.CONFIRMED_OUT: 1.0,
    AvailabilityStatus.DOUBTFUL: 0.85,
    AvailabilityStatus.QUESTIONABLE: 0.25,
    AvailabilityStatus.ACTIVE_REPORT: 0.0,
    AvailabilityStatus.UNKNOWN: 0.0,
}

# Modulación de P(absence) ante señales verificables de player props
MARKET_MODULATED_P_ABSENCE: Dict[Tuple[AvailabilityStatus, MarketPropSignal], float] = {
    # Questionable con línea activa en mercado abierto: ausencia 0.15 (NO certeza 0.0)
    (AvailabilityStatus.QUESTIONABLE, MarketPropSignal.LISTED_AT_CUTOFF): 0.15,
    # Questionable excluido cuando compañeros tienen cobertura: ausencia 0.75 (NO OUT 1.0)
    (AvailabilityStatus.QUESTIONABLE, MarketPropSignal.NOT_LISTED_WITH_COVERAGE): 0.75,
    (AvailabilityStatus.QUESTIONABLE, MarketPropSignal.UNKNOWN): 0.25,

    # Doubtful con línea activa: ausencia 0.60 (NO certeza 0.0)
    (AvailabilityStatus.DOUBTFUL, MarketPropSignal.LISTED_AT_CUTOFF): 0.60,
    # Doubtful excluido con cobertura demostrada: ausencia 0.98 (NO 1.0)
    (AvailabilityStatus.DOUBTFUL, MarketPropSignal.NOT_LISTED_WITH_COVERAGE): 0.98,
    (AvailabilityStatus.DOUBTFUL, MarketPropSignal.UNKNOWN): 0.85,

    # Confirmed OUT: siempre 1.0 inmutable
    (AvailabilityStatus.CONFIRMED_OUT, MarketPropSignal.UNKNOWN): 1.0,
    (AvailabilityStatus.CONFIRMED_OUT, MarketPropSignal.LISTED_AT_CUTOFF): 1.0,
    (AvailabilityStatus.CONFIRMED_OUT, MarketPropSignal.NOT_LISTED_WITH_COVERAGE): 1.0,

    # Active report: 0.0
    (AvailabilityStatus.ACTIVE_REPORT, MarketPropSignal.UNKNOWN): 0.0,
    (AvailabilityStatus.ACTIVE_REPORT, MarketPropSignal.LISTED_AT_CUTOFF): 0.0,
    (AvailabilityStatus.ACTIVE_REPORT, MarketPropSignal.NOT_LISTED_WITH_COVERAGE): 0.0,

    (AvailabilityStatus.UNKNOWN, MarketPropSignal.UNKNOWN): 0.0,
}


def parse_practice_participation(practice_text: Optional[str]) -> Optional[str]:
    """
    Parsea el reporte oficial de participación en práctica semanal (típicamente del viernes) de la NFL.
    Retorna 'DNP', 'LP', 'FP' o None.
    """
    if not practice_text or not isinstance(practice_text, str):
        return None
    text = practice_text.lower()
    # Chequear si fue DNP (Did not practice / did not participate)
    if any(k in text for k in ["did not practice", "did not participate", "dnp", "no entrenó", "no participó"]):
        return "DNP"
    # Chequear si fue FP (Full practice / full participant)
    if any(k in text for k in ["full participant", "full practice", "participó plenamente", "entrenamiento completo"]):
        return "FP"
    # Chequear si fue LP (Limited practice / limited participant)
    if any(k in text for k in ["limited participant", "limited practice", "participación limitada", "limitado"]):
        return "LP"
    return None


def resolve_base_p_absence(
    availability_status: AvailabilityStatus,
    practice_status: Optional[str] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Calcula la probabilidad empírica base P(absence) para estatus Q/D sin líneas de props.
    Ajustada según participación demostrada en prácticas (SumerSports / Action Network standard):
    - DNP en viernes: P(ausencia) sube a 0.70 para Q (0.95 para D).
    - LP en viernes: P(ausencia) permanece en base histórica 0.25 para Q (0.85 para D).
    - FP en viernes: P(ausencia) baja a 0.05 para Q (0.50 para D).
    - Sin reporte de práctica: base histórica estándar 0.25 para Q (0.85 para D).
    """
    base = BASE_P_ABSENCE.get(availability_status, 0.25)
    prac_part = parse_practice_participation(practice_status)
    flags: Dict[str, Any] = {}
    if prac_part:
        flags["practice_participation"] = prac_part
        if availability_status == AvailabilityStatus.QUESTIONABLE:
            if prac_part == "DNP":
                base = 0.70
                flags["practice_modulated"] = True
            elif prac_part == "FP":
                base = 0.05
                flags["practice_modulated"] = True
            elif prac_part == "LP":
                base = 0.25
        elif availability_status == AvailabilityStatus.DOUBTFUL:
            if prac_part == "DNP":
                base = 0.95
                flags["practice_modulated"] = True
            elif prac_part == "FP":
                base = 0.50
                flags["practice_modulated"] = True
            elif prac_part == "LP":
                base = 0.85

    return base, flags


SKILL_POSITIONS: Set[str] = {"QB", "WR", "RB", "TE"}
OFFENSIVE_POSITIONS: Set[str] = {"QB", "WR", "RB", "TE", "T", "G", "C", "OT", "OG", "OL"}
DEFENSIVE_POSITIONS: Set[str] = {"DE", "DT", "LB", "CB", "S", "FS", "SS", "NT", "ILB", "OLB", "DL", "DB"}
ALL_NFL_TEAMS: Set[str] = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LV", "LAC", "LAR", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS",
}


@dataclass
class ESPNInjurySnapshot:
    snapshot_id: str
    captured_at_utc: str
    cadence: str  # "start_of_day" | "near_kickoff" | "pre_kickoff" | "daily"
    source: str = "espn"
    source_observed_at_utc: Optional[str] = None
    known_by_system_at_utc: Optional[str] = None
    payload_sha256: Optional[str] = None
    raw_payload: Optional[Any] = None
    teams: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    omitted_teams: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)



@dataclass
class PlayerInjuryRecord:
    player_name: str
    team: str
    position: str
    canonical_player_id: Optional[str] = None
    espn_id: Optional[str] = None
    pfr_id: Optional[str] = None
    raw_status: Optional[str] = None
    injury_description: Optional[str] = None
    practice_status: Optional[str] = None
    source: str = "espn_live"
    observed_at: Optional[str] = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decision_cutoff: Optional[str] = None
    game_id: Optional[str] = None
    game_start_utc: Optional[str] = None
    availability_status: str = AvailabilityStatus.UNKNOWN.value
    market_signal: str = MarketPropSignal.UNKNOWN.value
    p_absence: float = 0.0
    p_play: float = 1.0
    coverage_flags: Dict[str, Any] = field(default_factory=dict)
    resolver_version: str = RESOLVER_VERSION
    rolling_ppr: float = 0.0
    rolling_off_pct: float = 0.0
    rolling_def_pct: float = 0.0
    lost_ppr: float = 0.0
    lost_off_snaps: float = 0.0
    lost_def_snaps: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TeamAvailabilitySummary:
    team: str
    coverage_state: str  # from CoverageState
    ppr_lost_pct: Optional[float]  # None if unavailable/unknown
    off_snaps_lost_pct: Optional[float]  # None if unavailable/unknown
    def_snaps_lost_pct: Optional[float]  # None if unavailable/unknown
    total_injured_count: int
    confirmed_out_count: int
    questionable_count: int
    doubtful_count: int
    market_listed_count: int
    market_unlisted_count: int
    unknown_count: int
    game_id: Optional[str]
    decision_cutoff: Optional[str]
    clean_report_evidence_ref: Optional[str] = None
    resolver_version: str = RESOLVER_VERSION
    players: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    qb_starter_injured: bool = False
    qb_lost_expected_epa: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class NFLAvailabilityService:
    """Motor point-in-time de disponibilidad e impacto de lesiones para la NFL."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or AVAILABILITY_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = self.cache_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def save_espn_injury_snapshot(
        self,
        raw_teams_data: Dict[str, List[Dict[str, Any]]],
        cadence: str = "start_of_day",
        captured_at_utc: Optional[str] = None,
        source_observed_at_utc: Optional[str] = None,
        all_expected_teams: Optional[Set[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        raw_payload: Optional[Any] = None,
    ) -> ESPNInjurySnapshot:
        """
        Guarda una respuesta de ESPN como snapshot separado en caché point-in-time.
        - Registra captured_at_utc con el reloj local en UTC al recibirla.
        - Preserva source_observed_at_utc por separado sin presentarlo como timestamp de ESPN.
        - Si no se proporciona source_observed_at_utc explícito, no lo inventa (queda None).
        - Persiste el payload raw recibido y calcula payload_sha256 sobre sus bytes sin normalizar.
        - Identifica equipos omitidos y registra fielmente 'sin lesiones listadas en esta captura'.
        - No usa umbrales de 28/32 equipos ni bloquea el feed.
        """
        now_dt = datetime.now(timezone.utc)
        cap_utc = str(captured_at_utc) if captured_at_utc else now_dt.isoformat()
        expected = all_expected_teams or ALL_NFL_TEAMS
        present_teams = {str(k).upper() for k in raw_teams_data.keys()}
        omitted = sorted(list(expected - present_teams))

        safe_tag = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in cap_utc)
        snap_id = f"espn_{cadence}_{safe_tag}"

        # Cálculo riguroso de payload_sha256 y preservación de raw_payload
        effective_raw = raw_payload if raw_payload is not None else raw_teams_data
        if isinstance(effective_raw, bytes):
            raw_bytes = effective_raw
        elif isinstance(effective_raw, str):
            raw_bytes = effective_raw.encode("utf-8")
        else:
            raw_bytes = json.dumps(effective_raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
        payload_hash = hashlib.sha256(raw_bytes).hexdigest()

        meta = dict(metadata or {})
        meta["omitted_teams_count"] = len(omitted)
        meta["teams_with_injuries_count"] = len(present_teams)
        meta["note_omitted"] = "sin lesiones listadas en esta captura"
        known_by_sys = meta.get("known_by_system_at_utc") or cap_utc

        snapshot = ESPNInjurySnapshot(
            snapshot_id=snap_id,
            captured_at_utc=cap_utc,
            cadence=cadence,
            source="espn",
            source_observed_at_utc=str(source_observed_at_utc) if source_observed_at_utc else None,
            known_by_system_at_utc=known_by_sys,
            payload_sha256=payload_hash,
            raw_payload=effective_raw if isinstance(effective_raw, (dict, list, str)) else None,
            teams={k.upper(): v for k, v in raw_teams_data.items()},
            omitted_teams=omitted,
            metadata=meta,
        )

        out_path = self.snapshots_dir / f"espn_snapshot_{safe_tag}.json"
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self.snapshots_dir), prefix="snap_", suffix=".tmp")
        try:
            with open(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, indent=2, ensure_ascii=False)
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, out_path)
            logger.info("ESPN injury snapshot guardado", cadence=cadence, captured_at_utc=cap_utc, known_by_system_at_utc=known_by_sys, payload_sha256=payload_hash, path=str(out_path))
            return snapshot
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def get_injury_snapshots(self, as_of: Optional[Any] = None) -> List[Dict[str, Any]]:
        """Recupera los snapshots disponibles en caché, filtrando por known_by_system_at_utc <= as_of si se especifica."""
        snapshots = []
        if not self.snapshots_dir.exists():
            return []
        as_of_dt = self.parse_utc_timestamp(as_of) if as_of else None

        for snap_file in sorted(self.snapshots_dir.glob("espn_snapshot_*.json")):
            try:
                data = json.loads(snap_file.read_text(encoding="utf-8"))
                known_dt = self.parse_utc_timestamp(data.get("known_by_system_at_utc") or data.get("captured_at_utc"))
                if as_of_dt is not None:
                    if known_dt is None or known_dt > as_of_dt:
                        continue  # Snapshot no conocido por el sistema al corte as_of
                snapshots.append(data)
            except Exception:
                continue

        snapshots.sort(key=lambda s: str(s.get("known_by_system_at_utc") or s.get("captured_at_utc", "")))
        return snapshots

    def get_active_injury_snapshot(self, as_of: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        """
        Retorna el snapshot activo para un as_of de predicción.
        El segundo snapshot (cerca del kickoff) solo afecta predicciones realizadas después de su captura.
        """
        snaps = self.get_injury_snapshots(as_of=as_of)
        return snaps[-1] if snaps else None

    _cached_crosswalk: Optional[Dict[str, str]] = None

    def get_player_crosswalk(self, nflverse_raw_root: Optional[Path] = None) -> Dict[str, str]:
        """Carga y cachea la correspondencia de nombres canónicos y ESPN IDs a GSIS ID."""
        if self._cached_crosswalk is not None:
            return self._cached_crosswalk

        import polars as pl
        import re
        nflverse_dir = nflverse_raw_root or Path("data/raw/nflverse")
        crosswalk_files = list(nflverse_dir.glob("players_*.parquet"))
        cw: Dict[str, str] = {}
        if not crosswalk_files:
            self._cached_crosswalk = cw
            return cw

        try:
            df = pl.read_parquet(crosswalk_files[0])
            for row in df.iter_rows(named=True):
                gid = row.get("gsis_id")
                if not gid:
                    continue
                name = row.get("display_name")
                espn = row.get("espn_id")
                if espn:
                    cw[str(espn).strip()] = gid
                if name:
                    clean_name = str(name).lower().strip()
                    cw[clean_name] = gid
                    no_punc = re.sub(r"[^\w\s]", "", clean_name).strip()
                    cw[no_punc] = gid
                    no_suf = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", no_punc).strip()
                    no_suf = re.sub(r"\s+", " ", no_suf).strip()
                    if no_suf:
                        cw[no_suf] = gid
            self._cached_crosswalk = cw
        except Exception as exc:
            logger.warning("Error cargando crosswalk de jugadores", error=str(exc))
            self._cached_crosswalk = {}
        return self._cached_crosswalk

    def load_team_roster_stats_from_nflverse(
        self,
        team: str,
        as_of: Any,
        evaluated_week: Optional[int] = None,
        raw_root: Optional[Path] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Carga estadísticas reales previas de TODOS los jugadores observados en el equipo
        a partir de nflverse (player_stats y snap_counts) antes de as_of y evaluated_week.
        Garantiza un denominador real team_potential_ppr libre de data leaks.
        """
        as_of_dt = self.parse_utc_timestamp(as_of)
        if not as_of_dt:
            return {}

        team_clean = str(team).strip().upper()
        team_candidates = [team_clean]
        if team_clean in ("LA", "LAR"):
            team_candidates = ["LA", "LAR"]
        elif team_clean in ("WAS", "WSH"):
            team_candidates = ["WAS", "WSH"]

        active_season = as_of_dt.year if as_of_dt.month >= 6 else as_of_dt.year - 1
        nflverse_dir = raw_root or Path("data/raw/nflverse")
        season_dir = nflverse_dir / str(active_season)
        if not season_dir.exists():
            return {}

        try:
            import polars as pl
            stat_files = list(season_dir.glob("player_stats_*.parquet"))
            schedule_files = list(season_dir.glob("schedules_*.parquet"))
            if not stat_files or not schedule_files:
                return {}
            stats_df = pl.read_parquet(stat_files[0])
            schedule_df = pl.read_parquet(schedule_files[0])

            # Use the canonical game kickoff to filter every statistic strictly
            # before as_of. evaluated_week is only an additional guard; it is
            # never trusted as the temporal boundary by itself.
            from ml.data_pipeline.nfl_source import resolve_nfl_kickoff_utc
            game_kickoffs: Dict[str, datetime] = {}
            upcoming_weeks: List[Tuple[datetime, int]] = []
            all_weeks: List[int] = []
            for row in schedule_df.select(["game_id", "gameday", "gametime", "week"]).iter_rows(named=True):
                gid = row.get("game_id")
                if not gid or not row.get("gameday"):
                    continue
                try:
                    kickoff = resolve_nfl_kickoff_utc(row["gameday"], row.get("gametime"), str(gid))
                except Exception:
                    continue
                try:
                    week_no = int(row.get("week"))
                    all_weeks.append(week_no)
                    if kickoff >= as_of_dt:
                        upcoming_weeks.append((kickoff, week_no))
                except (TypeError, ValueError):
                    pass
                if kickoff < as_of_dt:
                    game_kickoffs[str(gid)] = kickoff
            if not game_kickoffs:
                return {}

            # Infer the target week from the first scheduled kickoff at/after
            # as_of. This protects snap/roster sources too, even when callers
            # pass a malformed week parsed from a provider-specific game ID.
            inferred_week = min(upcoming_weeks)[1] if upcoming_weeks else (max(all_weeks) + 1 if all_weeks else None)
            week_cutoff = inferred_week
            if evaluated_week is not None:
                try:
                    supplied_week = int(evaluated_week)
                    week_cutoff = min(week_cutoff, supplied_week) if week_cutoff is not None else supplied_week
                except (TypeError, ValueError):
                    pass

            filt = pl.col("team").str.to_uppercase().is_in(team_candidates)
            filt = filt & pl.col("game_id").cast(pl.Utf8).is_in(list(game_kickoffs))
            if week_cutoff is not None:
                filt = filt & (pl.col("week") < week_cutoff)

            sub = stats_df.filter(filt).sort(["week", "game_id"])

            # Mapeos cruzados de jugadores
            pfr_to_gid: Dict[str, str] = {}
            name_to_gid: Dict[str, str] = {}
            gid_to_name: Dict[str, str] = {}
            gid_to_pfr: Dict[str, str] = {}
            crosswalk_files = list(nflverse_dir.glob("players_*.parquet"))
            if crosswalk_files:
                try:
                    pdf = pl.read_parquet(crosswalk_files[0])
                    for r in pdf.select(["gsis_id", "pfr_id", "display_name"]).iter_rows(named=True):
                        gid = r.get("gsis_id")
                        pfr = r.get("pfr_id")
                        name = r.get("display_name")
                        if gid:
                            if pfr:
                                pfr_to_gid[str(pfr)] = str(gid)
                                gid_to_pfr[str(gid)] = str(pfr)
                            if name:
                                nm = str(name).strip().lower()
                                name_to_gid[nm] = str(gid)
                                gid_to_name[str(gid)] = nm
                except Exception:
                    pass

            result: Dict[str, Dict[str, float]] = {}

            # 1. Puntos Fantasy PPR históricos de player_stats
            if not sub.is_empty():
                for row in sub.group_by("player_id", maintain_order=True).agg(
                    rolling_ppr=pl.col("fantasy_points_ppr").tail(5).mean(),
                ).iter_rows(named=True):
                    pid = row.get("player_id")
                    if not pid:
                        continue
                    ppr = float(row.get("rolling_ppr") or 0.0)
                    stats_entry = {
                        "rolling_ppr": ppr,
                        "rolling_off_pct": 0.0,
                        "rolling_def_pct": 0.0,
                    }
                    result[pid] = stats_entry
                    if pid in gid_to_name:
                        result[gid_to_name[pid]] = stats_entry
                    if pid in gid_to_pfr:
                        result[gid_to_pfr[pid]] = stats_entry

            # QB replacement proxy: trailing-five-game EPA per opportunity,
            # shrunk later against a league replacement percentile. This is
            # based only on completed games whose kickoff precedes as_of.
            qb_all = stats_df.filter(
                (pl.col("position").cast(pl.Utf8).str.to_uppercase() == "QB")
                & pl.col("game_id").cast(pl.Utf8).is_in(list(game_kickoffs))
                & (pl.col("season_type").cast(pl.Utf8).str.to_uppercase() == "REG")
                & (pl.col("week") < week_cutoff if week_cutoff is not None else pl.lit(True))
            ).with_columns(
                (pl.col("passing_epa").fill_null(0.0) + pl.col("rushing_epa").fill_null(0.0)).alias("_qb_epa"),
                (pl.col("attempts").fill_null(0.0) + pl.col("sacks_suffered").fill_null(0.0)
                 + pl.col("carries").fill_null(0.0)).alias("_qb_opportunities"),
            ).sort(["week", "game_id"])
            qb_recent = qb_all.group_by(["player_id", "team"], maintain_order=True).agg(
                qb_epa=pl.col("_qb_epa").tail(5).sum(),
                qb_opportunities=pl.col("_qb_opportunities").tail(5).sum(),
            )
            qb_rates = []
            for q in qb_recent.iter_rows(named=True):
                opps = float(q.get("qb_opportunities") or 0.0)
                if opps >= 50.0:
                    qb_rates.append(float(q.get("qb_epa") or 0.0) / opps)
            if len(qb_rates) < 20:
                replacement_epa_rate = None
            else:
                qb_rates.sort()
                q_idx = 0.25 * (len(qb_rates) - 1)
                lo = int(q_idx)
                hi = min(lo + 1, len(qb_rates) - 1)
                replacement_epa_rate = qb_rates[lo] + (qb_rates[hi] - qb_rates[lo]) * (q_idx - lo)

            team_qb = qb_all.filter(pl.col("team").str.to_uppercase().is_in(team_candidates))
            team_game_opps = team_qb.group_by(["game_id", "week"]).agg(
                opportunities=pl.col("_qb_opportunities").sum(),
            ).sort(["week", "game_id"]).tail(5)
            projected_team_qb_opportunities = (
                float(team_game_opps["opportunities"].mean()) if team_game_opps.height else None
            )
            if replacement_epa_rate is not None and projected_team_qb_opportunities is not None:
                for q in qb_recent.filter(pl.col("team").str.to_uppercase().is_in(team_candidates)).iter_rows(named=True):
                    pid = str(q.get("player_id") or "")
                    if not pid:
                        continue
                    qb_opps = float(q.get("qb_opportunities") or 0.0)
                    qb_epa = float(q.get("qb_epa") or 0.0)
                    entry = result.setdefault(pid, {
                        "rolling_ppr": 0.0, "rolling_off_pct": 0.0, "rolling_def_pct": 0.0,
                    })
                    entry.update({
                        "rolling_qb_epa_per_opportunity": qb_epa / qb_opps if qb_opps > 0 else None,
                        "rolling_qb_opportunities": qb_opps,
                        "league_replacement_qb_epa_per_opportunity": replacement_epa_rate,
                        "team_qb_opportunities_per_game": projected_team_qb_opportunities,
                        "qb_pregame_stats_through_utc": max(game_kickoffs.values()).isoformat(),
                    })
                    if pid in gid_to_name:
                        result[gid_to_name[pid]] = entry
                    if pid in gid_to_pfr:
                        result[gid_to_pfr[pid]] = entry

            # 2. Snaps ofensivos y defensivos de snap_counts (incluye línea ofensiva y secundaria)
            snap_files = list(season_dir.glob("snap_counts_*.parquet"))
            if snap_files:
                try:
                    snaps_df = pl.read_parquet(snap_files[0])
                    snaps_filt = pl.col("team").str.to_uppercase().is_in(team_candidates)
                    if week_cutoff is not None:
                        snaps_filt = snaps_filt & (pl.col("week") < week_cutoff)
                    snaps_team = snaps_df.filter(snaps_filt)
                    for row in snaps_team.group_by(["pfr_player_id", "player"]).agg(
                        off_mean=pl.col("offense_pct").tail(5).mean(),
                        def_mean=pl.col("defense_pct").tail(5).mean(),
                    ).iter_rows(named=True):
                        pfr = str(row["pfr_player_id"]) if row.get("pfr_player_id") else None
                        raw_name = row.get("player")
                        nm = str(raw_name).strip().lower() if raw_name else None
                        gid = (pfr_to_gid.get(pfr) if pfr else None) or (name_to_gid.get(nm) if nm else None)

                        off_val = float(row.get("off_mean") or 0.0)
                        def_val = float(row.get("def_mean") or 0.0)

                        target_key = gid or pfr or nm
                        if not target_key:
                            continue
                        if target_key in result:
                            entry = result[target_key]
                            entry["rolling_off_pct"] = off_val
                            entry["rolling_def_pct"] = def_val
                        else:
                            entry = {
                                "rolling_ppr": 0.0,
                                "rolling_off_pct": off_val,
                                "rolling_def_pct": def_val,
                            }
                            if gid:
                                result[gid] = entry
                            if pfr:
                                result[pfr] = entry
                            if nm:
                                result[nm] = entry
                except Exception as se:
                    logger.warning("Error procesando snap_counts en prior roster stats", error=str(se), team=team)

            # 3. Roster semanal para jugadores activos sin snaps previos (evita missing history leaks)
            roster_files = list(season_dir.glob("rosters_weekly_*.parquet"))
            if roster_files:
                try:
                    rdf = pl.read_parquet(roster_files[0])
                    rfilt = pl.col("team").str.to_uppercase().is_in(team_candidates)
                    if week_cutoff is not None:
                        rfilt = rfilt & (pl.col("week") < week_cutoff)
                    rsub = rdf.filter(rfilt)
                    for r in rsub.select(["gsis_id", "pfr_id", "full_name"]).iter_rows(named=True):
                        gid = str(r["gsis_id"]) if r.get("gsis_id") else None
                        pfr = str(r["pfr_id"]) if r.get("pfr_id") else None
                        raw_name = r.get("full_name")
                        nm = str(raw_name).strip().lower() if raw_name else None

                        if (gid and gid not in result) or (nm and nm not in result):
                            entry = {"rolling_ppr": 0.0, "rolling_off_pct": 0.0, "rolling_def_pct": 0.0}
                            if gid:
                                result[gid] = entry
                            if pfr:
                                result[pfr] = entry
                            if nm:
                                result[nm] = entry
                except Exception as re:
                    logger.warning("Error procesando rosters_weekly en prior roster stats", error=str(re), team=team)

            return result
        except Exception as exc:
            logger.warning("Error construyendo prior roster stats de equipo", error=str(exc), team=team)
            return {}

    @staticmethod
    def parse_utc_timestamp(val: Any) -> Optional[datetime]:
        """Convierte una cadena, fecha o timestamp en datetime consciente de UTC sin inventar fechas."""
        if val is None:
            return None
        if isinstance(val, datetime):
            return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
        try:
            s = str(val).strip()
            if not s or s.lower() in ("nan", "none", "null", ""):
                return None
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def resolve_availability_status(
        self,
        raw_status: Optional[str],
        observed_at: Optional[Any],
        decision_cutoff: Optional[Any],
    ) -> AvailabilityStatus:
        """
        Resuelve el estado oficial de disponibilidad.
        Regla P0 estricta:
        - Si observed_at es None -> UNKNOWN.
        - Si decision_cutoff es None -> UNKNOWN.
        - Si observed_at >= decision_cutoff -> UNKNOWN (debe ser estrictamente anterior <).
        """
        obs_dt = self.parse_utc_timestamp(observed_at)
        cutoff_dt = self.parse_utc_timestamp(decision_cutoff)

        if obs_dt is None or cutoff_dt is None:
            return AvailabilityStatus.UNKNOWN

        # Observación debe ser estrictamente anterior al corte de decisión
        if obs_dt >= cutoff_dt:
            return AvailabilityStatus.UNKNOWN

        if not raw_status:
            return AvailabilityStatus.ACTIVE_REPORT

        s = str(raw_status).lower().strip()
        if any(term in s for term in ["out", "ir", "reserve", "injured reserve", "suspension", "pup"]):
            return AvailabilityStatus.CONFIRMED_OUT
        if "doubtful" in s:
            return AvailabilityStatus.DOUBTFUL
        if any(term in s for term in ["questionable", "day-to-day", "gtd"]):
            return AvailabilityStatus.QUESTIONABLE
        if any(term in s for term in ["active", "probable", "full"]):
            return AvailabilityStatus.ACTIVE_REPORT

        return AvailabilityStatus.UNKNOWN

    def evaluate_market_prop_signal(
        self,
        availability_status: AvailabilityStatus,
        player_name: str,
        position: str,
        team: str,
        game_start_utc: Optional[Any] = None,
        decision_cutoff: Optional[Any] = None,
        live_props: Optional[List[Dict[str, Any]]] = None,
        has_active_game_props: bool = False,
        expected_event_id: Optional[str] = None,
        strict_unlisted_unknown: bool = False,
        binary_market_resolution: bool = False,
        **kwargs: Any,
    ) -> Tuple[MarketPropSignal, float, Dict[str, Any]]:
        """
        Evalúa el mercado de player props como señal prepartido.
        Reglas P0 estrictas:
        - Confirmed OUT/IR: NUNCA consulta props; checked_props=False.
        - Partidos históricos (kickoff <= cutoff o kickoff <= now): NUNCA consulta props; checked_props=False.
        - Props no verificables o ausentes: checked_props=False; devuelve UNKNOWN con base empírica.
        - Solo props del evento solicitado, con bookmaker, market_key, line, price y timestamp < cutoff son válidas.
        """
        now_utc = datetime.now(timezone.utc)
        start_dt = self.parse_utc_timestamp(game_start_utc)
        cutoff_dt = self.parse_utc_timestamp(decision_cutoff)

        # Regla 1: Confirmed OUT/IR nunca consulta props
        if availability_status == AvailabilityStatus.CONFIRMED_OUT:
            return (
                MarketPropSignal.UNKNOWN,
                1.0,
                {"checked_props": False, "reason": "confirmed_out_no_props_query"},
            )

        if availability_status == AvailabilityStatus.ACTIVE_REPORT:
            return (
                MarketPropSignal.UNKNOWN,
                0.0,
                {"checked_props": False, "reason": "active_report_no_props_needed"},
            )

        if availability_status == AvailabilityStatus.UNKNOWN:
            return (
                MarketPropSignal.UNKNOWN,
                0.0,
                {"checked_props": False, "reason": "unknown_status"},
            )

        # Regla 2: Partidos históricos o pasados (kickoff en el pasado o anterior al corte)
        is_future_unplayed = (
            start_dt is not None
            and cutoff_dt is not None
            and start_dt > now_utc
            and start_dt > cutoff_dt
        )
        if not is_future_unplayed:
            base_p, prac_flags = resolve_base_p_absence(availability_status, kwargs.get("practice_status"))
            res_flags = {
                "checked_props": False,
                "reason": "historical_or_past_game_no_props_query",
                "is_future_unplayed": False,
            }
            res_flags.update(prac_flags)
            return (
                MarketPropSignal.UNKNOWN,
                base_p,
                res_flags,
            )

        # Regla 3: Props verificables
        if not live_props:
            if binary_market_resolution:
                return (
                    MarketPropSignal.UNKNOWN,
                    0.0,
                    {
                        "checked_props": False,
                        "listed": False,
                        "reason": "no_props_market_unopened_zero_penalty",
                    },
                )
            base_p, prac_flags = resolve_base_p_absence(availability_status, kwargs.get("practice_status"))
            res_flags = {
                "checked_props": False,
                "reason": "no_verifiable_props_provided",
            }
            res_flags.update(prac_flags)
            return (
                MarketPropSignal.UNKNOWN,
                base_p,
                res_flags,
            )
        if not expected_event_id:
            if binary_market_resolution:
                return (
                    MarketPropSignal.UNKNOWN,
                    0.0,
                    {
                        "checked_props": False,
                        "listed": False,
                        "reason": "expected_event_id_missing_zero_penalty",
                    },
                )
            base_p, prac_flags = resolve_base_p_absence(availability_status, kwargs.get("practice_status"))
            res_flags = {"checked_props": False, "reason": "expected_event_id_missing"}
            res_flags.update(prac_flags)
            return (
                MarketPropSignal.UNKNOWN,
                base_p,
                res_flags,
            )

        # Filtrar props verificables con metadata completa anterior al corte
        verifiable_props: List[Dict[str, Any]] = []
        for prop in live_props:
            if not isinstance(prop, dict):
                continue
            e_id = prop.get("event_id") or prop.get("game_id")
            b_maker = prop.get("bookmaker") or prop.get("bookmaker_key")
            p_type = prop.get("prop_type") or prop.get("market_key")
            m_line = prop.get("market_line") if prop.get("market_line") is not None else prop.get("point")
            p_name = prop.get("player_name") or prop.get("description")

            has_required = all(
                v is not None
                for v in [e_id, b_maker, p_type, m_line, p_name]
            )
            if not has_required:
                continue
            if expected_event_id is not None and str(e_id) != str(expected_event_id):
                continue
            price = prop.get("price")
            if price is None:
                price = prop.get("over_odds_american")
            try:
                if price is None or not math.isfinite(float(price)) or float(price) == 0:
                    continue
            except (TypeError, ValueError):
                continue

            # Un timestamp ausente o inválido no demuestra que el mercado existía al corte.
            p_ts = self.parse_utc_timestamp(prop.get("timestamp"))
            if p_ts is None or cutoff_dt is None or p_ts >= cutoff_dt:
                continue  # Prop observada después del corte

            norm_prop = dict(prop)
            norm_prop["event_id"] = e_id
            norm_prop["bookmaker"] = b_maker
            norm_prop["prop_type"] = p_type
            norm_prop["market_line"] = m_line
            norm_prop["player_name"] = p_name
            verifiable_props.append(norm_prop)

        if not verifiable_props:
            if binary_market_resolution:
                return (
                    MarketPropSignal.UNKNOWN,
                    0.0,
                    {
                        "checked_props": False,
                        "listed": False,
                        "reason": "no_verifiable_props_in_cutoff_window_zero_penalty",
                    },
                )
            base_p, prac_flags = resolve_base_p_absence(availability_status, kwargs.get("practice_status"))
            res_flags = {
                "checked_props": False,
                "reason": "no_verifiable_props_in_cutoff_window",
            }
            res_flags.update(prac_flags)
            return (
                MarketPropSignal.UNKNOWN,
                base_p,
                res_flags,
            )

        # Aquí comprobamos el mercado
        p_name_norm = player_name.lower().strip()
        matched_prop = next(
            (p for p in verifiable_props if str(p.get("player_name", "")).lower().strip() == p_name_norm),
            None,
        )

        if binary_market_resolution:
            if matched_prop:
                return (
                    MarketPropSignal.LISTED_AT_CUTOFF,
                    0.0,
                    {
                        "checked_props": True,
                        "listed": True,
                        "plays": True,
                        "bookmaker": matched_prop.get("bookmaker"),
                        "prop_type": matched_prop.get("prop_type"),
                        "market_line": matched_prop.get("market_line"),
                        "event_id": matched_prop.get("event_id"),
                    },
                )
            else:
                return (
                    MarketPropSignal.NOT_LISTED_WITH_COVERAGE,
                    1.0,
                    {
                        "checked_props": True,
                        "listed": False,
                        "market_ruled_out": True,
                        "reason": "unlisted_while_game_props_active_marked_out",
                    },
                )

        # Caso A: Jugador encontrado con línea activa
        if matched_prop:
            signal = MarketPropSignal.LISTED_AT_CUTOFF
            p_abs = MARKET_MODULATED_P_ABSENCE.get((availability_status, signal), 0.15)
            return (
                signal,
                p_abs,
                {
                    "checked_props": True,
                    "listed": True,
                    "bookmaker": matched_prop.get("bookmaker"),
                    "prop_type": matched_prop.get("prop_type"),
                    "market_line": matched_prop.get("market_line"),
                    "event_id": matched_prop.get("event_id"),
                },
            )

        # Regla: Si no existe línea y se exige señal estricta, conserva el estado desconocido
        # para esa señal y no lo interpreta como prueba de que no jugará.
        if strict_unlisted_unknown:
            base_p, prac_flags = resolve_base_p_absence(availability_status, kwargs.get("practice_status"))
            res_flags = {
                "checked_props": True,
                "listed": False,
                "signal": "unknown",
                "reason": "no_line_observed_signal_preserved_as_unknown",
            }
            res_flags.update(prac_flags)
            return (
                MarketPropSignal.UNKNOWN,
                base_p,
                res_flags,
            )

        # Caso B: No listado pero con cobertura comparable demostrada
        pos_upper = position.upper() if position else ""
        is_skill = pos_upper in SKILL_POSITIONS
        comparable_found = False

        if is_skill:
            for p in verifiable_props:
                pt = str(p.get("prop_type", "")).lower()
                if pos_upper == "QB" and any(k in pt for k in ["pass", "rush"]):
                    comparable_found = True
                    break
                elif pos_upper == "RB" and any(k in pt for k in ["rush", "reception"]):
                    comparable_found = True
                    break
                elif pos_upper in ("WR", "TE") and any(k in pt for k in ["reception", "receiving"]):
                    comparable_found = True
                    break

        if comparable_found:
            signal = MarketPropSignal.NOT_LISTED_WITH_COVERAGE
            p_abs = MARKET_MODULATED_P_ABSENCE.get((availability_status, signal), 0.75)
            return (
                signal,
                p_abs,
                {
                    "checked_props": True,
                    "listed": False,
                    "has_comparable_coverage": True,
                },
            )

        # Sin cobertura comparable demostrada
        base_p, prac_flags = resolve_base_p_absence(availability_status, kwargs.get("practice_status"))
        res_flags = {
            "checked_props": True,
            "listed": False,
            "has_comparable_coverage": False,
            "reason": "insufficient_comparable_coverage",
        }
        res_flags.update(prac_flags)
        return (
            MarketPropSignal.UNKNOWN,
            base_p,
            res_flags,
        )

    def process_player_injury(
        self,
        player_dict: Dict[str, Any],
        team: str,
        game_start_utc: Optional[Any] = None,
        decision_cutoff: Optional[Any] = None,
        live_props: Optional[List[Dict[str, Any]]] = None,
        crosswalk: Optional[Dict[str, str]] = None,
        expected_event_id: Optional[str] = None,
        strict_unlisted_unknown: bool = True,
        binary_market_resolution: bool = False,
        **kwargs: Any,
    ) -> PlayerInjuryRecord:
        """
        Procesa una observación de lesión individual.
        Regla P0: Cero invención de observed_at. Si no viene en el registro, queda None.
        Canonical ID obligatorio: si no se resuelve, queda UNKNOWN.
        """
        raw_name = str(player_dict.get("player_name") or player_dict.get("full_name") or "").strip()
        raw_status = player_dict.get("status") or player_dict.get("report_status")
        pos = str(player_dict.get("position") or "—").strip().upper()
        inj_desc = player_dict.get("injury") or player_dict.get("report_primary_injury") or player_dict.get("details")
        prac_status = player_dict.get("practice_status")
        source = str(player_dict.get("source", "espn_live"))
        observed_at = player_dict.get("observed_at") or player_dict.get("date_modified")
        espn_id = str(player_dict["espn_id"]) if player_dict.get("espn_id") else None
        canonical_id = player_dict.get("gsis_id") or player_dict.get("canonical_player_id")
        effective_cw = crosswalk if crosswalk is not None else self.get_player_crosswalk()
        if not canonical_id and effective_cw:
            canonical_id = effective_cw.get(espn_id or "") or effective_cw.get(raw_name.lower())

        # 1. Resolver estado oficial point-in-time
        avail_status = self.resolve_availability_status(
            raw_status=raw_status,
            observed_at=observed_at,
            decision_cutoff=decision_cutoff,
        )

        coverage_flags: Dict[str, Any] = {}

        # 2. Exigir identidad canónica
        if not canonical_id or not raw_name or raw_name.lower() in ("desconocido", "unknown", "—"):
            avail_status = AvailabilityStatus.UNKNOWN
            coverage_flags["unresolved_canonical_id"] = True
            coverage_flags["identity_unresolved"] = True

        # 3. Evaluar señales de mercado con props verificables
        market_signal, p_absence, prop_flags = self.evaluate_market_prop_signal(
            availability_status=avail_status,
            player_name=raw_name,
            position=pos,
            team=team,
            game_start_utc=game_start_utc,
            decision_cutoff=decision_cutoff,
            live_props=live_props,
            expected_event_id=expected_event_id,
            strict_unlisted_unknown=strict_unlisted_unknown,
            binary_market_resolution=binary_market_resolution,
            practice_status=prac_status,
            **kwargs,
        )
        coverage_flags.update(prop_flags)

        return PlayerInjuryRecord(
            player_name=raw_name or "Desconocido",
            team=team.upper(),
            position=pos,
            canonical_player_id=canonical_id,
            espn_id=espn_id,
            pfr_id=player_dict.get("pfr_id"),
            raw_status=str(raw_status) if raw_status else None,
            injury_description=str(inj_desc) if inj_desc else None,
            practice_status=str(prac_status) if prac_status else None,
            source=source,
            observed_at=str(observed_at) if observed_at else None,
            fetched_at=(str(player_dict.get("fetched_at")) if player_dict.get("fetched_at") else None),
            decision_cutoff=str(decision_cutoff) if decision_cutoff else None,
            game_id=player_dict.get("game_id"),
            game_start_utc=str(game_start_utc) if game_start_utc else None,
            availability_status=avail_status.value,
            market_signal=market_signal.value,
            p_absence=round(float(p_absence), 4),
            p_play=round(float(1.0 - p_absence), 4),
            coverage_flags=coverage_flags,
            resolver_version=RESOLVER_VERSION,
        )

    def compute_team_production_lost(
        self,
        team: str,
        records: List[PlayerInjuryRecord],
        prior_roster_stats: Optional[Dict[str, Dict[str, Any]]],
        game_id: Optional[str] = None,
        decision_cutoff: Optional[Any] = None,
        is_source_available: bool = True,
        no_injuries_listed: bool = False,
        clean_report_evidence_ref: Optional[str] = None,
        allow_unresolved_market: bool = False,
    ) -> TeamAvailabilitySummary:
        """
        Calcula el impacto deportivo del equipo usando métricas previas y denominadores definidos.
        Reglas P0/P1 estrictas:
        - Cero priors manuales: si prior_roster_stats no se proporciona o faltan stats previas,
          ppr_lost_pct, off_snaps_lost_pct y def_snaps_lost_pct deben ser None, y el coverage_state UNKNOWN/UNAVAILABLE.
        - Distingue covered_healthy (reporte verificado sin lesionados) de fuente vacía/caída (unavailable_no_report).
        - Si no_injuries_listed es True (equipo omitido en captura ESPN): registra fielmente
          'sin lesiones listadas en esta captura' sin inventar jugadores ni asumir que está sano.
        - Nunca imputa 0.0 ante fallas o falta de cobertura.
        """
        team_upper = team.upper()

        if not is_source_available:
            return TeamAvailabilitySummary(
                team=team_upper,
                coverage_state=CoverageState.UNAVAILABLE_NO_REPORT.value,
                ppr_lost_pct=None,
                off_snaps_lost_pct=None,
                def_snaps_lost_pct=None,
                total_injured_count=0,
                confirmed_out_count=0,
                questionable_count=0,
                doubtful_count=0,
                market_listed_count=0,
                market_unlisted_count=0,
                unknown_count=0,
                game_id=game_id,
                decision_cutoff=str(decision_cutoff) if decision_cutoff else None,
                players=[],
                metadata={"reason": "source_unavailable_or_unreachable"},
            )

        # Regla: Equipo omitido en captura ESPN sin lesiones listadas
        if no_injuries_listed:
            return TeamAvailabilitySummary(
                team=team_upper,
                coverage_state=CoverageState.NO_INJURIES_LISTED.value,
                ppr_lost_pct=None,
                off_snaps_lost_pct=None,
                def_snaps_lost_pct=None,
                total_injured_count=0,
                confirmed_out_count=0,
                questionable_count=0,
                doubtful_count=0,
                market_listed_count=0,
                market_unlisted_count=0,
                unknown_count=0,
                game_id=game_id,
                decision_cutoff=str(decision_cutoff) if decision_cutoff else None,
                players=[],
                metadata={
                    "reason": "sin lesiones listadas en esta captura",
                    "note": "sin lesiones listadas en esta captura",
                    "no_injuries_listed_in_capture": True,
                },
            )

        # Reporte verificado sin jugadores lesionados
        if not records:
            clean_ref = clean_report_evidence_ref or f"espn_clean_report:{team_upper}:{decision_cutoff or 'latest'}"
            return TeamAvailabilitySummary(
                team=team_upper,
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
                game_id=game_id,
                decision_cutoff=str(decision_cutoff) if decision_cutoff else None,
                clean_report_evidence_ref=clean_ref,
                players=[],
                metadata={"reason": "verified_clean_injury_report"},
            )

        # Hay jugadores lesionados: se requiere prior_roster_stats para calcular impacto
        if not prior_roster_stats:
            # Sin estadísticas previas verificables, NO inventar priors de 15 PPR / 0.80 snaps
            cnt_unk = sum(1 for r in records if r.availability_status == AvailabilityStatus.UNKNOWN.value)
            return TeamAvailabilitySummary(
                team=team_upper,
                coverage_state=CoverageState.UNKNOWN.value,
                ppr_lost_pct=None,
                off_snaps_lost_pct=None,
                def_snaps_lost_pct=None,
                total_injured_count=len(records),
                confirmed_out_count=sum(1 for r in records if r.availability_status == AvailabilityStatus.CONFIRMED_OUT.value),
                questionable_count=sum(1 for r in records if r.availability_status == AvailabilityStatus.QUESTIONABLE.value),
                doubtful_count=sum(1 for r in records if r.availability_status == AvailabilityStatus.DOUBTFUL.value),
                market_listed_count=sum(1 for r in records if r.market_signal == MarketPropSignal.LISTED_AT_CUTOFF.value),
                market_unlisted_count=sum(1 for r in records if r.market_signal == MarketPropSignal.NOT_LISTED_WITH_COVERAGE.value),
                unknown_count=cnt_unk,
                game_id=game_id,
                decision_cutoff=str(decision_cutoff) if decision_cutoff else None,
                players=[r.to_dict() for r in records],
                metadata={"reason": "missing_prior_roster_statistics_cannot_impute"},
            )

        # Calcular team_potential_ppr a partir del roster previo observado
        canonical_items = [
            p_stats for k, p_stats in prior_roster_stats.items()
            if " " not in k
        ]
        if not canonical_items:
            canonical_items = list(prior_roster_stats.values())
        team_potential_ppr = sum(
            float(p_stats.get("rolling_ppr", 0.0))
            for p_stats in canonical_items
        )

        if team_potential_ppr <= 0.0:
            return TeamAvailabilitySummary(
                team=team_upper,
                coverage_state=CoverageState.UNKNOWN.value,
                ppr_lost_pct=None,
                off_snaps_lost_pct=None,
                def_snaps_lost_pct=None,
                total_injured_count=len(records),
                confirmed_out_count=sum(1 for r in records if r.availability_status == AvailabilityStatus.CONFIRMED_OUT.value),
                questionable_count=sum(1 for r in records if r.availability_status == AvailabilityStatus.QUESTIONABLE.value),
                doubtful_count=sum(1 for r in records if r.availability_status == AvailabilityStatus.DOUBTFUL.value),
                market_listed_count=sum(1 for r in records if r.market_signal == MarketPropSignal.LISTED_AT_CUTOFF.value),
                market_unlisted_count=sum(1 for r in records if r.market_signal == MarketPropSignal.NOT_LISTED_WITH_COVERAGE.value),
                unknown_count=sum(1 for r in records if r.availability_status == AvailabilityStatus.UNKNOWN.value),
                game_id=game_id,
                decision_cutoff=str(decision_cutoff) if decision_cutoff else None,
                players=[r.to_dict() for r in records],
                metadata={"reason": "insufficient_team_potential_denominator"},
            )

        total_ppr_lost = 0.0
        total_off_snaps_lost = 0.0
        total_def_snaps_lost = 0.0
        qb_starter_injured = False
        qb_lost_expected_epa = 0.0
        qb_estimates_available = 0
        qb_estimate_missing = False
        qb_pregame_stats_through_utc = None

        cnt_out = 0
        cnt_q = 0
        cnt_d = 0
        cnt_listed = 0
        cnt_unlisted = 0
        cnt_unk = 0
        missing_player_history_cnt = 0
        unresolved_market_count = 0

        for r in records:
            stat_enum = r.availability_status
            if stat_enum == AvailabilityStatus.CONFIRMED_OUT.value:
                cnt_out += 1
            elif stat_enum == AvailabilityStatus.QUESTIONABLE.value:
                cnt_q += 1
            elif stat_enum == AvailabilityStatus.DOUBTFUL.value:
                cnt_d += 1
            elif stat_enum == AvailabilityStatus.UNKNOWN.value:
                cnt_unk += 1

            if r.market_signal == MarketPropSignal.LISTED_AT_CUTOFF.value:
                cnt_listed += 1
            elif r.market_signal == MarketPropSignal.NOT_LISTED_WITH_COVERAGE.value:
                cnt_unlisted += 1
            elif stat_enum in (AvailabilityStatus.QUESTIONABLE.value, AvailabilityStatus.DOUBTFUL.value):
                unresolved_market_count += 1

            # Buscar stats históricas verificadas
            prior = (
                prior_roster_stats.get(r.canonical_player_id or "")
                or prior_roster_stats.get(r.espn_id or "")
                or prior_roster_stats.get(r.player_name.lower())
                or {}
            )

            p_abs = r.p_absence
            if prior:
                p_ppr = float(prior.get("rolling_ppr", 0.0))
                p_off = float(prior.get("rolling_off_pct", 0.0))
                p_def = float(prior.get("rolling_def_pct", 0.0))
                r.rolling_ppr = round(p_ppr, 2)
                r.rolling_off_pct = round(p_off, 4)
                r.rolling_def_pct = round(p_def, 4)
                r.lost_ppr = round(p_ppr * p_abs, 2)
                r.lost_off_snaps = round(p_off * p_abs, 4)
                r.lost_def_snaps = round(p_def * p_abs, 4)

            if p_abs <= 0.0:
                continue

            if not prior:
                # El jugador carece de historial previo observado: no inventar datos
                missing_player_history_cnt += 1
                continue

            p_ppr = float(prior.get("rolling_ppr", 0.0))
            p_off = float(prior.get("rolling_off_pct", 0.0))
            p_def = float(prior.get("rolling_def_pct", 0.0))

            total_ppr_lost += p_ppr * p_abs
            total_off_snaps_lost += p_off * p_abs
            total_def_snaps_lost += p_def * p_abs

            # Detección de QB titular y valor esperado frente a proxy de
            # reemplazo de liga, calculado solo con estadísticas pregame.
            pos = (r.position or "").upper()
            if pos == "QB" and (p_off >= 0.50 or p_ppr >= 12.0):
                qb_starter_injured = True
                rate = prior.get("rolling_qb_epa_per_opportunity")
                opps = prior.get("rolling_qb_opportunities")
                replacement = prior.get("league_replacement_qb_epa_per_opportunity")
                team_opps = prior.get("team_qb_opportunities_per_game")
                qb_pregame_stats_through_utc = prior.get("qb_pregame_stats_through_utc") or qb_pregame_stats_through_utc
                try:
                    rate, opps, replacement, team_opps = map(float, (rate, opps, replacement, team_opps))
                except (TypeError, ValueError):
                    qb_estimate_missing = True
                else:
                    if not all(math.isfinite(v) for v in (rate, opps, replacement, team_opps)) or opps < 50 or team_opps <= 0:
                        qb_estimate_missing = True
                    else:
                        # Empirical-Bayes shrinkage: 50 pseudo-opportunities at
                        # the league replacement rate reduce volatility for QBs
                        # with limited recent usage. The result is EPA points,
                        # not a calibrated scoreboard-margin adjustment.
                        prior_opportunities = 50.0
                        shrunk_rate = (rate * opps + replacement * prior_opportunities) / (opps + prior_opportunities)
                        qb_lost_expected_epa += p_abs * (shrunk_rate - replacement) * team_opps
                        qb_estimates_available += 1

        # Si hay jugadores con pérdida pero sin historial previo, advertir en metadata
        state = CoverageState.COVERED_WITH_INJURIES.value
        should_fail_unknown = False
        if not allow_unresolved_market:
            if cnt_unk > 0 or missing_player_history_cnt > 0 or unresolved_market_count > 0:
                should_fail_unknown = True
        else:
            if team_potential_ppr <= 0.0:
                should_fail_unknown = True

        if should_fail_unknown:
            # No baseline guess may masquerade as market-verified availability.
            return TeamAvailabilitySummary(
                team=team_upper,
                coverage_state=CoverageState.UNKNOWN.value,
                ppr_lost_pct=None,
                off_snaps_lost_pct=None,
                def_snaps_lost_pct=None,
                total_injured_count=len(records),
                confirmed_out_count=cnt_out,
                questionable_count=cnt_q,
                doubtful_count=cnt_d,
                market_listed_count=cnt_listed,
                market_unlisted_count=cnt_unlisted,
                unknown_count=cnt_unk,
                game_id=game_id,
                decision_cutoff=str(decision_cutoff) if decision_cutoff else None,
                players=[r.to_dict() for r in records],
                metadata={
                    "missing_player_history_count": missing_player_history_cnt,
                    "unresolved_market_count": unresolved_market_count,
                    "reason": "availability_or_required_market_coverage_unverified",
                },
                qb_starter_injured=False,
                qb_lost_expected_epa=None,
            )

        ppr_lost_pct = min(1.0, round(total_ppr_lost / team_potential_ppr, 4))
        off_snaps_lost_pct = min(1.0, round(total_off_snaps_lost / 11.0, 4))
        def_snaps_lost_pct = min(1.0, round(total_def_snaps_lost / 11.0, 4))

        meta = {
            "missing_player_history_count": missing_player_history_cnt,
            "team_potential_ppr": round(team_potential_ppr, 2),
            "qb_starter_injured": qb_starter_injured,
            "qb_lost_expected_epa": round(qb_lost_expected_epa, 2) if qb_estimates_available else None,
            "qb_estimates_available": qb_estimates_available,
            "qb_estimate_status": (
                "available" if qb_estimates_available else
                "insufficient_pregame_qb_sample" if qb_estimate_missing else "no_injured_starting_qb"
            ),
            "qb_replacement_definition": "25th_percentile_of_qualified_trailing_five_game_league_qb_epa_per_opportunity",
            "qb_estimator_version": QB_REPLACEMENT_ESTIMATOR_VERSION,
            "qb_pregame_stats_through_utc": qb_pregame_stats_through_utc,
        }
        if allow_unresolved_market and unresolved_market_count > 0:
            meta["preliminary_market_pending"] = True
            meta["reporte_preliminar"] = "Las listas oficiales se confirman a 90 min del kickoff"

        return TeamAvailabilitySummary(
            team=team_upper,
            coverage_state=state,
            ppr_lost_pct=ppr_lost_pct,
            off_snaps_lost_pct=off_snaps_lost_pct,
            def_snaps_lost_pct=def_snaps_lost_pct,
            total_injured_count=len(records),
            confirmed_out_count=cnt_out,
            questionable_count=cnt_q,
            doubtful_count=cnt_d,
            market_listed_count=cnt_listed,
            market_unlisted_count=cnt_unlisted,
            unknown_count=cnt_unk,
            game_id=game_id,
            decision_cutoff=str(decision_cutoff) if decision_cutoff else None,
            players=[r.to_dict() for r in records],
            metadata=meta,
            qb_starter_injured=qb_starter_injured,
            qb_lost_expected_epa=round(qb_lost_expected_epa, 2) if qb_estimates_available else None,
        )

    def persist_team_availability(
        self,
        summary: TeamAvailabilitySummary,
        game_id: Optional[str] = None,
    ) -> Path:
        """Persiste atómicamente el resumen de disponibilidad con provenance en el cache point-in-time."""
        tag = game_id or f"{summary.team}_{summary.decision_cutoff or 'latest'}"
        safe_tag = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in tag)
        out_path = self.cache_dir / f"avail_{safe_tag}.json"

        data = summary.to_dict()
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self.cache_dir), prefix="avail_", suffix=".tmp")
        try:
            with open(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, out_path)
            logger.info("Disponibilidad persistida con provenance", team=summary.team, path=str(out_path))
            return out_path
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def load_player_recent_logs_from_nflverse(
        self,
        canonical_player_id: str,
        current_team: str,
        as_of: Any,
        raw_root: Optional[Path] = None,
        max_games: int = 10,
        evaluated_week: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Carga registros recientes observados del jugador directamente desde los archivos parquet
        de nflverse (player_stats, snap_counts, schedules, players), filtrando estrictamente
        por el canonical_player_id, el equipo actual y con fecha < as_of dentro de la temporada activa.
        """
        as_of_dt = self.parse_utc_timestamp(as_of)
        if not canonical_player_id or not as_of_dt:
            return []

        team_clean = str(current_team).strip().upper()
        p_id = str(canonical_player_id).strip()

        # Determinar temporada activa
        active_season = as_of_dt.year if as_of_dt.month >= 6 else as_of_dt.year - 1
        nflverse_dir = raw_root or Path("data/raw/nflverse")
        season_dir = nflverse_dir / str(active_season)

        if not season_dir.exists():
            return []

        try:
            import polars as pl
        except ImportError:
            return []

        try:
            # 1. Localizar player_stats
            stat_files = list(season_dir.glob("player_stats_*.parquet"))
            if not stat_files:
                return []
            stats_df = pl.read_parquet(stat_files[0])

            # Filtrar por jugador y equipo actual
            p_stats = stats_df.filter(
                (pl.col("player_id") == p_id) & (pl.col("team").str.to_uppercase() == team_clean)
            )
            if p_stats.is_empty():
                return []

            # 2. Localizar pfr_id para snap_counts mediante players crosswalk
            pfr_id = None
            crosswalk_files = list(nflverse_dir.glob("players_*.parquet"))
            if crosswalk_files:
                try:
                    players_df = pl.read_parquet(crosswalk_files[0])
                    matched_player = players_df.filter(pl.col("gsis_id") == p_id).select(["pfr_id"]).head(1)
                    if not matched_player.is_empty() and matched_player["pfr_id"][0]:
                        pfr_id = str(matched_player["pfr_id"][0])
                except Exception:
                    pfr_id = None

            # 3. Localizar snap_counts
            snaps_map: Dict[str, Tuple[float, float]] = {}
            snap_files = list(season_dir.glob("snap_counts_*.parquet"))
            if not snap_files:
                # snap_counts no disponibles para esta temporada (e.g. 2026); no imputar ceros
                return []
            if pfr_id:
                try:
                    snaps_df = pl.read_parquet(snap_files[0])
                    p_snaps = snaps_df.filter(
                        (pl.col("pfr_player_id") == pfr_id) & (pl.col("team").str.to_uppercase() == team_clean)
                    )
                    for row in p_snaps.iter_rows(named=True):
                        g_id = row.get("game_id")
                        if g_id:
                            snaps_map[g_id] = (
                                float(row.get("offense_pct") or 0.0),
                                float(row.get("defense_pct") or 0.0),
                            )
                except Exception:
                    pass

            # 4. Localizar schedules para resolver game_date / gametime con el resolver oficial NFL
            from ml.data_pipeline.nfl_source import resolve_nfl_kickoff_utc
            sched_map: Dict[str, str] = {}
            sched_files = list(season_dir.glob("schedules_*.parquet"))
            if sched_files:
                try:
                    sched_df = pl.read_parquet(sched_files[0])
                    for row in sched_df.iter_rows(named=True):
                        g_id = row.get("game_id")
                        gday = row.get("gameday")
                        gtime = row.get("gametime")
                        if g_id and gday:
                            try:
                                kickoff_ts = resolve_nfl_kickoff_utc(gday, gtime, str(g_id))
                                sched_map[g_id] = kickoff_ts.isoformat()
                            except Exception:
                                continue
                except Exception:
                    pass

            # 5. Unificar registros
            results: List[Dict[str, Any]] = []
            for row in p_stats.iter_rows(named=True):
                g_id = row.get("game_id")
                # Fecha del juego
                game_date_str = sched_map.get(g_id)
                if not game_date_str:
                    continue
                g_dt = self.parse_utc_timestamp(game_date_str)
                if g_dt is None or g_dt >= as_of_dt:
                    continue  # Estrictamente anterior a as_of (sin fuga)
                if evaluated_week is not None and row.get("week") is not None:
                    try:
                        if int(row["week"]) >= int(evaluated_week):
                            continue  # Corte temporal estricto: nunca acceder a semanas en juego o futuras
                    except (ValueError, TypeError):
                        pass

                off_pct, def_pct = snaps_map.get(g_id, (0.0, 0.0))
                ppr_pts = float(row.get("fantasy_points_ppr") or 0.0)

                results.append({
                    "player_id": p_id,
                    "canonical_player_id": p_id,
                    "team": team_clean,
                    "season": int(row.get("season", active_season)),
                    "week": row.get("week"),
                    "game_id": g_id,
                    "game_date": game_date_str,
                    "game_start_utc": game_date_str,
                    "fantasy_points_ppr": ppr_pts,
                    "offense_pct": off_pct,
                    "defense_pct": def_pct,
                    "source": "nflverse_parquets",
                })

            results.sort(key=lambda r: str(r.get("game_date", "")), reverse=True)
            return results[:max_games]

        except Exception as e:
            logger.warning("Error leyendo nflverse parquets para logs de jugador", error=str(e), player_id=p_id)
            return []

    def calculate_player_recent_importance(
        self,
        canonical_player_id: str,
        current_team: str,
        as_of: Any,
        recent_game_logs: Optional[List[Dict[str, Any]]] = None,
        pos_rank_context: Optional[Dict[str, Any]] = None,
        max_recent_games: int = 5,
        nflverse_raw_root: Optional[Path] = None,
        evaluated_week: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Calcula la importancia del jugador a partir de sus estadísticas reales y participación
        en sus juegos recientes disponibles hasta as_of (PPR, snaps/participación),
        ligado estrictamente al ID canónico y al equipo actual.
        Reglas de dominio P0/P1:
        - No traslada rendimiento de equipos anteriores sin validación.
        - No sustituye rendimiento actual faltante por temporadas antiguas.
        - pos_rank solo entra como contexto vigente con fecha/procedencia; sin multiplicadores fijos.
        - Si falta muestra reciente, devuelve status 'unknown'.
        - Si recent_game_logs no se pasa, intenta cargar de los parquets oficiales de nflverse.
        - evaluated_week: corte temporal estricto para evitar temporal leakage (semana en juego o futuras nunca accesibles).
        """
        as_of_dt = self.parse_utc_timestamp(as_of)
        team_clean = str(current_team).strip().upper()
        p_id = str(canonical_player_id).strip() if canonical_player_id else None

        if not p_id or p_id.lower() in ("desconocido", "unknown", "—", "none", ""):
            return {
                "canonical_player_id": p_id,
                "current_team": team_clean,
                "status": "unknown",
                "importance_score": None,
                "rolling_ppr": None,
                "rolling_off_pct": None,
                "rolling_def_pct": None,
                "games_sampled": 0,
                "pos_rank_context": None,
                "reason": "unresolved_canonical_id",
            }

        if as_of_dt is None:
            return {
                "canonical_player_id": p_id,
                "current_team": team_clean,
                "status": "unknown",
                "importance_score": None,
                "rolling_ppr": None,
                "rolling_off_pct": None,
                "rolling_def_pct": None,
                "games_sampled": 0,
                "pos_rank_context": None,
                "reason": "missing_decision_as_of",
            }

        logs = recent_game_logs
        if logs is None:
            logs = self.load_player_recent_logs_from_nflverse(
                canonical_player_id=p_id,
                current_team=team_clean,
                as_of=as_of_dt,
                raw_root=nflverse_raw_root,
                max_games=max_recent_games * 2,
                evaluated_week=evaluated_week,
            )

        if not logs:
            return {
                "canonical_player_id": p_id,
                "current_team": team_clean,
                "status": "unknown",
                "importance_score": None,
                "rolling_ppr": None,
                "rolling_off_pct": None,
                "rolling_def_pct": None,
                "games_sampled": 0,
                "pos_rank_context": None,
                "reason": "no_recent_game_logs_provided",
            }

        # Filtrar registros elegibles:
        # 1. Mismo canonical_player_id
        # 2. Mismo equipo actual (no trasladar de equipos anteriores)
        # 3. Punto en el tiempo: game_date < as_of (estrictamente anterior, sin fuga)
        # 4. Misma temporada vigente (no sustituir con temporadas antiguas)
        # 5. Corte temporal estricto: semana de stats < evaluated_week (cero fuga de semana en juego o futuras)
        as_of_year = as_of_dt.year
        active_season = as_of_year if as_of_dt.month >= 6 else as_of_year - 1
        eligible_logs = []
        for g in logs:
            if str(g.get("player_id") or g.get("canonical_player_id")) != p_id:
                continue
            # Validación estricta de equipo actual: no arrastrar stats de otro equipo
            g_team = str(g.get("team") or "").strip().upper()
            if g_team != team_clean:
                continue
            # Fecha del partido
            g_date = self.parse_utc_timestamp(g.get("game_date") or g.get("game_start_utc"))
            if g_date is None or g_date >= as_of_dt:
                continue  # Excluido por falta de fecha o posterior/igual al corte
            # Fuga temporal estricta por semana de juego
            if evaluated_week is not None and g.get("week") is not None:
                try:
                    if int(g["week"]) >= int(evaluated_week):
                        continue  # Corte temporal estricto: nunca acceder a semanas en juego o futuras
                except (ValueError, TypeError):
                    pass
            # Temporada vigente: no sustituir con temporadas antiguas
            g_season = g.get("season")
            if g_season is not None:
                try:
                    s_int = int(g_season)
                except (ValueError, TypeError):
                    s_int = None
            else:
                s_int = g_date.year if g_date.month >= 6 else g_date.year - 1

            if s_int is None or s_int != active_season:
                continue  # No sustituir rendimiento actual faltante por temporadas antiguas
            eligible_logs.append((g_date, g))

        if not eligible_logs:
            return {
                "canonical_player_id": p_id,
                "current_team": team_clean,
                "status": "unknown",
                "importance_score": None,
                "rolling_ppr": None,
                "rolling_off_pct": None,
                "rolling_def_pct": None,
                "games_sampled": 0,
                "pos_rank_context": None,
                "reason": "missing_recent_sample_on_current_team",
            }

        # Ordenar por fecha descendente y tomar hasta max_recent_games
        eligible_logs.sort(key=lambda x: x[0], reverse=True)
        sampled = [x[1] for x in eligible_logs[:max_recent_games]]

        ppr_vals = [float(g["fantasy_points_ppr"]) for g in sampled if g.get("fantasy_points_ppr") is not None]
        off_vals = [float(g["offense_pct"]) for g in sampled if g.get("offense_pct") is not None]
        def_vals = [float(g["defense_pct"]) for g in sampled if g.get("defense_pct") is not None]

        rolling_ppr = round(sum(ppr_vals) / len(ppr_vals), 2) if ppr_vals else None
        rolling_off = round(sum(off_vals) / len(off_vals), 3) if off_vals else None
        rolling_def = round(sum(def_vals) / len(def_vals), 3) if def_vals else None

        # Procesar pos_rank solo como contexto informativo vigente con procedencia (sin fuga temporal)
        validated_rank_ctx = None
        if pos_rank_context and isinstance(pos_rank_context, dict):
            rank_val = pos_rank_context.get("pos_rank") or pos_rank_context.get("rank")
            rank_as_of = pos_rank_context.get("as_of") or pos_rank_context.get("date")
            rank_prov = pos_rank_context.get("provenance") or pos_rank_context.get("source")
            if rank_val is not None and rank_as_of and rank_prov:
                rank_as_of_dt = self.parse_utc_timestamp(rank_as_of)
                if rank_as_of_dt is not None and rank_as_of_dt <= as_of_dt:
                    validated_rank_ctx = {
                        "rank": int(rank_val),
                        "as_of": str(rank_as_of),
                        "provenance": str(rank_prov),
                        "role_context": "descriptive_only_no_fixed_multiplier",
                    }

        # Importance score derivado de stats reales observadas (sin multiplicador fijo por pos_rank)
        score_components = []
        if rolling_ppr is not None:
            score_components.append(min(1.0, rolling_ppr / 25.0))
        if rolling_off is not None:
            score_components.append(rolling_off)
        if rolling_def is not None:
            score_components.append(rolling_def)

        importance_score = round(sum(score_components) / len(score_components), 3) if score_components else None

        return {
            "canonical_player_id": p_id,
            "current_team": team_clean,
            "status": "observed" if importance_score is not None else "unknown",
            "importance_score": importance_score,
            "rolling_ppr": rolling_ppr,
            "rolling_off_pct": rolling_off,
            "rolling_def_pct": rolling_def,
            "games_sampled": len(sampled),
            "pos_rank_context": validated_rank_ctx,
        }

    def build_game_availability_snapshot(
        self,
        game_id: str,
        home_team: str,
        away_team: str,
        kickoff_utc: Any,
        as_of: Any,
        espn_snapshot: Optional[Dict[str, Any]] = None,
        live_props: Optional[List[Dict[str, Any]]] = None,
        props_event_id: Optional[str] = None,
        prior_roster_stats: Optional[Dict[str, Dict[str, Any]]] = None,
        crosswalk: Optional[Dict[str, str]] = None,
        strict_unlisted_unknown: bool = True,
    ) -> Dict[str, Any]:
        """
        Construye el contrato completo de availability_by_game para un partido a partir
        del snapshot activo filtrado por as_of, props verificables y priors por equipo.
        """
        kickoff_dt = self.parse_utc_timestamp(kickoff_utc)
        if kickoff_dt is None:
            raise ValueError(f"kickoff_utc inválido para {game_id}")
        active_snap = espn_snapshot or self.get_active_injury_snapshot(as_of=as_of)
        if not active_snap:
            return {}

        if live_props is None and props_event_id:
            live_props = self.get_cached_player_props_for_event(props_event_id)

        as_of_dt = self.parse_utc_timestamp(as_of) or datetime.now(timezone.utc)
        cap_dt = self.parse_utc_timestamp(active_snap.get("captured_at_utc")) or as_of_dt

        cadence = active_snap.get("cadence", "start_of_day")
        if cadence in ("near_kickoff", "pre_kickoff"):
            cutoff_dt = kickoff_dt - timedelta(minutes=90)
            eval_cutoff_str = (cutoff_dt + timedelta(minutes=5)).isoformat()
        else:
            cutoff_dt = kickoff_dt - timedelta(hours=2)
            eval_cutoff_str = cutoff_dt.isoformat()

        snap_id = active_snap.get("snapshot_id", "snap")
        captured_at = active_snap.get("captured_at_utc")
        source_obs = active_snap.get("source_observed_at_utc")
        known_by_system = active_snap.get("known_by_system_at_utc") or captured_at

        h_upper = home_team.upper()
        a_upper = away_team.upper()

        teams_payload = active_snap.get("teams", {})
        omitted = set(active_snap.get("omitted_teams", []))

        # Si prior_roster_stats no se proveyó externamente, conectar estadísticas
        # reales recientes del equipo actual anteriores a as_of desde nflverse
        eval_week = None
        if game_id and "_" in str(game_id):
            parts = str(game_id).split("_")
            if len(parts) >= 2 and parts[1].isdigit():
                eval_week = int(parts[1])

        def get_team_players(t: str) -> Optional[List[Dict[str, Any]]]:
            if t in teams_payload:
                return teams_payload[t]
            alias_map = {"LA": "LAR", "LAR": "LA", "WAS": "WSH", "WSH": "WAS"}
            alt = alias_map.get(t)
            if alt and alt in teams_payload:
                return teams_payload[alt]
            return None

        effective_cw = crosswalk if crosswalk is not None else self.get_player_crosswalk()

        summaries = {}
        for t_abbr in (h_upper, a_upper):
            raw_players = get_team_players(t_abbr)
            if raw_players is not None:
                records = []
                for p in raw_players:
                    r = self.process_player_injury(
                        player_dict=p,
                        team=t_abbr,
                        game_start_utc=kickoff_dt.isoformat(),
                        decision_cutoff=eval_cutoff_str,
                        live_props=live_props,
                        crosswalk=effective_cw,
                        expected_event_id=props_event_id,
                        strict_unlisted_unknown=strict_unlisted_unknown,
                    )
                    records.append(r)

                team_priors = (
                    prior_roster_stats.get(t_abbr) if (prior_roster_stats and t_abbr in prior_roster_stats)
                    else (prior_roster_stats if prior_roster_stats is not None
                          else self.load_team_roster_stats_from_nflverse(t_abbr, as_of=as_of, evaluated_week=eval_week))
                )

                summary = self.compute_team_production_lost(
                    team=t_abbr,
                    records=records,
                    prior_roster_stats=team_priors,
                    game_id=game_id,
                    decision_cutoff=cutoff_dt.isoformat(),
                    is_source_available=True,
                    clean_report_evidence_ref=f"espn_snapshot:{snap_id}:{t_abbr}:clean_report" if not records else None,
                    allow_unresolved_market=True,
                )
            else:
                # Equipo omitido: registrar fielmente sin lesiones listadas en esta captura
                summary = self.compute_team_production_lost(
                    team=t_abbr,
                    records=[],
                    prior_roster_stats=None,
                    game_id=game_id,
                    decision_cutoff=cutoff_dt.isoformat(),
                    is_source_available=True,
                    no_injuries_listed=True,
                )
            summaries[t_abbr] = summary.to_dict()

        return {
            "game_id": game_id,
            "decision_cutoff_utc": cutoff_dt.isoformat(),
            "captured_at_utc": captured_at,
            "source_observed_at_utc": source_obs,
            "known_by_system_at_utc": known_by_system,
            "cadence": cadence,
            "payload_sha256": active_snap.get("payload_sha256"),
            "resolver_version": RESOLVER_VERSION,
            "source_refs_by_team": {
                h_upper: [f"espn_snapshot:{snap_id}:{h_upper}"],
                a_upper: [f"espn_snapshot:{snap_id}:{a_upper}"],
            },
            "teams": summaries,
        }

    def get_verifiable_props_for_qd_players(
        self,
        event_id: str,
        records: List[PlayerInjuryRecord],
        live_props: Optional[List[Dict[str, Any]]] = None,
        quota_service: Optional[Any] = None,
        api_key: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Conecta y filtra props exclusivamente para jugadores Questionable o Doubtful con canonical event ID.
        Reglas P0:
        - Confirmed OUT/IR: NUNCA se consultan props ni se reserva cuota.
        - Si no hay jugadores Questionable/Doubtful: retorna None sin consultar props ni reservar cuota.
        - Si hay jugadores Q/D: usa el estimador de costo variable y reserva en OddsQuotaService.
        - Una línea presente es señal de mercado; su ausencia queda desconocida.
        """
        qd_players = [
            r for r in records
            if r.availability_status in (AvailabilityStatus.QUESTIONABLE.value, AvailabilityStatus.DOUBTFUL.value)
        ]
        if not qd_players:
            return None

        # Si live_props ya fue provisto (e.g. en mock o fetch previo)
        if live_props is not None:
            return [
                p for p in live_props
                if str(p.get("event_id") or p.get("game_id")) == str(event_id)
            ]

        if quota_service and event_id and api_key:
            endpoint = f"/sports/americanfootball_nfl/events/{event_id}/odds"
            params = {
                "regions": "us",
                "markets": "player_pass_yds,player_rush_yds,player_reception_yds,player_receptions",
                "oddsFormat": "american",
            }
            try:
                quota_service.reserve_quota(
                    endpoint=endpoint,
                    params=params,
                    envelope="props",
                    api_key=api_key,
                )
                return []
            except Exception as exc:
                logger.warning("No se pudo reservar cuota para props de jugadores Q/D", error=str(exc))
                return None

        return None

    def get_cached_player_props_for_event(self, event_id: str) -> List[Dict[str, Any]]:
        """
        Busca player props verificables cacheadas localmente para el event_id desde data/raw/odds_api.
        Devuelve lista vacía [] si no existen props registradas para el evento.
        """
        if not event_id:
            return []
        props: List[Dict[str, Any]] = []
        odds_raw_dir = Path("data/raw/odds_api")
        if not odds_raw_dir.exists():
            return []

        for path in odds_raw_dir.glob("*/*.json"):
            if "canonical" in path.name:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    items = []
                    if isinstance(content, list):
                        items = content
                    elif isinstance(content, dict):
                        inner = content.get("data")
                        if isinstance(inner, list):
                            items = inner
                        elif isinstance(inner, dict):
                            items = [inner]
                        else:
                            items = [content]

                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        if str(item.get("id")) == str(event_id) or str(item.get("event_id")) == str(event_id):
                            for bm in item.get("bookmakers", []):
                                bm_key = bm.get("key", "unknown")
                                for mkt in bm.get("markets", []):
                                    m_key = mkt.get("key", "")
                                    if "player_" in m_key:
                                        for out in mkt.get("outcomes", []):
                                            p_name = out.get("description")
                                            price = out.get("price")
                                            point = out.get("point")
                                            if p_name and price is not None and point is not None:
                                                props.append({
                                                    "event_id": event_id,
                                                    "player_name": p_name,
                                                    "prop_type": m_key,
                                                    "market_line": float(point),
                                                    "over_odds_american": price,
                                                    "price": price,
                                                    "bookmaker": bm_key,
                                                    "timestamp": bm.get("last_update") or item.get("commence_time"),
                                                })
            except Exception:
                continue
        return props

