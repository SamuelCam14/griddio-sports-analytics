"""
Servicio de Lesiones y Pérdida de Producción en Vivo (Live Production Lost & Market Cross-Referencing).
Correlaciona en tiempo real el parte médico oficial de ESPN con las líneas activas de The Odds API (Player Props).

Lógica de Resolución en Presente (SSOT):
1. Si un jugador está reportado oficialmente en ESPN como 'OUT', 'IR' o 'RESERVE':
   -> Baja confirmada (100% de pérdida de producción/snaps).
2. Si un jugador está reportado como 'QUESTIONABLE' o 'DOUBTFUL':
   -> Se contrasta con las líneas de props de casas de apuestas (The Odds API):
      a) Si las casas de apuestas tienen líneas de props activas para ese jugador (yardas, puntos, etc.):
         -> El mercado sharp confirma con alta probabilidad su participación -> Resuelto como ACTIVO (0% de pérdida).
      b) Si las casas de apuestas han abierto props para el partido pero excluyeron al jugador:
         -> El mercado confirma su descarte / inactividad -> Resuelto como OUT (100% de pérdida).
      c) Si el partido aún no tiene props abiertas (ej. faltan varios días para el encuentro):
         -> Se aplica una expectativa base pre-mercado calibrada (0.25 para Questionable, 0.85 para Doubtful).
"""
import os
import json
import time
from typing import Dict, Any, Optional, Set
import structlog

logger = structlog.get_logger(__name__)

CACHE_DIR = "data/cache"
os.makedirs(CACHE_DIR, exist_ok=True)


class FantasyInjuryService:
    def __init__(self):
        self.cache_dir = CACHE_DIR

    def get_live_production_lost(
        self,
        sport: str = "NFL",
        active_prop_players: Optional[Set[str]] = None,
        has_active_game_props: bool = False,
    ) -> Dict[str, Dict[str, float]]:
        """
        Calcula la pérdida de producción dinámica para cada escuadra, resolviendo
        la incertidumbre médica con confirmación de mercado en tiempo real.
        """
        sport_upper = sport.upper()
        if sport_upper not in ("NFL", "NBA"):
            return {}

        cache_file = os.path.join(self.cache_dir, f"{sport_upper.lower()}_live_production_lost.json")

        # Comprobar caché local reciente (< 2 horas) solo si no se pasó filtro de props en memoria
        if not active_prop_players and os.path.exists(cache_file):
            try:
                mtime = os.path.getmtime(cache_file)
                if (time.time() - mtime) / 3600.0 < 2.0:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass

        try:
            from app.services.matchup_context_service import get_live_injuries
            all_injuries = get_live_injuries(sport_upper)

            normalized_props_set = {p.lower().strip() for p in (active_prop_players or set())}

            skill_pos = {"QB", "WR", "RB", "TE", "PG", "SG", "SF", "PF", "C"}
            def_pos = {"DE", "DT", "LB", "CB", "S", "FS", "SS", "NT", "ILB", "OLB"}
            off_pos = {"QB", "WR", "RB", "TE", "T", "G", "C", "OT", "OG"}
            den = 11.0 if sport_upper == "NFL" else 5.0

            result: Dict[str, Dict[str, float]] = {}

            for team_key, inj_list in all_injuries.items():
                if not inj_list:
                    result[team_key] = {
                        "ppr_lost_pct": 0.0,
                        "off_snaps_lost_pct": 0.0,
                        "def_snaps_lost_pct": 0.0,
                    }
                    continue

                effective_out = 0.0
                effective_quest = 0.0
                off_lost_count = 0.0
                def_lost_count = 0.0
                skill_lost_count = 0.0

                for p in inj_list:
                    p_name = p.get("player_name", "").lower().strip()
                    p_stat = p.get("status", "").upper()
                    p_pos = p.get("position", "")

                    is_skill = p_pos in skill_pos
                    is_off = p_pos in off_pos
                    is_def = p_pos in def_pos

                    # 1. Bajas definitivas confirmadas en parte oficial
                    if p_stat in ("OUT", "IR", "RESERVE", "INJURED RESERVE"):
                        loss_weight = 1.0
                    # 2. Correlación con Props de Casas de Apuestas (Mercado Sharp)
                    elif p_stat in ("QUESTIONABLE", "DOUBTFUL", "DAY-TO-DAY"):
                        if p_name in normalized_props_set:
                            # Casas de apuestas tienen línea activa abierta -> El jugador jugará
                            loss_weight = 0.0
                        elif has_active_game_props and is_skill:
                            # Hay props abiertas en el partido pero este jugador fue retirado -> No jugará
                            loss_weight = 1.0
                        else:
                            # Pre-mercado o sin props abiertas aún: expectativa probabilística
                            loss_weight = 0.85 if p_stat == "DOUBTFUL" else 0.25
                    else:
                        loss_weight = 0.0

                    if loss_weight > 0.0:
                        if is_skill:
                            skill_lost_count += loss_weight
                        if is_off:
                            off_lost_count += loss_weight
                        if is_def:
                            def_lost_count += loss_weight

                off_lost_pct = min(1.0, round(off_lost_count / den, 4))
                def_lost_pct = min(1.0, round(def_lost_count / den, 4))
                ppr_lost_pct = min(1.0, round(skill_lost_count * (0.15 if sport_upper == "NFL" else 0.20), 4))

                metrics = {
                    "ppr_lost_pct": ppr_lost_pct,
                    "off_snaps_lost_pct": off_lost_pct,
                    "def_snaps_lost_pct": def_lost_pct,
                }
                result[team_key] = metrics
                result[team_key.upper()] = metrics

            if not active_prop_players:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)

            logger.info("Live production lost calculated with market resolution", sport=sport_upper, teams=len(result))
            return result

        except Exception as e:
            logger.error("Error computing live production lost", error=str(e), sport=sport_upper)
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            return {}
