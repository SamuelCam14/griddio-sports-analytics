import structlog
import pandas as pd
import polars as pl
import nflreadpy as nfl
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = structlog.get_logger(__name__)

CACHE_DIR = "data/cache"
os.makedirs(CACHE_DIR, exist_ok=True)

class DataQualityError(Exception):
    pass

class InjuryNormalizer:
    @staticmethod
    def resolve_live_status(report_status: Any, player_name: str, team: str) -> str:
        """
        Jerarquía de Verdad:
        1. Official 'OUT', 'IR', 'DOUBTFUL' -> OUT
        2. Si 'QUESTIONABLE', usar Vegas Props como proxy en Game Day (mockeado aquí hasta conectar The Odds API).
        3. Si no hay Props, se asume ACTIVE (para evitar falsos negativos prematuros).
        """
        if pd.isna(report_status):
            return 'ACTIVE'
        s = str(report_status).lower()
        if 'out' in s or 'ir' in s or 'reserve' in s or 'doubtful' in s: return 'OUT'
        if 'questionable' in s:
            # TODO: Conectar a The Odds API para resolver. Por ahora, asumimos ACTIVE.
            return 'ACTIVE'
        return 'ACTIVE'

class FantasyInjuryService:
    def __init__(self):
        self.cache_file = os.path.join(CACHE_DIR, "nfl_live_production_lost.json")
        
    def _is_cache_valid(self, max_age_hours: int = 12) -> bool:
        if not os.path.exists(self.cache_file):
            return False
        mtime = os.path.getmtime(self.cache_file)
        age_hours = (time.time() - mtime) / 3600.0
        return age_hours < max_age_hours

    def get_live_production_lost(self, sport: str, season: int, week: int) -> Dict[str, Dict[str, float]]:
        """
        Retorna { "TEAM": {"ppr_lost_pct": X, "off_snaps_lost_pct": Y, "def_snaps_lost_pct": Z} }
        """
        if sport.upper() != "NFL":
            return {}

        if self._is_cache_valid(max_age_hours=12):
            with open(self.cache_file, "r") as f:
                return json.load(f)
                
        try:
            logger.info("Calculando pérdida de producción en vivo...", season=season, week=week)
            
            # Replicamos la matemática exacta del script de entrenamiento
            stats = nfl.load_player_stats([season]).to_pandas()
            snaps = nfl.load_snap_counts([season]).to_pandas()
            injuries = nfl.load_injuries([season]).to_pandas()
            rosters = nfl.load_rosters_weekly([season]).to_pandas()
            players = nfl.load_players().to_pandas()
            
            crosswalk = players[['pfr_id', 'gsis_id']].dropna().drop_duplicates(subset=['pfr_id'])
            snaps = pd.merge(snaps, crosswalk, left_on='pfr_player_id', right_on='pfr_id', how='left')
            snaps.rename(columns={'gsis_id': 'player_id'}, inplace=True)
            stats.rename(columns={'player_id': 'player_id_stats'}, inplace=True)
            stats.rename(columns={'player_id_stats': 'player_id'}, inplace=True)
            rosters.rename(columns={'gsis_id': 'player_id'}, inplace=True)
            injuries.rename(columns={'gsis_id': 'player_id'}, inplace=True)
            
            snaps_sub = snaps[['season', 'week', 'player_id', 'team', 'offense_pct', 'defense_pct']].dropna(subset=['player_id'])
            stats_sub = stats[['season', 'week', 'player_id', 'fantasy_points_ppr']].dropna(subset=['player_id'])
            game_logs = pd.merge(snaps_sub, stats_sub, on=['season', 'week', 'player_id'], how='outer')
            game_logs['offense_pct'] = game_logs['offense_pct'].fillna(0.0)
            game_logs['defense_pct'] = game_logs['defense_pct'].fillna(0.0)
            game_logs['fantasy_points_ppr'] = game_logs['fantasy_points_ppr'].fillna(0.0)
            game_logs = game_logs.sort_values(by=['season', 'week'])
            
            game_logs['rolling_ppr'] = game_logs.groupby('player_id')['fantasy_points_ppr'].transform(lambda x: x.rolling(window=6, min_periods=1).mean())
            game_logs['rolling_off_pct'] = game_logs.groupby('player_id')['offense_pct'].transform(lambda x: x.rolling(window=6, min_periods=1).mean())
            game_logs['rolling_def_pct'] = game_logs.groupby('player_id')['defense_pct'].transform(lambda x: x.rolling(window=6, min_periods=1).mean())
            
            game_logs['time_idx'] = game_logs['season'] * 100 + game_logs['week']
            rosters['time_idx'] = rosters['season'] * 100 + rosters['week']
            rosters = rosters.sort_values('time_idx')
            game_logs = game_logs.sort_values('time_idx').dropna(subset=['time_idx'])
            rosters['time_idx_search'] = rosters['time_idx'] - 1
            
            roster_strength = pd.merge_asof(
                rosters[['season', 'week', 'team', 'player_id', 'time_idx', 'time_idx_search']],
                game_logs[['player_id', 'time_idx', 'rolling_ppr', 'rolling_off_pct', 'rolling_def_pct']],
                left_on='time_idx_search',
                right_on='time_idx',
                by='player_id',
                direction='backward'
            )
            roster_strength['rolling_ppr'] = roster_strength['rolling_ppr'].fillna(0.0)
            roster_strength['rolling_off_pct'] = roster_strength['rolling_off_pct'].fillna(0.0)
            roster_strength['rolling_def_pct'] = roster_strength['rolling_def_pct'].fillna(0.0)
            
            team_potential = roster_strength.groupby(['season', 'week', 'team'])['rolling_ppr'].sum().reset_index()
            team_potential.rename(columns={'rolling_ppr': 'team_potential_ppr'}, inplace=True)
            
            injuries['normalized_status'] = injuries.apply(lambda r: InjuryNormalizer.resolve_live_status(r['report_status'], r.get('full_name'), r.get('team')), axis=1)
            out_injuries = injuries[injuries['normalized_status'] == 'OUT'].copy()
            
            lost_prod = pd.merge(
                out_injuries[['season', 'week', 'team', 'player_id']],
                roster_strength[['season', 'week', 'player_id', 'rolling_ppr', 'rolling_off_pct', 'rolling_def_pct']],
                on=['season', 'week', 'player_id'],
                how='inner'
            )
            
            team_lost = lost_prod.groupby(['season', 'week', 'team']).agg({
                'rolling_ppr': 'sum', 'rolling_off_pct': 'sum', 'rolling_def_pct': 'sum'
            }).reset_index()
            team_lost.rename(columns={'rolling_ppr': 'team_lost_ppr', 'rolling_off_pct': 'team_lost_off_pct', 'rolling_def_pct': 'team_lost_def_pct'}, inplace=True)
            
            final_df = pd.merge(team_potential, team_lost, on=['season', 'week', 'team'], how='left')
            final_df.fillna(0.0, inplace=True)
            
            final_df['ppr_lost_pct'] = final_df['team_lost_ppr'] / final_df['team_potential_ppr'].clip(lower=1.0)
            final_df['off_snaps_lost_pct'] = (final_df['team_lost_off_pct'] / 11.0).clip(upper=1.0)
            final_df['def_snaps_lost_pct'] = (final_df['team_lost_def_pct'] / 11.0).clip(upper=1.0)
            
            current_week_df = final_df[final_df['week'] == week]
            result = {}
            for _, row in current_week_df.iterrows():
                result[row['team']] = {
                    "ppr_lost_pct": round(row['ppr_lost_pct'], 4),
                    "off_snaps_lost_pct": round(row['off_snaps_lost_pct'], 4),
                    "def_snaps_lost_pct": round(row['def_snaps_lost_pct'], 4)
                }
                
            with open(self.cache_file, "w") as f:
                json.dump(result, f)
            return result
            
        except Exception as e:
            logger.error("Error al procesar data en vivo.", error=str(e))
            if os.path.exists(self.cache_file) and self._is_cache_valid(max_age_hours=48):
                logger.warning("Fallback a caché de hasta 48h.")
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            raise DataQualityError("Datos de Lesiones no disponibles.")

if __name__ == "__main__":
    svc = FantasyInjuryService()
    print(svc.get_live_production_lost("NFL", 2024, 18))
