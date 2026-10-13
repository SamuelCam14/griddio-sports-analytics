# Reporte de Ejecución — Fase 04: Baselines, Modelos, Ensemble y Validación

**Fecha de ejecución:** 2026-10-12  
**Alcance:** Exclusivamente NFL (`nfl_team_only_temporal_v1`).  
**Estado de la fase:** `review` (entregado para auditoría).  
**Entorno:** Docker container `sports_api` (Python 3.12.15, scikit-learn 1.6.1). Host: Windows 11.

---

## 1. Protocolo de Validación y Comparación de Modelos

Conforme a `docs/nfl_model_execution/04-model-validation.md`, se evaluaron cuatro enfoques sobre el dataset `nfl_team_only_training.csv` (4,398 partidos, 2010 a 2026):
1. **Majority Baseline (Frecuencia de victoria local):** Brier 0.2469, Log Loss 0.6869 en holdout.
2. **Logistic Regression (StandardScaler + L2):** Baseline estadístico paramétrico.
3. **HistGradientBoosting (Gradient Boosting de Scikit-Learn):** Candidato no lineal.
4. **Ensemble Blend (50/50 blend de Logistic + HistGradientBoosting):** Evaluado explícitamente en CV y holdout.

### Resultados en Holdout Cronológico Bloqueado (824 partidos: 2023-10-22 a 2026-10-05)

| Modelo / Enfoque | Brier Score (menor es mejor) | Log Loss | Accuracy | ROC AUC | ECE (Calibración) | Supera Majority Brier en todos los Folds |
|---|---:|---:|---:|---:|---:|:---:|
| **Majority Baseline** | 0.2469 | 0.6869 | 55.2% | 0.5000 | — | — |
| **Logistic Regression** | **0.2267** | **0.6446** | **62.99%** | **0.6682** | **0.01728** | **SÍ (4 de 4)** |
| **HistGradientBoosting** | 0.2518 | 0.7073 | 58.25% | 0.6203 | 0.0412 | NO (peor que baseline) |
| **Ensemble Blend** | 0.2330 | 0.6558 | 60.92% | 0.6540 | 0.0210 | NO (peor que Logistic) |

---

## 2. Decisión de Selección de Modelo (Auditoría de Parsimonia)

Conforme a la directriz explícita de Fase 04:
> *"Si ensemble no supera de forma consistente al mejor modelo, mantener el modelo simple."*

1. **Veredicto:** Se retiene **Logistic Regression** (`nfl_team_only_temporal_v1`) como el único modelo seleccionado.
2. **Rechazo del Ensemble y de HistGradientBoosting:** HistGradientBoosting presentó sobreajuste en los folds y degradó el Brier score en holdout (0.2518, peor que el baseline de 0.2469). El ensamble consecuentemente degradó la calibración y el Brier score a 0.2330 (frente al 0.2267 de Regresión Logística pura).
3. **Evaluación de Calibración (ECE sin Calibración Posterior):**
   - Se mide el Error de Calibración Esperado (ECE = 0.01728 / 1.728% en holdout, registrado exactamente en el artefacto local `model_metadata.json`) como métrica diagnóstica descriptiva.
   - El código en ejecución **no aplica calibrador posterior** (no hay escalamiento de Platt ni regresión isotónica post-hoc acoplada a la inferencia).
4. **Estado del Holdout y Gobernanza Experimental:**
   - Se documenta explícitamente que la partición holdout (824 partidos, 2023-2026) **participó en la comparación y selección final** entre modelos (Logistic vs HGB vs Blend), por lo que **ya no constituye una confirmación out-of-sample independiente no contaminada**.
   - Toda salida generada se conserva exclusivamente como **experimental**, y el gate de producción se mantiene estrictamente **cerrado** (`production_enabled: false`, `production_eligible: false`).

---

## 3. Evidencia de Validación

```bash
docker exec -e PYTHONPATH=/app sports_api pytest -v tests/test_nfl_models_phase04.py
```
**Resultado:** 4 passed en 2.17s.
