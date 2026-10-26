# Informe Técnico de Reconciliación — Modelos, Datasets y Variantes NFL

**Fecha de emisión:** 2026-10-25  
**Objetivo:** Resolver la aparente discrepancia metodológica entre el reporte de Fase 04 (`team_only`, 824/825 partidos en holdout) y el artefacto activo en `backend/ml/models/nfl/` (`injury_aware`, 95 partidos en holdout), formalizando la procedencia, cortes temporales, features, modelos y código de entrenamiento de cada variante.

---

## 1. Resumen Ejecutivo de la Reconciliación

No existe una contradicción física de datos, sino **dos linajes de experimentación temporal claramente diferenciados** con propósitos, requisitos de cobertura y espacios de hipótesis distintos:

1. **Linaje Base Macro (`team_only` / Fase 04):**
   - **Objetivo:** Establecer el baseline temporal predictivo utilizando exclusivamente estadísticas macro de equipo (EPA acumulado, success rate, puntos, márgenes y descanso).
   - **Dataset:** `data/processed/nfl_team_only_training.csv` (4,412 partidos, 2010 a 2026).
   - **Holdout / Lockbox:** 825 partidos (`2023-10-29` a `2026-10-12`). *(Reporte preliminar de Fase 04 citaba 824 partidos hasta el `2026-10-05` previo a la última semana)*.
   - **Modelo Ganador:** `LogisticRegression` simple. HistGradientBoosting y el Ensemble fueron formalmente **rechazados** por parsimonia (degradaron el Brier score y sufrieron sobreajuste).

2. **Linaje Multimodal con Disponibilidad (`injury_aware` / Fase 06+):**
   - **Objetivo:** Cuantificar el impacto incremental de bajas e inactivos verificados point-in-time sobre la probabilidad de victoria.
   - **Dataset:** `data/processed/nfl_training.csv` (672 partidos, 2013 a 2025).
   - **Razón del tamaño reducido:** **3,740 partidos excluidos** debido a que carecen de snapshots de inactivos verificados para ambos equipos. Por principio de gobernanza **fail-closed**, no se imputaron datos desconocidos con 0% de lesiones.
   - **Holdout / Lockbox:** 95 partidos (`2022-11-20` a `2025-01-26`).
   - **Modelo Vigente en Artefacto:** Ensamble de 3 modelos (`logistic` acotado, `hist_gradient_boosting` monótono y `random_forest`).

> [!WARNING]
> **Falsa Equivalencia Metodológica:**
> Las métricas de `team_only` (825 partidos en holdout) y de `injury_aware` (95 partidos en holdout) **no son directamente comparables**. Evalúan periodos cronológicos distintos, con soportes muestrales de escala diferente (825 vs 95) y con diferentes niveles de incertidumbre muestral.

---

## 2. Matriz Comparativa Exhaustiva

| Parámetro / Dimensión | Variante 1: `team_only` (Base Macro) | Variante 2: `injury_aware` (Multimodal) |
|---|---|---|
| **Identificador de Versión** | `nfl_team_only_temporal_v1` | `nfl_temporal_v1` |
| **Archivo CSV Procesado** | `data/processed/nfl_team_only_training.csv` | `data/processed/nfl_training.csv` |
| **Tamaño en Disco** | 2,176,552 bytes (~2.18 MB) | 401,374 bytes (~401 KB) |
| **SHA-256 del Dataset CSV** | `d19b9908b0e4dfae2bcd61a4b202110f60d71fa3f63038afd0fd72cfd1255375` | `94f7d8cc560237b76c2bc92d9932a279d7815560de0ae29882b499cfd7f2ba48` |
| **Archivo Manifest** | `data/processed/nfl_team_only_training.manifest.json` | `data/processed/nfl_training.manifest.json` |
| **SHA-256 Manifiesto Fuente** | `dd68beecef952ccd001ce0104380ed9ac9088c445fbedbacaef4a843498fb445` | `dd68beecef952ccd001ce0104380ed9ac9088c445fbedbacaef4a843498fb445` |
| **Total de Partidos** | **4,412** | **672** |
| **Filtros de Exclusión Aplicados** | 16 sin historial previo (<1 juego)<br>14 empates excluidos | 16 sin historial previo (<1 juego)<br>14 empates excluidos<br>**3,740 sin disponibilidad verificada** |
| **Rango Temporal Total** | `2010-09-19` a `2026-10-12` (16 temporadas) | `2013-09-15` a `2025-01-26` (semanas con reportes) |
| **Número de Features** | **26 features** | **32 features** (26 macro + 6 lesiones) |
| **Features de Disponibilidad** | Ninguna (`include_availability: false`) | `home_ppr_lost_pct`, `home_off_snaps_lost_pct`, `home_def_snaps_lost_pct`, `away_ppr_lost_pct`, `away_off_snaps_lost_pct`, `away_def_snaps_lost_pct` |
| **Muestra Folds CV (80%)** | 3,587 partidos en 4 folds temporales | 577 partidos en 4 folds temporales |
| **Muestra Holdout (20%)** | **825 partidos** | **95 partidos** |
| **Fechas del Holdout** | `2023-10-29` a `2026-10-12` | `2022-11-20` a `2025-01-26` |
| **Majority Baseline Brier** | 0.2477 (Log Loss 0.6885) | 0.2478 (Log Loss 0.6887) |
| **Logistic Regression Brier** | **0.2258** (Log Loss 0.6424, Acc 63.15%) | **0.2260** (Log Loss 0.6433, Acc 63.16%) |
| **HistGradientBoosting Brier** | 0.2518 (Peor que baseline) | 0.2295 |
| **Ensemble Blend Brier** | 0.2312 (Peor que Logistic) | **0.2264** (Log Loss 0.6440, Acc 67.37%) |
| **Comportamiento en Folds** | Logistic superó a baseline en 4 de 4 folds | Ensemble **perdió contra baseline en Fold 4** (0.2659 vs 0.2534) |
| **Decisión de Selección** | `single_model_retained` (Logistic) | `ensemble_selected` (Ensamble de 3 modelos) |
| **Criterio de Selección** | Parsimonia estricta de Fase 04 | Bifurcación hardcodeada en `train_temporal.py` |
| **Directorio de Artefactos** | `backend/ml/models/nfl/team_only/` | `backend/ml/models/nfl/` |
| **Gate Operativo** | `production_enabled: false` | `production_enabled: true` |

---

## 3. Desglose del Espacio de Features

### 3.1 Features Macro de Equipo (Comunes a Ambas Variantes: 26)
Calculadas estrictamente con partidos finalizados antes de la fecha del evento (`observed < game_date`), con ventana móvil de 10 juegos y mínimo de 1 juego de historial:
1. `home_win_rate` / `away_win_rate`: % de victorias en los últimos 10 juegos.
2. `home_avg_margin` / `away_avg_margin`: Margen promedio de puntos en los últimos 10 juegos.
3. `home_avg_points_for` / `away_avg_points_for`: Puntos anotados por partido.
4. `home_avg_points_against` / `away_avg_points_against`: Puntos permitidos por partido.
5. `home_days_rest` / `away_days_rest`: Días transcurridos desde el partido anterior.
6. `home_is_back_to_back` / `away_is_back_to_back`: Indicador booleano (siempre 0 en NFL regular, mantenido por esquema común).
7. `home_games_prior` / `away_games_prior`: Número de juegos disputados en la ventana actual.
8. `home_avg_off_epa_per_play` / `away_avg_off_epa_per_play`: Eficiencia ofensiva en Expected Points Added por jugada.
9. `home_avg_off_success_rate` / `away_avg_off_success_rate`: Tasa de éxito ofensivo (% de jugadas con EPA positivo).
10. `home_avg_def_epa_allowed_per_play` / `away_avg_def_epa_allowed_per_play`: Eficiencia defensiva permitida.
11. `home_avg_def_success_allowed` / `away_avg_def_success_allowed`: Tasa de éxito permitida al rival.
12. `diff_win_rate`, `diff_avg_margin`, `diff_avg_points_for`, `diff_days_rest`: Diferenciales directos (Local − Visitante).

### 3.2 Features de Lesiones y Disponibilidad (Exclusivas de `injury_aware`: 6)
Inyectadas en `backend/scripts/ml/build_training_datasets.py` a partir de reportes oficiales de inactivos verificados point-in-time:
1. `home_ppr_lost_pct`: % de puntos fantasy PPR de la ofensiva local ausentes para el partido por lesión o baja.
2. `home_off_snaps_lost_pct`: % de volumen de jugadas ofensivas de los jugadores inactivos respecto al total del equipo.
3. `home_def_snaps_lost_pct`: % de volumen de jugadas defensivas de defensores inactivos.
4. `away_ppr_lost_pct`: % de producción fantasy PPR ausente en la ofensiva visitante.
5. `away_off_snaps_lost_pct`: % de snaps ofensivos ausentes en el equipo visitante.
6. `away_def_snaps_lost_pct`: % de snaps defensivos ausentes en el equipo visitante.

---

## 4. Auditoría del Código de Entrenamiento e Inferencia

### 4.1 Construcción de Datasets (`backend/scripts/ml/build_training_datasets.py`)
- El script soporta dos invocaciones mediante `--variant`:
  - `python build_training_datasets.py --sport nfl --variant team_only`
  - `python build_training_datasets.py --sport nfl --variant injury_aware`
- En la variante `injury_aware`, la línea 96 ejecuta:
  ```python
  dataset = dataset.dropna(subset=availability_columns).copy()
  quality['rows_without_verified_availability'] = int(before_coverage_filter - len(dataset))
  ```
  Esto descarta de inmediato cualquier partido que no tenga el registro simultáneo de ambos equipos en la tabla de disponibilidad histórica verificada, asegurando que ningún partido use valores ficticios o ceros no documentados.

### 4.2 Entrenamiento y Selección (`backend/ml/training/train_temporal.py`)
- Al entrenar con `--variant team_only`, guarda los modelos en `backend/ml/models/nfl/team_only/` y fija `production_enabled: false`. Asigna `selected_model: "logistic"` conforme a las directrices de parsimonia.
- Al entrenar con `--variant injury_aware`, guarda los modelos en `backend/ml/models/nfl/` y en la versión actual asigna `selected_model: "ensemble"` con `production_enabled: true`.

---

## 5. Dictamen y Conclusiones del Punto 1

1. **Reconciliación Exitosa:** Se confirma la procedencia, SHA-256, estructura y reglas de exclusión de ambos datasets y artefactos.
2. **Delimitación de Experimentos:** El reporte de Fase 04 aplica **únicamente** a la variante macro `team_only` sobre 4,412 partidos y su holdout de 825 juegos. No debe confundirse con la variante `injury_aware` de 672 partidos y holdout de 95 juegos.
3. **Discrepancia de Selección de Ensamble Identificada (para Punto 4 del Plan):** En la variante `injury_aware`, el Ensamble Blend perdió contra el baseline de mayoría en el Fold 4 (Brier 0.2659 vs 0.2534). La selección de "ensemble" en `train_temporal.py` ocurrió por regla estática en lugar de un criterio de dominancia consistente entre folds y holdout.
4. **Vía Libre para el Punto 2:** Queda fijada la separación entre ambas variantes, permitiendo establecer un periodo de evaluación prospectivo independiente para validar candidatos sin sesgos de selección retrospectiva.
