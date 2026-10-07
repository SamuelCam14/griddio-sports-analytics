import pandas as pd
import polars as pl
import nflreadpy as nfl
import structlog
import warnings

warnings.filterwarnings('ignore')
logger = structlog.get_logger(__name__)

def build_nfl_historical_injury_features(seasons: list[int], rolling_window: int = 6) -> pd.DataFrame:
    """
    Construye las features de lesiones para entrenamiento histórico.
    Aplica la constante 11.0 para snaps, merge ASOF para evitar leaks,
    y resuelve "Questionable" comprobando el Box Score histórico (Ground Truth).
    """
    logger.info("Cargando datos históricos de la NFL...", seasons=seasons)
    stats = nfl.load_player_stats(seasons).to_pandas()
    snaps = nfl.load_snap_counts(seasons).to_pandas()
    injuries = nfl.load_injuries(seasons).to_pandas()
    rosters = nfl.load_rosters_weekly(seasons).to_pandas()
    players = nfl.load_players().to_pandas()
    
    # Mapeo de PFR_ID a GSIS_ID para snaps
    crosswalk = players[['pfr_id', 'gsis_id']].dropna().drop_duplicates(subset=['pfr_id'])
    snaps = pd.merge(snaps, crosswalk, left_on='pfr_player_id', right_on='pfr_id', how='left')
    snaps.rename(columns={'gsis_id': 'player_id'}, inplace=True)
    stats.rename(columns={'player_id': 'player_id_stats'}, inplace=True)
    stats.rename(columns={'player_id_stats': 'player_id'}, inplace=True)
    rosters.rename(columns={'gsis_id': 'player_id'}, inplace=True)
    injuries.rename(columns={'gsis_id': 'player_id'}, inplace=True)
    
    # Consolidar Stats y Snaps
    snaps_sub = snaps[['season', 'week', 'player_id', 'team', 'offense_pct', 'defense_pct']].dropna(subset=['player_id'])
    stats_sub = stats[['season', 'week', 'player_id', 'fantasy_points_ppr']].dropna(subset=['player_id'])
    game_logs = pd.merge(snaps_sub, stats_sub, on=['season', 'week', 'player_id'], how='outer')
    game_logs['offense_pct'] = game_logs['offense_pct'].fillna(0.0)
    game_logs['defense_pct'] = game_logs['defense_pct'].fillna(0.0)
    game_logs['fantasy_points_ppr'] = game_logs['fantasy_points_ppr'].fillna(0.0)
    game_logs = game_logs.sort_values(by=['season', 'week'])
    
    # Ventana Móvil
    logger.info("Calculando promedios móviles...")
    game_logs['rolling_ppr'] = game_logs.groupby('player_id')['fantasy_points_ppr'].transform(
        lambda x: x.rolling(window=rolling_window, min_periods=1).mean()
    )
    game_logs['rolling_off_pct'] = game_logs.groupby('player_id')['offense_pct'].transform(
        lambda x: x.rolling(window=rolling_window, min_periods=1).mean()
    )
    game_logs['rolling_def_pct'] = game_logs.groupby('player_id')['defense_pct'].transform(
        lambda x: x.rolling(window=rolling_window, min_periods=1).mean()
    )
    
    # Anti-Leak Roster ASOF
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
    
    # Lesiones Históricas con Resolución de "Questionable" vía Ground Truth (Box Score)
    played_this_week = game_logs[['season', 'week', 'player_id']].copy()
    played_this_week['did_play'] = True
    injuries = pd.merge(injuries, played_this_week, on=['season', 'week', 'player_id'], how='left')
    injuries['did_play'] = injuries['did_play'].fillna(False)
    
    def normalize_history_status(row):
        if pd.isna(row['report_status']): return 'ACTIVE'
        s = str(row['report_status']).lower()
        if 'out' in s or 'ir' in s or 'reserve' in s or 'doubtful' in s: return 'OUT'
        if 'questionable' in s:
            return 'ACTIVE' if row['did_play'] else 'OUT'
        return 'ACTIVE'

    injuries['normalized_status'] = injuries.apply(normalize_history_status, axis=1)
    out_injuries = injuries[injuries['normalized_status'] == 'OUT'].copy()
    
    lost_prod = pd.merge(
        out_injuries[['season', 'week', 'team', 'player_id']],
        roster_strength[['season', 'week', 'player_id', 'rolling_ppr', 'rolling_off_pct', 'rolling_def_pct']],
        on=['season', 'week', 'player_id'],
        how='inner'
    )
    
    team_lost = lost_prod.groupby(['season', 'week', 'team']).agg({
        'rolling_ppr': 'sum',
        'rolling_off_pct': 'sum',
        'rolling_def_pct': 'sum'
    }).reset_index()
    team_lost.rename(columns={
        'rolling_ppr': 'team_lost_ppr',
        'rolling_off_pct': 'team_lost_off_pct',
        'rolling_def_pct': 'team_lost_def_pct'
    }, inplace=True)
    
    final_df = pd.merge(team_potential, team_lost, on=['season', 'week', 'team'], how='left')
    final_df.fillna(0.0, inplace=True)
    
    final_df['ppr_lost_pct'] = final_df['team_lost_ppr'] / final_df['team_potential_ppr'].clip(lower=1.0)
    final_df['off_snaps_lost_pct'] = (final_df['team_lost_off_pct'] / 11.0).clip(upper=1.0)
    final_df['def_snaps_lost_pct'] = (final_df['team_lost_def_pct'] / 11.0).clip(upper=1.0)
    
    logger.info("Pipeline histórico finalizado.", rows=len(final_df))
    return final_df[['season', 'week', 'team', 'ppr_lost_pct', 'off_snaps_lost_pct', 'def_snaps_lost_pct']]

if __name__ == "__main__":
    df = build_nfl_historical_injury_features([2024])
    print(df.sort_values('off_snaps_lost_pct', ascending=False).head(5))
