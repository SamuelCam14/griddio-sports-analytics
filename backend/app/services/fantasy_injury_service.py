"""
Servicio de Lesiones y Pérdida de Producción en Vivo (Live Production Lost & Market Cross-Referencing).
Correlaciona en tiempo real el parte médico oficial con las líneas activas de The Odds API (Player Props).

En NFL (Fase 02 - Requisitos Auditados Codex):
- Delega en NFLAvailabilityService aplicando contratos estrictos point-in-time y reglas de mercado.
- Bajas confirmadas (OUT/IR): NUNCA se consultan props para revertir su estado.
- Questionable/Doubtful: Se contrastan con props solo para juegos futuros en ventana de cutoff con props verificables.
- Cero props sintéticas: prohibida la generación de props ficticias de bookmakers.
- Cobertura explícita: distingue covered_healthy de unavailable_no_report. Nunca imputa 0.0 ante fallas.
- Persistencia con provenance y validación de vigencia del cutoff pregame.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import structlog

from app.services.nfl_availability_service import (
    CoverageState,
    NFLAvailabilityService,
    TeamAvailabilitySummary,
)

logger = structlog.get_logger(__name__)

CACHE_DIR = "data/cache"
os.makedirs(CACHE_DIR, exist_ok=True)


class FantasyInjuryService:
    def __init__(self, cache_dir: str = CACHE_DIR):
        self.cache_dir = cache_dir
        self.nfl_service = NFLAvailabilityService(cache_dir=Path(cache_dir) / "nfl_availability")
        self._last_availability_summaries: Dict[str, TeamAvailabilitySummary] = {}

    def get_live_production_lost(
        self,
        sport: str = "NFL",
        decision_cutoff: Optional[Any] = None,
        game_start_utc: Optional[Any] = None,
        live_props: Optional[List[Dict[str, Any]]] = None,
        prior_roster_stats: Optional[Dict[str, Dict[str, float]]] = None,
        game_id: Optional[str] = None,
        crosswalk: Optional[Dict[str, str]] = None,
        expected_teams: Optional[List[str]] = None,
        props_event_id: Optional[str] = None,
        strict_unlisted_unknown: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Calcula la pérdida de producción dinámica para cada escuadra, resolviendo
        la incertidumbre médica con confirmación de mercado en tiempo real.
        """
        sport_upper = sport.upper()
        if sport_upper not in ("NFL", "NBA"):
            return {}

        # ── Ruta NFL Point-in-Time (Fase 02) ──
        if sport_upper == "NFL":
            return self._get_nfl_live_production_lost(
                decision_cutoff=decision_cutoff,
                game_start_utc=game_start_utc,
                live_props=live_props,
                prior_roster_stats=prior_roster_stats,
                game_id=game_id,
                crosswalk=crosswalk,
                expected_teams=expected_teams,
                props_event_id=props_event_id,
                strict_unlisted_unknown=strict_unlisted_unknown,
            )

        # ── Ruta NBA (Heredada / Fuera de alcance en Fase 02) ──
        return self._get_nba_live_production_lost()

    def _get_nfl_live_production_lost(
        self,
        decision_cutoff: Optional[Any] = None,
        game_start_utc: Optional[Any] = None,
        live_props: Optional[List[Dict[str, Any]]] = None,
        prior_roster_stats: Optional[Dict[str, Dict[str, float]]] = None,
        game_id: Optional[str] = None,
        crosswalk: Optional[Dict[str, str]] = None,
        expected_teams: Optional[List[str]] = None,
        props_event_id: Optional[str] = None,
        strict_unlisted_unknown: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """Implementación point-in-time para la NFL desacoplada de fallbacks heurísticos ciegos."""
        now_utc = datetime.now(timezone.utc)
        cutoff_dt = self.nfl_service.parse_utc_timestamp(decision_cutoff)
        start_dt = self.nfl_service.parse_utc_timestamp(game_start_utc)

        # Si no se pasó cutoff explícito, fail-closed: cutoff debe ser definido por el caller prepartido
        if cutoff_dt is None:
            logger.warning("NFL availability consultada sin decision_cutoff explícito")
            return {}

        try:
            active_snap = self.nfl_service.get_active_injury_snapshot(as_of=cutoff_dt)
            if active_snap and isinstance(active_snap.get("teams"), dict) and active_snap["teams"]:
                all_injuries = active_snap["teams"]
                is_source_available = True
            else:
                from app.services.matchup_context_service import get_live_injuries
                all_injuries = get_live_injuries("NFL")
                is_source_available = isinstance(all_injuries, dict) and bool(all_injuries)

            result: Dict[str, Dict[str, Any]] = {}
            self._last_availability_summaries.clear()

            if not is_source_available:
                logger.error("Fuente de lesiones no disponible o vacía; no se imputa cero")
                return {}

            for team_key, inj_list in all_injuries.items():
                if expected_teams and str(team_key).upper() not in {str(t).upper() for t in expected_teams}:
                    continue
                # Procesar cada jugador con el motor de disponibilidad point-in-time
                player_records = []
                for p in inj_list:
                    rec = self.nfl_service.process_player_injury(
                        player_dict=p,
                        team=team_key,
                        game_start_utc=start_dt.isoformat() if start_dt else None,
                        decision_cutoff=cutoff_dt.isoformat(),
                        live_props=live_props,
                        crosswalk=crosswalk,
                        expected_event_id=props_event_id,
                        strict_unlisted_unknown=strict_unlisted_unknown,
                    )
                    player_records.append(rec)

                # Calcular resumen agregado con paridad de schema
                summary = self.nfl_service.compute_team_production_lost(
                    team=team_key,
                    records=player_records,
                    prior_roster_stats=prior_roster_stats,
                    game_id=game_id,
                    decision_cutoff=cutoff_dt.isoformat(),
                    is_source_available=is_source_available,
                )
                self._last_availability_summaries[team_key.upper()] = summary

                # Persistir snapshot de disponibilidad con provenance
                try:
                    self.nfl_service.persist_team_availability(summary, game_id=game_id)
                except Exception as pe:
                    logger.warning("No se pudo persistir snapshot individual de disponibilidad", error=str(pe))

                metrics = {
                    "coverage_state": summary.coverage_state,
                    "ppr_lost_pct": summary.ppr_lost_pct,
                    "off_snaps_lost_pct": summary.off_snaps_lost_pct,
                    "def_snaps_lost_pct": summary.def_snaps_lost_pct,
                    "unknown_count": summary.unknown_count,
                    "confirmed_out_count": summary.confirmed_out_count,
                    "questionable_count": summary.questionable_count,
                    "doubtful_count": summary.doubtful_count,
                }
                result[team_key] = metrics
                result[team_key.upper()] = metrics

            # Registrar fielmente equipos esperados omitidos sin bloquear el feed ni asumir que están sanos
            if expected_teams:
                present_upper = {str(key).upper() for key in all_injuries}
                for exp_team in expected_teams:
                    exp_upper = str(exp_team).upper()
                    if exp_upper not in present_upper and exp_upper not in self._last_availability_summaries:
                        omitted_summary = self.nfl_service.compute_team_production_lost(
                            team=exp_upper,
                            records=[],
                            prior_roster_stats=prior_roster_stats,
                            game_id=game_id,
                            decision_cutoff=cutoff_dt.isoformat(),
                            is_source_available=True,
                            no_injuries_listed=True,
                        )
                        self._last_availability_summaries[exp_upper] = omitted_summary
                        omitted_metrics = {
                            "coverage_state": omitted_summary.coverage_state,
                            "ppr_lost_pct": omitted_summary.ppr_lost_pct,
                            "off_snaps_lost_pct": omitted_summary.off_snaps_lost_pct,
                            "def_snaps_lost_pct": omitted_summary.def_snaps_lost_pct,
                            "unknown_count": omitted_summary.unknown_count,
                            "confirmed_out_count": omitted_summary.confirmed_out_count,
                            "questionable_count": omitted_summary.questionable_count,
                            "doubtful_count": omitted_summary.doubtful_count,
                            "note": "sin lesiones listadas en esta captura",
                        }
                        result[exp_upper] = omitted_metrics
                        result[exp_team] = omitted_metrics

            logger.info("NFL point-in-time production lost computed", teams=len(self._last_availability_summaries))
            return result

        except Exception as e:
            logger.error("Error computing NFL point-in-time production lost", error=str(e))
            return {}

    def _get_nba_live_production_lost(self) -> Dict[str, Dict[str, Any]]:
        """Lógica heredada NBA preservada sin alteraciones (fuera de alcance actual)."""
        cache_file = os.path.join(self.cache_dir, "nba_live_production_lost.json")

        if os.path.exists(cache_file):
            try:
                mtime = os.path.getmtime(cache_file)
                if (time.time() - mtime) / 3600.0 < 2.0:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass

        try:
            from app.services.matchup_context_service import get_live_injuries
            all_injuries = get_live_injuries("NBA")

            skill_pos = {"PG", "SG", "SF", "PF", "C"}
            den = 5.0

            result: Dict[str, Dict[str, Any]] = {}
            for team_key, inj_list in all_injuries.items():
                if not inj_list:
                    result[team_key] = {
                        "coverage_state": "covered_healthy",
                        "ppr_lost_pct": 0.0,
                        "off_snaps_lost_pct": 0.0,
                        "def_snaps_lost_pct": 0.0,
                    }
                    continue

                off_lost_count = 0.0
                def_lost_count = 0.0
                skill_lost_count = 0.0

                for p in inj_list:
                    p_stat = p.get("status", "").upper()
                    p_pos = p.get("position", "")

                    if p_stat in ("OUT", "IR", "RESERVE", "INJURED RESERVE"):
                        loss_weight = 1.0
                    elif p_stat in ("QUESTIONABLE", "DOUBTFUL", "DAY-TO-DAY"):
                        loss_weight = 0.85 if p_stat == "DOUBTFUL" else 0.25
                    else:
                        loss_weight = 0.0

                    if loss_weight > 0.0:
                        if p_pos in skill_pos:
                            skill_lost_count += loss_weight
                        off_lost_count += loss_weight
                        def_lost_count += loss_weight

                off_lost_pct = min(1.0, round(off_lost_count / den, 4))
                def_lost_pct = min(1.0, round(def_lost_count / den, 4))
                ppr_lost_pct = min(1.0, round(skill_lost_count * 0.20, 4))

                metrics = {
                    "coverage_state": "covered_with_injuries",
                    "ppr_lost_pct": ppr_lost_pct,
                    "off_snaps_lost_pct": off_lost_pct,
                    "def_snaps_lost_pct": def_lost_pct,
                }
                result[team_key] = metrics
                result[team_key.upper()] = metrics

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)

            return result
        except Exception as e:
            logger.error("Error computing NBA live production lost", error=str(e))
            return {}

    def get_last_availability_summaries(self) -> Dict[str, TeamAvailabilitySummary]:
        """Retorna los resúmenes detallados y provenance de la última ejecución."""
        return self._last_availability_summaries
