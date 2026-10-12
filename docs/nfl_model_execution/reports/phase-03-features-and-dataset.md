# Reporte de Ejecución — Fase 03: Catálogo de Features NFL y Dataset Canónico

**Fecha de ejecución:** 2026-10-12  
**Alcance:** Exclusivamente NFL (`nfl_team_only_temporal_v1` y `nfl_temporal_v1`).  
**Estado de la fase:** `review` (entregado para auditoría).  
**Entorno:** Docker container `sports_api` (Python 3.12.15, scikit-learn 1.6.1, pandas 2.2.3, polars 1.23.0). Host: Windows 11.

---

## 1. Resumen de Implementación y Entregables

Conforme a la especificación de `docs/nfl_model_execution/03-features-and-dataset.md`, se consolidó el catálogo de features canónicas y los datasets versionados para los dos variantes autorizados: `team_only` y `injury_aware`.

### Catálogo de Features por Grupo

1. **Grupo A — Baseline Temporal (14 features):**
   - Win rate histórico, margen promedio, puntos a favor y en contra observados estrictamente antes de la fecha del partido.
   - Rest days calculados por kickoff UTC real.
   - Features: `home_win_rate`, `away_win_rate`, `home_avg_margin`, `away_avg_margin`, `home_avg_points_for`, `away_avg_points_for`, `home_avg_points_against`, `away_avg_points_against`, `home_days_rest`, `away_days_rest`, `home_is_back_to_back`, `away_is_back_to_back`, `home_games_prior`, `away_games_prior`.
   - Diferenciales: `diff_win_rate`, `diff_avg_margin`, `diff_avg_points_for`, `diff_days_rest` (4 features).

2. **Grupo B — Eficiencia de Equipo desde nflverse PBP (8 features):**
   - EPA/play y success rate ofensivo y defensivo calculados desde play-by-play histórico previo:
   - `home_avg_off_epa_per_play`, `away_avg_off_epa_per_play`, `home_avg_off_success_rate`, `away_avg_off_success_rate`.
   - `home_avg_def_epa_allowed_per_play`, `away_avg_def_epa_allowed_per_play`, `home_avg_def_success_allowed`, `away_avg_def_success_allowed`.

3. **Grupo C — Disponibilidad e Injuries (6 features, exclusivo de `injury_aware`):**
   - `home_ppr_lost_pct`, `away_ppr_lost_pct`, `home_off_snaps_lost_pct`, `away_off_snaps_lost_pct`, `home_def_snaps_lost_pct`, `away_def_snaps_lost_pct`.
   - Prohibida su imputación con ceros o medias en `team_only`.

### Esquema y Dimensiones Canónicas

| Dataset | Filas | Columnas Totales | Features Numéricas | Fechas Cubiertas | Exclusiones Contabilizadas | Hash SHA-256 |
|---|---:|---:|---:|---|---|---|
| `nfl_team_only_training.csv` | 4,398 | 39 | 26 | 2010-09-19 a 2026-10-05 | 16 sin historial, 13 empates | `4acb0edcc0345884ba71d297a8c8f1080dd5501719cb19065e99563e6e5d60a4` |
| `nfl_training.csv` (`injury_aware`) | 184 | 45 | 32 | 2020-09-27 a 2025-01-26 | 4,214 sin disponibilidad verificada | `9013d946ed2c0ef84f0532d64d779936ebc67e2ddc31d2de48fa115b912f969e` |

---

## 2. Invarianza Temporal y Prevención de Fugas

Se verificó mediante pruebas de software automatizadas (`tests/test_nfl_features_phase03.py`):
1. **Invarianza ante mutaciones futuras:** La alteración de puntajes o estadísticas en juegos posteriores a la fecha de corte no modifica en un solo bit el vector de features de partidos anteriores.
2. **Aislamiento el mismo día:** Partidos disputados el mismo día de calendario no intercambian resultados ni alimentan features de partidos concurrentes.
3. **Estricta precedencia:** Para el 100% de las 4,398 filas, `feature_max_observed_game_date < game_date`.
4. **Paridad entrenamiento/inferencia:** `build_matchup_features` produce valores idénticos a `build_temporal_dataset`.

---

## 3. Evidencia de Validación

```bash
docker exec -e PYTHONPATH=/app sports_api pytest -v tests/test_nfl_features_phase03.py
```
**Resultado:** 8 passed en 1.25s.
